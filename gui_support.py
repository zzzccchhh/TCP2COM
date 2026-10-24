"""Non-Tk validation, atomic profiles and serialized bridge/USB lifecycle."""

import argparse
import copy
import json
import logging
from pathlib import Path
import queue
import threading
import time
import os
import tempfile

from bridge_settings import _unique_object
from tcp2com import App, build_parser, parse_args
from usb_diagnostics import USBMonitor
from device_capabilities import capabilities


class FormParser(argparse.ArgumentParser):
    def error(self, message):
        raise ValueError(message)


def default_settings():
    return vars(build_parser().parse_args([]))


def settings_to_args(settings):
    actions = {a.dest: a for a in build_parser()._actions
               if a.dest not in ("help", "config", "profile", "list_coms", "open_config")}
    argv = []
    for key, value in settings.items():
        if key in ("config", "profile", "list_coms", "open_config"):
            continue
        if key not in actions:
            raise ValueError(f"未知配置项：{key}")
        action = actions[key]
        if value == "" and action.type in (int, float):
            raise ValueError(f"{key} 不能为空")
        if isinstance(action, argparse._StoreTrueAction):
            if type(value) is not bool:
                raise ValueError(f"{key} 必须为布尔值")
            if value:
                argv.append(action.option_strings[0])
        elif value is not None and value != "":
            argv.extend((action.option_strings[0], str(value)))
    return parse_args(argv, parser_class=FormParser)


def read_profiles(path):
    document = json.loads(Path(path).read_text(encoding="utf-8-sig"), object_pairs_hook=_unique_object)
    if not isinstance(document, dict) or set(document) != {"profiles"} or not isinstance(document["profiles"], dict):
        raise ValueError("配置文件只能包含 profiles 对象")
    return document["profiles"]


def load_profile(path, name):
    args = parse_args(["--config", str(path), "--profile", name], parser_class=FormParser)
    return {key: value for key, value in vars(args).items()
            if key not in ("config", "profile", "list_coms", "open_config")}


def atomic_json(path, document):
    path = Path(path).resolve()
    fd, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as stream:
            json.dump(document, stream, ensure_ascii=False, indent=2, allow_nan=False)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def save_profile(path, name, settings):
    if not name.strip():
        raise ValueError("请输入配置名称")
    args = settings_to_args(settings)
    profiles = read_profiles(path) if Path(path).exists() else {}
    profiles[name] = {key: value for key, value in vars(args).items()
                      if key not in ("config", "profile", "list_coms", "open_config") and value is not None}
    atomic_json(path, {"profiles": profiles})


class EventUI:
    """Workers own this bounded adapter, never a Tk window or Tk variable."""
    def __init__(self, limit=2000):
        self.events = queue.Queue(limit)
        self.lock = threading.Lock()
        self.dropped = 0
        self.last_state = "未启动"
        self.rate = ""
        self.warnings = {}

    def message(self, text):
        try:
            self.events.put_nowait((time.time(), str(text)))
        except queue.Full:
            with self.lock:
                self.dropped += 1

    def state(self, state, text):
        with self.lock:
            if self.last_state == text:
                return
            self.last_state = text
        self.message(text)

    def warning_once(self, key, text, interval=3):
        with self.lock:
            now = time.monotonic()
            if now - self.warnings.get(key, -interval) < interval:
                return
            self.warnings[key] = now
        self.message(text)

    def set_rate_line(self, text, refresh=None):
        with self.lock:
            self.rate = text


class BridgeController:
    def __init__(self, app_factory=App, usb_factory=USBMonitor):
        self.ui = EventUI()
        self.commands = queue.Queue(8)
        self.lock = threading.Lock()
        self.app = None
        self.usb = None
        self.running_settings = None
        self.state = "已停止"
        self.busy = False
        self.closing = False
        self.app_factory = app_factory
        self.usb_factory = usb_factory
        self.log_handler = EventLogHandler(self.ui)
        logging.getLogger("tcp2com").addHandler(self.log_handler)
        self.thread = threading.Thread(target=self._work, name="gui-lifecycle", daemon=True)
        self.thread.start()

    def submit(self, command, args=None):
        with self.lock:
            if self.closing or self.busy:
                return False
            self.busy = True
            self.state = "正在处理…"
        self.commands.put_nowait((command, copy.deepcopy(args)))
        return True

    def snapshot(self):
        with self.lock:
            return self.app, self.usb, copy.deepcopy(self.running_settings), self.state, self.busy

    def _stop_app(self):
        with self.lock:
            app, self.app = self.app, None
            self.running_settings = None
        if app:
            app.stop()

    def _usb(self, args):
        with self.lock:
            previous, self.usb = self.usb, None
        if previous:
            previous.close()
        if args and capabilities(args.device).usb and args.usb_port and not self.closing:
            monitor = self.usb_factory(args.usb_port, args.usb_baud, self.ui.message, args.usb_log, device=args.device)
            monitor.start()
            with self.lock:
                self.usb = monitor

    def _work(self):
        while not self.closing:
            try:
                command, args = self.commands.get(timeout=0.1)
            except queue.Empty:
                app = self.snapshot()[0]
                if app and app.close_event.is_set():
                    self._stop_app()
                    with self.lock:
                        self.state = "已停止（连接结束或错误，见日志）"
                continue
            try:
                if command in ("start", "restart", "stop"):
                    self._stop_app()
                if command in ("start", "restart") and not self.closing:
                    self.ui.rate = ""
                    self._usb(None)
                    # USB belongs to its own lifecycle, independent of payload forwarding.
                    payload = copy.deepcopy(args)
                    payload.usb_port = payload.usb_log = None
                    logging.getLogger("tcp2com").setLevel(logging.DEBUG if args.verbose else logging.WARNING)
                    app = self.app_factory(payload, self.ui)
                    if app.start():
                        with self.lock:
                            self.app = app
                            self.running_settings = vars(copy.deepcopy(args))
                        stats = threading.Thread(target=app.run, name="gui-rates", daemon=True)
                        app.threads.append(stats)
                        stats.start()
                        self._usb(args)
                    else:
                        app.stop()
                elif command == "usb":
                    self._usb(args)
                    with self.lock:
                        if self.running_settings:
                            for key in ("usb_port", "usb_baud", "usb_log"):
                                self.running_settings[key] = getattr(args, key)
                elif command == "stop":
                    self._usb(None)
            except Exception as exc:
                self.ui.message(f"操作失败：{exc}")
                self._stop_app()
            finally:
                with self.lock:
                    self.busy = False
                    self.state = "运行中" if self.app else "已停止"
                self.commands.task_done()
        self._stop_app()
        self._usb(None)
        logging.getLogger("tcp2com").removeHandler(self.log_handler)
        with self.lock:
            self.busy = False
            self.state = "已关闭"

    def close(self):
        with self.lock:
            self.closing = True
            app = self.app
        if app:
            app.close_event.set()
            app.conn.close_current("关闭窗口")


class EventLogHandler(logging.Handler):
    def __init__(self, ui):
        super().__init__()
        self.ui = ui

    def emit(self, record):
        self.ui.message(f"{record.levelname} | {record.getMessage()}")


def config_job(events, action, client, patch=None, context=None):
    """Standalone worker: result queue contains data/exceptions only, no Tk ownership."""
    try:
        result = client.read() if action == "read" else client.save(patch)
        event = (action, client.identity, result, None)
    except Exception as exc:
        # Do not send exception tracebacks holding request/password frames to the UI.
        exc.__traceback__ = None
        exc.__context__ = None
        exc.__cause__ = None
        event = (action, client.identity, None, exc)
    events.put(event if context is None else (*event, context))
