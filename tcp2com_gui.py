#!/usr/bin/env python3
"""Chinese desktop front end; the CLI and byte forwarding engine remain shared."""

from collections import deque
from contextlib import contextmanager
import copy
import gc
import json
from pathlib import Path
import queue
import re
import threading
import time
import tkinter as tk
from tkinter import filedialog, messagebox, ttk
import webbrowser

from c5_config import C5Client, ConfigError, SaveOutcomeUnknown, changed_config, public_config, public_draft_config
from c5_usb_config import C5USBClient
from device_capabilities import DEVICES, BANDS, capabilities
from gui_support import (BridgeController, atomic_json, config_job, default_settings,
                         load_profile, settings_to_args)
from gui_state import DRAFT_FIELDS, FORM_FIELDS, VERSION, load_state, save_state
from reset_control import ResetPanel
from serial.tools import list_ports

BASE = Path(__file__).resolve().parent
DEVICE = {caps.label: key for key, caps in DEVICES.items()}
MODES = {"TCP 客户端（电脑连接设备）": "tcp-client", "TCP 服务端（电脑等待设备）": "tcp-server",
         "UDP（固定对端）": "udp"}
ESP_LEVEL = re.compile(r"^\s*([EWIDV])\s+\(\s*\d+\s*\)")


def payload_interfaces(device):
    if device == "c5":
        return {"请选择接口（旧接口已移除）": -1, "UART 原始透传": 0,
                "SPI传输": 4, "UART GPIO 流控透传": 3}
    return {"UART 原始透传": 0, "SPI WBR/1": 1, "UART WBR/1": 2}


class Form:
    def __init__(self, parent):
        self.variables = {}
        self.widgets = {}
        self.options = {}
        canvas = tk.Canvas(parent, highlightthickness=0)
        scrollbar = ttk.Scrollbar(parent, orient="vertical", command=canvas.yview)
        self.frame = ttk.Frame(canvas, padding=12)
        window = canvas.create_window((0, 0), window=self.frame, anchor="nw")
        self.frame.bind("<Configure>", lambda event: canvas.configure(scrollregion=canvas.bbox("all")))
        canvas.bind("<Configure>", lambda event: canvas.itemconfigure(window, width=event.width))
        canvas.configure(yscrollcommand=scrollbar.set)
        scrollbar.pack(side="right", fill="y")
        canvas.pack(fill="both", expand=True)
        self.frame.columnconfigure(1, weight=1)
        self.row = 0

    def note(self, text):
        label = ttk.Label(self.frame, text=text, wraplength=840, foreground="#46566b", justify="left")
        label.grid(row=self.row, column=0, columnspan=3, sticky="ew", pady=(4, 12))
        self.frame.bind("<Configure>", lambda event: label.configure(wraplength=max(240, event.width - 32)), add="+")
        self.row += 1

    def add(self, key, label, value="", options=None, boolean=False, secret=False, browse=None, serial_port=False):
        variable = tk.BooleanVar(self.frame, value=bool(value)) if boolean else tk.StringVar(self.frame, value=value)
        self.variables[key] = variable
        ttk.Label(self.frame, text=label).grid(row=self.row, column=0, sticky="w", padx=(0, 16), pady=5)
        if boolean:
            widget = ttk.Checkbutton(self.frame, variable=variable)
        elif serial_port:
            widget = ttk.Combobox(self.frame, textvariable=variable, state="normal")
        elif options:
            self.options[key] = options if isinstance(options, dict) else {str(v): v for v in options}
            widget = ttk.Combobox(self.frame, textvariable=variable, values=list(self.options[key]), state="readonly")
        else:
            widget = ttk.Entry(self.frame, textvariable=variable, show="●" if secret else "")
        widget.grid(row=self.row, column=1, sticky="ew", pady=5)
        if browse:
            ttk.Button(self.frame, text="选择…", command=browse).grid(row=self.row, column=2, padx=6)
        self.widgets[key] = widget
        self.row += 1
        self.set(key, value)
        return variable

    def set(self, key, value):
        if key in self.options:
            value = next((label for label, raw in self.options[key].items() if raw == value), value)
        self.variables[key].set("" if value is None else value)

    def get(self, key):
        value = self.variables[key].get()
        if key in self.options:
            if value not in self.options[key]:
                raise ValueError(f"{key} 选项无效")
            return self.options[key][value]
        return value

    def enable(self, key, enabled):
        self.widgets[key].configure(state=("readonly" if key in self.options else "normal") if enabled else "disabled")

    def values(self):
        return {key: self.get(key) for key in self.variables}


