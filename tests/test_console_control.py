"""Console hit testing, input records and a real hidden Windows console probe."""

import contextlib
import ctypes
from ctypes import wintypes
import io
import os
import pathlib
import subprocess
import sys
import tempfile
import traceback
import unittest
from unittest import mock

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
from console_control import Coord, InputRecord, WindowsConsoleControl, clip_cells, wrap_cells
import tcp2com


class FakeConsole:
    def __init__(self):
        self.events = []
        self.row = 0
        self.bounds = None
        self.closed = False
        self.renders = []

    def clear_line(self):
        self.bounds = None

    def render(self, rate):
        self.renders.append(rate)
        self.bounds = (0, 24, self.row)

    def hit_test(self, x, y):
        return self.bounds is not None and y == self.bounds[2] and 0 <= x < self.bounds[1]

    def read_events(self):
        events, self.events = self.events, []
        return events

    def close(self):
        self.closed = True


class ConsoleTests(unittest.TestCase):
    def test_clip_chinese_to_columns_without_wrapping(self):
        self.assertEqual(clip_cells("A中文B", 4), "A中")
        self.assertEqual(clip_cells("A中文B", 5), "A中文")
        self.assertEqual(clip_cells("A\u0301B", 1), "A\u0301")
        self.assertEqual(clip_cells("中文", 1), "")
        self.assertEqual(clip_cells("→X", 2), "→")
        self.assertEqual("".join(wrap_cells("串口→TCP 1234 丢弃0", 8)), "串口→TCP 1234 丢弃0")

    def test_click_tracks_logs_resize_and_only_entry_opens_panel(self):
        output = io.StringIO()
        output.isatty = lambda: True
        with contextlib.redirect_stdout(output), \
                mock.patch.object(tcp2com, "enable_windows_vt", return_value=False), \
                mock.patch.object(tcp2com, "WindowsConsoleControl", FakeConsole):
            ui = tcp2com.ConsoleUI()
            ui.enable_reset_entry()
            control = ui.reset_entry
            control.events = [("click", 1, 0)]
            self.assertTrue(ui.poll_reset_entry())
            control.row = 8
            ui.message("TCP 已重连")
            control.events = [("click", 1, 0), ("click", 24, 8)]
            self.assertFalse(ui.poll_reset_entry())
            control.events = [("click", 23, 8)]
            self.assertTrue(ui.poll_reset_entry())
            control.row = 10
            control.events = [("resize",), ("click", 1, 10)]
            self.assertTrue(ui.poll_reset_entry())
            control.events = [("open",)]
            self.assertTrue(ui.poll_reset_entry())
            ui.disable_reset_entry()
            self.assertTrue(control.closed)
            self.assertFalse(ui.poll_reset_entry())

    def test_redirection_does_not_enable_mouse_or_create_an_entry(self):
        with contextlib.redirect_stdout(io.StringIO()), \
                mock.patch.object(tcp2com, "WindowsConsoleControl") as factory:
            ui = tcp2com.ConsoleUI()
            ui.enable_reset_entry()
            self.assertIsNone(ui.reset_entry)
            factory.assert_not_called()

    def test_console_failure_restores_mode_and_keeps_logging(self):
        output = io.StringIO()
        output.isatty = lambda: True
        with contextlib.redirect_stdout(output), \
                mock.patch.object(tcp2com, "enable_windows_vt", return_value=False), \
                mock.patch.object(tcp2com, "WindowsConsoleControl", FakeConsole):
            ui = tcp2com.ConsoleUI()
            ui.enable_reset_entry()
            control = ui.reset_entry
            with mock.patch.object(control, "read_events", side_effect=OSError("unavailable")):
                self.assertFalse(ui.poll_reset_entry())
            self.assertTrue(control.closed)
            self.assertIsNone(ui.reset_entry)
            ui.message("桥接仍运行")
            self.assertIn("桥接仍运行", output.getvalue())

    @unittest.skipUnless(os.name == "nt", "Windows console APIs required")
    def test_native_console_in_hidden_process(self):
        # A separate hidden console avoids changing this terminal's input mode.
        with tempfile.TemporaryDirectory() as directory:
            report = pathlib.Path(directory) / "probe.txt"
            startup = subprocess.STARTUPINFO()
            startup.dwFlags |= subprocess.STARTF_USESHOWWINDOW
            startup.wShowWindow = 0
            result = subprocess.run(
                [sys.executable, "-B", str(pathlib.Path(__file__).resolve()),
                 "--console-probe", str(report)],
                creationflags=subprocess.CREATE_NEW_CONSOLE, startupinfo=startup,
                timeout=10,
            )
            detail = report.read_text(encoding="utf-8") if report.exists() else "No probe report"
            self.assertEqual(result.returncode, 0, detail)
            self.assertEqual(detail, "OK")


