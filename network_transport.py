"""IPv4 transports shared by the existing binary bridge. No payload framing."""

import queue
import socket
import threading


def close_socket(sock):
    if sock is not None:
        try:
            sock.close()
        except OSError:
            pass


class TCPServer:
    """Accept independently so extra clients are rejected during an active session."""

    def __init__(self, address, notify, expected_host=None):
        self.notify = notify
        self.expected_ip = socket.gethostbyname(expected_host) if expected_host else None
        self.lock = threading.Lock()
        self.active = None
        self.pending = queue.Queue(maxsize=1)
        self.closed = threading.Event()
        self.listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        try:
            # EXCLUSIVEADDRUSE prevents another Windows process from stealing the port.
            if hasattr(socket, "SO_EXCLUSIVEADDRUSE"):
                self.listener.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
            else:
                self.listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            self.listener.bind(address)
            self.listener.listen(4)
            self.listener.settimeout(0.2)
        except OSError:
            self.listener.close()
            raise
        self.address = self.listener.getsockname()
        self.thread = threading.Thread(target=self._accept, name="tcp-accept", daemon=True)
        self.thread.start()

    def _accept(self):
        while not self.closed.is_set():
            try:
                sock, address = self.listener.accept()
            except socket.timeout:
                continue
            except OSError as exc:
                if not self.closed.is_set():
                    self.notify(f"监听失败：{exc}")
                    self.closed.set()
                return
            with self.lock:
                reject = (self.closed.is_set() or self.active is not None or
                          (self.expected_ip and address[0] != self.expected_ip))
                if not reject:
                    self.active = sock
                    self.pending.put_nowait(sock)
            if reject:
                close_socket(sock)
                self.notify(f"拒绝额外连接或非指定设备：{address[0]}:{address[1]}")

    def connect(self, stop):
        while not stop.is_set() and not self.closed.is_set():
            try:
                return self.pending.get(timeout=0.1)
            except queue.Empty:
                pass
        raise OSError("监听已关闭")

    def release(self, sock):
        with self.lock:
            if self.active is sock:
                self.active = None

    def close(self):
        self.closed.set()
        close_socket(self.listener)
        with self.lock:
            close_socket(self.active)
            self.active = None
        self.thread.join(timeout=1)


class DatagramSendError(OSError):
    def __init__(self, cause, sent):
        super().__init__(str(cause))
        self.sent = sent


class DatagramStream:
    """Fixed peer; UDP boundaries become a byte stream at the local COM endpoint."""

    def __init__(self, local, remote, payload_size, stop, record):
        self.peer = (socket.gethostbyname(remote[0]), remote[1])
        self.payload_size = payload_size
        self.stop = stop
        self.record = record
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            self.sock.bind(local)
            self.sock.settimeout(0.2)
        except OSError:
            self.sock.close()
            raise
        self.address = self.sock.getsockname()

    def recv(self, _size):
        while not self.stop.is_set():
            try:
                # Always read a whole IPv4 datagram, independently of --bufsize.
                data, peer = self.sock.recvfrom(65535)
            except socket.timeout:
                continue
            if peer != self.peer:
                self.record("udp_rejected", 1)
                continue
            self.record("udp_rx_packets", 1)
            if data:
                return data
            self.record("udp_empty", 1)
        raise OSError("UDP 已停止")

    def sendall(self, payload):
        sent = 0
        try:
            while sent < len(payload) and not self.stop.is_set():
                part = payload[sent:sent + self.payload_size]
                size = self.sock.sendto(part, self.peer)
                if size != len(part):
                    raise OSError("UDP 非完整报文发送")
                sent += size
                self.record("udp_tx_packets", 1)
            if sent != len(payload):
                raise OSError("UDP 发送已停止")
        except OSError as exc:
            raise DatagramSendError(exc, sent) from exc

    def getpeername(self):
        return self.peer

    def shutdown(self, _how):
        self.close()

    def close(self):
        self.sock.close()
