#!/usr/bin/env python3
"""Local desktop window for the React frontend; no public server or cloud service."""

import argparse
from contextlib import contextmanager
import ctypes
import importlib
import io
import logging
from logging.handlers import RotatingFileHandler
import os
from pathlib import Path
import sys
import tempfile
import threading

from desktop_appearance import BACKGROUNDS, load_preference, preference_path, resolved_theme

BASE = Path(__file__).resolve().parent


class StartupError(Exception):
    """An actionable prerequisite failure, displayed without requiring a console."""


class _WindowedStream(io.TextIOWrapper):
    def isatty(self):
        # Windows treats NUL as a character device, even though no terminal exists.
        return False


@contextmanager
def _standard_streams():
    """pythonw has no stdio; libraries still expect writable stream objects."""
    originals = {name: getattr(sys, name) for name in ("stdin", "stdout", "stderr")}
    opened = []
    try:
        for name, stream in originals.items():
            if stream is None:
                raw = open(os.devnull, "rb" if name == "stdin" else "wb", buffering=0)
                replacement = _WindowedStream(raw, encoding="utf-8", write_through=True)
                opened.append(replacement)
                setattr(sys, name, replacement)
        yield
    finally:
        for name, stream in originals.items():
            setattr(sys, name, stream)
        for stream in opened:
            stream.close()


@contextmanager
def _startup_log():
    logger = logging.Logger("wifi_bridge.startup", level=logging.ERROR)
    local = Path(os.environ.get("LOCALAPPDATA") or tempfile.gettempdir())
    candidates = [local / "WiFiBridge" / "logs" / "startup.log",
                  Path(tempfile.gettempdir()) / "WiFiBridge" / "logs" / "startup.log"]
    handler, path = None, None
    for candidate in candidates:
        try:
            candidate.parent.mkdir(parents=True, exist_ok=True)
            handler = RotatingFileHandler(candidate, maxBytes=512 * 1024, backupCount=2, encoding="utf-8")
            handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
            logger.addHandler(handler)
            path = candidate
            break
        except OSError:
            continue
    if handler is None:
        logger.addHandler(logging.NullHandler())
    previous_hook = threading.excepthook

    def thread_error(args):
        logger.error("Unhandled worker exception in %s", args.thread.name if args.thread else "unknown",
                     exc_info=(args.exc_type, args.exc_value, args.exc_traceback))
        previous_hook(args)

    threading.excepthook = thread_error
    try:
        yield logger, path
    finally:
        threading.excepthook = previous_hook
        if handler:
            logger.removeHandler(handler)
            handler.close()


def _show_error(message, windowed, log_path):
    if log_path:
        message += f"\n\n错误日志：{log_path}"
    else:
        message += "\n\n无法写入错误日志，请检查目录权限。"
    if windowed and os.name == "nt":
        ctypes.windll.user32.MessageBoxW(None, message, "Wi-Fi Bridge · 启动失败", 0x10 | 0x10000)
    else:
        print(message, file=sys.stderr)


def _runtime():
    try:
        webview = importlib.import_module("webview")
        importlib.import_module("serial")
        return webview
    except ImportError as exc:
        raise StartupError("缺少新版界面所需的 Python 依赖。\n请在程序目录运行：\n"
                           "py -3 -m pip install -r requirements-web.txt") from exc


def _launch(args):
    entry = BASE / "frontend" / "dist" / "index.html"
    if not entry.is_file():
        raise StartupError("前端尚未构建。请运行程序目录中的“启动_新版界面.bat”完成首次构建，\n"
                           "然后双击“启动_新版界面.vbs”。")
    webview = _runtime()
    from web_service import WebService
    webview.settings["ALLOW_DOWNLOADS"] = True
    appearance_path = preference_path()
    appearance = load_preference(appearance_path)
    service = WebService(appearance_path=appearance_path)
    try:
        # pywebview preserves fragments when mapping local files to its private HTTP server.
        url = str(entry) + "#desktop-appearance=" + appearance
        window = webview.create_window("Wi-Fi Bridge · 无线串口工作台", url, js_api=service,
                                       width=1420, height=920, min_size=(960, 680),
                                       background_color=BACKGROUNDS[resolved_theme(appearance)])
        window.events.closing += service._shutdown
        webview.start(gui="edgechromium", debug=args.debug, http_server=True)
    finally:
        # Also clean up if create_window/start fails before the closing event is registered.
        service._shutdown()


def main(argv=None):
    parser = argparse.ArgumentParser(description="Wi-Fi Bridge 网页桌面界面")
    parser.add_argument("--debug", action="store_true", help="启用 WebView 开发工具")
    parser.add_argument("--windowed", action="store_true", help=argparse.SUPPRESS)
    args = parser.parse_args(argv)
    windowed = args.windowed or sys.stderr is None
    with _standard_streams(), _startup_log() as (logger, log_path):
        try:
            _launch(args)
            return 0
        except StartupError as exc:
            logger.error("Startup prerequisite failed: %s", exc)
            _show_error(str(exc), windowed, log_path)
        except Exception:
            logger.exception("Desktop startup failed")
            _show_error("无法启动新版界面，请查看错误日志。\n"
                        "请确认 Python 依赖和 Microsoft Edge WebView2 Runtime 已正确安装。", windowed, log_path)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
