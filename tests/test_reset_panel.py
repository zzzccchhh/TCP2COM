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
from types import SimpleNamespace

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
import tcp2com
from reset_control import ConsoleResetController, ResetOutcomeUnknown, ResetPanel


class PanelLayoutTests(unittest.TestCase):
    def test_scaled_fonts_and_multiline_status_are_not_clipped(self):
        try:
            import tkinter as tk
        except ImportError:
            self.skipTest("Tk unavailable")

        for scaling in (1.333, 2.0):
            with self.subTest(scaling=scaling):
                try:
                    root = tk.Tk()
                except tk.TclError:
                    self.skipTest("Tk display unavailable")
                root.withdraw()
                root.tk.call("tk", "scaling", scaling)
                app = SimpleNamespace(
                    args=SimpleNamespace(host="192.168.137.200", control_port=8081,
                                         com="COM1", baud=1152000),
                    close_event=threading.Event(),
                    conn=SimpleNamespace(snapshot=lambda: (None, 0),
                                         close_current=lambda reason: None),
                )
                panel = None
                try:
                    panel = ResetPanel(app, root)
                    # 在屏幕外进行真实布局，避免弹出可见测试窗口。
                    panel.root.geometry("+30000+30000")
                    panel.show()
                    for message in (
                        "等待操作（0/5）",
                        "复位结果未知：等待控制响应超时；不会自动重发，请检查设备状态",
                        "复位完成：GPIO0 已释放，恢复正常状态灯",
                    ):
                        panel._set_status(message)
                        root.update()
                        label = panel.root.winfo_children()[-1]
                        self.assertTrue(label.winfo_ismapped())
                        self.assertGreaterEqual(label.winfo_height(), label.winfo_reqheight())
                        self.assertLessEqual(label.winfo_y() + label.winfo_height(),
                                             panel.root.winfo_height())
                        self.assertGreaterEqual(panel.root.winfo_height(),
                                                panel.root.winfo_reqheight())
                finally:
                    if panel is not None:
                        panel.shutdown()
                    root.destroy()


