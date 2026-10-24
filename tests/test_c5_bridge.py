import contextlib
import io
import json
from pathlib import Path
import queue
import socket
import tempfile
import threading
import time
import unittest
from unittest import mock

import tcp2com
from network_transport import DatagramStream, DatagramSendError
from usb_diagnostics import USBMonitor, parse_stats


class QuietUI:
    def __init__(self):
        self.messages = []

    def message(self, text):
        self.messages.append(text)

    def state(self, _state, text):
        self.message(text)

    def warning_once(self, _key, text):
        self.message(text)

    def set_rate_line(self, text, _interval):
        self.message(text)


def free_port():
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def wait_for(condition, timeout=3):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if condition():
            return
        threading.Event().wait(0.01)
    raise AssertionError("condition timed out")


def receive(sock, length):
    result = bytearray()
    while len(result) < length:
        block = sock.recv(length - len(result))
        if not block:
            raise AssertionError("unexpected EOF")
        result.extend(block)
    return bytes(result)


class SettingsTests(unittest.TestCase):
    def parse_error(self, arguments):
        with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
            tcp2com.parse_args(arguments)

    def test_legacy_and_c5_capability_defaults(self):
        legacy = tcp2com.parse_args(["--host", "127.0.0.1", "--com", "loop://"])
        self.assertEqual(legacy.mode, "tcp-client")
        self.assertFalse(legacy.no_reset_ui)
        c5 = tcp2com.parse_args(["--device", "c5", "--mode", "tcp-server", "--com", "loop://"])
        self.assertTrue(c5.no_reset_ui)
        self.assertIsNone(c5.host)

    def test_profiles_override_and_relative_capture_path(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "settings.json"
            path.write_text(json.dumps({"profiles": {"test": {
                "device": "c5", "mode": "tcp-server", "port": 8088, "capture": "raw.bin"
            }}}), encoding="utf-8")
            args = tcp2com.parse_args(["--config", str(path), "--profile", "test", "--port", "8099"])
            self.assertEqual(args.port, 8099)
            self.assertEqual(args.capture, str(Path(directory) / "raw.bin"))
            self.assertTrue(args.no_reset_ui)

    def test_shipped_profiles_are_valid(self):
        path = Path(__file__).resolve().parents[1] / "tcp2com_config.json"
        for name in json.loads(path.read_text(encoding="utf-8"))["profiles"]:
            with self.subTest(profile=name):
                args = tcp2com.parse_args(["--config", str(path), "--profile", name])
                self.assertEqual(args.no_reset_ui, args.device in ("c5", "s3"))

    def test_invalid_profiles_and_values_are_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "settings.json"
            for settings in ({"unknown": 3}, {"port": True}, {"mode": "bad"},
                             {"no_reset_ui": "false"}, {"baud": "1152000"}):
                path.write_text(json.dumps({"profiles": {"test": settings}}), encoding="utf-8")
                self.parse_error(["--config", str(path), "--profile", "test"])
            path.write_text('{"profiles":{},"profiles":{}}', encoding="utf-8")
            self.parse_error(["--config", str(path), "--profile", "test"])
        for extra in (["--timeout", "nan"], ["--stats-interval", "inf"], ["--reconnect", "1e300"],
                      ["--udp-payload", "65508"], ["--listen", "example.com"],
                      ["--capture", "raw.bin"], ["--usb-port", "LOOP://"],
                      ["--usb-log", "log.txt"]):
            self.parse_error(["--device", "c5", "--host", "127.0.0.1", "--com", "loop://", *extra])

    def test_web_entry_does_not_require_a_payload_endpoint(self):
        with mock.patch("tcp2com.webbrowser.open", return_value=True) as browser:
            self.assertEqual(tcp2com.main(["--device", "c5", "--open-config"]), 0)
        browser.assert_called_once_with("http://192.168.4.1/")


class BridgeTests(unittest.TestCase):
    def test_new_peer_waits_for_old_read_before_forwarding_first_packet(self):
        args = tcp2com.parse_args(["--device", "c5", "--mode", "tcp-server", "--listen", "127.0.0.1",
                                  "--port", str(free_port()), "--com", "loop://", "--timeout", ".01"])
        device = tcp2com.serial.serial_for_url("loop://", timeout=.01, write_timeout=.5)
        self.addCleanup(device.close)
        entered, release, wrote = threading.Event(), threading.Event(), threading.Event()
        read, write = device.read, device.write
        def held_read(size):
            if not entered.is_set():
                entered.set()
                release.wait(2)
                return b"old-offline-bytes"
            return read(size)
        def observed_write(data):
            wrote.set()
            return write(data)
        device.read, device.write = held_read, observed_write
        app = tcp2com.App(args, QuietUI())
        self.addCleanup(app.stop)
        self.addCleanup(release.set)
        with mock.patch("tcp2com.serial.serial_for_url", return_value=device):
            self.assertTrue(app.start())
        self.assertTrue(entered.wait(1))
        with socket.create_connection(app.server.address, timeout=2) as peer:
            peer.settimeout(2)
            peer.sendall(b"first-new-packet")
            wait_for(lambda: app.conn.snapshot()[0] is not None)
            self.assertFalse(wrote.wait(.05))
            release.set()
            self.assertEqual(receive(peer, len(b"first-new-packet")), b"first-new-packet")
            self.assertEqual(app.stats.snapshot()["com_to_tcp_drop"], len(b"old-offline-bytes"))

    def test_serial_read_cannot_move_bytes_across_connection_boundary(self):
        for initially_connected in (False, True):
            with self.subTest(initially_connected=initially_connected):
                args = tcp2com.parse_args(["--device", "c5", "--mode", "tcp-server", "--com", "loop://"])
                app = tcp2com.App(args, QuietUI())
                if initially_connected:
                    app.conn.set(mock.Mock())
                def read(_):
                    app.conn.set(mock.Mock())
                    app.close_event.set()
                    return b"old-session-payload"
                app.ser = mock.Mock()
                app.ser.read.side_effect = read
                app._serial_read_worker()
                self.assertTrue(app.q_com_to_tcp.empty())
                app.conn.close_current("test complete")

    def app(self, mode="tcp-server", extra=()):
        args = tcp2com.parse_args(["--device", "c5", "--mode", mode,
                                  "--listen", "127.0.0.1", "--port", str(free_port()),
                                  "--timeout", "0.01", *extra])
        app = tcp2com.App(args, QuietUI())
        self.assertTrue(app.start(), app.ui.messages)
        self.addCleanup(app.stop)
        return app

    def test_server_binary_loop_rejects_second_peer_and_accepts_reconnect(self):
        app = self.app(extra=("--com", "loop://"))
        payload = bytes(range(256)) * 16
        with socket.create_connection(app.server.address, timeout=2) as peer:
            peer.settimeout(3)
            peer.sendall(payload)
            self.assertEqual(receive(peer, len(payload)), payload)
            with socket.create_connection(app.server.address, timeout=2) as second:
                second.settimeout(2)
                try:
                    self.assertEqual(second.recv(1), b"")
                except ConnectionResetError:
                    pass
        wait_for(lambda: app.conn.snapshot()[0] is None and app.server.active is None)
        with socket.create_connection(app.server.address, timeout=2) as peer:
            peer.settimeout(3)
            peer.sendall(b"new-session\x00\xff")
            self.assertEqual(receive(peer, 13), b"new-session\x00\xff")
        self.assertEqual(app.stats.snapshot()["com_to_tcp_drop"], 0)

    def test_capture_flushes_short_connection_tail_and_preserves_existing_file(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "capture.bin"
            app = self.app(extra=("--capture", str(path)))
            payload = bytes(range(256)) * 200
            with socket.create_connection(app.server.address, timeout=2) as peer:
                peer.sendall(payload)
            wait_for(lambda: path.stat().st_size == len(payload))
            self.assertEqual(path.read_bytes(), payload)
            app.stop()
            another = tcp2com.App(app.args, QuietUI())
            self.assertFalse(another.start())
            self.assertEqual(path.read_bytes(), payload)

    def test_bind_failure_releases_com_and_exits(self):
        with socket.socket() as listener:
            listener.bind(("127.0.0.1", 0))
            listener.listen(1)
            args = tcp2com.parse_args(["--device", "c5", "--mode", "tcp-server",
                                      "--listen", "127.0.0.1", "--port", str(listener.getsockname()[1]),
                                      "--com", "loop://"])
            app = tcp2com.App(args, QuietUI())
            self.assertFalse(app.start())
            self.assertFalse(app.ser.is_open)

    def test_server_optional_source_filter_rejects_other_ip(self):
        app = self.app(extra=("--com", "loop://", "--host", "192.0.2.1"))
        with socket.create_connection(app.server.address, timeout=2) as peer:
            peer.settimeout(2)
            try:
                self.assertEqual(peer.recv(1), b"")
            except ConnectionResetError:
                pass
        self.assertIsNone(app.conn.snapshot()[0])
        self.assertIsNone(app.server.active)

    def test_queue_backpressure_and_old_generation_cancellation(self):
        args = tcp2com.parse_args(["--host", "localhost", "--com", "loop://"])
        app = tcp2com.App(args, QuietUI())
        first, second = socket.socketpair()
        self.addCleanup(first.close)
        self.addCleanup(second.close)
        generation = app.conn.set(first)
        bounded = queue.Queue(maxsize=1)
        bounded.put((generation, b"already queued"))
        results = []
        worker = threading.Thread(target=lambda: results.append(
            app._enqueue(bounded, "tcp_to_com", generation, b"must not enter new session")))
        worker.start()
        wait_for(lambda: app.pressure_active["tcp_to_com"])
        app.conn.close_current("test disconnect")
        worker.join(1)
        self.assertEqual(results, [False])
        self.assertEqual(bounded.qsize(), 1)
        self.assertEqual(app.stats.snapshot()["tcp_to_com_drop"], 26)

    def test_partial_serial_writes_preserve_binary_order(self):
        args = tcp2com.parse_args(["--host", "localhost", "--com", "loop://"])
        app = tcp2com.App(args, QuietUI())
        first, second = socket.socketpair()
        self.addCleanup(first.close)
        self.addCleanup(second.close)
        generation = app.conn.set(first)
        written = bytearray()
        def partial(block):
            data = bytes(block[:3])
            written.extend(data)
            return len(data)
        app.ser = mock.Mock()
        app.ser.write.side_effect = partial
        payload = bytes(range(256))
        app.q_tcp_to_com.put((generation, payload))
        worker = threading.Thread(target=app._serial_write_worker)
        app.threads.append(worker)
        worker.start()
        try:
            wait_for(lambda: app.stats.snapshot()["tcp_to_com_bytes"] == len(payload))
            self.assertEqual(bytes(written), payload)
        finally:
            app.stop()

    def test_capture_disk_error_counts_only_unwritten_remainder(self):
        args = tcp2com.parse_args(["--device", "c5", "--host", "localhost", "--capture", "unused.bin"])
        app = tcp2com.App(args, QuietUI())
        app.capture = mock.Mock()
        app.capture.write.side_effect = [3, OSError("disk full")]
        with self.assertRaises(tcp2com.CaptureWriteError):
            app._capture_write(b"abcdefgh")
        self.assertEqual(app.stats.snapshot()["tcp_to_com_bytes"], 3)
        self.assertEqual(app.stats.snapshot()["tcp_to_com_drop"], 5)

    def test_udp_com_roundtrip_and_rogue_peer_filter(self):
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as peer, \
                socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as rogue:
            peer.bind(("127.0.0.1", 0))
            peer.settimeout(3)
            app = self.app("udp", ("--host", "127.0.0.1", "--peer-port", str(peer.getsockname()[1]),
                                   "--udp-payload", "257", "--com", "loop://"))
            rogue.sendto(b"must-not-forward", app.udp.address)
            payload = bytes(range(256)) * 4
            peer.sendto(payload, app.udp.address)
            result = bytearray()
            while len(result) < len(payload):
                block, address = peer.recvfrom(65535)
                self.assertEqual(address, app.udp.address)
                self.assertLessEqual(len(block), 257)
                result.extend(block)
            self.assertEqual(bytes(result), payload)
            self.assertEqual(app.stats.snapshot()["udp_rejected"], 1)
            self.assertEqual(app.stats.snapshot()["udp_rx_packets"], 1)
            self.assertEqual(app.stop(), 0)

    def test_missing_usb_keeps_actual_network_roundtrip_running(self):
        original = tcp2com.serial.serial_for_url
        def open_serial(url, *args, **kwargs):
            if url == "missing-usb":
                raise OSError("unplugged")
            return original(url, *args, **kwargs)
        with mock.patch("usb_diagnostics.serial.serial_for_url", side_effect=open_serial):
            app = self.app(extra=("--com", "loop://", "--usb-port", "missing-usb"))
            try:
                wait_for(lambda: any("网络透传继续" in text for text in app.ui.messages))
                with socket.create_connection(app.server.address, timeout=2) as peer:
                    peer.settimeout(3)
                    peer.sendall(b"USB unplugged\x00\xff")
                    self.assertEqual(receive(peer, 15), b"USB unplugged\x00\xff")
            finally:
                app.stop()

    def test_full_udp_datagram_not_truncated_by_small_read_buffer(self):
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as peer:
            peer.bind(("127.0.0.1", 0))
            records = {}
            stream = DatagramStream(("127.0.0.1", 0), peer.getsockname(), 1200,
                                    threading.Event(), lambda key, n: records.update({key: records.get(key, 0) + n}))
            self.addCleanup(stream.close)
            payload = bytes(range(256)) * 255 + b"z" * 227
            self.assertEqual(len(payload), 65507)
            peer.sendto(b"", stream.address)
            peer.sendto(payload, stream.address)
            self.assertEqual(stream.recv(256), payload)
            self.assertEqual(records["udp_empty"], 1)

    def test_udp_partial_send_reports_confirmed_bytes(self):
        stream = DatagramStream(("127.0.0.1", 0), ("127.0.0.1", 6060), 3,
                                threading.Event(), lambda *_: None)
        stream.close()
        stream.sock = mock.Mock()
        stream.sock.sendto.side_effect = [3, OSError("network gone")]
        with self.assertRaises(DatagramSendError) as failure:
            stream.sendall(b"abcdefgh")
        self.assertEqual(failure.exception.sent, 3)


class USBTests(unittest.TestCase):
    def test_multiline_stats_preserve_session_directions_and_drop_counts(self):
        latest = {}
        for line in (
            "I stats: IP=192.168.4.1 ch=6 RSSI=-42 session=7 local->net=123 B/s net->local=456 B/s",
            "I stats: queues local->net=1/4194304 peak=100 net->local=2/1048576 peak=200",
            "I stats: drops offline=0 stage=1 full=2 session=99 uart_errors=3",
        ):
            latest.update(parse_stats(line))
        self.assertEqual(latest["session"], "7")
        self.assertEqual(latest["drops.session"], "99")
        self.assertEqual(latest["queue.local_to_net.peak"], "100")
        self.assertEqual(latest["queue.net_to_local.peak"], "200")
        self.assertEqual(latest["rate.net_to_local"], "456")
        self.assertNotIn("peak", latest)
        self.assertEqual(parse_stats("I stats: future unknown=x"), {})

    def test_stats_and_unknown_lines(self):
        self.assertEqual(parse_stats("I (1) stats: IP=192.168.4.1 ch=36 RSSI=-42 session=2")["RSSI"], "-42")
        self.assertEqual(parse_stats("I startup: ready"), {})
        self.assertEqual(parse_stats("I stats: link=ZF-SPI SPI rx/tx=123/456 protocol_errors=0")["protocol_errors"], "0")

    def test_missing_usb_does_not_stop_payload_bridge(self):
        ui = QuietUI()
        with mock.patch("usb_diagnostics.serial.serial_for_url", side_effect=OSError("unplugged")):
            monitor = USBMonitor("missing", 115200, ui.message)
            monitor.start()
            wait_for(lambda: any("网络透传继续" in text for text in ui.messages))
            monitor.close()
        self.assertFalse(monitor.thread.is_alive())

    def test_usb_reader_does_not_assert_control_lines_and_records_text(self):
        class Device:
            def __init__(self):
                self.sent = False
            def open(self):
                if self.dtr or self.rts:
                    raise AssertionError("control lines asserted")
            def read(self, _size):
                if not self.sent:
                    self.sent = True
                    return b"I (1) stats: IP=192.168.4.1 ch=6 RSSI=-42 session=1\n"
                threading.Event().wait(0.01)
                return b""
            def __enter__(self):
                return self
            def __exit__(self, *_):
                pass
        with tempfile.TemporaryDirectory() as directory:
            logfile = Path(directory) / "usb.log"
            with mock.patch("usb_diagnostics.serial.serial_for_url", return_value=Device()):
                monitor = USBMonitor("COM99", 115200, lambda _: None, str(logfile))
                monitor.start()
                try:
                    wait_for(lambda: monitor.snapshot().get("RSSI") == "-42")
                finally:
                    monitor.close()
            self.assertIn("RSSI=-42", logfile.read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
