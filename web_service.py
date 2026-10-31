"""Desktop web UI adapter. Payload forwarding stays in the existing bridge engine."""

from collections import deque
import copy
from functools import wraps
import ipaddress
import json
from pathlib import Path
import queue
import re
import threading
import time

from serial.tools import list_ports

from c5_config import C5Client, ConfigError, changed_config, public_config
from c5_usb_config import C5USBClient
from device_capabilities import capabilities
from gui_state import CHOICES, BOOLEANS, DRAFT_FIELDS, FORM_FIELDS, VERSION, clean_state, load_state, save_state
from gui_support import BridgeController, default_settings, load_profile, settings_to_args
from reset_control import FiveClickGate, ResetError, perform_reset

BASE = Path(__file__).resolve().parent
LEVEL = re.compile(r"^\s*([EWIDV])\s+\(\s*\d+\s*\)")
ANSI = re.compile(r"\x1b\[[0-?]*[ -/]*[@-~]")
EDIT_KEYS = {key for fields in FORM_FIELDS.values() for key in fields} | {"config_url", "config_transport"}


def endpoint(function):
    """Only data crosses the JS bridge; never expose traceback/password frames."""
    @wraps(function)
    def wrapped(self, *args, **kwargs):
        try:
            return {"ok": True, "data": function(self, *args, **kwargs)}
        except (ValueError, OSError, KeyError, TypeError) as exc:
            return {"ok": False, "error": str(exc) if isinstance(exc, (ValueError, OSError)) else "请求格式不正确"}
    return wrapped


def default_device_config(device):
    station = {"ssid": "", "static_ip": False, "ip": "192.168.137.200",
               "gateway": "192.168.137.1", "netmask": "255.255.255.0", "dns": "192.168.137.1"}
    result = {"link_mode": 0, "mode": 0, "band": 0, "baud": 1152000, "tcp_port": 8080,
              "ap_channel": 1, "ap_ssid": f"ESP32-{device.upper()}-Bridge", "ap_ip": "192.168.4.1",
              "ap_gateway": "192.168.4.1", "ap_netmask": "255.255.255.0", "net_role": 0,
              "auto_reconnect": True, "remote_ip": "192.168.137.1", "remote_port": 8080,
              "local_port": 6060, "reconnect_ms": 1000, "connect_timeout_ms": 10000,
              "up_kb": 4096, "down_kb": 1024, "pc": copy.deepcopy(station), "shared": copy.deepcopy(station)}
    if device in ("c5", "s3"):
        result["reset_pin"] = 0
    return result


def config_to_draft(config):
    result = {}
    for group, keys in DRAFT_FIELDS.items():
        source = config.get(group, {}) if group in ("pc", "shared") else config
        result[group] = {key: copy.deepcopy(source[key]) if (group, key) in CHOICES or key in BOOLEANS
                         else str(source[key]) for key in keys if key in source}
    return result


def draft_to_config(draft, device):
    result = default_device_config(device)
    for group, values in draft.items():
        if group in DRAFT_FIELDS and isinstance(values, dict):
            target = result[group] if group in ("pc", "shared") else result
            for key, value in values.items():
                if key in DRAFT_FIELDS[group]:
                    if type(target.get(key)) is int and isinstance(value, str) and value.isascii() and value.isdigit():
                        value = int(value)
                    target[key] = value
    return result


