import pathlib
import socket
import sys
import threading
import unittest
from unittest import mock

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
from reset_control import FiveClickGate, ResetError, ResetOutcomeUnknown, perform_reset


class ClickTests(unittest.TestCase):
    def test_fifth_click_and_busy(self):
        gate = FiveClickGate()
        self.assertEqual([gate.click(t) for t in [0, .1, .2, .3, .4]],
                         [False, False, False, False, True])
        self.assertFalse(gate.click(.5))
        gate.finish()
        self.assertFalse(gate.click(.6))
        self.assertEqual(gate.count, 1)

    def test_window_is_from_first_click_and_boundary_expires(self):
        gate = FiveClickGate()
        for t in [0, 1, 2, 2.9]:
            self.assertFalse(gate.click(t))
        self.assertFalse(gate.click(3))
        self.assertEqual(gate.count, 1)
        gate.expire(6)
        self.assertEqual(gate.count, 0)


class ProtocolTests(unittest.TestCase):
    def exchange(self, handler, timeout=1, cancel=None, device="esp01s"):
        received = []
        errors = []
        listener = socket.socket()
        listener.bind(("127.0.0.1", 0))
        listener.listen(1)
        port = listener.getsockname()[1]

        def server():
            try:
                with listener:
                    conn, _ = listener.accept()
                    with conn:
                        conn.settimeout(2)
                        handler(conn, received)
            except BaseException as exc:
                errors.append(exc)

        worker = threading.Thread(target=server, daemon=True)
        worker.start()
        progress = []
        try:
            perform_reset("127.0.0.1", port, progress.append,
                          cancel or threading.Event(), timeout, device=device)
        finally:
            worker.join(3)
            self.assertFalse(worker.is_alive())
            if errors:
                raise errors[0]
        return received, progress

    @staticmethod
    def request(conn, received):
        conn.sendall(b"ESP01S-RESET/1 ")
        conn.sendall(b"READY\n")
        line = bytearray()
        while not line.endswith(b"\n"):
            block = conn.recv(64)
            if not block:
                raise EOFError("控制客户端在发送完整命令前断开")
            line.extend(block)
        received.append(bytes(line))
        return line.decode().strip().split()[1]

    def test_request_reader_stops_on_eof(self):
        for prefix in (b"", b"RESET partial"):
            with self.subTest(prefix=prefix):
                conn = mock.Mock()
                conn.recv.side_effect = [prefix, b""] if prefix else [b""]
                received = []
                with self.assertRaises(EOFError):
                    self.request(conn, received)
                self.assertEqual(received, [])
                self.assertEqual(conn.recv.call_count, 2 if prefix else 1)

    def test_fragmented_greeting_coalesced_responses(self):
        def handler(conn, received):
            request_id = self.request(conn, received)
            conn.sendall(f"STARTED {request_id}\nDONE {request_id}\n".encode())
        received, progress = self.exchange(handler)
        self.assertEqual(len(received), 1)
        self.assertRegex(received[0].decode(), r"^RESET [0-9a-f]{32}\n$")
        self.assertEqual(len(progress), 1)

    def test_s3_handshake_and_single_request(self):
        def handler(conn, received):
            conn.sendall(b"S3-RESET/1 READY\n")
            line = conn.recv(80)
            received.append(line)
            request_id = line.decode().strip().split()[1]
            conn.sendall(f"STARTED {request_id}\nDONE {request_id}\n".encode())
        received, progress = self.exchange(handler, device="s3")
        self.assertEqual(len(received), 1)
        self.assertEqual(len(progress), 1)

    def test_duplicate_already_complete(self):
        def handler(conn, received):
            request_id = self.request(conn, received)
            conn.sendall(f"DONE {request_id}\n".encode())
        _, progress = self.exchange(handler)
        self.assertEqual(progress, [])

    def test_wrong_greeting_sends_no_command(self):
        def handler(conn, received):
            conn.sendall(b"OTHER_PROTOCOL\n")
            self.assertEqual(conn.recv(64), b"")
        with self.assertRaises(ResetError) as caught:
            self.exchange(handler)
        self.assertNotIsInstance(caught.exception, ResetOutcomeUnknown)

    def test_disconnect_after_request_is_unknown_without_retry(self):
        def handler(conn, received):
            self.request(conn, received)
            # No response: closing the connection must not cause a second RESET.
        with self.assertRaises(ResetOutcomeUnknown):
            self.exchange(handler)

    def test_wrong_response_id_is_unknown(self):
        def handler(conn, received):
            self.request(conn, received)
            conn.sendall(b"DONE 00000000000000000000000000000000\n")
        with self.assertRaises(ResetOutcomeUnknown):
            self.exchange(handler)

    def test_busy_is_confirmed_not_executed(self):
        def handler(conn, received):
            request_id = self.request(conn, received)
            conn.sendall(f"BUSY {request_id}\n".encode())
        with self.assertRaises(ResetError) as caught:
            self.exchange(handler)
        self.assertNotIsInstance(caught.exception, ResetOutcomeUnknown)

    def test_cancel_before_command(self):
        cancel = threading.Event()
        cancel.set()

        def handler(conn, received):
            self.assertEqual(conn.recv(64), b"")
        with self.assertRaises(ResetError) as caught:
            self.exchange(handler, cancel=cancel)
        self.assertNotIsInstance(caught.exception, ResetOutcomeUnknown)

    def test_timeout_after_started_is_unknown(self):
        def handler(conn, received):
            request_id = self.request(conn, received)
            conn.sendall(f"STARTED {request_id}\n".encode())
            self.assertEqual(conn.recv(64), b"")
        with self.assertRaises(ResetOutcomeUnknown):
            self.exchange(handler, timeout=.15)


if __name__ == "__main__":
    unittest.main()
