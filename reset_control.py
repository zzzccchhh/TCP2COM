"""独立 TCP 复位协议与五次点击窗口，不参与串口透传。"""

import queue
import socket
import threading
import time
import uuid


class ResetError(Exception):
    """确认未执行或请求发送前失败。"""


class ResetOutcomeUnknown(ResetError):
    """请求可能已执行，禁止自动重发。"""


class FiveClickGate:
    def __init__(self, window=3.0, required=5):
        self.window = window
        self.required = required
        self.count = 0
        self.started = 0.0
        self.busy = False

    def expire(self, now):
        if self.count and now - self.started >= self.window:
            self.count = 0

    def click(self, now):
        if self.busy:
            return False
        self.expire(now)
        if not self.count:
            self.started = now
        self.count += 1
        if self.count == self.required:
            self.count = 0
            self.busy = True
            return True
        return False

    def finish(self):
        self.busy = False
        self.count = 0


def perform_reset(host, port, progress, cancel, timeout=5.0, *, device="esp01s"):
    """仅发送一次 RESET；收到匹配编号的 DONE 才认定完成。"""
    attempted = False
    try:
        with socket.create_connection((host, port), timeout=timeout) as sock:
            sock.settimeout(0.1)
            deadline = time.monotonic() + timeout
            pending = bytearray()

            def read_line():
                while True:
                    if cancel.is_set():
                        raise ResetError("控制操作已取消")
                    if time.monotonic() >= deadline:
                        raise ResetError("等待控制响应超时")
                    if b"\n" in pending:
                        line, _, remaining = pending.partition(b"\n")
                        pending[:] = remaining
                        if len(line) > 79:
                            raise ResetError("控制响应过长")
                        return line.decode("ascii")
                    if len(pending) > 79:
                        raise ResetError("控制响应过长")
                    try:
                        data = sock.recv(80 - len(pending))
                    except socket.timeout:
                        continue
                    if not data:
                        raise ResetError("控制连接已断开")
                    pending.extend(data)

            # 先验证固件协议，旧固件/错误端口不会收到 RESET 命令。
            greeting = "C5-RESET/1 READY" if device == "c5" else "ESP01S-RESET/1 READY"
            if read_line() != greeting:
                raise ResetError("控制协议不匹配，请检查固件和控制端口")
            if cancel.is_set():
                raise ResetError("控制操作已取消")
            request_id = uuid.uuid4().hex
            attempted = True
            sock.sendall(f"RESET {request_id}\n".encode("ascii"))
            response = read_line()
            if response == f"BUSY {request_id}":
                attempted = False
                raise ResetError("ESP 正在执行其他复位，本次未执行")
            if response == f"STARTED {request_id}":
                progress("C5 已开始复位：所选引脚拉低，数据灯快速呼吸" if device == "c5" else "ESP 已开始复位：GPIO0 拉低，GPIO2 快闪")
                response = read_line()
            if response != f"DONE {request_id}":
                raise ResetError("控制响应无效或请求编号不匹配")
    except (OSError, UnicodeError, ResetError) as exc:
        if attempted:
            raise ResetOutcomeUnknown(
                f"复位结果未知：{exc}；不会自动重发，请检查设备状态"
            ) from exc
        raise ResetError(f"复位未发送：{exc}；请检查控制端口及固件支持") from exc


class ConsoleResetController:
    """隐藏的 Tk 根窗口只处理命令行打开请求和复位弹窗。"""

    def __init__(self, app):
        import tkinter as tk

        self.app = app
        self.root = tk.Tk()
        self.root.withdraw()
        self.panel = None
        self.closed = False
        self.app.ui.enable_reset_entry()
        self.poll_job = self.root.after(50, self.poll)

    def open_reset_panel(self):
        if self.closed or self.app.close_event.is_set():
            return
        if self.panel is None:
            self.panel = ResetPanel(self.app, self.root)
        self.panel.show()

    def poll(self):
        self.poll_job = None
        if self.closed:
            return
        if self.app.close_event.is_set():
            self.close()
            return
        if self.app.ui.poll_reset_entry():
            self.open_reset_panel()
        self.poll_job = self.root.after(50, self.poll)

    def close(self):
        if self.closed:
            return
        self.closed = True
        self.app.ui.disable_reset_entry()
        if self.poll_job is not None:
            self.root.after_cancel(self.poll_job)
            self.poll_job = None
        if self.panel is not None:
            self.panel.shutdown()
        self.root.destroy()

    def run(self):
        try:
            self.root.mainloop()
        finally:
            self.close()
            if self.panel is not None and self.panel.worker is not None:
                self.panel.worker.join(timeout=0.5)


