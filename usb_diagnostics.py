"""Optional native USB log reader; never writes to the payload connection."""

import re
import threading
import time

import serial


def parse_stats(line):
    """Keep original lines; unknown/new fields never interrupt log collection."""
    if "stats:" not in line:
        return {}
    fields = {}
    for key, value in re.findall(r"([A-Za-z_][A-Za-z_0-9/ .>\-]*?)=([^\s]+)", line.split("stats:", 1)[1]):
        fields[key.strip()] = value
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
                    device.dtr = False
                    device.rts = False
                    device.open()
                    with self.lock:
                        self.connected = True
                    self.notify(f"USB 日志已连接：{self.port}")
                    pending = bytearray()
                    with device:
                        while not self.stop_event.is_set():
                            chunk = device.read(1024)
                            pending.extend(chunk)
                            while b"\n" in pending:
                                raw, _, pending = pending.partition(b"\n")
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
                except (serial.SerialException, OSError, ValueError) as exc:
                    with self.lock:
                        self.connected = False
                    if not self.stop_event.is_set():
                        self.notify(f"USB 日志不可用：{exc}；网络透传继续，2 秒后重试")
                        self.stop_event.wait(2)
                finally:
                    with self.lock:
                        self.connected = False
        except OSError as exc:
            self.notify(f"USB 日志文件打开失败：{exc}；网络透传继续")
        finally:
            if output:
                output.close()

    def close(self):
        self.stop_event.set()
        if self.thread:
            self.thread.join(timeout=1)
