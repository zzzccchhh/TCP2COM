"""One native USB owner for diagnostics and explicit C5 configuration requests."""

import json
import re
import threading
import time
import uuid

import serial

from bridge_settings import _unique_object
from c5_config import ConfigError, SaveOutcomeUnknown

PREFIX = b"C5CFG "


def parse_stats(line):
    """Parse each firmware record independently so repeated names cannot collide."""
    if "stats:" not in line:
        return {}
    record = line.split("stats:", 1)[1].strip()
    if record.startswith("IP="):
        names = {"IP": "IP", "ch": "ch", "RSSI": "RSSI", "session": "session",
                 "local->net": "rate.local_to_net", "net->local": "rate.net_to_local"}
        return {names[key]: value for key, value in re.findall(
            r"(?:^|\s)(IP|ch|RSSI|session|local->net|net->local)=([^\s]+)", record)}
    patterns = (
        (r"^queues local->net=(\S+) peak=(\d+) net->local=(\S+) peak=(\d+)$",
         ("queue.local_to_net", "queue.local_to_net.peak", "queue.net_to_local", "queue.net_to_local.peak")),
        (r"^drops offline=(\d+) stage=(\d+) full=(\d+) session=(\d+) uart_errors=(\d+)$",
         ("drops.offline", "drops.stage", "drops.full", "drops.session", "drops.uart_errors")),
        (r"^link=(\S+) SPI rx/tx=(\d+)/(\d+) protocol_errors=(\d+)(?: .*)?$",
         ("link", "spi.rx", "spi.tx", "protocol_errors")),
        (r"^totals UART rx/tx=(\d+)/(\d+) TCP rx/tx=(\d+)/(\d+)$",
         ("uart.rx", "uart.tx", "network.rx", "network.tx")),
        (r"^UART backlog equivalent=(\d+)/(\d+) ms$", ("backlog.local_to_net_ms", "backlog.net_to_local_ms")),
        (r"^internal free/min=(\d+)/(\d+) PSRAM free=(\d+) stacks free RX/TX/TCP=(\d+)/(\d+)/(\d+) bytes$",
         ("heap.internal.free", "heap.internal.min", "heap.psram.free", "stack.rx", "stack.tx", "stack.tcp")),
    )
    fields = {}
    for pattern, names in patterns:
        match = re.fullmatch(pattern, record)
        if match:
            fields = {key: value for key, value in zip(names, match.groups()) if value is not None}
            break
    return fields