class BridgeGUI:
    def __init__(self, root, profile_path=None, controller=None, state_path=None):
        self.root = root
        self.root.title("Wi-Fi 桥接工具 · ESP32-S3 / ESP32-C5 / ESP-01S")
        self.root.geometry("1120x820")
        self.root.minsize(900, 650)
        self.controller = controller or BridgeController()
        self.closing = False
        self.destroyed = False
        self.reset_panel = None
        self.forms = []
        self.http_events = queue.Queue(4)
        self.http_thread = None
        self.http_busy = False
        self.config_job_transport = None
        self.baseline = None
        self.read_identity = None
        self.read_device = None
        self.selected_context = None
        self.context_epoch = 0
        self.logs = deque(maxlen=3000)
        self.log_dirty = False
        self.last_refresh = 0
        self.current_usb = None
        self.poll_job = None
        self._state_loading = True
        self._device_loading = False
        self._state_job = None
        self._device_drafts = {}
        self._profile_base = (Path(profile_path).resolve().parent if profile_path else BASE).resolve()
        self._default_profile_path = Path(profile_path).resolve() if profile_path else self._profile_base / "tcp2com_config.json"
        self._state_path = Path(state_path) if state_path is not None else self._profile_base / "tcp2com_last_state.json"
        self.status = tk.StringVar(root, value="已停止")
        self.applied = tk.StringVar(root, value="未启动")
        self.address = tk.StringVar(root, value="")
        self.runtime = tk.StringVar(root, value="")
        self.rates = tk.StringVar(root, value="网络接收 / 本地写入 / 网络发送：0 B/s")
        self.totals = tk.StringVar(root, value="队列 0 / 0 · 累计丢弃 0 / 0")
        self.bridge_detail = tk.StringVar(root, value="")
        self.c5_status = tk.StringVar(root, value="尚未读取设备；可选择 USB 单线连接，或 Wi-Fi 配置热点")
        self.c5_dirty = tk.StringVar(root, value="请先读取")
        self.usb_status = tk.StringVar(root, value="USB 诊断未启动")
        self.usb_master_status = tk.StringVar(root, value="USB：已关闭")
        self.filter_text = tk.StringVar(root)
        self.log_source = tk.StringVar(root, value="全部")
        self.autoscroll = tk.BooleanVar(root, value=True)
        self.state_status = tk.StringVar(root, value="界面设置会自动记住，连接需手动启动")
        self._build()
        self._device_defaults = self._device_values()
        saved, warning = load_state(self._state_path)
        self._set_settings({**default_settings(), "device": "c5", "mode": "tcp-server", "com": "COM1"})
        migrated = False
        migration_notice = None
        if saved:
            self._device_drafts = saved["device_drafts"]
            self._restore_state(saved)
        elif warning is None and self._default_profile_path.is_file():
            # One-time upgrade for installations that only have the old default profile.
            try:
                self._set_settings(load_profile(self._default_profile_path, "c5-spi-tcp"))
                migrated = True
                migration_notice = "已迁移旧默认电脑配置"
            except (ValueError, OSError):
                migration_notice = "旧默认电脑配置无法迁移，已使用内置默认参数"
        self._state_loading = False
        self._sync()
        # Startup must not rewrite a damaged/newer file or start any connection.
        self._cancel_state_save()
        notices = (["已恢复上次界面设置；连接尚未启动"] if saved else [])
        notices.extend(note for note in (warning, migration_notice) if note)
        if notices:
            self.state_status.set("；".join(notices))
        if migrated:
            try:
                save_state(self._state_path, self._state_snapshot())
            except (OSError, ValueError):
                self.state_status.set("旧配置已读取，但无法保存设置；请检查目录权限")
        self.root.protocol("WM_DELETE_WINDOW", self.close)
        self.poll_job = self.root.after(100, self.poll)

    def _build(self):
        outer = ttk.Frame(self.root, padding=12)
        outer.pack(fill="both", expand=True)
        title = ttk.Frame(outer)
        title.pack(fill="x")
        ttk.Label(title, text="Wi-Fi 桥接工具", font=("Microsoft YaHei UI", 17, "bold")).pack(side="left")
        ttk.Label(title, textvariable=self.status, foreground="#166534").pack(side="right")
        actions = ttk.Frame(outer)
        actions.pack(fill="x", pady=(0, 8))
        self.start_button = ttk.Button(actions, text="启动", command=lambda: self.start("start"))
        self.stop_button = ttk.Button(actions, text="停止", command=self.stop)
        self.restart_button = ttk.Button(actions, text="应用参数并重启", command=lambda: self.start("restart"))
        self.reset_button = ttk.Button(actions, text="远程复位", command=self.show_reset)
        for button in (self.start_button, self.stop_button, self.restart_button, self.reset_button):
            button.pack(side="left", padx=(0, 6))
        for button in (self.stop_button, self.restart_button):
            button.configure(state="disabled")
        ttk.Label(actions, textvariable=self.applied).pack(side="right")
        usb_bar = ttk.Frame(outer)
        usb_bar.pack(fill="x", pady=(0, 6))
        self.usb_master_button = ttk.Button(usb_bar, text="连接 USB", command=self.toggle_usb)
        self.usb_master_button.pack(side="left")
        ttk.Label(usb_bar, textvariable=self.usb_master_status).pack(side="left", padx=8)
        self.tabs = ttk.Notebook(outer)
        self.tabs.pack(fill="both", expand=True)
        self.network = self._tab("网络连接")
        self.network.note("这里设置电脑的网络角色。C5/S3 的 UART/SPI、三种 Wi-Fi 联网模式请在设备用户配置中修改。")
        self.network.add("device", "设备", "c5", DEVICE)
        self.network.add("mode", "电脑网络角色", "tcp-server", MODES)
        self.network.add("host", "设备地址 / 服务端来源 IP 筛选", "")
        self.network.add("listen", "电脑监听 IPv4", "0.0.0.0")
        self.network.add("port", "目标端口 / 电脑本地端口", "8080")
        self.network.add("peer_port", "UDP 对端端口", "6060")
        self.network.add("udp_payload", "UDP 每包最大发送字节", "1200")
        self.network.note("TCP 服务端可留空来源 IP；CH32 应填写电脑真实 IPv4，不能填写 0.0.0.0。UDP 已绑定不代表设备在线。")
        self.local = self._tab("COM 串口")
        self.local.add("com", "业务 COM（也支持 loop://）", "COM1", serial_port=True)
        ttk.Button(self.local.frame, text="刷新串口列表", command=self.refresh_ports).grid(row=0, column=2, padx=6)
        self.local.add("baud", "电脑本地波特率", "1152000")
        self.local.add("bytesize", "数据位", "8", ("5", "6", "7", "8"))
        self.local.add("parity", "校验位", "N", ("N", "E", "O", "M", "S"))
        self.local.add("stopbits", "停止位", "1", ("1", "1.5", "2"))
        self.local.add("flow", "流控", "none", {"无": "none", "RTS/CTS": "rtscts", "DSR/DTR": "dsrdtr"})
        self.local.note("程序打开已有 COM，不创建虚拟串口。本地波特率不会修改设备 UART 或 CH32 SPI。")
        self.advanced = self._tab("高级参数")
        for key, label, value in (("timeout", "COM 读超时（秒，可为 0）", "0.02"),
                                  ("write_timeout", "COM 写超时（秒）", "2"),
                                  ("reconnect", "TCP 客户端重连间隔（0 关闭）", "2"),
                                  ("connect_timeout", "TCP 连接超时（秒）", "4"),
                                  ("keepalive_idle", "Keepalive 空闲时间（0 关闭）", "3"),
                                  ("keepalive_interval", "Keepalive 探测间隔（秒）", "1"),
                                  ("keepalive_count", "Keepalive 探测次数", "5"),
                                  ("bufsize", "单次读缓冲（256～1048576 字节）", "65536"),
                                  ("stats_interval", "统计刷新间隔（秒）", "0.25"),
                                  ("control_port", "ESP-01S 复位控制端口", "8081")):
            self.advanced.add(key, label, value)
        self.advanced.add("no_reset_ui", "关闭 ESP-01S 复位入口", False, boolean=True)
        self.advanced.add("verbose", "调试日志", False, boolean=True)
        self._build_c5()
        self.usb_form = self._tab("USB 诊断")
        self.usb_form.note("USB 日志及配置需要手动连接；断开后可供 ESP-IDF monitor 使用。业务 COM 启动桥接时会被占用，请选择不同端口。")
        self.usb_form.add("usb_port", "USB COM（日志 / 配置，留空关闭）", "", serial_port=True)
        self.c5_usb_combo.configure(textvariable=self.usb_form.variables["usb_port"])
        ttk.Button(self.usb_form.frame, text="刷新串口", command=self.refresh_ports).grid(row=1, column=2, padx=6)
        for widget in (self.local.widgets["com"], self.usb_form.widgets["usb_port"]):
            widget.configure(postcommand=self.refresh_ports)
        self.usb_form.add("usb_baud", "USB 波特率", "115200")
        self.usb_form.add("usb_log", "USB 追加文本日志（可选）", "", browse=lambda: self.browse_field(self.usb_form, "usb_log"))
        ttk.Button(self.usb_form.frame, text="连接 / 应用 USB", command=self.apply_usb).grid(
            row=self.usb_form.row, column=0, sticky="w", pady=8)
        ttk.Button(self.usb_form.frame, text="断开 USB", command=self.disconnect_usb).grid(
            row=self.usb_form.row, column=1, sticky="w", pady=8)
        self.usb_form.row += 1
        ttk.Label(self.usb_form.frame, textvariable=self.usb_status, wraplength=800).grid(
            row=self.usb_form.row, column=0, columnspan=3, sticky="w")
        self.usb_form.row += 1
        self.usb_tree = ttk.Treeview(self.usb_form.frame, columns=("value",), height=8)
        self.usb_tree.heading("#0", text="固件诊断字段（含 RSSI / 信道 / 内存 / SPI 错误）")
        self.usb_tree.heading("value", text="最近值")
        self.usb_tree.grid(row=self.usb_form.row, column=0, columnspan=3, sticky="ew", pady=8)
        self._build_logs()
        self.footer = ttk.Frame(outer, padding=(0, 8, 0, 0))
        self.footer.pack(side="bottom", fill="x", before=self.tabs)
        self.footer_canvas = tk.Canvas(self.footer, highlightthickness=0, height=1)
        footer_scroll = ttk.Scrollbar(self.footer, orient="vertical", command=self.footer_canvas.yview)
        footer_scroll.pack(side="right", fill="y")
        self.footer_canvas.pack(side="left", fill="both", expand=True)
        self.footer_canvas.configure(yscrollcommand=footer_scroll.set)
        footer_content = ttk.Frame(self.footer_canvas)
        footer_window = self.footer_canvas.create_window((0, 0), window=footer_content, anchor="nw")
        self.footer_labels = []
        for variable in (self.runtime, self.address, self.rates, self.totals, self.bridge_detail):
            label = ttk.Label(footer_content, textvariable=variable, justify="left")
            label.pack(fill="x", anchor="w")
            self.footer_labels.append(label)
        label = ttk.Label(footer_content, textvariable=self.state_status, justify="left")
        label.pack(fill="x", anchor="w")
        self.footer_labels.append(label)

        def resize_footer(_event=None):
            self.footer_canvas.configure(scrollregion=self.footer_canvas.bbox("all"))
            height = min(footer_content.winfo_reqheight(), max(80, self.root.winfo_height() // 3))
            if int(self.footer_canvas.cget("height")) != height:
                self.footer_canvas.configure(height=height)

        def resize_footer_width(event):
            self.footer_canvas.itemconfigure(footer_window, width=event.width)
            for label in self.footer_labels:
                label.configure(wraplength=max(1, event.width - 4))

        def scroll_footer(event):
            self.footer_canvas.yview_scroll(-int(event.delta / 120), "units")
            return "break"

        footer_content.bind("<Configure>", resize_footer)
        self.footer_canvas.bind("<Configure>", resize_footer_width)
        self.root.bind("<Configure>", lambda event: resize_footer() if event.widget is self.root else None, add="+")
        for widget in (self.footer_canvas, footer_content, *self.footer_labels):
            widget.bind("<MouseWheel>", scroll_footer)
        for form in (self.network, self.local, self.advanced, self.usb_form):
            for variable in form.variables.values():
                variable.trace_add("write", lambda *_: self._sync())
        self.config_transport.trace_add("write", lambda *_: self._sync())
        self.config_url.trace_add("write", lambda *_: self._sync())

    def _tab(self, text):
        frame = ttk.Frame(self.tabs)
        self.tabs.add(frame, text=text)
        form = Form(frame)
        self.forms.append(form)
        return form

    def _build_c5(self):
        self.c5_frame = ttk.Frame(self.tabs, padding=8)
        self.tabs.add(self.c5_frame, text="C5 用户配置")
        connection = ttk.Frame(self.c5_frame)
        connection.pack(fill="x", pady=(0, 6))
        ttk.Label(connection, text="连接方式").pack(side="left")
        self.config_transport = tk.StringVar(self.root, value="Wi-Fi HTTP")
        self.transport_combo = ttk.Combobox(connection, textvariable=self.config_transport,
                                            values=("USB", "Wi-Fi HTTP"), state="readonly", width=13)
        self.transport_combo.pack(side="left", padx=6)
        ttk.Label(connection, text="USB COM").pack(side="left")
        self.c5_usb_combo = ttk.Combobox(connection, width=16, postcommand=self.refresh_ports)
        self.c5_usb_combo.pack(side="left", padx=6)
        self.usb_connect_button = ttk.Button(connection, text="连接 / 应用 USB", command=self.connect_c5_usb)
        self.usb_connect_button.pack(side="left", padx=2)
        ttk.Button(connection, text="刷新串口", command=self.refresh_ports).pack(side="left", padx=2)
        toolbar = ttk.Frame(self.c5_frame)
        toolbar.pack(fill="x")
        ttk.Label(toolbar, text="配置地址").pack(side="left")
        self.config_url = tk.StringVar(self.root, value="http://192.168.4.1/")
        self.url_entry = ttk.Entry(toolbar, textvariable=self.config_url, width=28)
        self.url_entry.pack(side="left", fill="x", expand=True, padx=6)
        self.c5_buttons = []
        for text, callback in (("打开网页", self.open_web), ("读取 C5", self.read_c5),
                               ("保存并重启 C5", self.save_c5)):
            button = ttk.Button(toolbar, text=text, command=callback)
            button.pack(side="left", padx=2)
            self.c5_buttons.append(button)
        self.setup_hint = tk.StringVar(self.root)
        ttk.Label(self.c5_frame, textvariable=self.setup_hint, wraplength=1000).pack(anchor="w", pady=(8, 2))
        ttk.Label(self.c5_frame, textvariable=self.c5_status, foreground="#1d4ed8", wraplength=1000).pack(anchor="w", pady=4)
        ttk.Label(self.c5_frame, textvariable=self.usb_status, wraplength=1000).pack(anchor="w")
        bar = ttk.Frame(self.c5_frame)
        bar.pack(fill="x", pady=4)
        for text, callback in (("撤销编辑", self.undo_c5), ("备份（不含密码）", self.backup_c5), ("导入备份到编辑区", self.import_c5)):
            button = ttk.Button(bar, text=text, command=callback)
            button.pack(side="left", padx=(0, 6))
            self.c5_buttons.append(button)
        self.draft_button = ttk.Button(bar, text="恢复上次草稿", command=self.restore_device_draft)
        self.draft_button.pack(side="left", padx=(0, 6))
        ttk.Label(bar, textvariable=self.c5_dirty, foreground="#9a3412").pack(side="right")
        tabs = ttk.Notebook(self.c5_frame)
        tabs.pack(fill="both", expand=True)
        self.c5_forms = {}
        for name, title in (("common", "接口与联网"), ("ap", "自建热点 AP"), ("pc", "电脑热点 STA"), ("shared", "共享 Wi-Fi STA")):
            frame = ttk.Frame(tabs)
            tabs.add(frame, text=title)
            self.c5_forms[name] = Form(frame)
        form = self.c5_forms["common"]
        form.note("新固件读取当前运行配置；临时应用值可能尚未保存。明确保存后写 NVS 并重启，业务连接会断开。")
        form.add("link_mode", "设备业务接口", 0, payload_interfaces("c5"))
        form.add("flow_pin", "串口流控线（高暂停 / 低允许）", 1, {"未选择": 0, "SCK GPIO23": 1, "MOSI GPIO10": 2, "MISO GPIO9": 3, "CS GPIO5": 4})
        form.add("reset_pin", "外部复位线（仅串口模式）", 0, {"关闭": 0, "SCK GPIO23": 1, "MOSI GPIO10": 2, "MISO GPIO9": 3, "CS GPIO5": 4})
        form.add("mode", "设备 Wi-Fi 联网模式", 0, {"自建热点 AP": 0, "连接电脑热点": 1, "连接共享 Wi-Fi": 2})
        form.add("band", "频段", 0, {"2.4 GHz": 0, "5 GHz": 1, "自动（AP 使用 2.4 GHz）": 2})
        form.add("baud", "设备 UART 波特率（SPI 模式保留）", "1152000")
        form.add("tcp_port", "设备默认 TCP 服务端端口", "8080")
        for key, label, default in (
                ("remote_ip","设备目标 IP","192.168.137.1"),
                ("remote_port","设备目标端口","8080"),("local_port","设备本地端口（0 自动分配）","6060"),
                ("reconnect_ms","设备重连间隔 ms","1000"),("connect_timeout_ms","设备连接超时 ms","10000"),
                ("up_kb","设备上行缓冲 KiB","4096"),("down_kb","设备下行缓冲 KiB","1024")):
            form.add(key,label,default)
        form.add("net_role","设备网络角色",0,{"TCP 服务端":0,"TCP 客户端":1,"UDP":2})
        form.add("auto_reconnect","设备自动重连",True,boolean=True)
        form.note("上行 64～6144 KiB、下行 64～2048 KiB，总和最多 6144 KiB。接口、波特率和队列容量需重启生效。")
        ap = self.c5_forms["ap"]
        ap.note("密码留空保留原密码；设备不返回旧密码。AP 不支持开放网络。更改业务 AP IP 不改变配置热点地址。")
        for key, label in (("ap_ssid", "SSID"), ("ap_password", "新密码（8～63 字节，留空保留）"),
                           ("ap_channel", "信道"), ("ap_ip", "IP"), ("ap_gateway", "网关"), ("ap_netmask", "子网掩码")):
            ap.add(key, label, secret=key == "ap_password")
        for name in ("pc", "shared"):
            station = self.c5_forms[name]
            station.note("密码留空保留；切换为开放网络需明确勾选“清空密码”。DHCP 时静态地址模板仍保留。")
            station.add("ssid", "SSID")
            station.add("password", "新密码（留空保留）", secret=True)
            station.add("clear_password", "开放网络 / 清空原密码", False, boolean=True)
            station.add("static_ip", "使用静态 IP（否则 DHCP）", False, boolean=True)
            for key, label in (("ip", "IP"), ("gateway", "网关"), ("netmask", "子网掩码"), ("dns", "DNS")):
                station.add(key, label)
            station.variables["static_ip"].trace_add("write", lambda *_, name=name: self._station_sync(name))
        for form in self.c5_forms.values():
            for variable in form.variables.values():
                variable.trace_add("write", lambda *_: self._device_form_changed())

    def _build_logs(self):
        frame = ttk.Frame(self.tabs, padding=8)
        self.tabs.add(frame, text="运行日志")
        toolbar = ttk.Frame(frame)
        toolbar.pack(fill="x", pady=(0, 6))
        ttk.Label(toolbar, text="筛选").pack(side="left")
        ttk.Entry(toolbar, textvariable=self.filter_text, width=24).pack(side="left", padx=6)
        ttk.Label(toolbar, text="来源").pack(side="left")
        ttk.Combobox(toolbar, textvariable=self.log_source, values=("全部", "上位机", "USB / ESP"),
                     state="readonly", width=10).pack(side="left", padx=6)
        ttk.Checkbutton(toolbar, text="自动滚动（取消以暂停）", variable=self.autoscroll).pack(side="left")
        ttk.Button(toolbar, text="清空", command=self.clear_logs).pack(side="right")
        ttk.Button(toolbar, text="导出日志…", command=self.export_logs).pack(side="right", padx=6)
        self.log_text = tk.Text(frame, wrap="word", state="disabled", font=("Consolas", 10))
        for level, color in {"E": "#c62828", "W": "#a65c00", "I": "#18733b",
                             "D": "#315a9b", "V": "#666666"}.items():
            self.log_text.tag_configure(level, foreground=color)
        scrollbar = ttk.Scrollbar(frame, command=self.log_text.yview)
        self.log_text.configure(yscrollcommand=scrollbar.set)
        scrollbar.pack(side="right", fill="y")
        self.log_text.pack(fill="both", expand=True)
        self.filter_text.trace_add("write", lambda *_: setattr(self, "log_dirty", True))
        self.log_source.trace_add("write", lambda *_: setattr(self, "log_dirty", True))

    @contextmanager
    def _pause_device(self):
        previous = self._device_loading
        self._device_loading = True
        try:
            yield
        finally:
            self._device_loading = previous

    def _device_values(self):
        result = {group: {key: form.get(key) for key in DRAFT_FIELDS[group] if key in form.variables}
                  for group, form in self.c5_forms.items()}
        if self.network.get("device") != "c5":
            result["common"].pop("flow_pin", None)
        return result

    def _remember_device_draft(self, only_if_missing=False):
        if self._state_loading or self._device_loading or self.closing:
            return
        device = self.network.get("device")
        if device not in ("c5", "s3") or (only_if_missing and device in self._device_drafts):
            return
        self._device_drafts[device] = self._device_values()
        self._schedule_state_save()
        self.draft_button.configure(state="normal" if not self.http_busy else "disabled")

    def _device_form_changed(self):
        if self._state_loading or self._device_loading:
            return
        self._c5_changes()
        self._remember_device_draft()

    def _apply_device_draft(self, draft):
        with self._pause_device():
            for group, fields in draft.items():
                if group not in self.c5_forms:
                    continue
                for key, value in fields.items():
                    if key in DRAFT_FIELDS[group] and key in self.c5_forms[group].variables:
                        if group == "common" and key == "link_mode" and self.network.get("device") == "c5" and value in (1, 2):
                            value = -1
                        self.c5_forms[group].set(key, value)
            self._clear_passwords()
            for name in ("pc", "shared"):
                self._station_sync(name)
        self._c5_changes()

    def restore_device_draft(self):
        if self.http_busy or self.closing:
            return
        draft = self._device_drafts.get(self.network.get("device"))
        if draft is not None:
            self._apply_device_draft(draft)
            self.c5_status.set("已恢复上次草稿；" + ("基于本次读取，尚未保存到设备" if self.baseline is not None
                                                   else "尚未读取设备，保存前请重新读取"))
            self._schedule_state_save()

    def _restore_state(self, saved):
        for name, fields in saved["forms"].items():
            form = getattr(self, name)
            for key, value in fields.items():
                form.set(key, value)
        if "config_url" in saved:
            self.config_url.set(saved["config_url"])
        if "config_transport" in saved:
            self.config_transport.set("USB" if saved["config_transport"] == "usb" else "Wi-Fi HTTP")

    def _state_snapshot(self):
        return {"version": VERSION,
                "forms": {name: {key: getattr(self, name).get(key) for key in fields}
                          for name, fields in FORM_FIELDS.items()},
                "config_transport": "usb" if self.config_transport.get() == "USB" else "http",
                "config_url": self.config_url.get(), "device_drafts": self._device_drafts}

    def _cancel_state_save(self):
        if self._state_job is not None:
            self.root.after_cancel(self._state_job)
            self._state_job = None

    def _schedule_state_save(self):
        if self._state_loading or self.closing:
            return
        self._cancel_state_save()
        self._state_job = self.root.after(500, self._save_last_state)

    def _save_last_state(self):
        self._cancel_state_save()
        try:
            save_state(self._state_path, self._state_snapshot())
            self.state_status.set("已记住界面设置；下次启动自动恢复")
        except (OSError, ValueError):
            # Local persistence failures must not stop forwarding or closing.
            self.state_status.set("无法保存上次设置，请检查状态文件位置和目录权限")

    def _set_settings(self, settings):
        values = {**default_settings(), **settings}
        previous = self._state_loading
        self._state_loading = True
        try:
            for form in (self.network, self.local, self.advanced, self.usb_form):
                for key in form.variables:
                    form.set(key, values.get(key))
            self.config_url.set(values["config_url"])
            self.config_transport.set("USB" if values["config_transport"] == "usb" else "Wi-Fi HTTP")
        finally:
            self._state_loading = previous
        if not previous:
            self._sync()

    def settings(self):
        result = {}
        for form in (self.network, self.local, self.advanced, self.usb_form):
            result.update(form.values())
        result["config_url"] = self.config_url.get().strip()
        result["config_transport"] = "usb" if self.config_transport.get() == "USB" else "http"
        if result["config_transport"] == "http":
            C5Client(result["config_url"])
        if not capabilities(result["device"]).usb:
            result["usb_port"] = result["usb_log"] = None
        return result

    def _sync(self):
        if self._state_loading or not hasattr(self, "usb_form"):
            return
        device = self.network.get("device")
        caps = capabilities(device)
        context = (device, self.config_transport.get(), self.config_url.get(), self.usb_form.get("usb_port").strip().casefold())
        if context != self.selected_context:
            device_changed = self.selected_context is None or self.selected_context[0] != device
            self.selected_context = context
            self._invalidate_config()
            self.c5_status.set("设备或配置连接已改变，请重新读取")
            if device_changed and caps.usb:
                with self._pause_device():
                    form = self.c5_forms["common"]
                    form.options["band"] = {label: value for label, value in BANDS.items() if value in caps.bands}
                    form.widgets["band"].configure(values=list(form.options["band"]))
                    form.options["link_mode"] = payload_interfaces(device)
                    form.widgets["link_mode"].configure(values=list(form.options["link_mode"]))
                    self._apply_device_draft(self._device_defaults)
                    draft = self._device_drafts.get(device)
                    if draft is not None:
                        self._apply_device_draft(draft)
                        self.c5_status.set("已恢复上次编辑草稿；尚未读取设备，保存前请重新读取")
        reset_form = self.c5_forms["common"]
        reset_value = reset_form.get("reset_pin")
        reset_form.options["reset_pin"] = {"关闭": 0, f"SCK GPIO{18 if device == 's3' else 23}": 1, "MOSI GPIO10": 2, "MISO GPIO9": 3, "CS GPIO5": 4}
        reset_form.widgets["reset_pin"].configure(values=list(reset_form.options["reset_pin"]))
        with self._pause_device():
            reset_form.set("reset_pin", reset_value)
        if self.reset_panel and not self.reset_panel.closed:
            self.reset_panel.set_context(device, self.controller.snapshot()[0], self.network.get("host"))
        self.setup_hint.set(caps.setup_hint if caps.usb else "ESP-01S 使用原有透传与复位功能。")
        self.tabs.tab(self.c5_frame, text=f"{caps.label} 用户配置" if caps.usb else "设备用户配置")
        self.c5_buttons[1].configure(text=f"读取 {caps.label}")
        self.c5_buttons[2].configure(text=f"保存并重启 {caps.label}")
        mode = self.network.get("mode")
        self.network.enable("listen", mode != "tcp-client")
        for key in ("peer_port", "udp_payload"):
            self.network.enable(key, mode == "udp")
        for key in self.usb_form.variables:
            self.usb_form.enable(key, caps.usb)
        for key in ("control_port", "no_reset_ui"):
            self.advanced.enable(key, device == "esp01s" and mode == "tcp-client")
        self.tabs.tab(self.c5_frame, state="normal" if caps.usb else "disabled")
        if not caps.usb:
            self.baseline = None
            self.read_identity = None
            self._clear_passwords()
            self.c5_status.set("请选择 C5/S3 设备后读取配置")
        self._c5_enable()
        self._schedule_state_save()

    def _invalidate_config(self):
        self.context_epoch += 1
        self.baseline = self.read_identity = self.read_device = None
        self._clear_passwords()

    def _station_sync(self, name):
        form = self.c5_forms[name]
        for key in ("ip", "gateway", "netmask", "dns"):
            form.enable(key, form.get("static_ip"))

    def _c5_enable(self):
        enabled = capabilities(self.network.get("device")).usb and not self.http_busy and not self.closing
        for index, button in enumerate(self.c5_buttons):
            button.configure(state="normal" if enabled and (index in (0, 1) or self.baseline is not None) else "disabled")
        usb = self.config_transport.get() == "USB"
        self.c5_buttons[0].configure(state="normal" if enabled and not usb else "disabled")
        self.url_entry.configure(state="normal" if not self.http_busy and not usb else "disabled")
        self.transport_combo.configure(state="readonly" if enabled else "disabled")
        self.c5_usb_combo.configure(state="normal" if enabled and usb else "disabled")
        self.usb_connect_button.configure(state="normal" if enabled and usb else "disabled")
        self.network.widgets["device"].configure(state="disabled" if self.http_busy else "readonly")
        self.draft_button.configure(state="normal" if enabled and self.network.get("device") in self._device_drafts
                                    else "disabled")
        for key in ("flow_pin","reset_pin","net_role","auto_reconnect","remote_ip","remote_port","local_port",
                    "reconnect_ms","connect_timeout_ms","up_kb","down_kb"):
            self.c5_forms["common"].enable(key,enabled and self.baseline is not None and key in self.baseline)
        form = self.c5_forms["common"]
        links = payload_interfaces(self.network.get("device"))
        try:
            current = form.get("link_mode")
        except ValueError:
            current = -1 if self.network.get("device") == "c5" else 0
        if form.options["link_mode"] != links:
            form.options["link_mode"] = links
            form.widgets["link_mode"].configure(values=list(links))
            with self._pause_device():
                if current not in links.values():
                    current = -1 if self.network.get("device") == "c5" else 0
                form.set("link_mode", current)
        self._c5_changes()

    def _c5_changes(self):
        if self.baseline is None:
            self.c5_dirty.set("请先读取")
            return
        if self.network.get("device") == "c5" and self.c5_forms["common"].get("link_mode") == -1:
            self.c5_dirty.set("旧接口已移除，请重新选择接口")
            self.c5_buttons[2].configure(state="disabled")
            return
        self.c5_buttons[2].configure(state="normal" if not self.http_busy and not self.closing else "disabled")
        try:
            passwords = {"ap": self.c5_forms["ap"].get("ap_password"),
                         **{name: self.c5_forms[name].get("password") for name in ("pc", "shared")}}
            clear = [name for name in ("pc", "shared") if self.c5_forms[name].get("clear_password")]
            dirty = changed_config(self.baseline, self.edited_c5(), passwords, clear, device=self.network.get("device"))
            needs_save = self.baseline.get("unsaved") or self.baseline.get("migration_requires_setup")
            self.c5_dirty.set("已修改，尚未保存" if dirty else
                              "当前配置待明确保存并重启" if needs_save else "与上次读取一致")
        except ValueError:
            self.c5_dirty.set("编辑未保存，请检查参数")

    def browse_field(self, form, key):
        path = filedialog.asksaveasfilename(parent=self.root, defaultextension=".log")
        if path:
            form.set(key, path)

    def refresh_ports(self):
        ports = [port.device for port in list_ports.comports()]
        for widget in (self.local.widgets["com"], self.usb_form.widgets["usb_port"], self.c5_usb_combo):
            widget.configure(values=ports)
        self.controller.ui.message("可用串口：" + ("、".join(ports) or "未发现，可手动填写端口或 loop://"))

    def _reset_shutdown(self):
        if self.reset_panel:
            self.reset_panel.shutdown()
            self.reset_panel = None

    def start(self, command):
        if self.http_busy:
            self.error("配置请求正在进行，请等待完成后再启动或重启桥接")
            return
        try:
            values = self.settings()
            values["usb_port"] = values["usb_log"] = None
            args = settings_to_args(values)
            self.controller.submit(command, args)
        except ValueError as exc:
            self.error(exc)

    def stop(self):
        if self.http_busy:
            self.error("配置请求正在进行，请等待完成后再停止 USB")
            return
        self.controller.submit("stop")

    def apply_usb(self):
        if self.http_busy:
            self.error("配置请求正在进行，请等待完成后再更改 USB 连接")
            return
        try:
            app = self.controller.snapshot()[0]
            # USB edits do not require unapplied network/COM edits to be valid.
            values = vars(copy.deepcopy(app.args)) if app else {
                "device": "c5", "mode": "tcp-server", "com": "diagnostics-only"}
            values.update(self.usb_form.values())
            values["device"] = self.network.get("device")
            if not capabilities(values["device"]).usb:
                raise ValueError("请切换到 C5/S3 设备后设置 USB 诊断；停止按钮也可关闭已运行的诊断")
            args = settings_to_args(values)
            if not self.controller.submit("usb", args):
                raise ValueError("连接操作正在进行，请稍后再试")
            self._invalidate_config()
            return True
        except ValueError as exc:
            self.error(exc)

    def disconnect_usb(self):
        if self.controller.submit("usb"):
            if (self.read_identity and self.read_identity[0] == "usb") or self.config_job_transport == "usb":
                self._invalidate_config()
            if self.config_job_transport == "usb":
                self.c5_status.set("USB 正在关闭；若保存已发送，请重新连接并读取核对保存结果")
        else:
            self.error("连接操作正在进行，请稍后再试")

    def toggle_usb(self):
        if self.controller.snapshot()[1]:
            self.disconnect_usb()
        else:
            self.connect_c5_usb()

    def connect_c5_usb(self):
        if not self.usb_form.get("usb_port").strip():
            self.error("请先选择设备的原生 USB COM")
            return
        if self.apply_usb():
            self.c5_status.set("正在连接 USB；" + capabilities(self.network.get("device")).setup_hint + " 连接完成后点击读取。")

    def _config_client(self):
        device = self.network.get("device")
        if self.config_transport.get() == "USB":
            monitor = self.controller.snapshot()[1]
            if not monitor or monitor.port.casefold() != self.usb_form.get("usb_port").strip().casefold():
                raise ConfigError("请先连接所选 USB COM，再读取设备")
            if monitor.capabilities != capabilities(device):
                raise ConfigError("USB 连接的设备选择已改变，请重新连接 USB 后读取")
            client = C5USBClient(monitor, device=device)
            if self.read_device == device and self.read_identity == client.identity:
                client.baseline = copy.deepcopy(self.baseline)
            return client
        client = C5Client(self.config_url.get(), device=device)
        identity = self.read_identity
        if self.read_device == device and identity and len(identity) == 4 and identity[:3] == client.identity:
            client.bind_session(identity[3])
            client.baseline = copy.deepcopy(self.baseline)
        return client

    def show_reset(self):
        app = self.controller.snapshot()[0]
        device = self.network.get("device")
        if not self.reset_panel or self.reset_panel.closed:
            self.reset_panel = ResetPanel(app, self.root, device=device, host=self.network.get("host"))
        else:
            self.reset_panel.set_context(device, app, self.network.get("host"))
        self.reset_panel.show()

    def open_web(self):
        try:
            C5Client(self.config_url.get())
            webbrowser.open(self.config_url.get())
        except ValueError as exc:
            self.error(exc)

    def _http(self, action, patch=None):
        if self.http_busy or not capabilities(self.network.get("device")).usb:
            return
        client = self._config_client()
        if action == "read":
            self.baseline = self.read_identity = None
        self.http_busy = True
        self.config_job_transport = client.identity[0]
        self._c5_enable()
        self.c5_status.set("正在读取…" if action == "read" else "正在保存到 NVS，请等待响应…")
        context = (self.context_epoch, self.network.get("device"))
        self.http_thread = threading.Thread(target=config_job, args=(self.http_events, action, client, patch, context),
                                            name="c5-config", daemon=True)
        self.http_thread.start()

    def read_c5(self):
        try:
            self._http("read")
        except ValueError as exc:
            self.error(exc)

    def _clear_passwords(self):
        with self._pause_device():
            self.c5_forms["ap"].set("ap_password", "")
            for name in ("pc", "shared"):
                self.c5_forms[name].set("password", "")
                self.c5_forms[name].set("clear_password", False)

    def _fill_c5(self, document):
        form = self.c5_forms["common"]
        form.options["link_mode"] = payload_interfaces(self.network.get("device"))
        form.widgets["link_mode"].configure(values=list(form.options["link_mode"]))
        with self._pause_device():
            for name in ("common", "ap"):
                form = self.c5_forms[name]
                for key in form.variables:
                    if key in document:
                        form.set(key, document[key])
            for name in ("pc", "shared"):
                for key, value in document[name].items():
                    self.c5_forms[name].set(key, value)
                self._station_sync(name)
            self._clear_passwords()
        # A fresh device read must not destroy the separately saved edit draft.
        self._remember_device_draft(only_if_missing=True)
        self._c5_changes()

    def edited_c5(self):
        result = self.c5_forms["common"].values()
        if self.network.get("device") != "c5" or not self.baseline or "flow_pin" not in self.baseline:
            result.pop("flow_pin", None)
        if self.network.get("device") not in ("c5", "s3") or not self.baseline or "reset_pin" not in self.baseline:
            result.pop("reset_pin", None)
        for key in ("baud", "tcp_port", "remote_port", "local_port", "reconnect_ms", "connect_timeout_ms", "up_kb", "down_kb"):
            try:
                result[key] = int(result[key])
            except ValueError as exc:
                raise ConfigError(f"{key} 必须为整数") from exc
        result.update({key: value for key, value in self.c5_forms["ap"].values().items() if key != "ap_password"})
        try:
            result["ap_channel"] = int(result["ap_channel"])
        except ValueError as exc:
            raise ConfigError("信道必须为整数") from exc
        for name in ("pc", "shared"):
            result[name] = {key: value for key, value in self.c5_forms[name].values().items()
                            if key not in ("password", "clear_password")}
        if self.baseline is not None:
            for key in ("flow_pin","reset_pin","net_role","auto_reconnect","remote_ip","remote_port","local_port",
                        "reconnect_ms","connect_timeout_ms","up_kb","down_kb"):
                if key not in self.baseline:
                    result.pop(key,None)
        return public_config(result, self.network.get("device"))

    def save_c5(self):
        try:
            if (self.baseline is None or self.read_device != self.network.get("device") or
                    self.read_identity != self._config_client().identity):
                raise ConfigError("请先从当前 USB 连接或配置地址读取所选设备，之后才能保存")
            passwords = {"ap": self.c5_forms["ap"].get("ap_password"),
                         **{name: self.c5_forms[name].get("password") for name in ("pc", "shared")}}
            clear = [name for name in ("pc", "shared") if self.c5_forms[name].get("clear_password")]
            patch = changed_config(self.baseline, self.edited_c5(), passwords, clear, device=self.network.get("device"))
            if not patch and not (self.baseline.get("unsaved") or self.baseline.get("migration_requires_setup")):
                self.c5_status.set("没有修改，无需写入 NVS")
                return
            label = capabilities(self.network.get("device")).label
            if messagebox.askokcancel(f"保存 {label} 并重启", f"将修改写入 {label} NVS 并重启，业务连接会中断。继续？", parent=self.root):
                self._http("save", patch)
                self._clear_passwords()
        except ValueError as exc:
            self.error(exc)

    def undo_c5(self):
        if self.baseline:
            self._fill_c5(self.baseline)
            self._remember_device_draft()
            self.c5_status.set("已恢复到上次读取内容；未写入设备")

    def backup_c5(self):
        try:
            config = self.edited_c5()
            path = filedialog.asksaveasfilename(parent=self.root, defaultextension=".json", initialfile=f"{self.network.get('device')}-config-backup.json")
            if path:
                atomic_json(path, {"format": "wifi-bridge-user-config-v1", "device": self.network.get("device"), "config": config})
                self.c5_status.set("已备份编辑区（不含密码）；恢复到其他设备时需重新填写密码")
        except (ValueError, OSError) as exc:
            self.error(exc)

    def import_c5(self):
        path = filedialog.askopenfilename(parent=self.root, filetypes=[("JSON", "*.json")])
        if not path:
            return
        try:
            document = json.loads(Path(path).read_text(encoding="utf-8-sig"))
            if not isinstance(document, dict) or document.get("format") not in ("c5-user-config-v1", "wifi-bridge-user-config-v1"):
                raise ConfigError("不支持的设备备份格式（与电脑 profiles 文件不同）")
            self._fill_c5(public_draft_config(document["config"], self.network.get("device")))
            self._remember_device_draft()
            self.c5_status.set("已导入到编辑区；旧接口需重新选择，密码未导入，尚未写入设备")
        except (KeyError, ValueError, OSError) as exc:
            self.error(exc)

    def _http_results(self):
        try:
            event = self.http_events.get_nowait()
        except queue.Empty:
            return
        self.http_busy = False
        self.config_job_transport = None
        action, identity, document, error = event[:4]
        if len(event) == 5 and event[4] != (self.context_epoch, self.network.get("device")):
            if action == "save" and identity[0] == "usb":
                self.c5_status.set("USB 保存结果待确认；请重新连接并读取核对，勿直接重发")
            else:
                self.c5_status.set("配置连接已改变，已丢弃旧请求结果；请重新读取")
            self._c5_enable()
            return
        if action == "read" and not error and identity[0] == "usb":
            monitor = self.controller.snapshot()[1]
            try:
                current = monitor.config_identity() if monitor else None
            except ConfigError:
                current = None
            if current != identity:
                self._invalidate_config()
                self.c5_status.set("USB 连接已改变，已丢弃旧请求结果；请重新读取")
                self._c5_enable()
                return
        if error:
            self.c5_status.set(str(error))
            if isinstance(error, ConfigError) or identity[0] == "usb":
                self.baseline = self.read_identity = None
        elif action == "read":
            self.baseline = document
            self.read_identity = identity
            self.read_device = self.network.get("device")
            self._fill_c5(document)
            detail = f"来源 {document['config_source']}"
            if "revision" in document:
                detail += f" · 编辑修订 {document['revision']} · 保存修订 {document.get('saved_revision', '未知')}"
            if document.get("unsaved"):
                detail += " · 当前运行值尚未保存"
            if document.get("migration_requires_setup"):
                removed = "旧业务接口恢复" if "flow_pin" in document else "旧 SPI 恢复"
                detail += f" · {removed}：业务接口已停用，请明确选择新接口后保存重启"
            self.c5_status.set(f"已读取配置 · {detail} · {time.strftime('%H:%M:%S')}；修改后需明确保存")
        else:
            self.baseline = self.read_identity = None
            self.c5_status.set("已写入 NVS，设备即将重启；USB / HTTP 均需重新进入配网，再读取核对。普通模式 USB 只输出日志。")
        self._c5_enable()

    def clear_logs(self):
        self.logs.clear()
        self.log_dirty = True

    def export_logs(self):
        path = filedialog.asksaveasfilename(parent=self.root, defaultextension=".log")
        if path:
            try:
                Path(path).write_text("\n".join(line for _, line, _ in self.logs) + "\n", encoding="utf-8")
            except OSError as exc:
                self.error(exc)

    def _logs(self):
        for _ in range(200):
            try:
                timestamp, source, text = self.controller.ui.events.get_nowait()
            except queue.Empty:
                break
            match = ESP_LEVEL.match(text) if source == "usb" else None
            label = "USB / ESP" if source == "usb" else "上位机"
            line = time.strftime("%H:%M:%S", time.localtime(timestamp)) + f" [{label}] " + text
            self.logs.append((source, line, match.group(1) if match else ""))
            self.log_dirty = True
        if self.log_dirty:
            position = self.log_text.yview()[0]
            search = self.filter_text.get().casefold()
            self.log_text.configure(state="normal")
            self.log_text.delete("1.0", "end")
            selected = self.log_source.get()
            for source, line, level in self.logs:
                if (selected == "上位机" and source != "host") or (selected == "USB / ESP" and source != "usb"):
                    continue
                if search in line.casefold():
                    self.log_text.insert("end", line + "\n", level)
            self.log_text.configure(state="disabled")
            self.log_text.see("end") if self.autoscroll.get() else self.log_text.yview_moveto(position)
            self.log_dirty = False

    def poll(self):
        self.poll_job = None
        if self.closing:
            if not self.controller.thread.is_alive() and not (self.http_thread and self.http_thread.is_alive()):
                self._destroy()
                return
            self.poll_job = self.root.after(100, self.poll)
            return
        self._http_results()
        self._logs()
        app, usb, running, state, busy = self.controller.snapshot()
        self.usb_master_button.configure(text="关闭 USB" if usb else "连接 USB",
                                         state="disabled" if busy or self.closing else "normal")
        if busy:
            self.usb_master_status.set("USB：正在处理连接操作…" if usb else "USB：正在处理…")
        elif usb:
            snapshot = usb.status()
            state_text = "正在关闭 / 关闭失败" if usb.stop_event.is_set() else ("已连接" if snapshot["connected"] else "连接中 / 重试中")
            self.usb_master_status.set(f"USB：{state_text} · {usb.port}")
        else:
            self.usb_master_status.set("USB：已关闭")
        if self.reset_panel and not self.reset_panel.closed:
            self.reset_panel.set_context(self.network.get("device"), app, self.network.get("host"))
        self.status.set(state)
        self.start_button.configure(state="normal" if not busy and not self.http_busy and not app else "disabled")
        self.stop_button.configure(state="normal" if not busy and not self.http_busy and (app or usb) else "disabled")
        self.restart_button.configure(state="normal" if not busy and not self.http_busy and app else "disabled")
        self.reset_button.configure(state="disabled" if self.closing else "normal")
        try:
            values = self.settings()
            values["usb_port"] = values["usb_log"] = None
            edited = vars(settings_to_args(values))
            if running:
                # Display refresh and the HTTP endpoint are applied immediately in the UI.
                for key in ("stats_interval", "config_url", "config_transport", "usb_port", "usb_baud", "usb_log"):
                    running[key] = edited[key]
            self.applied.set("参数已应用" if running == edited else "参数未应用" if app else "未启动")
        except ValueError:
            self.applied.set("参数待修正 / 未应用")
        now = time.monotonic()
        try:
            refresh = max(.1, min(float(self.advanced.get("stats_interval")), 5))
        except ValueError:
            refresh = .25
        self.controller.ui.stats_interval = refresh
        if now - self.last_refresh >= refresh:
            self.last_refresh = now
            if app:
                self.runtime.set(f"正在使用：{app.args.device} / {app.args.mode} · "
                                 f"{app.args.com} / {app.args.baud} / {app.args.bytesize}{app.args.parity}{app.args.stopbits}")
                self.rates.set(self.controller.ui.rate.strip())
                stats = app.stats.snapshot()
                queued, peak = app.q_tcp_to_com.byte_snapshot()
                calls = stats["serial_write_calls"]
                avg_us = stats["serial_write_us"] // calls if calls else 0
                self.totals.set(f"队列 {app.q_tcp_to_com.qsize()} / {app.q_com_to_tcp.qsize()} · "
                                f"累计丢弃 {stats['tcp_to_com_drop']} / {stats['com_to_tcp_drop']} B · "
                                f"UDP 收/发/拒绝 {stats['udp_rx_packets']}/{stats['udp_tx_packets']}/{stats['udp_rejected']} · "
                                f"日志队列丢弃 {self.controller.ui.dropped} 条")
                self.bridge_detail.set(f"网络→COM待写 {queued} B（峰值 {peak} B，不含正在写入） · "
                                       f"COM写均值/最大 {avg_us}/{stats['serial_write_max_us']} us · "
                                       f"正在写 {stats['serial_write_pending_bytes']} B/{stats['serial_write_pending_us']} us · "
                                       f"短写/超时 {stats['serial_write_short']}/{stats['serial_write_timeouts']} · "
                                       f"入队耗时累计 {stats['enqueue_wait_us']} us")
                sock = app.conn.snapshot()[0]
                try:
                    self.address.set(f"本地 {sock.getsockname()} · 对端 {sock.getpeername()}" if sock and not app.udp else
                                     f"已绑定 {app.udp.address if app.udp else app.server.address if app.server else '连接中'} · "
                                     f"{('固定对端 ' + str(app.udp.peer)) if app.udp else '等待设备连接'}")
                except (OSError, AttributeError):
                    self.address.set("连接状态正在变化，等待更新")
            else:
                self.runtime.set("")
                self.address.set("业务桥接已停止")
                self.rates.set("网络接收 / 本地写入 / 网络发送：0 B/s")
                self.bridge_detail.set("")
            self._usb_status(usb, now)
        self.poll_job = self.root.after(100, self.poll)

    def _usb_status(self, usb, now):
        if self.baseline is not None and self.read_identity and self.read_identity[0] == "usb":
            try:
                current = usb.config_identity() if usb else None
            except ConfigError:
                current = None
            if current != self.read_identity:
                self._invalidate_config()
                self.c5_status.set("USB 连接或配网会话已改变，请重新读取")
                self._c5_enable()
        if not usb:
            self.usb_status.set("USB 诊断未启动")
            if self.current_usb is not None:
                self.usb_tree.delete(*self.usb_tree.get_children())
            self.current_usb = None
            return
        snapshot = usb.status()
        age = None if snapshot["updated_at"] is None else now - snapshot["updated_at"]
        self.usb_status.set(f"{usb.port} · {'已连接' if snapshot['connected'] else '离线 / 重试中'} · "
                            + ("尚无 stats" if age is None else f"stats {age:.1f} 秒前更新" + ("（已过期）" if age > 15 else "")))
        fields = snapshot["fields"]
        if self.current_usb != (usb, fields):
            self.usb_tree.delete(*self.usb_tree.get_children())
            for key, value in fields.items():
                self.usb_tree.insert("", "end", text=key, values=(value,))
            self.current_usb = (usb, copy.deepcopy(fields))

    def error(self, error):
        messagebox.showerror("操作未完成", str(error), parent=self.root)

    def close(self):
        if self.closing:
            return
        self._save_last_state()
        self.closing = True
        self.status.set("正在关闭并释放资源…")
        self._reset_shutdown()
        self._clear_passwords()
        self.controller.close()
        for button in (self.start_button, self.stop_button, self.restart_button, self.reset_button, *self.c5_buttons):
            button.configure(state="disabled")

    def _destroy(self):
        self._cancel_state_save()
        if self.poll_job is not None:
            self.root.after_cancel(self.poll_job)
        self.root.destroy()
        self.destroyed = True
        # All workers have exited. Collect Tcl callbacks/variables on their owning thread.
        for form in (*self.forms, *self.c5_forms.values()):
            form.variables.clear()
        gc.collect()


def main():
    root = tk.Tk()
    style = ttk.Style(root)
    if "vista" in style.theme_names():
        style.theme_use("vista")
    root.option_add("*Font", ("Microsoft YaHei UI", 10))
    BridgeGUI(root)
    root.mainloop()


if __name__ == "__main__":
    main()
