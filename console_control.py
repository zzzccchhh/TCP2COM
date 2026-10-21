"""Windows 命令行中的复位入口；所有调用由 ConsoleUI 的锁串行化。"""

import ctypes
from ctypes import wintypes
import os
import unicodedata


class Coord(ctypes.Structure):
    _fields_ = [("X", wintypes.SHORT), ("Y", wintypes.SHORT)]


class SmallRect(ctypes.Structure):
    _fields_ = [(name, wintypes.SHORT) for name in ("Left", "Top", "Right", "Bottom")]


class ScreenInfo(ctypes.Structure):
    _fields_ = [("size", Coord), ("cursor", Coord), ("attributes", wintypes.WORD),
                ("window", SmallRect), ("maximum", Coord)]


class KeyChar(ctypes.Union):
    _fields_ = [("UnicodeChar", wintypes.WCHAR), ("AsciiChar", ctypes.c_char)]


class KeyEvent(ctypes.Structure):
    _fields_ = [("down", wintypes.BOOL), ("repeat", wintypes.WORD),
                ("key", wintypes.WORD), ("scan", wintypes.WORD),
                ("char", KeyChar), ("control", wintypes.DWORD)]


class MouseEvent(ctypes.Structure):
    _fields_ = [("position", Coord), ("buttons", wintypes.DWORD),
                ("control", wintypes.DWORD), ("flags", wintypes.DWORD)]


class InputEvent(ctypes.Union):
    _fields_ = [("mouse", MouseEvent), ("key", KeyEvent), ("size", Coord)]


class InputRecord(ctypes.Structure):
    _fields_ = [("kind", wintypes.WORD), ("event", InputEvent)]


def clip_cells(text, width):
    """按终端列宽截断，避免中文被拆开或输出触及右边缘自动换行。"""
    result = []
    used = 0
    for char in text:
        size = 0 if unicodedata.combining(char) else (
            # 中文控制台可能把箭头等歧义宽度字符按两列显示，保守预留两列。
            2 if unicodedata.east_asian_width(char) in ("W", "F", "A") else 1
        )
        if used + size > width:
            break
        result.append(char)
        used += size
    return "".join(result)


def wrap_cells(text, width):
    lines = []
    while text:
        line = clip_cells(text, width)
        if not line:
            line, text = "?", text[1:]
        else:
            text = text[len(line):]
        lines.append(line)
    return lines