class WindowLifecycleTests(unittest.TestCase):
    def setUp(self):
        try:
            import tkinter as tk
            self.root = tk.Tk()
        except ImportError:
            self.skipTest("Tk unavailable")
        except tk.TclError:
            self.skipTest("Tk display unavailable")
        self.root.withdraw()
        self.app = SimpleNamespace(
            args=SimpleNamespace(host="127.0.0.1", port=8080, control_port=8081,
                                 com="COM1", baud=1152000),
            close_event=threading.Event(),
            conn=mock.Mock(snapshot=lambda: (None, 0)),
            ui=mock.Mock(poll_reset_entry=mock.Mock(return_value=False)),
        )
        with mock.patch.object(tk, "Tk", return_value=self.root):
            self.window = ConsoleResetController(self.app)
        self.root.withdraw()
        self.root.geometry("+30000+30000")

    def tearDown(self):
        self.window.close()
        if self.window.panel is not None and self.window.panel.worker is not None:
            self.window.panel.worker.join(1)

    def open_panel(self):
        self.window.open_reset_panel()
        self.window.panel.root.geometry("+30000+30000")
        return self.window.panel

    def test_panel_is_lazy_reused_and_close_clears_partial_clicks(self):
        self.root.update()
        self.assertIsNone(self.window.panel)
        self.assertEqual(self.root.state(), "withdrawn")
        self.assertEqual(self.root.winfo_children(), [])
        self.assertFalse(any(child.winfo_class() == "Toplevel"
                             for child in self.root.winfo_children()))
        panel = self.open_panel()
        for _ in range(4):
            panel.button.invoke()
        self.assertEqual(panel.gate.count, 4)
        # Exercise the same callback as the title-bar close button.
        panel.root.tk.call(panel.root.protocol("WM_DELETE_WINDOW"))
        self.assertEqual(panel.root.state(), "withdrawn")
        self.assertEqual(panel.gate.count, 0)
        self.assertFalse(self.app.close_event.is_set())
        self.app.conn.close_current.assert_not_called()
        self.assertIs(self.open_panel(), panel)
        self.assertIs(self.open_panel(), panel)
        panel.button.invoke()
        self.assertEqual(panel.gate.count, 1)
        self.assertIsNone(panel.worker)
        self.assertEqual(sum(child.winfo_class() == "Toplevel"
                             for child in self.root.winfo_children()), 1)

    def test_hidden_reset_keeps_waiting_and_preserves_unknown_result(self):
        started = threading.Event()
        release = threading.Event()

        def reset(_host, _port, progress, cancel):
            progress("ESP 已开始复位")
            started.set()
            if not release.wait(2):
                raise AssertionError("Test did not release reset worker")
            self.assertFalse(cancel.is_set())
            raise ResetOutcomeUnknown("复位结果未知：控制连接已断开")

        panel = self.open_panel()
        try:
            with mock.patch("reset_control.perform_reset", side_effect=reset) as request:
                for _ in range(5):
                    panel.button.invoke()
                self.assertTrue(started.wait(1))
                panel.close()
                self.assertFalse(panel.cancel.is_set())
                self.assertTrue(panel.gate.busy)
                self.assertIs(self.open_panel(), panel)
                panel.button.invoke()
                self.assertEqual(request.call_count, 1)
                panel.close()
                release.set()
                panel.worker.join(1)
                self.assertFalse(panel.worker.is_alive())
                time.sleep(.06)
                self.root.update()
                self.assertFalse(panel.gate.busy)
                self.assertIn("结果未知", panel.status.get())
                self.assertEqual(panel.root.state(), "withdrawn")
                self.assertIs(self.open_panel(), panel)
                self.assertIn("结果未知", panel.status.get())
                self.assertEqual(request.call_count, 1)
                self.assertFalse(self.app.close_event.is_set())
                self.app.conn.close_current.assert_not_called()
        finally:
            release.set()

    def test_console_exit_cleans_up_hidden_panel_and_input(self):
        panel = self.open_panel()
        panel.close()
        self.app.close_event.set()  # Ctrl+C / bridge failure uses the same flag.
        self.window.run()
        self.assertTrue(self.window.closed)
        self.assertTrue(panel.closed)
        self.assertTrue(panel.cancel.is_set())
        self.assertIsNone(self.window.poll_job)
        self.assertIsNone(panel.poll_job)
        self.app.ui.disable_reset_entry.assert_called_once()
        self.app.conn.close_current.assert_not_called()


