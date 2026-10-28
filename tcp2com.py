#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""ESP-01S / ESP32-C5 / ESP32-S3 TCP、UDP <-> COM 双向桥接。

特性：
- TCP -> 串口、串口 -> TCP 双向透传
- 同一连接状态只提示一次；速率行在命令行底部刷新
- 断线后关闭旧 socket 并自动重连
- TCP Keepalive 参数可配置
- 有界队列 + TCP 背压，避免队列满时主动丢弃较新的图像数据
- 按连接代次隔离数据，尽量避免旧连接数据混入重连后的新连接
- 串口/TCP 批量读写，支持高波特率

依赖：py -3 -m pip install pyserial
"""

import argparse
import ctypes
from collections import deque
import logging
import os
import queue
import signal
import socket
import sys
import threading
import time
import math
import webbrowser
from typing import Optional, Tuple

from console_control import WindowsConsoleControl
from reset_control import ConsoleResetController
from bridge_settings import load_defaults
from device_capabilities import DEVICES, capabilities
from network_transport import TCPServer, DatagramStream, DatagramSendError

try:
    import serial
    from serial.tools import list_ports
except ImportError:
    sys.stderr.write("缺少 pyserial，请执行：py -3 -m pip install pyserial\n")
    raise SystemExit(1)

from usb_diagnostics import USBMonitor

log = logging.getLogger("tcp2com")

CONNECT_TIMEOUT = 4.0
QUEUE_BLOCKS = 128
QUEUE_CHUNK_BYTES = 16 * 1024
TCP_SEND_BATCH_BYTES = 64 * 1024
TCP_SEND_BATCH_WINDOW = 0.002
SERIAL_BATCH_BYTES = 64 * 1024
# Smaller serial write calls keep rate reporting responsive when Windows
# blocks until bytes have drained through the UART driver.
SERIAL_WRITE_CHUNK_BYTES = 4 * 1024
RATE_WINDOW_SECONDS = 1.0
WARN_INTERVAL = 3.0


_BYTESIZES = {
    "5": serial.FIVEBITS,
    "6": serial.SIXBITS,
    "7": serial.SEVENBITS,
    "8": serial.EIGHTBITS,
}
_PARITIES = {
    "N": serial.PARITY_NONE,
    "E": serial.PARITY_EVEN,
    "O": serial.PARITY_ODD,
    "M": serial.PARITY_MARK,
    "S": serial.PARITY_SPACE,
}
_STOPBITS = {
    "1": serial.STOPBITS_ONE,
    "1.5": serial.STOPBITS_ONE_POINT_FIVE,
    "2": serial.STOPBITS_TWO,
}


def human_bytes(value: float) -> str:
    value = float(value)
    for unit in ("B", "kB", "MB", "GB", "TB"):
        if value < 1000 or unit == "TB":
            return f"{int(value)} B" if unit == "B" else f"{value:.1f} {unit}"
        value /= 1000.0
    return f"{value:.1f} TB"


def close_socket(sock: Optional[socket.socket]) -> None:
    if sock is None:
        return
    try:
        sock.shutdown(socket.SHUT_RDWR)
    except OSError:
        pass
    try:
        sock.close()
    except OSError:
        pass


def configure_socket(sock: socket.socket, keepalive_idle: float,
                     keepalive_interval: float, keepalive_count: int) -> None:
    """设置已建立 TCP socket 的低延迟与 Keepalive。"""
    sock.settimeout(None)
    try:
        sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
    except OSError as exc:
        log.debug("TCP_NODELAY 设置失败：%s", exc)

    if keepalive_idle <= 0:
        try:
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_KEEPALIVE, 0)
        except OSError:
            pass
        return

    try:
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_KEEPALIVE, 1)
        if os.name == "nt":
            idle_ms = max(1000, int(keepalive_idle * 1000))
            interval_ms = max(1000, int(keepalive_interval * 1000))
            sock.ioctl(socket.SIO_KEEPALIVE_VALS, (1, idle_ms, interval_ms))
            # Windows 的 Keepalive 探测次数通常由系统参数控制。
        else:
            if hasattr(socket, "TCP_KEEPIDLE"):
                sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_KEEPIDLE,
                                max(1, int(keepalive_idle)))
            if hasattr(socket, "TCP_KEEPINTVL"):
                sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_KEEPINTVL,
                                max(1, int(keepalive_interval)))
            if hasattr(socket, "TCP_KEEPCNT"):
                sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_KEEPCNT,
                                max(1, int(keepalive_count)))
    except (OSError, AttributeError) as exc:
        log.warning("TCP Keepalive 配置失败，可能使用系统默认参数：%s", exc)


def flow_kwargs(flow: str) -> dict:
    if flow == "rtscts":
        return {"rtscts": True}
    if flow == "dsrdtr":
        return {"dsrdtr": True}
    return {}


def enable_windows_vt() -> bool:
    """尝试启用 Windows 终端 ANSI 控制序列，失败时使用回车覆盖模式。"""
    if os.name != "nt" or not sys.stdout.isatty():
        return False
    try:
        kernel32 = ctypes.windll.kernel32
        handle = kernel32.GetStdHandle(-11)  # STD_OUTPUT_HANDLE
        mode = ctypes.c_uint32()
        if not kernel32.GetConsoleMode(handle, ctypes.byref(mode)):
            return False
        return bool(kernel32.SetConsoleMode(handle, mode.value | 0x0004))
    except Exception:
        return False


class ConsoleUI:
    """状态消息只在状态变化时打印；速率单独在终端底行刷新。"""
    def __init__(self):
        self.lock = threading.RLock()
        self.interactive = bool(sys.stdout.isatty())
        self.ansi = enable_windows_vt()
        self.rate_line = "等待连接..."
        self.last_state = None
        self.last_warn = {}
        self.last_rate_print = 0.0
        self.reset_entry = None

    def enable_reset_entry(self):
        if not self.interactive:
            self.message("当前输出不是交互式控制台，不显示急停入口；桥接继续运行")
            return
        with self.lock:
            try:
                self.reset_entry = WindowsConsoleControl()
            except OSError as exc:
                self.message(f"无法启用命令行急停入口：{exc}；桥接继续运行")
            else:
                self.message("点击底行的急停入口或按 R 打开复位窗口；Ctrl+C 退出桥接")

    def disable_reset_entry(self):
        with self.lock:
            control, self.reset_entry = self.reset_entry, None
            if control is not None:
                try:
                    control.clear_line()
                except OSError:
                    pass
                try:
                    control.close()
                except OSError:
                    pass

    def _reset_entry_failed(self, exc):
        self.disable_reset_entry()
        self.message(f"命令行急停入口已停用：{exc}；桥接继续运行")

    def poll_reset_entry(self):
        with self.lock:
            if self.reset_entry is None:
                return False
            try:
                opened = False
                for event in self.reset_entry.read_events():
                    if self.reset_entry is None:
                        break
                    if event[0] == "resize":
                        self._draw_rate_locked()
                    elif event[0] == "open" or (
                        event[0] == "click" and self.reset_entry.hit_test(*event[1:])
                    ):
                        opened = True
                return opened
            except OSError as exc:
                self._reset_entry_failed(exc)
                return False

    def _clear_current_line(self) -> None:
        if self.reset_entry is not None:
            try:
                self.reset_entry.clear_line()
                return
            except OSError as exc:
                self._reset_entry_failed(exc)
        if self.ansi:
            sys.stdout.write("\r\x1b[2K")
        elif self.interactive:
            sys.stdout.write("\r" + (" " * 240) + "\r")

    def _draw_rate_locked(self) -> None:
        if self.interactive:
            if self.reset_entry is not None:
                try:
                    self.reset_entry.render(self.rate_line)
                    return
                except OSError as exc:
                    self._reset_entry_failed(exc)
            self._clear_current_line()
            sys.stdout.write(self.rate_line[:230])
            sys.stdout.flush()

    def message(self, message: str) -> None:
        stamp = time.strftime("%H:%M:%S")
        with self.lock:
            if self.interactive:
                self._clear_current_line()
            sys.stdout.write(f"[{stamp}] {message}\n")
            sys.stdout.flush()
            self._draw_rate_locked()

    def state(self, key: str, message: str) -> None:
        with self.lock:
            if self.last_state == key:
                return
            self.last_state = key
        self.message(message)

    def warning_once(self, key: str, message: str, interval: float = WARN_INTERVAL) -> None:
        now = time.monotonic()
        with self.lock:
            previous = self.last_warn.get(key, 0.0)
            if now - previous < interval:
                return
            self.last_warn[key] = now
        self.message("警告：" + message)

    def set_rate_line(self, text: str, refresh_interval: float) -> None:
        with self.lock:
            self.rate_line = text
            if self.interactive:
                self._draw_rate_locked()
            else:
                # 重定向到文件/管道时无法固定显示底行，降频输出避免刷屏。
                now = time.monotonic()
                if now - self.last_rate_print >= max(1.0, refresh_interval):
                    sys.stdout.write(text + "\n")
                    sys.stdout.flush()
                    self.last_rate_print = now


class Connection:
    """管理当前 socket 和连接代次，隔离重新连接前后的数据。"""
    def __init__(self):
        self._lock = threading.Lock()
        self._sock = None
        self._generation = 0

    def snapshot(self) -> Tuple[Optional[socket.socket], int]:
        with self._lock:
            return self._sock, self._generation

    def set(self, sock: socket.socket) -> int:
        with self._lock:
            old = self._sock
            self._generation += 1
            generation = self._generation
            self._sock = sock
        if old is not None and old is not sock:
            close_socket(old)
        return generation

    def is_current_generation(self, generation: int) -> bool:
        with self._lock:
            return self._sock is not None and self._generation == generation

    def is_current(self, sock: socket.socket, generation: int) -> bool:
        with self._lock:
            return self._sock is not None and self._sock is sock and self._generation == generation

    def close_current(self, reason: str) -> None:
        with self._lock:
            sock = self._sock
            self._sock = None
            if sock is not None:
                self._generation += 1
        if sock is not None:
            close_socket(sock)

    def kill(self, sock: socket.socket, reason: str) -> bool:
        with self._lock:
            owned = self._sock is sock
            if owned:
                self._sock = None
                self._generation += 1
        close_socket(sock)
        return owned


class Stats:
    KEYS = ("tcp_rx_bytes", "tcp_to_com_bytes", "com_to_tcp_bytes", "tcp_to_com_drop", "com_to_tcp_drop",
            "udp_rx_packets", "udp_tx_packets", "udp_rejected", "udp_empty")

    def __init__(self):
        self.lock = threading.Lock()
        self.values = {key: 0 for key in self.KEYS}

    def add(self, key: str, amount: int) -> None:
        if amount <= 0:
            return
        with self.lock:
            self.values[key] += amount

    def snapshot(self) -> dict:
        with self.lock:
            return dict(self.values)


class App:
    def __init__(self, args, ui: ConsoleUI):
        self.args = args
        self.ui = ui
        self.ser = None
        self.server = None
        self.udp = None
        self.usb = None
        self.last_udp_rx = None
        self.close_event = threading.Event()
        self.conn = Connection()
        self.stats = Stats()
        self.exit_code = 0
        # 队列项格式：(connection_generation, bytes)
        self.q_tcp_to_com = queue.Queue(maxsize=QUEUE_BLOCKS)
        self.q_com_to_tcp = queue.Queue(maxsize=QUEUE_BLOCKS)
        self.threads = []
        self.stopped = False
        self.pressure_lock = threading.Lock()
        self.pressure_active = {"tcp_to_com": False, "com_to_tcp": False}
        self.pressure_warn_time = {"tcp_to_com": 0.0, "com_to_tcp": 0.0}
        self.serial_reader_ready = threading.Condition()
        self.serial_reader_generation = 0

    def start(self) -> bool:
        try:
            self.ser = serial.serial_for_url(
                self.args.com,
                baudrate=self.args.baud,
                bytesize=_BYTESIZES[self.args.bytesize],
                parity=_PARITIES[self.args.parity],
                stopbits=_STOPBITS[self.args.stopbits],
                timeout=self.args.timeout,
                write_timeout=self.args.write_timeout,
                **flow_kwargs(self.args.flow),
            )
            if self.args.mode == "tcp-server":
                self.server = TCPServer((self.args.listen, self.args.port), self.ui.message, self.args.host)
            elif self.args.mode == "udp":
                self.udp = DatagramStream((self.args.listen, self.args.port),
                                          (self.args.host, self.args.peer_port), self.args.udp_payload,
                                          self.close_event, self._udp_record)
        except (serial.SerialException, OSError, ValueError) as exc:
            self.ui.message(f"启动失败：{exc}；请检查串口占用及网络地址")
            self.exit_code = 1
            self.stop()
            return False

        workers = [
            (self._tcp_recv_worker, "tcp-recv"),
            (self._serial_write_worker, "serial-write"),
            (self._serial_read_worker, "serial-read"),
            (self._tcp_send_worker, "tcp-send"),
        ]
        for target, name in workers:
            thread = threading.Thread(target=target, name=name, daemon=True)
            thread.start()
            self.threads.append(thread)

        if self.ser:
            self.ui.message(
                f"桥接启动：{self.args.com}，{self.args.baud} baud，"
                f"{self.args.bytesize}{self.args.parity}{self.args.stopbits}，流控={self.args.flow}"
            )
        if self.args.mode == "tcp-client":
            self.ui.message(f"TCP 目标：{self.args.host}:{self.args.port}")
        else:
            address = self.server.address if self.server else self.udp.address
            self.ui.message(f"{self.args.mode} 已绑定：{address[0]}:{address[1]}")
            if self.server:
                self.ui.message("CH32 目标填写电脑热点/局域网 IPv4 地址（不是 0.0.0.0），并先启动本程序；断线后需主控重新连接")
            else:
                self.ui.message(f"UDP 固定对端 {self.args.host}:{self.args.peer_port}；报文载荷 {self.args.udp_payload} B；不保证送达/顺序")
        if capabilities(self.args.device).usb:
            self.ui.message(f"{capabilities(self.args.device).label} 无 TCP 8081 远程复位；SPI 模式 GPIO12 为软件 RST 输入")
        if self.args.usb_port:
            self.usb = USBMonitor(self.args.usb_port, self.args.usb_baud, self.ui.message, self.args.usb_log, device=self.args.device)
            self.usb.start()
        self.ui.message(
            f"参数：重连={self.args.reconnect:.1f}s，Keepalive 空闲={self.args.keepalive_idle:.1f}s，"
            f"探测间隔={self.args.keepalive_interval:.1f}s，缓冲={self.args.bufsize}B，"
            f"串口读超时={self.args.timeout:.3f}s，显示刷新={self.args.stats_interval:.2f}s"
        )
        if os.name == "nt":
            self.ui.message("说明：Windows Keepalive 探测次数通常由系统控制；断电检测并非即时")
        return True

    def _udp_record(self, key, amount):
        self.stats.add(key, amount)
        if key == "udp_rx_packets":
            self.last_udp_rx = time.monotonic()

    def run(self) -> None:
        last = time.monotonic()
        initial = self.stats.snapshot()
        history = deque([(last, initial)])
        refresh = max(0.1, min(self.args.stats_interval, 5.0))
        try:
            while not self.close_event.is_set():
                refresh = max(0.1, min(getattr(self.ui, "stats_interval", self.args.stats_interval), 5.0))
                self.close_event.wait(refresh)
                now = time.monotonic()
                current = self.stats.snapshot()
                history.append((now, current))

                # Display a rolling ~1-second average rather than one short
                # 250-ms bucket, which can look bursty for blocking serial writes.
                while len(history) > 2 and now - history[0][0] > RATE_WINDOW_SECONDS:
                    history.popleft()

                base_time, base = history[0]
                dt = max(now - base_time, 1e-6)
                tcp_rx_rate = (current["tcp_rx_bytes"] - base["tcp_rx_bytes"]) / dt
                tcp_to_com_rate = (current["tcp_to_com_bytes"] - base["tcp_to_com_bytes"]) / dt
                com_to_tcp_rate = (current["com_to_tcp_bytes"] - base["com_to_tcp_bytes"]) / dt
                sock, _ = self.conn.snapshot()
                state = "已连接" if sock is not None else "等待连接"
                if self.args.mode == "udp":
                    age = "尚未收包" if self.last_udp_rx is None else f"距收包 {now - self.last_udp_rx:.1f}s"
                    state = f"已绑定，{age}"
                q_depth = self.q_tcp_to_com.qsize()
                label = "UDP" if self.args.mode == "udp" else "TCP"
                rate = (
                    f"{label}:{state} | 网络收 {human_bytes(tcp_rx_rate)}/s | "
                    f"本地写 {human_bytes(tcp_to_com_rate)}/s | "
                    f"队列↓↑ {q_depth}/{self.q_com_to_tcp.qsize()}（各{QUEUE_BLOCKS}块） | "
                    f"本地→网络 {human_bytes(com_to_tcp_rate)}/s | "
                    f"累计丢弃 {human_bytes(current['tcp_to_com_drop'])}/"
                    f"{human_bytes(current['com_to_tcp_drop'])}"
                )
                if self.args.mode == "udp":
                    rate += (f" | UDP包收/发 {current['udp_rx_packets']}/{current['udp_tx_packets']}"
                             f" 拒收 {current['udp_rejected']}")
                self.ui.set_rate_line(rate, refresh)
        except KeyboardInterrupt:
            self.close_event.set()
            self.conn.close_current("用户退出")

    def stop(self) -> int:
        if self.stopped:
            return self.exit_code
        self.stopped = True
        self.close_event.set()
        self.conn.close_current("程序退出")
        if self.server:
            self.server.close()
        if self.udp:
            self.udp.close()
        if self.usb:
            self.usb.close()
        for thread in self.threads:
            thread.join(timeout=2.0)
            if thread.is_alive():
                self.ui.message(f"线程 {thread.name} 未在超时内退出")
        if self.ser is not None:
            try:
                self.ser.close()
            except (serial.SerialException, OSError):
                pass
        values = self.stats.snapshot()
        self.ui.message(
            "退出统计：网络→本地已写入 %s（丢弃 %s）；本地→网络已发送 %s（丢弃 %s）" % (
                human_bytes(values["tcp_to_com_bytes"]),
                human_bytes(values["tcp_to_com_drop"]),
                human_bytes(values["com_to_tcp_bytes"]),
                human_bytes(values["com_to_tcp_drop"]),
            )
        )
        if self.args.mode == "udp":
            self.ui.message(f"UDP 报文收/发={values['udp_rx_packets']}/{values['udp_tx_packets']}，"
                            f"非指定对端拒收={values['udp_rejected']}，空报文={values['udp_empty']}；不代表链路零丢包")
        return self.exit_code

    def _set_queue_pressure(self, name: str, active: bool) -> None:
        with self.pressure_lock:
            previous = self.pressure_active[name]
            if previous == active:
                return
            self.pressure_active[name] = active
        if active:
            self.ui.message(
                f"{name} 队列达到上限，暂停上游读取；UDP/真实串口的上游仍可能溢出，保留本地队列顺序"
            )
        else:
            self.ui.message(f"{name} 队列积压已缓解")

    def _enqueue(self, q: queue.Queue, name: str, generation: int, data: bytes) -> bool:
        """拆块后阻塞入队，队列满时暂停上游读取而非丢弃数据。"""
        for offset in range(0, len(data), QUEUE_CHUNK_BYTES):
            block = data[offset:offset + QUEUE_CHUNK_BYTES]
            while not self.close_event.is_set():
                if not self.conn.is_current_generation(generation):
                    remaining = len(data) - offset
                    self.stats.add(
                        "tcp_to_com_drop" if name == "tcp_to_com" else "com_to_tcp_drop",
                        remaining,
                    )
                    return False
                try:
                    q.put((generation, block), timeout=0.05)
                    if q.qsize() < max(1, QUEUE_BLOCKS // 2):
                        self._set_queue_pressure(name, False)
                    break
                except queue.Full:
                    self._set_queue_pressure(name, True)
            else:
                return False
        return True

    @staticmethod
    def _drain_queue(q: queue.Queue) -> int:
        total = 0
        while True:
            try:
                item = q.get_nowait()
                # 队列项：(generation, bytes)
                total += len(item[1])
            except queue.Empty:
                return total

    def _clear_old_queue(self, q: queue.Queue, stat_key: str, label: str) -> None:
        dropped = self._drain_queue(q)
        pressure_name = "tcp_to_com" if stat_key == "tcp_to_com_drop" else "com_to_tcp"
        self._set_queue_pressure(pressure_name, False)
        if dropped:
            self.stats.add(stat_key, dropped)
            self.ui.message(f"{label}：清理旧连接残留 {human_bytes(dropped)}")

    # --------------------------------------------------------
    # TCP 接收线程：唯一负责连接 / 重连；TCP -> 串口
    # --------------------------------------------------------
    def _tcp_recv_worker(self) -> None:
        addr = (self.args.host, self.args.port)
        announced_wait = False
        self.ui.state("connecting", f"网络模式：{self.args.mode}，等待数据通道...")

        while not self.close_event.is_set():
            sock = None
            try:
                if self.server:
                    sock = self.server.connect(self.close_event)
                elif self.udp:
                    sock = self.udp
                else:
                    sock = socket.create_connection(addr, timeout=self.args.connect_timeout)
                if not self.udp:
                    configure_socket(sock, self.args.keepalive_idle,
                                     self.args.keepalive_interval, self.args.keepalive_count)
                peer = sock.getpeername()
            except OSError as exc:
                close_socket(sock)
                if self.server:
                    self.server.release(sock)
                if self.close_event.is_set():
                    break
                if self.server or self.udp:
                    self.ui.message(f"监听/UDP 通道失败：{exc}")
                    self.exit_code = 1
                    self.close_event.set()
                    break
                if not announced_wait:
                    self.ui.state("waiting", f"TCP 连接失败，进入自动重试：{exc}")
                    announced_wait = True
                if self.args.reconnect <= 0:
                    self.exit_code = 1
                    self.close_event.set()
                    break
                self.close_event.wait(self.args.reconnect)
                continue

            if self.close_event.is_set():
                close_socket(sock)
                if self.server:
                    self.server.release(sock)
                break

            # 清理断线期间或上次会话遗留数据，再安装新连接代次。
            self._clear_old_queue(self.q_tcp_to_com, "tcp_to_com_drop", "重连前 TCP→串口队列")
            self._clear_old_queue(self.q_com_to_tcp, "com_to_tcp_drop", "重连前串口→TCP队列")
            if self.ser:
                try:
                    buffered = self.ser.in_waiting
                    self.ser.reset_input_buffer()
                    self.stats.add("com_to_tcp_drop", buffered)
                except (serial.SerialException, OSError) as exc:
                    close_socket(sock)
                    if self.server:
                        self.server.release(sock)
                    self.ui.message(f"串口会话隔离失败：{exc}，桥接将退出")
                    self.exit_code = 1
                    self.close_event.set()
                    break
            generation = self.conn.set(sock)
            if self.ser:
                # Do not forward the new peer's bytes until RX has finished
                # any old blocking read and armed a read for this generation.
                # This preserves the first loopback reply after reconnect.
                with self.serial_reader_ready:
                    while (self.serial_reader_generation != generation and
                           self.conn.is_current(sock, generation) and not self.close_event.is_set()):
                        self.serial_reader_ready.wait(.05)
            announced_wait = False
            self.ui.state("connected", f"{'UDP 已绑定，固定对端' if self.udp else 'TCP 已连接'} {peer[0]}:{peer[1]}")
            reason = "连接结束"

            try:
                while not self.close_event.is_set():
                    data = sock.recv(self.args.bufsize)
                    if not data:
                        reason = "服务端关闭连接（recv=0）"
                        break
                    # Count the actual TCP ingress separately from bytes accepted
                    # by the serial writer, so queue/backpressure can be diagnosed.
                    self.stats.add("tcp_rx_bytes", len(data))
                    if not self._enqueue(self.q_tcp_to_com, "tcp_to_com", generation, data):
                        reason = "连接代次变化或程序退出"
                        break
            except OSError as exc:
                reason = f"网络接收异常：{exc}"
                if not self.close_event.is_set() and self.udp:
                    self.exit_code = 1
                    self.close_event.set()
                    self.ui.message(reason)
            finally:
                self.conn.kill(sock, reason)
                self._clear_old_queue(self.q_tcp_to_com, "tcp_to_com_drop", "断线后 TCP→串口队列")
                self._clear_old_queue(self.q_com_to_tcp, "com_to_tcp_drop", "断线后串口→TCP队列")
                if self.server:
                    self.server.release(sock)
                if not self.close_event.is_set():
                    # 无论由接收线程还是发送线程首先发现故障，都只在状态变化时提示一次。
                    self.ui.state("waiting", f"网络会话结束（{reason}），{'继续监听，等待主控重连' if self.server else '等待重连'}")

            if self.close_event.is_set():
                break
            if self.server:
                continue
            if self.udp:
                break
            if self.args.reconnect <= 0:
                self.exit_code = 1
                self.close_event.set()
                break
            self.close_event.wait(self.args.reconnect)
            # waiting 状态不会每次重试都重复打印

    # --------------------------------------------------------
    # TCP -> 串口写线程：合并写入并丢弃过期连接代次
    # --------------------------------------------------------
    def _serial_write_worker(self) -> None:
        q = self.q_tcp_to_com
        pending = None

        while not self.close_event.is_set():
            if pending is not None:
                item = pending
                pending = None
            else:
                try:
                    item = q.get(timeout=0.1)
                except queue.Empty:
                    continue

            if q.qsize() < max(1, QUEUE_BLOCKS // 2):
                self._set_queue_pressure("tcp_to_com", False)

            generation, block = item
            if not self.conn.is_current_generation(generation):
                self.stats.add("tcp_to_com_drop", len(block))
                continue

            blocks = [block]
            total = len(block)
            deadline = time.monotonic() + 0.002

            while total < SERIAL_BATCH_BYTES:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    break
                try:
                    nxt = q.get(timeout=remaining)
                except queue.Empty:
                    break
                nxt_gen, nxt_block = nxt
                if nxt_gen != generation or total + len(nxt_block) > SERIAL_BATCH_BYTES:
                    pending = nxt
                    break
                blocks.append(nxt_block)
                total += len(nxt_block)

            # 批量写入前再次确认连接代次，防止旧会话数据进入新会话。
            if not self.conn.is_current_generation(generation):
                self.stats.add("tcp_to_com_drop", total)
                continue

            payload = b"".join(blocks)
            written = 0
            view = memoryview(payload)
            try:
                while view and not self.close_event.is_set():
                    if not self.conn.is_current_generation(generation):
                        self.stats.add("tcp_to_com_drop", len(view))
                        view = view[len(view):]
                        break

                    # Shorter writes produce timely progress updates and avoid
                    # counting a whole 64-KB batch only after a long blocking write.
                    chunk = view[:SERIAL_WRITE_CHUNK_BYTES]
                    n = self.ser.write(chunk)
                    if not n:
                        self.close_event.wait(0.001)
                        continue

                    written += n
                    self.stats.add("tcp_to_com_bytes", n)
                    view = view[n:]

                if view and self.close_event.is_set():
                    self.stats.add("tcp_to_com_drop", len(view))
                    view = view[len(view):]

            except serial.SerialTimeoutException:
                # A timeout does not reliably reveal whether the current OS write
                # partially reached the wire; count the unconfirmed remainder as lost.
                self.stats.add(
                    "tcp_to_com_drop",
                    max(0, len(payload) - written),
                )
                self.ui.warning_once(
                    "serial-write-timeout",
                    "串口写超时；本批剩余数据无法可靠重放，已记入丢弃计数",
                )
            except (serial.SerialException, OSError) as exc:
                # Previously confirmed chunks are already included in the byte counter.
                self.stats.add(
                    "tcp_to_com_drop",
                    max(0, len(payload) - written),
                )
                self.ui.message(f"串口写入失败：{exc}，桥接将退出")
                self.exit_code = 1
                self.close_event.set()
                self.conn.close_current("串口写入失败")
                return

    # --------------------------------------------------------
    # 串口读取线程：串口 -> TCP；无 TCP 时读走并计入丢弃
    # --------------------------------------------------------
    def _serial_read_worker(self) -> None:
        while not self.close_event.is_set():
            try:
                with self.serial_reader_ready:
                    read_sock, read_generation = self.conn.snapshot()
                    self.serial_reader_generation = read_generation if read_sock else 0
                    self.serial_reader_ready.notify_all()
                data = self.ser.read(self.args.bufsize)
            except (serial.SerialException, OSError) as exc:
                if not self.close_event.is_set():
                    self.ui.message(f"串口读取失败：{exc}，桥接将退出")
                    self.exit_code = 1
                    self.close_event.set()
                    self.conn.close_current("串口读取失败")
                return

            if not data:
                continue

            sock, generation = self.conn.snapshot()
            if sock is None or read_sock is None or generation != read_generation:
                self.stats.add("com_to_tcp_drop", len(data))
                continue

            self._enqueue(self.q_com_to_tcp, "com_to_tcp", generation, data)

    # --------------------------------------------------------
    # 串口 -> TCP 发送线程：批量合并，并且不跨连接发送旧数据
    # --------------------------------------------------------
    def _tcp_send_worker(self) -> None:
        q = self.q_com_to_tcp
        pending = None

        while not self.close_event.is_set():
            sock, current_generation = self.conn.snapshot()
            if sock is None:
                if pending is not None:
                    if not self.conn.is_current_generation(pending[0]):
                        self.stats.add("com_to_tcp_drop", len(pending[1]))
                        pending = None
                dropped = 0
                while pending is None:
                    try:
                        item = q.get_nowait()
                    except queue.Empty:
                        break
                    if self.conn.is_current_generation(item[0]):
                        pending = item
                    else:
                        dropped += len(item[1])
                self._set_queue_pressure("com_to_tcp", False)
                if dropped:
                    self.stats.add("com_to_tcp_drop", dropped)
                self.close_event.wait(0.03)
                continue

            if pending is not None:
                item = pending
                pending = None
            else:
                try:
                    item = q.get(timeout=0.1)
                except queue.Empty:
                    continue

            if q.qsize() < max(1, QUEUE_BLOCKS // 2):
                self._set_queue_pressure("com_to_tcp", False)

            generation, block = item
            # q.get may span a disconnect and reconnect. Refresh the socket after waiting.
            sock, current_generation = self.conn.snapshot()
            if generation != current_generation or not self.conn.is_current(sock, generation):
                self.stats.add("com_to_tcp_drop", len(block))
                continue

            blocks = [block]
            total = len(block)
            deadline = time.monotonic() + TCP_SEND_BATCH_WINDOW

            while total < TCP_SEND_BATCH_BYTES:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    break
                try:
                    nxt = q.get(timeout=remaining)
                except queue.Empty:
                    break
                nxt_gen, nxt_block = nxt
                if nxt_gen != generation or total + len(nxt_block) > TCP_SEND_BATCH_BYTES:
                    pending = nxt
                    break
                blocks.append(nxt_block)
                total += len(nxt_block)

            if not self.conn.is_current(sock, generation):
                self.stats.add("com_to_tcp_drop", total)
                continue

            payload = b"".join(blocks)
            try:
                sock.sendall(payload)
                self.stats.add("com_to_tcp_bytes", len(payload))
            except OSError as exc:
                confirmed = exc.sent if isinstance(exc, DatagramSendError) else 0
                self.stats.add("com_to_tcp_bytes", confirmed)
                self.stats.add("com_to_tcp_drop", len(payload) - confirmed)
                self.conn.kill(sock, f"TCP 发送失败：{exc}")
                # 接收线程会发现 socket 失效并统一处理重连。

    # --------------------------------------------------------
    # 统计行：由主线程刷新，不每秒新增日志行
    # --------------------------------------------------------


def build_parser(parser_class=argparse.ArgumentParser):
    parser = parser_class(
        prog="tcp2com",
        description="ESP-01S / ESP32-C5 / ESP32-S3 TCP、UDP 与 COM 双向桥接",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument("--config", help="JSON 配置文件；仅显式指定时读取")
    parser.add_argument("--profile", help="JSON profiles 中的配置名称")
    parser.add_argument("--device", choices=tuple(DEVICES), default="esp01s", help="设备能力；C5/S3 禁用旧复位入口")
    parser.add_argument("--mode", choices=("tcp-client", "tcp-server", "udp"), default="tcp-client")
    parser.add_argument("--listen", default="0.0.0.0", help="TCP 服务端/UDP 本地 IPv4 绑定地址")
    parser.add_argument("--peer-port", type=int, default=6060, help="UDP 对端设备本地端口")
    parser.add_argument("--udp-payload", type=int, default=1200, help="本地→UDP 每包最大载荷，1～65507")
    parser.add_argument("--usb-port", help="C5/S3 原生 USB 日志 COM；与业务 COM 不同")
    parser.add_argument("--usb-baud", type=int, default=115200, help="USB 日志串口参数")
    parser.add_argument("--usb-log", help="USB 文本日志文件，追加保存")
    parser.add_argument("--open-config", action="store_true", help="仅打开设备配网页面后退出；需先手动进入配置热点")
    parser.add_argument("--config-url", default="http://192.168.4.1/", help="设备配网页面地址")
    parser.add_argument("--config-transport", choices=("http", "usb"), default="http", help="GUI 的设备配置连接方式")
    parser.add_argument("--host", help="TCP 客户端/UDP 的对端地址；TCP 服务端可选的来源 IP 筛选")
    parser.add_argument("--port", type=int, default=8080, help="TCP 客户端目标端口；TCP 服务端/UDP 本地监听端口")
    parser.add_argument("--control-port", type=int, default=8081, help="ESP 独立复位控制端口")
    parser.add_argument("--no-reset-ui", action="store_true", help="关闭命令行急停入口及复位弹窗功能")
    parser.add_argument("--com", help="本地串口，例如 COM5")
    parser.add_argument("--list-coms", action="store_true", help="列出本机串口")
    parser.add_argument("--baud", type=int, default=1152000, help="串口波特率")
    parser.add_argument("--bytesize", choices=list(_BYTESIZES), default="8", help="数据位")
    parser.add_argument("--parity", choices=list(_PARITIES), default="N", help="校验位")
    parser.add_argument("--stopbits", choices=list(_STOPBITS), default="1", help="停止位")
    parser.add_argument("--flow", choices=["none", "rtscts", "dsrdtr"], default="none", help="串口流控")
    parser.add_argument("--timeout", type=float, default=0.02, help="串口读取超时秒数")
    parser.add_argument("--write-timeout", type=float, default=2.0, help="串口写入超时秒数")
    parser.add_argument("--reconnect", type=float, default=2.0, help="断线后的重连间隔；0 表示不重连")
    parser.add_argument("--keepalive-idle", type=float, default=3.0, help="TCP 空闲多少秒后开始 Keepalive；0 表示关闭")
    parser.add_argument("--keepalive-interval", type=float, default=1.0, help="Keepalive 探测间隔秒数")
    parser.add_argument("--keepalive-count", type=int, default=5, help="Keepalive 探测次数；Windows 通常由系统决定")
    parser.add_argument("--bufsize", type=int, default=65536, help="单次 TCP/串口读取大小")
    parser.add_argument("--stats-interval", type=float, default=0.25, help="底部实时速率刷新间隔秒数")
    parser.add_argument("--connect-timeout", type=float, default=CONNECT_TIMEOUT, help="TCP 连接建立超时秒数")
    parser.add_argument("-v", "--verbose", action="store_true", help="输出调试日志")
    return parser


def parse_args(argv=None, parser_class=argparse.ArgumentParser):
    parser = build_parser(parser_class)
    load_defaults(parser, argv)
    args = parser.parse_args(argv)

    if args.list_coms:
        return args
    if args.open_config:
        if not args.config_url.startswith(("http://", "https://")):
            parser.error("--config-url 必须使用 http:// 或 https://")
        return args
    if args.mode != "tcp-server" and not args.host:
        parser.error("TCP 客户端和 UDP 需要 --host")
    if not args.com or not args.com.strip():
        parser.error("业务桥接必须指定 --com 串口")
    if not 1 <= args.port <= 65535:
        parser.error("--port 必须在 1～65535 之间")
    if not capabilities(args.device).reset or args.mode != "tcp-client":
        args.no_reset_ui = True
    if not 1 <= args.control_port <= 65535 or (not args.no_reset_ui and args.control_port == args.port):
        parser.error("--control-port 必须在 1～65535 之间，且不能与透传端口相同")
    if args.baud <= 0:
        parser.error("--baud 必须大于 0")
    if not 256 <= args.bufsize <= (1 << 20):
        parser.error("--bufsize 范围为 256～1048576")
    if args.reconnect < 0 or args.keepalive_idle < 0 or args.keepalive_interval <= 0 or args.keepalive_count <= 0:
        parser.error("重连 / Keepalive 参数不合法")
    if args.timeout < 0 or args.write_timeout <= 0 or args.connect_timeout <= 0 or args.stats_interval <= 0:
        parser.error("超时参数和速率刷新间隔必须大于 0（串口 timeout 可为 0）")
    for key in ("reconnect", "keepalive_idle", "keepalive_interval", "timeout", "write_timeout", "connect_timeout", "stats_interval"):
        value = getattr(args, key)
        if not 0 <= value <= 86400 or not math.isfinite(value):
            parser.error(f"{key} 必须为有限数值，且不超过 86400 秒")
    if not 1 <= args.peer_port <= 65535 or not 1 <= args.udp_payload <= 65507:
        parser.error("UDP 对端端口范围 1～65535，载荷范围 1～65507")
    if args.usb_baud <= 0 or (args.usb_log and not args.usb_port):
        parser.error("USB 波特率必须大于 0；--usb-log 需要 --usb-port")
    if args.usb_port and not capabilities(args.device).usb:
        parser.error("--usb-port 仅用于 C5/S3 原生 USB 日志")
    if args.usb_port and args.com and args.usb_port.casefold() == args.com.casefold():
        parser.error("USB 日志端口不能与业务 COM 相同")
    try:
        socket.inet_pton(socket.AF_INET, args.listen)
    except OSError:
        parser.error("--listen 必须是本机 IPv4 地址或 0.0.0.0")
    return args


def list_coms():
    ports = sorted(list_ports.comports(), key=lambda port: port.device)
    if not ports:
        print("未发现串口设备")
        return
    print("可用串口：")
    for port in ports:
        print(f"  {port.device:12s} {port.description}")


def main(argv=None):
    args = parse_args(argv)
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, OSError):
        pass

    logging.basicConfig(level=logging.DEBUG if args.verbose else logging.WARNING)
    if args.list_coms:
        list_coms()
        return 0
    if args.open_config:
        print(capabilities(args.device if args.device != "esp01s" else "c5").setup_hint + " 普通业务模式返回 403。")
        return 0 if webbrowser.open(args.config_url) else 1

    ui = ConsoleUI()
    app = App(args, ui)
    if not app.start():
        return 1

    def on_signal(signum, _frame):
        ui.message(f"收到退出信号 {signum}，正在退出")
        app.close_event.set()
        app.conn.close_current("收到退出信号")

    for name in ("SIGINT", "SIGBREAK", "SIGTERM"):
        sig = getattr(signal, name, None)
        if sig is not None:
            try:
                signal.signal(sig, on_signal)
            except (ValueError, OSError):
                pass

    try:
        if args.no_reset_ui:
            app.run()
        else:
            try:
                controller = ConsoleResetController(app)
            except Exception as exc:
                ui.message(f"无法初始化复位弹窗：{exc}；继续运行控制台桥接")
                app.run()
            else:
                stats_thread = threading.Thread(target=app.run, name="stats", daemon=True)
                app.threads.append(stats_thread)
                stats_thread.start()
                controller.run()
    except KeyboardInterrupt:
        # Ctrl+C in terminals that do not deliver the registered signal
        # handler still results in a normal cleanup path.
        app.close_event.set()
    finally:
        app.stop()

    return app.exit_code


if __name__ == "__main__":
    sys.exit(main())