def native_probe():
    import signal
    import time

    control = WindowsConsoleControl()
    api = control.api
    api.WriteConsoleInputW.argtypes = [wintypes.HANDLE, ctypes.POINTER(InputRecord),
                                      wintypes.DWORD, ctypes.POINTER(wintypes.DWORD)]
    api.WriteConsoleInputW.restype = wintypes.BOOL
    api.SetConsoleScreenBufferSize.argtypes = [wintypes.HANDLE, Coord]
    api.SetConsoleScreenBufferSize.restype = wintypes.BOOL
    api.GenerateConsoleCtrlEvent.argtypes = [wintypes.DWORD, wintypes.DWORD]
    api.GenerateConsoleCtrlEvent.restype = wintypes.BOOL
    api.ReadConsoleOutputCharacterW.argtypes = [wintypes.HANDLE, wintypes.LPWSTR,
                                               wintypes.DWORD, Coord,
                                               ctypes.POINTER(wintypes.DWORD)]
    api.ReadConsoleOutputCharacterW.restype = wintypes.BOOL
    original = control.original_mode
    try:
        assert ctypes.sizeof(InputRecord) == 20
        mode = wintypes.DWORD()
        control._check(api.GetConsoleMode(control.input, ctypes.byref(mode)))
        assert mode.value & 1  # Ctrl+C remains processed by the console.
        assert mode.value & 0x10
        assert not mode.value & 0x40
        before = control.screen_info()
        control.render("串口→TCP " + "1234567890" * 40 + " ENDMARK")
        after = control.screen_info()
        assert after.cursor.Y == before.cursor.Y, (
            f"Status line wrapped: before=({before.cursor.X},{before.cursor.Y}), "
            f"after=({after.cursor.X},{after.cursor.Y}), size={before.size.X}, "
            f"window=({before.window.Left},{before.window.Right}), bounds={control.bounds}"
        )
        assert control.hit_test(0, after.cursor.Y)
        assert control.hit_test(control.bounds[1] - 1, after.cursor.Y)
        assert not control.hit_test(control.bounds[1], after.cursor.Y)
        assert not control.hit_test(0, after.cursor.Y - 1)
        contents = ctypes.create_unicode_buffer(after.size.X * control.region[1] + 1)
        read = wintypes.DWORD()
        control._check(api.ReadConsoleOutputCharacterW(
            control.output, contents, len(contents) - 1,
            Coord(0, control.region[0]), ctypes.byref(read)
        ))
        assert "ENDMARK" in contents.value, "Trailing status fields were clipped"

        def send(records):
            batch = (InputRecord * len(records))(*records)
            written = wintypes.DWORD()
            control._check(api.WriteConsoleInputW(
                control.input, batch, len(batch), ctypes.byref(written)
            ))
            events = []
            for _ in range(8):
                events.extend(control.read_events())
            return events

        down, move, up = InputRecord(), InputRecord(), InputRecord()
        for record in (down, move, up):
            record.kind = 2
            record.event.mouse.position = Coord(1, after.cursor.Y)
        down.event.mouse.buttons = 1
        move.event.mouse.buttons = 1
        move.event.mouse.flags = 1
        events = send([down, move, up])
        assert [e for e in events if e[0] == "click"] == [("click", 1, after.cursor.Y)]
        key = InputRecord()
        key.kind = 1
        key.event.key.down = 1
        key.event.key.key = ord("R")
        assert ("open",) in send([key])

        # Verify hit testing follows output scrolling and a real buffer resize.
        control.clear_line()
        control._write("\r\n重连日志\r\n")
        control.render("透传已重连")
        assert not control.hit_test(1, after.cursor.Y)
        size = control.screen_info().size
        control._check(api.SetConsoleScreenBufferSize(control.output, Coord(size.X + 10, size.Y)))
        resized = []
        for _ in range(8):
            resized.extend(control.read_events())
        assert ("resize",) in resized
        assert control.bounds is None
        control.render("窗口已缩放")
        assert control.hit_test(1, control.screen_info().cursor.Y)

        # At the end of the screen buffer, reserving multiple status rows
        # scrolls the buffer; the button must use its new absolute row.
        control.clear_line()
        bottom = control.screen_info().size.Y - 1
        control._check(api.SetConsoleCursorPosition(control.output, Coord(0, bottom)))
        control.render("串口→TCP " + "1234567890" * 20)
        assert control.region[0] + control.region[1] - 1 == bottom
        assert control.hit_test(1, control.region[0])
        assert not control.hit_test(1, bottom)

        interrupted = []
        signal.signal(signal.SIGINT, lambda *_: interrupted.append(True))
        control._check(api.GenerateConsoleCtrlEvent(0, 0))
        deadline = time.monotonic() + 1
        while not interrupted and time.monotonic() < deadline:
            time.sleep(.01)
        assert interrupted, "Ctrl+C handler did not run"
    finally:
        control.close()
    mode = wintypes.DWORD()
    control._check(api.GetConsoleMode(control.input, ctypes.byref(mode)))
    assert mode.value == original, "Console input mode was not restored"


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "--console-probe":
        try:
            native_probe()
        except BaseException:
            pathlib.Path(sys.argv[2]).write_text(traceback.format_exc(), encoding="utf-8")
            raise SystemExit(1)
        pathlib.Path(sys.argv[2]).write_text("OK", encoding="utf-8")
    else:
        unittest.main()