class PanelIntegrationTests(unittest.TestCase):
    def test_console_click_bridge_echo_reconnect_hidden_reset_and_shutdown(self):
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
        continue_after_close = threading.Event()
        closed_echo_received = threading.Event()
        reconnected = threading.Event()
        continue_after_reset = threading.Event()
        final_echo_received = threading.Event()
        reset_started = threading.Event()
        finish_reset = threading.Event()
        failures = []
        requests = []
        windows = []
        servers = []
        workers = []

        def listen(handler, connections=1):
            listener = socket.socket()
            listener.bind(("127.0.0.1", 0))
            listener.listen(1)
            listener.settimeout(8)
            servers.append(listener)

            def serve():
                try:
                    with listener:
                        for index in range(connections):
                            conn, _ = listener.accept()
                            with conn:
                                conn.settimeout(8)
                                handler(conn, index)
                except BaseException as exc:
                    failures.append(exc)
            worker = threading.Thread(target=serve, daemon=True)
            workers.append(worker)
            worker.start()
            return listener.getsockname()[1]

        def echo(conn):
            conn.sendall(payload)
            echoed = bytearray()
            while len(echoed) < len(payload):
                block = conn.recv(len(payload) - len(echoed))
                if not block:
                    break
                echoed.extend(block)
            self.assertEqual(bytes(echoed), payload)

        def bridge(conn, index):
            echo(conn)
            if index == 0:
                echo_received.set()
                self.assertTrue(continue_after_close.wait(6))
                echo(conn)
                closed_echo_received.set()
                # Force a reconnect after proving that closing the panel kept
                # the original payload socket alive.
            else:
                reconnected.set()
                self.assertTrue(continue_after_reset.wait(6))
                echo(conn)
                final_echo_received.set()
                self.assertEqual(conn.recv(1), b"")

        def control(conn, _index):
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
            reset_started.set()
            self.assertTrue(finish_reset.wait(6))
            conn.sendall(f"DONE {request_id}\n".encode())
            self.assertEqual(conn.recv(1), b"")

        bridge_port = listen(bridge, connections=2)
        control_port = listen(control)

        case = self

        from test_console_control import FakeConsole

        class TestController(ConsoleResetController):
            def __init__(self, app):
                super().__init__(app)
                self.root.withdraw()
                windows.append(self)
                self.deadline = time.monotonic() + 6
                self.stage = 0
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
                if echo_received.is_set() and self.stage == 0:
                    case.assertIsNone(self.panel)
                    case.assertEqual(self.root.state(), "withdrawn")
                    case.assertEqual(self.root.winfo_children(), [])
                    self.app.ui.reset_entry.events.append(("click", 1, 0))
                    self.stage = 1
                if self.stage == 1 and self.panel is not None:
                    self.panel.close()
                    case.assertFalse(self.app.close_event.is_set())
                    continue_after_close.set()
                    self.stage = 2
                if closed_echo_received.is_set() and reconnected.is_set() and self.stage == 2:
                    self.app.ui.reset_entry.events.append(("click", 1, 0))
                    self.stage = 3
                if self.stage == 3 and self.panel.root.state() != "withdrawn":
                    panel = self.panel
                    for _ in range(5):
                        panel.button.invoke()
                    case.assertTrue(panel.gate.busy)
                    case.assertEqual(str(panel.button["state"]), "disabled")
                    self.stage = 4
                if reset_started.is_set() and self.stage == 4:
                    panel = self.panel
                    panel.close()
                    case.assertFalse(panel.cancel.is_set())
                    self.open_reset_panel()
                    case.assertTrue(panel.gate.busy)
                    panel.button.invoke()  # Hidden/reopened request must not repeat.
                    panel.close()
                    finish_reset.set()
                    self.stage = 5
                if self.stage == 5 and not self.panel.gate.busy:
                    case.assertIn("复位完成", self.panel.status.get())
                    case.assertEqual(self.panel.root.state(), "withdrawn")
                    self.open_reset_panel()
                    case.assertIn("复位完成", self.panel.status.get())
                    self.panel.close()
                    continue_after_reset.set()
                    self.stage = 6
                if self.stage == 6 and final_echo_received.is_set():
                    self.finished = True
                    self.app.close_event.set()  # Same path as console Ctrl+C.
                    return
                self.root.after(20, self.drive)

        try:
            output = io.StringIO()
            output.isatty = lambda: True
            with mock.patch.object(tcp2com, "ConsoleResetController", TestController), \
                    mock.patch.object(tcp2com, "WindowsConsoleControl", FakeConsole), \
                    mock.patch.object(tcp2com, "enable_windows_vt", return_value=False), \
                    contextlib.redirect_stdout(output):
                result = tcp2com.main([
                    "--host", "127.0.0.1", "--port", str(bridge_port),
                    "--control-port", str(control_port), "--com", "loop://",
                    "--keepalive-idle", "0", "--reconnect", ".1",
                ])
        finally:
            finish_reset.set()
            continue_after_close.set()
            continue_after_reset.set()
            for window in windows:
                window.close()
            for listener in servers:
                listener.close()
            for worker in workers:
                worker.join(9)
        if failures:
            raise failures[0]
        self.assertEqual(result, 0)
        self.assertTrue(windows[0].finished)
        self.assertEqual(len(requests), 1)
        self.assertTrue(echo_received.is_set())
        self.assertTrue(closed_echo_received.is_set())
        self.assertTrue(reconnected.is_set())
        self.assertTrue(final_echo_received.is_set())
        self.assertTrue(windows[0].panel.closed)
        self.assertFalse(windows[0].panel.worker.is_alive())
        self.assertTrue(all(not worker.is_alive() for worker in windows[0].app.threads))


if __name__ == "__main__":
    unittest.main()
