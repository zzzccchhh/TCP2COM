"""Independent, bounded TCP firmware-log subscriber; never opens a COM port."""

import copy
import ipaddress
import re
import socket
import threading
import time

from usb_diagnostics import parse_stats


def log_target(host, port):
    host = str(ipaddress.IPv4Address(str(host).strip()))
    if not str(port).isdigit() or not 1024 <= int(port) <= 65535:
        raise ValueError("日志端口应为 1024～65535")
    return host, int(port)


class NetworkLogMonitor:
    def __init__(self, host, port, notify, event=None):
        self.host, self.port = log_target(host, port)
        self.notify = notify
        self.event = event or (lambda text: None)
        self.stop_event = threading.Event()
        self.lock = threading.Lock()
        self.sock = None
        self.connected = False
        self.message = "连接中"
        self.fields = {}
        self.updated = None
        self.dropped = 0
        self.thread = threading.Thread(target=self._run, name="network-log", daemon=True)

    def start(self):
        self.thread.start()

    def status(self):
        with self.lock:
            return {"host": self.host, "port": self.port, "connected": self.connected,
                    "message": self.message, "fields": copy.deepcopy(self.fields),
                    "age": None if self.updated is None else max(0, time.monotonic() - self.updated),
                    "dropped": self.dropped, "closing": self.stop_event.is_set()}

    def close(self):
        self.stop_event.set()
        with self.lock:
            sock = self.sock
        if sock:
            try:
                sock.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
        if self.thread.ident:
            self.thread.join(5)

    def _run(self):
        while not self.stop_event.is_set():
            try:
                with socket.create_connection((self.host, self.port), timeout=2) as sock:
                    sock.settimeout(.25)
                    sock.setsockopt(socket.SOL_SOCKET, socket.SO_KEEPALIVE, 1)
                    with self.lock:
                        self.sock, self.connected, self.message = sock, True, "已连接"
                        self.fields, self.updated = {}, None
                    self.event(f"网络日志已连接：{self.host}:{self.port}")
                    buffer = bytearray()
                    discard = False
                    while not self.stop_event.is_set():
                        try:
                            data = sock.recv(4096)
                        except socket.timeout:
                            continue
                        if not data:
                            raise ConnectionError("设备关闭连接")
                        for part in data.splitlines(keepends=True):
                            # Only LF terminates an ESP log record. Bound unfinished lines.
                            buffer.extend(part)
                            if len(buffer) > 8192:
                                buffer.clear()
                                if not discard:
                                    with self.lock:
                                        self.dropped += 1
                                discard = True
                            if part.endswith(b"\n"):
                                if not discard:
                                    text = buffer.rstrip(b"\r\n").decode("utf-8", errors="replace")
                                    if text and not text.startswith(("C5CFG ", "BRIDGECFG ")):
                                        self.notify(text)
                                        fields = parse_stats(re.sub(r"\x1b\[[0-?]*[ -/]*[@-~]", "", text))
                                        if fields:
                                            with self.lock:
                                                self.fields.update(fields)
                                                self.updated = time.monotonic()
                                buffer.clear()
                                discard = False
            except (OSError, ValueError) as exc:
                with self.lock:
                    self.message = f"重试中：{exc}"
            finally:
                with self.lock:
                    self.sock, self.connected = None, False
            self.stop_event.wait(1)
        with self.lock:
            self.message = "已断开"