class WebService:
    def __init__(self, controller=None, state_path=None):
        self._lock = threading.RLock()
        self._controller = controller or BridgeController()
        self._path = Path(state_path) if state_path else BASE / "tcp2com_last_state.json"
        self._settings = {**default_settings(), "device": "c5", "mode": "tcp-server", "com": "COM1"}
        self._drafts = {}
        self._baseline = self._identity = None
        self._epoch = 0
        self._config_busy = False
        self._config_done = threading.Event()
        self._config_done.set()
        self._config_transport = None
        self._closing = False
        self._timer = None
        self._dirty = False
        self._logs = deque(maxlen=3000)
        self._sequence = 0
        self._log_generation = 0
        self._sample = None
        self._rates = [0, 0, 0]
        self._gate = FiveClickGate()
        self._reset_context = None
        self._reset_cancel = threading.Event()
        self._reset_thread = None
        self._reset_message = "3 秒内连续点击 5 次，复位外部主控"
        self._status = "尚未读取设备"
        self._state_status = "设置会自动记住；连接需手动启动"
        saved, warning = load_state(self._path)
        if saved:
            for values in saved["forms"].values():
                self._settings.update(values)
            for key in ("config_url", "config_transport"):
                if key in saved:
                    self._settings[key] = saved[key]
            self._drafts = saved["device_drafts"]
            self._state_status = "已恢复上次设置；连接尚未启动"
        elif warning is None and (BASE / "tcp2com_config.json").is_file():
            try:
                self._settings.update(load_profile(BASE / "tcp2com_config.json", "c5-spi-tcp"))
                self._state_status = "已读取旧默认配置；连接尚未启动"
            except (OSError, ValueError):
                pass
        if warning:
            self._state_status = warning
        for fields in FORM_FIELDS.values():
            for key in fields:
                if key not in BOOLEANS:
                    value = self._settings.get(key)
                    self._settings[key] = "" if value is None else str(value)
        self._controller.ui.message("Wi-Fi Bridge 工作台已就绪。业务桥接与 USB 需要手动连接。")

    def _document(self):
        return {"version": VERSION, "forms": {name: {key: self._settings.get(key, "") for key in keys}
                for name, keys in FORM_FIELDS.items()}, "config_transport": self._settings["config_transport"],
                "config_url": self._settings["config_url"], "device_drafts": copy.deepcopy(self._drafts)}

    def _persist(self):
        with self._lock:
            self._timer = None
            if not self._dirty:
                return
            try:
                save_state(self._path, self._document())
                self._dirty = False
                self._state_status = "已记住界面设置"
            except (OSError, ValueError):
                self._state_status = "无法保存界面设置，请检查目录权限"

    def _schedule_save(self):
        self._dirty = True
        if self._timer:
            self._timer.cancel()
        self._timer = threading.Timer(.5, self._persist)
        self._timer.daemon = True
        self._timer.start()

    def _context(self):
        return (self._settings["device"], self._settings["config_transport"],
                self._settings["config_url"], str(self._settings["usb_port"] or "").strip().casefold())

    def _invalidate(self, message="连接已改变，请重新读取设备"):
        self._epoch += 1
        self._baseline = self._identity = None
        self._status = message

    def _ensure_open(self):
        if self._closing:
            raise ValueError("窗口正在关闭")

    def _client(self):
        device = self._settings["device"]
        if not capabilities(device).usb:
            raise ConfigError("ESP-01S 使用透传及远程复位，不支持此配置接口")
        if self._settings["config_transport"] == "usb":
            monitor = self._controller.snapshot()[1]
            if not monitor or monitor.port.casefold() != str(self._settings["usb_port"]).strip().casefold():
                raise ConfigError("请先连接所选 USB COM")
            if monitor.capabilities != capabilities(device):
                raise ConfigError("设备型号已改变，请重新连接 USB")
            client = C5USBClient(monitor, device=device)
        else:
            client = C5Client(str(self._settings["config_url"]).strip(), device=device)
            if self._identity and self._identity[:3] == client.identity and len(self._identity) == 4:
                client.bind_session(self._identity[3])
        if self._identity == client.identity:
            client.baseline = copy.deepcopy(self._baseline)
        return client

    def _check_usb_identity(self):
        if self._identity and self._identity[0] == "usb":
            monitor = self._controller.snapshot()[1]
            try:
                identity = monitor.config_identity() if monitor else None
            except ConfigError:
                identity = None
            if identity != self._identity:
                self._invalidate("USB 连接或配网会话已改变，请重新读取")

    @endpoint
    def initial(self):
        with self._lock:
            return {"settings": {key: self._settings.get(key, "") for key in EDIT_KEYS},
                    "drafts": copy.deepcopy(self._drafts),
                    "config": draft_to_config(self._drafts.get(self._settings["device"], {}), self._settings["device"]),
                    "state_status": self._state_status}

    @endpoint
    def ports(self):
        return [{"device": port.device, "description": port.description} for port in list_ports.comports()]

    @endpoint
    def update_settings(self, values):
        with self._lock:
            self._ensure_open()
            if not isinstance(values, dict) or set(values) - EDIT_KEYS:
                raise ValueError("未知设置字段")
            old = self._context()
            # clean_state allows incomplete text drafts, but whitelists types and choices.
            candidate = self._document()
            for fields in candidate["forms"].values():
                fields.update({key: value for key, value in values.items() if key in fields})
            candidate.update({key: values[key] for key in ("config_url", "config_transport") if key in values})
            cleaned, warnings = clean_state(candidate)
            if warnings:
                raise ValueError("设置包含无效字段或类型")
            context_changed = any(key in values and values[key] != self._settings[key]
                                  for key in ("device", "config_transport", "config_url", "usb_port"))
            if self._config_busy and context_changed:
                raise ValueError("配置请求进行中，请等待完成后切换设备或配置连接")
            self._settings.update(values)
            if old != self._context():
                self._invalidate()
                if old[0] != self._settings["device"] and not self._gate.busy:
                    self._gate.finish()
                    self._reset_context = None
            self._schedule_save()
            return {"epoch": self._epoch}

    @endpoint
    def bridge(self, command):
        with self._lock:
            self._ensure_open()
            if command not in ("start", "restart", "stop"):
                raise ValueError("未知桥接操作")
            if self._config_busy:
                raise ValueError("配置请求进行中，请等待完成")
            values = copy.deepcopy(self._settings)
            values["usb_port"] = values["usb_log"] = None
            args = settings_to_args(values) if command != "stop" else None
            if not self._controller.submit(command, args):
                raise ValueError("连接操作正在进行，请稍后再试")
            return True

    @endpoint
    def usb(self, connect):
        with self._lock:
            self._ensure_open()
            if connect:
                if self._config_busy:
                    raise ValueError("配置请求进行中，请等待完成后更改 USB")
                if not capabilities(self._settings["device"]).usb or not self._settings["usb_port"]:
                    raise ValueError("请选择 C5/S3 和原生 USB COM")
                app = self._controller.snapshot()[0]
                values = vars(copy.deepcopy(app.args)) if app else {"mode": "tcp-server", "com": "diagnostics-only"}
                values.update({key: self._settings[key] for key in ("device", "usb_port", "usb_baud", "usb_log")})
                args = settings_to_args(values)
            else:
                args = None
            if not self._controller.submit("usb", args):
                raise ValueError("连接操作正在进行，请稍后再试")
            if connect or (self._identity and self._identity[0] == "usb") or self._config_transport == "usb":
                self._invalidate("USB 连接已改变；如保存已发送，请重新读取核对结果，勿直接重发")
            return True

    @endpoint
    def remember_draft(self, device, config):
        with self._lock:
            self._ensure_open()
            if device not in ("c5", "s3"):
                raise ValueError("不支持的设备")
            candidate = self._document()
            candidate["device_drafts"][device] = config_to_draft(config)
            cleaned, warnings = clean_state(candidate)
            if warnings:
                raise ValueError("设备草稿包含无效字段")
            self._drafts = cleaned["device_drafts"]
            self._schedule_save()
            return True

    @endpoint
    def read_device(self):
        with self._lock:
            self._ensure_open()
            if self._config_busy or self._controller.snapshot()[4]:
                raise ValueError("连接或配置操作进行中")
            client = self._client()
            self._invalidate("正在读取设备配置…")
            epoch = self._epoch
            self._config_busy = True
            self._config_done.clear()
            self._config_transport = client.identity[0]
        try:
            document = client.read()
            with self._lock:
                if epoch != self._epoch or self._closing:
                    raise ConfigError("配置连接已改变，已丢弃旧读取结果")
                if client.identity[0] == "usb":
                    current = self._controller.snapshot()[1]
                    if current is None or current.config_identity() != client.identity:
                        raise ConfigError("USB 已重连，请重新读取")
                self._baseline = copy.deepcopy(document)
                self._identity = client.identity
                self._status = "已读取当前设备配置；修改后需明确保存"
                if document.get("unsaved"):
                    self._status += "；当前运行值尚未保存到 NVS"
                if document.get("migration_requires_setup"):
                    self._status += "；旧 SPI 恢复：业务接口已停用，请选择新接口并保存重启"
                device = self._settings["device"]
                if device not in self._drafts:
                    self._drafts[device] = config_to_draft(document)
                    self._schedule_save()
                return document
        except (ValueError, OSError) as exc:
            with self._lock:
                self._status = str(exc)
            raise
        finally:
            with self._lock:
                self._config_busy = False
                self._config_transport = None
                self._config_done.set()

    def _patch(self, edited, passwords, clear):
        self._check_usb_identity()
        client = self._client()
        if self._baseline is None or self._identity != client.identity:
            raise ConfigError("请先从当前连接读取设备，之后才能保存")
        candidate = copy.deepcopy(edited)
        for key in ("reset_pin", "net_role", "auto_reconnect", "remote_ip", "remote_port", "local_port",
                    "reconnect_ms", "connect_timeout_ms", "up_kb", "down_kb"):
            if key not in self._baseline:
                candidate.pop(key, None)
        patch = changed_config(self._baseline, candidate, passwords, clear, device=self._settings["device"])
        return client, patch

    @endpoint
    def preview_save(self, edited, passwords, clear):
        with self._lock:
            self._ensure_open()
            if self._config_busy:
                raise ValueError("配置操作进行中")
            _, patch = self._patch(edited, passwords, clear)
            changes = []
            for key, value in patch.items():
                for field, item in (value.items() if isinstance(value, dict) else [(None, value)]):
                    name = f"{key}.{field}" if field else key
                    secret = field == "password" or key == "ap_password"
                    changes.append({"field": name, "value": ("（清空密码）" if item == "" else "（更新密码）") if secret else str(item)})
            return {"changes": changes, "needed": bool(patch or self._baseline.get("unsaved")
                    or self._baseline.get("migration_requires_setup")), "epoch": self._epoch}

    @endpoint
    def save_device(self, edited, passwords, clear, epoch):
        with self._lock:
            self._ensure_open()
            if self._config_busy or epoch != self._epoch:
                raise ConfigError("读取依据已改变，请重新读取并检查修改")
            client, patch = self._patch(edited, passwords, clear)
            if not patch and not (self._baseline.get("unsaved") or self._baseline.get("migration_requires_setup")):
                return {"saved": False}
            self._config_busy = True
            self._config_done.clear()
            self._config_transport = client.identity[0]
            self._invalidate("正在保存到 NVS，等待设备确认…")
        try:
            client.save(patch)
            with self._lock:
                self._status = "已保存并请求重启；请重新进入配网并读取核对"
            return {"saved": True}
        except (ValueError, OSError) as exc:
            with self._lock:
                self._status = str(exc)
            raise
        finally:
            with self._lock:
                self._config_busy = False
                self._config_transport = None
                self._config_done.set()

    @endpoint
    def import_config(self, text):
        with self._lock:
            if not isinstance(text, str) or len(text.encode("utf-8")) > 131072:
                raise ValueError("备份文件过大")
            document = json.loads(text.lstrip("\ufeff"))
            if not isinstance(document, dict) or document.get("format") not in ("c5-user-config-v1", "wifi-bridge-user-config-v1"):
                raise ValueError("不支持的设备备份格式")
            return public_config(document["config"], self._settings["device"])

    @endpoint
    def backup_config(self, edited):
        with self._lock:
            device = self._settings["device"]
            return {"format": "wifi-bridge-user-config-v1", "device": device, "config": public_config(edited, device)}

    def _drain_logs(self):
        for _ in range(500):
            try:
                timestamp, source, text = self._controller.ui.events.get_nowait()
            except queue.Empty:
                break
            text = ANSI.sub("", text)
            match = LEVEL.match(text) if source == "usb" else None
            self._sequence += 1
            self._logs.append({"id": self._sequence, "time": time.strftime("%H:%M:%S", time.localtime(timestamp)),
                               "source": source, "level": match.group(1) if match else "", "text": text})

    @endpoint
    def clear_logs(self):
        with self._lock:
            self._drain_logs()
            self._logs.clear()
            self._log_generation += 1
            return self._sequence

    @endpoint
    def export_logs(self):
        with self._lock:
            self._drain_logs()
            return "\n".join(f"{row['time']} [{'USB / ESP' if row['source'] == 'usb' else '上位机'}] {row['text']}"
                             for row in self._logs) + "\n"

    @endpoint
    def poll(self, cursor=0):
        with self._lock:
            self._drain_logs()
            self._check_usb_identity()
            app, usb, running, state, busy = self._controller.snapshot()
            try:
                self._controller.ui.stats_interval = max(.1, min(float(self._settings["stats_interval"]), 5))
            except (ValueError, TypeError):
                self._controller.ui.stats_interval = .25
            stats, queued, peak = {}, 0, 0
            now = time.monotonic()
            connected, address = False, "业务桥接已停止"
            if app:
                stats = app.stats.snapshot()
                queued, peak = app.q_tcp_to_com.byte_snapshot()
                current = [stats[key] for key in ("tcp_rx_bytes", "tcp_to_com_bytes", "com_to_tcp_bytes")]
                if self._sample and self._sample[0] is app:
                    dt = now - self._sample[1]
                    if dt >= .1:
                        self._rates = [max(0, (a-b) / dt) for a, b in zip(current, self._sample[2])]
                        self._sample = (app, now, current)
                else:
                    self._rates = [0, 0, 0]
                    self._sample = (app, now, current)
                sock = app.conn.snapshot()[0]
                connected = sock is not None
                try:
                    if app.udp:
                        address = f"UDP 已绑定 {app.udp.address} · 固定对端 {app.udp.peer}（不代表设备在线）"
                    elif sock:
                        address = f"本地 {sock.getsockname()} → 对端 {sock.getpeername()}"
                    else:
                        address = "等待设备连接" if app.server else "连接中 / 重试中"
                except OSError:
                    address = "连接状态正在变化"
            else:
                self._sample, self._rates = None, [0, 0, 0]
            usb_status = None
            if usb:
                usb_status = copy.deepcopy(usb.status())
                updated = usb_status.pop("updated_at", None)
                usb_status["age"] = None if updated is None else max(0, now - updated)
                usb_status["port"] = usb.port
                usb_status["closing"] = usb.stop_event.is_set()
            applied = False
            valid = True
            try:
                values = copy.deepcopy(self._settings)
                values["usb_port"] = values["usb_log"] = None
                edited = vars(settings_to_args(values))
                applied = running is not None and all(edited.get(key) == value for key, value in running.items()
                    if key not in ("usb_port", "usb_baud", "usb_log", "config_url", "config_transport", "stats_interval"))
            except ValueError:
                valid = False
            self._gate.expire(now)
            return {"state": state, "running": bool(app), "connected": connected, "busy": busy,
                    "running_settings": running, "applied": applied, "valid": valid, "address": address,
                    "rates": self._rates, "stats": stats, "queued": queued, "peak": peak,
                    "usb": usb_status, "config_busy": self._config_busy, "config_status": self._status,
                    "baseline": copy.deepcopy(self._baseline), "epoch": self._epoch,
                    "state_status": self._state_status, "log_dropped": self._controller.ui.dropped,
                    "logs": [row for row in self._logs if row["id"] > cursor],
                    "cursor": self._sequence, "log_generation": self._log_generation,
                    "reset": {"count": self._gate.count, "busy": self._gate.busy, "message": self._reset_message}}

    @endpoint
    def reset_click(self, host="", port=8081):
        with self._lock:
            self._ensure_open()
            device = self._settings["device"]
            app = self._controller.snapshot()[0]
            if self._gate.busy:
                raise ValueError("复位请求进行中")
            if device == "esp01s":
                if not app or app.args.device != "esp01s" or app.args.no_reset_ui or app.close_event.is_set():
                    raise ValueError("请启动 ESP-01S TCP 客户端桥接并启用复位入口")
                host, port = app.args.host, app.args.control_port
            host = str(ipaddress.IPv4Address(str(host).strip()))
            port = int(port)
            if not 1 <= port <= 65535:
                raise ValueError("端口应为 1～65535")
            context = (device, host, port, id(app) if device == "esp01s" else None)
            if context != self._reset_context:
                self._gate.finish()
                self._reset_context = context
            if not self._gate.click(time.monotonic()):
                self._reset_message = f"已点击 {self._gate.count}/5（从首次点击起 3 秒内完成）"
                return {"count": self._gate.count}
            self._reset_message = "正在发送复位请求…"
            self._reset_thread = threading.Thread(target=self._reset_worker, args=(device, host, port), daemon=True)
            self._reset_thread.start()
            return {"count": 0}

    def _reset_worker(self, device, host, port):
        def progress(message):
            with self._lock:
                self._reset_message = message
        try:
            perform_reset(host, port, progress, self._reset_cancel, device=device)
            message = "复位输出已释放；请检查主控状态"
        except ResetError as exc:
            message = str(exc)
        with self._lock:
            self._reset_message = message
            self._controller.ui.message(message)
            self._gate.finish()

    def _shutdown(self):
        with self._lock:
            if self._closing:
                return
            self._closing = True
            self._invalidate()
            if self._timer:
                self._timer.cancel()
            self._persist()
            self._reset_cancel.set()
            self._controller.close()
        self._controller.thread.join(10)
        self._config_done.wait(10)
        if self._reset_thread:
            self._reset_thread.join(6)