class USBMonitor:
    def __init__(self, port, baud, notify, logfile=None):
        self.port = port
        self.baud = baud
        self.notify = notify
        self.logfile = logfile
        self.stop_event = threading.Event()
        self.thread = None
        self.lock = threading.Lock()
        self.latest = {}
        self.connected = False
        self.updated_at = None
        self.generation = None
        self.boot = None
        self.session = None
        self.pending = None
        self.request_lock = threading.Lock()

    def start(self):
        self.thread = threading.Thread(target=self._run, name="usb-diagnostics", daemon=True)
        self.thread.start()

    def snapshot(self):
        with self.lock:
            return dict(self.latest)

    def status(self):
        with self.lock:
            return {"connected": self.connected, "updated_at": self.updated_at,
                    "fields": dict(self.latest)}

    def config_identity(self):
        with self.lock:
            if not self.connected or self.stop_event.is_set():
                raise ConfigError("USB 未连接；选择 C5 原生 USB COM，点击连接后再读取")
            return ("usb", self.port.casefold(), self.generation, self.boot, self.session)

    def request(self, operation, patch=None, boot=None, generation=None, session=None, timeout=4):
        """Only the USB worker touches the handle; never replay a queued write."""
        with self.request_lock:
            request_id = uuid.uuid4().hex
            envelope = {"v": 2, "id": request_id, "op": operation}
            if session is not None:
                envelope["session"] = session
            if patch is not None:
                encoded = json.dumps(patch, ensure_ascii=False, separators=(",", ":"), allow_nan=False).encode("utf-8")
                if len(encoded) > 2048:
                    raise ConfigError("修改内容超过 2048 字节")
                envelope["patch"] = patch
            if boot is not None:
                envelope["boot"] = boot
            packet = b"\n" + PREFIX + json.dumps(envelope, ensure_ascii=False, separators=(",", ":"), allow_nan=False).encode("utf-8") + b"\n"
            if len(packet) - 2 > 2304:
                raise ConfigError("USB 配置报文过长")
            request = {"id": request_id, "packet": packet, "sent": False, "event": threading.Event(),
                       "reply": None, "failed": False, "deadline": time.monotonic() + timeout}
            with self.lock:
                if not self.connected or self.stop_event.is_set() or (generation is not None and generation != self.generation):
                    raise ConfigError("USB 连接已改变，请重新连接并读取 C5")
                self.pending = request
            try:
                request["event"].wait(timeout)
                with self.lock:
                    response = request["reply"]
                    if response is None or request["failed"]:
                        if operation == "save" and request["sent"]:
                            raise SaveOutcomeUnknown("保存结果待确认：USB 超时或断开，请重连后读取核对，勿直接重发")
                        raise ConfigError("USB 请求超时或断开；请先长按 GPIO24 ≥3 秒后松开进入配网，并检查 COM、固件版本和端口占用")
                    return response
            finally:
                with self.lock:
                    if self.pending is request:
                        self.pending = None
                    request["packet"] = b""

    def _send_request(self, device):
        with self.lock:
            request = self.pending
            if not request or request["sent"] or request["deadline"] <= time.monotonic():
                return
            # Mark before write: a partial/failed write may still reach the board.
            request["sent"] = True
            packet = request["packet"]
        if device.write(packet) != len(packet):
            raise OSError("partial USB configuration write")

    def _config_line(self, raw):
        if not raw.startswith(PREFIX):
            return False
        if len(raw) > 8192:
            return True
        # Never put protocol packets (including malformed/echoed requests) in logs.
        try:
            frame = json.loads(raw[len(PREFIX):].decode("utf-8"), object_pairs_hook=_unique_object)
            if (not isinstance(frame, dict) or type(frame.get("v")) is not int or frame["v"] != 2
                    or type(frame.get("session")) is not int or not 1 <= frame["session"] <= 0xffffffff
                    or type(frame.get("ok")) is not bool or not isinstance(frame.get("boot"), str)
                    or not re.fullmatch(r"[0-9a-f]{16}", frame["boot"])):
                return True
        except (ValueError, UnicodeError):
            return True
        with self.lock:
            request = self.pending
            if request and request["sent"] and frame.get("id") == request["id"]:
                self.boot = frame["boot"]
                self.session = frame["session"]
                request["reply"] = frame
                request["event"].set()
        return True

    def _disconnected(self):
        with self.lock:
            self.connected = False
            self.boot = None
            self.session = None
            if self.pending:
                self.pending["failed"] = True
                self.pending["event"].set()

    def _run(self):
        output = None
        try:
            if self.logfile:
                output = open(self.logfile, "a", encoding="utf-8")
            while not self.stop_event.is_set():
                try:
                    # Set inactive control lines before opening. Never pulse reset/download.
                    device = serial.serial_for_url(self.port, do_not_open=True)
                    device.baudrate = self.baud
                    device.timeout = 0.2
                    device.write_timeout = 0.5
                    device.dtr = False
                    device.rts = False
                    device.open()
                    with self.lock:
                        self.connected = True
                        self.generation = uuid.uuid4().hex
                        self.boot = None
                        self.session = None
                    self.notify(f"USB 日志 / 配置已连接：{self.port}")
                    pending = bytearray()
                    discard = False
                    with device:
                        while not self.stop_event.is_set():
                            self._send_request(device)
                            chunk = device.read(1024)
                            pending.extend(chunk)
                            while b"\n" in pending:
                                raw, _, pending = pending.partition(b"\n")
                                if discard:
                                    discard = False
                                    continue
                                if self._config_line(raw.rstrip(b"\r")):
                                    continue
                                line = raw.decode("utf-8", errors="replace").rstrip("\r")
                                with self.lock:
                                    fields = parse_stats(line)
                                    if fields:
                                        self.latest.update(fields)
                                        self.updated_at = time.monotonic()
                                self.notify(f"USB | {line}")
                                if output:
                                    output.write(line + "\n")
                                    output.flush()
                            if len(pending) > 16384:
                                self.notify("USB 日志行超过 16 KiB，已截断")
                                pending.clear()
                                discard = True
                except (serial.SerialException, OSError, ValueError):
                    self._disconnected()
                    if not self.stop_event.is_set():
                        self.notify("USB 通道不可用；网络透传继续，2 秒后重试")
                        self.stop_event.wait(2)
                finally:
                    self._disconnected()
        except OSError as exc:
            self.notify(f"USB 日志文件打开失败：{exc}；网络透传继续")
        finally:
            if output:
                output.close()

    def close(self):
        self.stop_event.set()
        self._disconnected()
        if self.thread:
            self.thread.join(timeout=2)