class WindowsConsoleControl:
    ENTRY = "[ 打开急停 / 复位窗口 ]"

    def __init__(self):
        if os.name != "nt":
            raise OSError("鼠标入口需要 Windows 控制台")
        self.api = ctypes.WinDLL("kernel32", use_last_error=True)
        signatures = {
            "GetStdHandle": ([wintypes.DWORD], wintypes.HANDLE),
            "GetConsoleMode": ([wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD)], wintypes.BOOL),
            "SetConsoleMode": ([wintypes.HANDLE, wintypes.DWORD], wintypes.BOOL),
            "GetConsoleScreenBufferInfo": ([wintypes.HANDLE, ctypes.POINTER(ScreenInfo)], wintypes.BOOL),
            "SetConsoleCursorPosition": ([wintypes.HANDLE, Coord], wintypes.BOOL),
            "FillConsoleOutputCharacterW": ([wintypes.HANDLE, wintypes.WCHAR, wintypes.DWORD,
                                             Coord, ctypes.POINTER(wintypes.DWORD)], wintypes.BOOL),
            "WriteConsoleW": ([wintypes.HANDLE, wintypes.LPCWSTR, wintypes.DWORD,
                                ctypes.POINTER(wintypes.DWORD), wintypes.LPVOID], wintypes.BOOL),
            "GetNumberOfConsoleInputEvents": ([wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD)], wintypes.BOOL),
            "ReadConsoleInputW": ([wintypes.HANDLE, ctypes.POINTER(InputRecord), wintypes.DWORD,
                                    ctypes.POINTER(wintypes.DWORD)], wintypes.BOOL),
        }
        for name, (args, result) in signatures.items():
            function = getattr(self.api, name)
            function.argtypes = args
            function.restype = result
        self.input = self.api.GetStdHandle(-10)
        self.output = self.api.GetStdHandle(-11)
        mode = wintypes.DWORD()
        self._check(self.api.GetConsoleMode(self.input, ctypes.byref(mode)))
        self.original_mode = mode.value
        self.screen_info()  # 输出重定向时不能安装鼠标入口，也不修改输入模式。
        self.bounds = None
        self.region = None
        self.left_down = False
        self.closed = False
        # 保留 Ctrl+C 的系统处理，关闭快速编辑和 VT 输入转换，直接读取鼠标记录。
        self._check(self.api.SetConsoleMode(self.input, (mode.value | 0x0099) & ~0x0240))

    @staticmethod
    def _check(result):
        if not result:
            raise ctypes.WinError(ctypes.get_last_error())

    def screen_info(self):
        info = ScreenInfo()
        self._check(self.api.GetConsoleScreenBufferInfo(self.output, ctypes.byref(info)))
        return info

    def clear_line(self):
        self.bounds = None
        info = self.screen_info()
        row, count = self.region or (info.cursor.Y, 1)
        row = max(0, min(row, info.size.Y - 1))
        written = wintypes.DWORD()
        for y in range(row, min(row + count, info.size.Y)):
            self._check(self.api.FillConsoleOutputCharacterW(
                self.output, " ", info.size.X, Coord(0, y), ctypes.byref(written)
            ))
        self._check(self.api.SetConsoleCursorPosition(self.output, Coord(0, row)))
        self.region = None

    def _write(self, text):
        written = wintypes.DWORD()
        self._check(self.api.WriteConsoleW(
            self.output, text, len(text.encode("utf-16-le")) // 2,
            ctypes.byref(written), None,
        ))

    def render(self, rate):
        self.clear_line()
        info = self.screen_info()
        width = max(1, min(info.size.X, info.window.Right + 1) - 1)
        entry = self.ENTRY
        if width < sum(2 if unicodedata.east_asian_width(c) in ("W", "F") else 1 for c in entry):
            entry = "[复位]" if width >= 6 else "[R]"
        lines = [clip_cells(entry, width)] + wrap_cells(rate, width)
        lines = lines[:max(1, min(info.size.Y, info.window.Bottom - info.window.Top + 1) - 1)]
        # 先分配足够的行，必要时由控制台滚屏，再记录最终的绝对坐标。
        self._write("\r\n" * (len(lines) - 1))
        row = self.screen_info().cursor.Y - len(lines) + 1
        for index, line in enumerate(lines):
            start = Coord(0, row + index)
            written = wintypes.DWORD()
            self._check(self.api.FillConsoleOutputCharacterW(
                self.output, " ", info.size.X, start, ctypes.byref(written)
            ))
            self._check(self.api.SetConsoleCursorPosition(self.output, start))
            self._write(line)
            if index == 0:
                end = self.screen_info()
                # 实测光标列数包含中文宽度，不使用 Python 字符数作为点击边界。
                self.bounds = (0, end.cursor.X, end.cursor.Y) if line else None
        self.region = (row, len(lines))
        self._check(self.api.SetConsoleCursorPosition(self.output, Coord(0, row)))

    def hit_test(self, x, y):
        if self.bounds is None:
            return False
        left, right, row = self.bounds
        return y == row and left <= x < right

    def read_events(self):
        if self.closed:
            return []
        pending = wintypes.DWORD()
        self._check(self.api.GetNumberOfConsoleInputEvents(self.input, ctypes.byref(pending)))
        if not pending.value:
            return []
        records = (InputRecord * min(pending.value, 64))()
        count = wintypes.DWORD()
        self._check(self.api.ReadConsoleInputW(
            self.input, records, len(records), ctypes.byref(count)
        ))
        events = []
        for record in records[:count.value]:
            if record.kind == 2:
                mouse = record.event.mouse
                down = bool(mouse.buttons & 1)
                if down and not self.left_down and mouse.flags in (0, 2):
                    events.append(("click", mouse.position.X, mouse.position.Y))
                self.left_down = down
            elif record.kind == 4:
                self.bounds = None
                events.append(("resize",))
            elif record.kind == 1 and record.event.key.down and record.event.key.key == ord("R"):
                events.append(("open",))
        return events

    def close(self):
        if self.closed:
            return
        self.closed = True
        self.bounds = None
        self._check(self.api.SetConsoleMode(self.input, self.original_mode))