class ResetPanel:
    """Single window for the selected device; in-flight requests keep their target."""
    def __init__(self, app, parent, *, device="esp01s", host=""):
        import tkinter as tk
        self.tk = tk
        self.app = None
        self.device = None
        self.root = tk.Toplevel(parent)
        self.root.withdraw()
        self.root.title("远程复位")
        self.gate = FiveClickGate()
        self.events = queue.Queue()
        self.cancel = threading.Event()
        self.worker = None
        self.closed = False
        self.active_device = None
        self.host = tk.StringVar(master=self.root, value=host)
        self.port = tk.StringVar(master=self.root, value="8081")
        self.device_text = tk.StringVar(master=self.root)
        self.bridge_state = tk.StringVar(master=self.root)
        self.hint = tk.StringVar(master=self.root)
        tk.Label(self.root, textvariable=self.device_text, font=("Microsoft YaHei UI", 11)).pack(pady=(14, 6))
        tk.Label(self.root, textvariable=self.bridge_state).pack()
        self.address_frame = tk.Frame(self.root)
        tk.Label(self.address_frame, text="C5 设备 IPv4 地址").pack(side="left")
        tk.Entry(self.address_frame, textvariable=self.host, width=18).pack(side="left", padx=5)
        tk.Label(self.address_frame, text="控制端口").pack(side="left")
        tk.Entry(self.address_frame, textvariable=self.port, width=7).pack(side="left", padx=5)
        self.address_frame.pack(padx=12, pady=5)
        self.button = tk.Button(self.root, text="复位外部主控", command=self.click,
                                bg="#b91c1c", fg="white", activebackground="#991b1b",
                                activeforeground="white", font=("Microsoft YaHei UI", 19, "bold"),
                                width=18, height=2)
        self.button.pack(pady=10)
        tk.Label(self.root, textvariable=self.hint).pack()
        self.status = tk.StringVar(master=self.root, value="等待操作（0/5）")
        tk.Label(self.root, textvariable=self.status, wraplength=520, fg="#374151").pack(padx=12, pady=10)
        self.set_context(device, app, host)
        self._fit_contents()
        self.root.protocol("WM_DELETE_WINDOW", self.close)
        self.poll_job = self.root.after(50, self.poll)

    def _fit_contents(self):
        self.root.update_idletasks()
        width = max(500, self.root.winfo_reqwidth())
        height = max(280, self.root.winfo_reqheight())
        self.root.minsize(width, height)
        self.root.geometry(f"{max(560, width, self.root.winfo_width())}x"
                           f"{max(300, height, self.root.winfo_height())}")

    def _set_status(self, message):
        self.status.set(message)
        self._fit_contents()

    def set_context(self, device, app=None, host=""):
        if self.closed:
            return
        changed = device != self.device
        target_changed = changed or (device == "esp01s" and app is not self.app)
        self.device, self.app = device, app
        if target_changed and not self.gate.busy:
            self.gate.finish()
            self.status.set("等待操作（0/5）")
        if changed and device == "c5" and not self.host.get():
            self.host.set(host)
        labels = {"c5": "ESP32-C5", "esp01s": "ESP-01S", "s3": "ESP32-S3"}
        self.device_text.set(f"当前设备：{labels.get(device, device)} · 外部主控复位")
        if device == "c5":
            self.address_frame.pack(before=self.button, padx=12, pady=5)
            self.hint.set("3 秒内点击 5 次 · 所选引脚开漏拉低约 1 秒")
        else:
            self.address_frame.pack_forget()
            self.hint.set("3 秒内点击 5 次 · GPIO0 拉低 1 秒后释放" if device == "esp01s"
                          else "当前固件不支持远程复位")
        self._refresh_state()

    def _available(self):
        if self.device == "c5":
            return True
        return (self.device == "esp01s" and self.app is not None and
                not self.app.close_event.is_set() and not getattr(self.app.args, "no_reset_ui", False) and
                getattr(self.app.args, "device", "esp01s") == "esp01s")

    def _refresh_state(self):
        if self.gate.busy:
            self.bridge_state.set(f"{self.active_device} 复位请求进行中；完成前不能再次触发")
        elif self.device == "c5":
            self.bridge_state.set("控制连接独立于业务桥接")
        elif self.device == "esp01s" and self._available():
            sock, _ = self.app.conn.snapshot()
            self.bridge_state.set(f"透传 {'已连接' if sock is not None else '未连接 / 重连中'}"
                                  f" · {self.app.args.com} · {self.app.args.baud} baud"
                                  f" · {self.app.args.host}:{self.app.args.control_port}")
        elif self.device == "esp01s":
            self.bridge_state.set("请启动 ESP-01S TCP 客户端桥接并启用复位入口")
        else:
            self.bridge_state.set("当前设备不支持远程复位")
        self.button.configure(state=self.tk.NORMAL if self._available() and not self.gate.busy else self.tk.DISABLED)

    def show(self):
        if self.closed: return
        self.root.deiconify(); self.root.lift(); self.root.focus_set()

    def click(self):
        if self.closed or self.gate.busy or not self._available(): return
        if not self.gate.click(time.monotonic()):
            self._set_status(f"已点击 {self.gate.count}/5（从首次点击起 3 秒内完成）")
            return
        if self.device == "c5":
            try:
                import ipaddress
                host = str(ipaddress.IPv4Address(self.host.get().strip()))
                port = int(self.port.get())
                if not 1 <= port <= 65535: raise ValueError()
            except ValueError:
                self.gate.finish(); self._set_status("请输入有效的 C5 IPv4 地址和端口")
                self._refresh_state()
                return
        else:
            host, port = self.app.args.host, self.app.args.control_port
        device = self.device
        self.active_device = "ESP32-C5" if device == "c5" else "ESP-01S"
        notify = getattr(getattr(self.app, "ui", None), "message", lambda _: None)
        self._refresh_state()
        self._set_status(f"正在向 {self.active_device} 发送复位请求…")
        self.worker = threading.Thread(target=self.request_reset,
                                       args=(host, port, self.events, self.cancel, notify, device),
                                       name="remote-reset", daemon=True)
        self.worker.start()

    @staticmethod
    def request_reset(host, port, events, cancel, notify, device="esp01s"):
        try:
            if device == "esp01s":
                perform_reset(host, port, lambda text: events.put((False, text)), cancel)
                message = "复位完成：GPIO0 已释放，恢复正常状态灯"
            else:
                perform_reset(host, port, lambda text: events.put((False, text)), cancel, device="c5")
                message = "复位输出已释放；请检查主控状态"
        except ResetError as exc:
            message = str(exc)
        message = f"{'ESP32-C5' if device == 'c5' else 'ESP-01S'}：{message}"
        notify(message)
        events.put((True, message))

    def poll(self):
        self.poll_job = None
        if self.closed: return
        old_count = self.gate.count
        self.gate.expire(time.monotonic())
        if old_count and not self.gate.count:
            self._set_status("点击超时，已清零（0/5）")
        while True:
            try: finished, message = self.events.get_nowait()
            except queue.Empty: break
            if not finished and self.active_device:
                message = f"{self.active_device}：{message}"
            self._set_status(message)
            if finished:
                self.gate.finish()
                self.active_device = None
        self._refresh_state()
        self.poll_job = self.root.after(50, self.poll)

    def close(self):
        if self.closed: return
        self.root.withdraw()
        if not self.gate.busy and self.gate.count:
            self.gate.finish()
            self._set_status("等待操作（0/5）")

    def shutdown(self):
        if self.closed: return
        self.closed = True
        self.cancel.set()
        if self.poll_job is not None:
            self.root.after_cancel(self.poll_job)
            self.poll_job = None
        self.root.destroy()
        self.status = None
        self.bridge_state = None
