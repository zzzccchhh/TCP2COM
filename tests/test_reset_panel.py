"""Run the real bridge and hidden Tk panel against local simulated ESP sockets."""
import contextlib
import io
import pathlib
import socket
import sys
import threading
import time
import unittest
from unittest import mock

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
import tcp2com
from reset_control import ResetPanel


class PanelIntegrationTests(unittest.TestCase):
    def test_bridge_binary_echo_button_and_shutdown(self):
        try:
            import tkinter as tk
            root = tk.Tk()
            root.withdraw()
            root.destroy()
        except ImportError:
            self.skipTest("Tk unavailable")
        except tk.TclError:
            self.skipTest("Tk display unavailable")

        payload = bytes(range(256)) * 16
        echo_received = threading.Event()
        failures = []
        requests = []
        panels = []
        servers = []
        workers = []

        def listen(handler):
            listener = socket.socket()
            listener.bind(("127.0.0.1", 0))
            listener.listen(1)
            listener.settimeout(8)
            servers.append(listener)

            def serve():
                try:
                    with listener:
                        conn, _ = listener.accept()
                        with conn:
                            conn.settimeout(8)
                            handler(conn)
                except BaseException as exc:
                    failures.append(exc)
            worker = threading.Thread(target=serve, daemon=True)
            workers.append(worker)
            worker.start()
            return listener.getsockname()[1]

        def bridge(conn):
            conn.sendall(payload)
            echoed = bytearray()
            while len(echoed) < len(payload):
                block = conn.recv(len(payload) - len(echoed))
                if not block:
                    break
                echoed.extend(block)
            self.assertEqual(bytes(echoed), payload)
            echo_received.set()
            self.assertEqual(conn.recv(1), b"")

        def control(conn):
            conn.sendall(b"ESP01S-RESET/1 READY\n")
            request = bytearray()
            while not request.endswith(b"\n"):
                block = conn.recv(64)
                if not block:
                    break
                request.extend(block)
            requests.append(bytes(request))
            request_id = request.decode().strip().split()[1]
            conn.sendall(f"STARTED {request_id}\n".encode())
            time.sleep(.1)
            conn.sendall(f"DONE {request_id}\n".encode())
            self.assertEqual(conn.recv(1), b"")

        bridge_port = listen(bridge)
        control_port = listen(control)

        class TestPanel(ResetPanel):
            def __init__(self, app):
                super().__init__(app)
                self.root.withdraw()
                panels.append(self)
                self.deadline = time.monotonic() + 6
                self.clicked = False
                self.finished = False
                self.root.report_callback_exception = self.callback_failed
                self.root.after(20, self.drive)

            def callback_failed(self, _type, value, _traceback):
                failures.append(value)
                self.close()

            def drive(self):
                if time.monotonic() >= self.deadline:
                    failures.append(AssertionError("GUI completion timed out"))
                    self.close()
                    return
                if echo_received.is_set() and not self.clicked:
                    for _ in range(5):
                        self.button.invoke()
                    self.clicked = True
                    assert self.gate.busy
                    assert str(self.button["state"]) == "disabled"
                    self.button.invoke()  # Disabled button must not send a second request.
                if self.clicked and not self.gate.busy:
                    self.finished = True
                    assert "复位完成" in self.status.get()
                    self.close()
                    return
                self.root.after(20, self.drive)

        try:
            with mock.patch.object(tcp2com, "ResetPanel", TestPanel), \
                    contextlib.redirect_stdout(io.StringIO()):
                result = tcp2com.main([
                    "--host", "127.0.0.1", "--port", str(bridge_port),
                    "--control-port", str(control_port), "--com", "loop://",
                    "--keepalive-idle", "0",
                ])
        finally:
            for panel in panels:
                panel.close()
            for listener in servers:
                listener.close()
            for worker in workers:
                worker.join(9)
        if failures:
            raise failures[0]
        self.assertEqual(result, 0)
        self.assertTrue(panels[0].finished)
        self.assertEqual(len(requests), 1)
        self.assertTrue(echo_received.is_set())
        self.assertTrue(all(not worker.is_alive() for worker in panels[0].app.threads))


if __name__ == "__main__":
    unittest.main()
