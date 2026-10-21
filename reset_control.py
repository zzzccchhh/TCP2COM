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


def perform_reset(host, port, progress, cancel, timeout=5.0):
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
            if read_line() != "ESP01S-RESET/1 READY":
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
                progress("ESP 已开始复位：GPIO0 拉低，GPIO2 快闪")
                response = read_line()
            if response != f"DONE {request_id}":
                raise ResetError("控制响应无效或请求编号不匹配")
    except (OSError, UnicodeError, ResetError) as exc:
        if attempted:
            raise ResetOutcomeUnknown(
                f"复位结果未知：{exc}；不会自动重发，请检查设备状态"
            ) from exc
        raise ResetError(f"复位未发送：{exc}；请检查控制端口及固件支持") from exc


class BridgeWindow:
    """主窗口管理桥接生命周期，复位面板按需创建并复用。"""

    def __init__(self, app):
        import tkinter as tk

        self.app = app
        self.root = tk.Tk()
        self.root.title("TCP ↔ COM · 桥接")
        self.panel = None
        self.closed = False
        tk.Label(self.root, text=f"{app.args.host}:{app.args.port} ↔ {app.args.com}",
                 font=("Microsoft YaHei UI", 11)).pack(padx=20, pady=(16, 8))
        self.bridge_state = tk.StringVar(master=self.root, value="桥接连接中…")
        tk.Label(self.root, textvariable=self.bridge_state).pack(padx=20)
        self.open_button = tk.Button(self.root, text="打开急停 / 复位面板",
                                     command=self.open_reset_panel)
        self.open_button.pack(padx=20, pady=14)
        tk.Label(self.root, text="关闭急停面板不影响桥接；关闭此主窗口退出程序。").pack(
            padx=20, pady=(0, 16)
        )
        self.root.protocol("WM_DELETE_WINDOW", self.close)
        self.poll_job = self.root.after(100, self.poll)

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
        sock, _ = self.app.conn.snapshot()
        self.bridge_state.set(
            f"透传 {'已连接' if sock is not None else '未连接 / 重连中'}"
            f" · {self.app.args.baud} baud"
        )
        self.poll_job = self.root.after(100, self.poll)

    def close(self):
        if self.closed:
            return
        self.closed = True
        self.app.close_event.set()
        self.app.conn.close_current("桥接主窗口关闭")
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
    def __init__(self, app, parent):
        # Tk 在主线程创建和更新；网络线程只写事件队列。
        import tkinter as tk

        self.tk = tk
        self.app = app
        self.root = tk.Toplevel(parent)
        self.root.withdraw()
        self.root.title("TCP ↔ COM · 远程复位")
        self.gate = FiveClickGate()
        self.events = queue.Queue()
        self.cancel = threading.Event()
        self.worker = None
        self.closed = False
        tk.Label(self.root, text=f"{app.args.host}:{app.args.control_port} → 外部单片机复位",
                 font=("Microsoft YaHei UI", 11)).pack(pady=(14, 6))
        self.bridge_state = tk.StringVar(master=self.root, value="桥接连接中…")
        tk.Label(self.root, textvariable=self.bridge_state).pack()
        self.button = tk.Button(
            self.root, text="急停 / 复位", command=self.click,
            bg="#b91c1c", fg="white", activebackground="#991b1b",
            activeforeground="white", font=("Microsoft YaHei UI", 19, "bold"),
            width=18, height=2,
        )
        self.button.pack(pady=10)
        tk.Label(self.root, text="3 秒内点击 5 次触发 · GPIO0 拉低 1 秒后释放").pack()
        self.status = tk.StringVar(master=self.root, value="等待操作（0/5）")
        tk.Label(self.root, textvariable=self.status, wraplength=520,
                 fg="#374151").pack(padx=12, pady=10)
        self._fit_contents()
        self.root.protocol("WM_DELETE_WINDOW", self.close)
        self.poll_job = self.root.after(50, self.poll)

    def _fit_contents(self):
        # 字体缩放和多行结果会改变所需高度，不能固定为 300px。
        self.root.update_idletasks()
        width = max(500, self.root.winfo_reqwidth())
        height = max(280, self.root.winfo_reqheight())
        self.root.minsize(width, height)
        self.root.geometry(
            f"{max(560, width, self.root.winfo_width())}x"
            f"{max(300, height, self.root.winfo_height())}"
        )

    def _set_status(self, message):
        self.status.set(message)
        self._fit_contents()

    def show(self):
        if self.closed:
            return
        self.root.deiconify()
        self.root.lift()
        self.root.focus_set()

    def click(self):
        if self.closed or self.app.close_event.is_set() or self.gate.busy:
            return
        if not self.gate.click(time.monotonic()):
            self._set_status(f"已点击 {self.gate.count}/5（从首次点击起 3 秒内完成）")
            return
        self.button.configure(state=self.tk.DISABLED)
        self._set_status("正在发送复位请求…")
        self.worker = threading.Thread(target=self.request_reset, name="remote-reset", daemon=True)
        self.worker.start()

    def request_reset(self):
        try:
            perform_reset(
                self.app.args.host, self.app.args.control_port,
                lambda text: self.events.put((False, text)), self.cancel,
            )
            message = "复位完成：GPIO0 已释放，恢复正常状态灯"
        except ResetError as exc:
            message = str(exc)
        self.app.ui.message(message)
        self.events.put((True, message))

    def poll(self):
        self.poll_job = None
        if self.closed:
            return
        if self.app.close_event.is_set():
            self.shutdown()
            return
        old_count = self.gate.count
        self.gate.expire(time.monotonic())
        if old_count and not self.gate.count:
            self._set_status("点击超时，已清零（0/5）")
        while True:
            try:
                finished, message = self.events.get_nowait()
            except queue.Empty:
                break
            self._set_status(message)
            if finished:
                self.gate.finish()
                self.button.configure(state=self.tk.NORMAL)
        sock, _ = self.app.conn.snapshot()
        self.bridge_state.set(
            f"透传 {'已连接' if sock is not None else '未连接 / 重连中'}"
            f" · {self.app.args.com} · {self.app.args.baud} baud"
        )
        self.poll_job = self.root.after(50, self.poll)

    def close(self):
        """仅隐藏面板；已发送的请求继续执行并保留结果。"""
        if self.closed:
            return
        self.root.withdraw()
        if not self.gate.busy and self.gate.count:
            self.gate.finish()
            self._set_status("等待操作（0/5）")

    def shutdown(self):
        """整个程序退出时才取消控制等待并销毁面板。"""
        if self.closed:
            return
        self.closed = True
        self.cancel.set()
        if self.poll_job is not None:
            self.root.after_cancel(self.poll_job)
            self.poll_job = None
        self.root.destroy()
