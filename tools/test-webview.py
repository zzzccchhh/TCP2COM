"""Exercise the production launcher with a hidden window and local serial loopback."""

import argparse
import ctypes
import json
import os
from pathlib import Path
import socket
import sys
import tempfile
import threading
import time
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from web_service import WebService
import tcp2com_web
from desktop_appearance import load_preference, save_preference


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--result-file", type=Path, help="Write results for a pythonw invocation")
    parser.add_argument("--visible", action="store_true", help="Also verify that the real desktop window is visible")
    parser.add_argument("--appearance", choices=("light", "dark"), default="dark", help="Verify first-paint desktop appearance")
    args = parser.parse_args()
    import webview

    directory = tempfile.TemporaryDirectory()
    appearance_path = Path(directory.name) / "appearance.json"
    save_preference(appearance_path, args.appearance)
    service = WebService(state_path=Path(directory.name) / "state.json", appearance_path=appearance_path)
    window = None
    create_window, start = webview.create_window, webview.start
    results = []
    report = {"ok": False, "console": None, "window_count": 0, "visible": False,
              "pid": os.getpid(), "parent": os.getppid()}

    def create_instrumented(*params, **options):
        nonlocal window
        options["hidden"] = not args.visible
        window = create_window(*params, **options)
        return window

    def start_and_verify(**options):
        return start(verify, **options)

    def verify():
        try:
            report["console"] = int(ctypes.windll.kernel32.GetConsoleWindow()) if os.name == "nt" else None
            report["window_count"] = len(webview.windows)
            if report["window_count"] != 1:
                raise AssertionError("Desktop startup created more than one window")
            if Path(sys.executable).name.lower() == "pythonw.exe" and report["console"]:
                raise AssertionError("Windowed startup unexpectedly has a console")
            deadline = time.monotonic() + 30
            while time.monotonic() < deadline:
                if window.evaluate_js("document.body.textContent.includes('本地运行')"):
                    break
                time.sleep(.1)
            else:
                raise AssertionError("React did not connect to the native API")
            theme = window.evaluate_js("document.documentElement.dataset.theme")
            if theme != args.appearance:
                raise AssertionError(f"Desktop startup lost its appearance: {theme}")
            next_theme = "light" if theme == "dark" else "dark"
            window.evaluate_js(f"""(() => {{
                const select = document.querySelector('select[aria-label="外观"]');
                select.value = '{next_theme}'; select.dispatchEvent(new Event('change', {{bubbles: true}}));
            }})()""")
            deadline = time.monotonic() + 8
            while load_preference(appearance_path) != next_theme and time.monotonic() < deadline:
                time.sleep(.05)
            if load_preference(appearance_path) != next_theme:
                raise AssertionError("Desktop theme change was not persisted")
            print(f"PASS: WebView2 appearance ({theme} startup, {next_theme} persistence)")
            optics = window.evaluate_js("""(() => {
                const card = document.querySelector('.connection-panel');
                return {mode: card?.dataset.glass,
                    expected: /Chrome|Chromium|Edg\\//.test(navigator.userAgent)
                        && CSS.supports('backdrop-filter', 'url(#probe)')
                        && matchMedia('(hover: hover) and (pointer: fine)').matches
                        && !matchMedia('(max-width: 760px)').matches ? 'refractive' : 'frosted',
                    filters: document.querySelectorAll('svg.liquid-glass-defs[data-glass-kind="surface"]').length,
                    controls: document.querySelectorAll('.liquid-segments .liquid-indicator[data-glass]').length,
                    overflow: document.documentElement.scrollWidth > innerWidth};
            })()""")
            if optics.get("mode") != optics["expected"] or optics["overflow"]:
                raise AssertionError(f"Native liquid glass did not initialize: {optics}")
            if optics["filters"] != (6 if optics["expected"] == "refractive" else 0):
                raise AssertionError(f"Unexpected native optical resources: {optics}")
            if optics["controls"] < 3:
                raise AssertionError(f"Native liquid controls did not initialize: {optics}")
            print(f"PASS: WebView2 liquid glass ({optics['mode']}, {optics['filters']} filters)")
            if os.name == "nt":
                from ctypes import wintypes
                user = ctypes.WinDLL("user32", use_last_error=True)
                user.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
                user.GetWindowTextW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
                user.IsWindowVisible.argtypes = [wintypes.HWND]
                visible = []
                @ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
                def inspect_window(hwnd, _):
                    pid = wintypes.DWORD()
                    user.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
                    title = ctypes.create_unicode_buffer(256)
                    user.GetWindowTextW(hwnd, title, len(title))
                    if pid.value == os.getpid() and title.value == window.title:
                        visible.append(bool(user.IsWindowVisible(hwnd)))
                    return True
                user.EnumWindows(inspect_window, 0)
                report["visible"] = any(visible)
                if args.visible and not report["visible"]:
                    raise AssertionError("VBS started Python but the desktop window is hidden")
            with socket.socket() as listener:
                listener.bind(("127.0.0.1", 0))
                port = listener.getsockname()[1]
            script = """(async () => {
                const settings = await window.pywebview.api.update_settings({device:'c5',mode:'tcp-server',
                    listen:'127.0.0.1',port:PORT,com:'loop://'});
                if (!settings.ok) throw Error(settings.error);
                const start = await window.pywebview.api.bridge('start');
                if (!start.ok) throw Error(start.error);
                return 'started';
            })()""".replace("PORT", json.dumps(str(port)))
            done = threading.Event()
            def completed(value):
                results.append(value)
                done.set()
            window.evaluate_js(script, completed)
            if not done.wait(10) or results != ["started"]:
                raise AssertionError(f"Native JS API call failed: {results}")
            deadline = time.monotonic() + 5
            while not service._controller.snapshot()[0] and time.monotonic() < deadline:
                time.sleep(.05)
            with socket.create_connection(("127.0.0.1", port), timeout=3) as peer:
                peer.settimeout(3)
                payload = bytes(range(256)) * 4
                peer.sendall(payload)
                received = bytearray()
                while len(received) < len(payload):
                    received.extend(peer.recv(4096))
                if bytes(received) != payload:
                    raise AssertionError("Native desktop bridge changed payload bytes")
            time.sleep(.4)
            if not window.evaluate_js("document.body.textContent.includes('COM 写入')"):
                raise AssertionError("React workspace did not render")
            print("PASS: WebView2 + React + JS/Python API + TCP/serial loopback + clean shutdown")
        except Exception as exc:
            results.append(exc)
            print(f"FAIL: {exc}", file=sys.stderr)
        finally:
            window.destroy()

    try:
        with mock.patch("web_service.WebService", return_value=service), \
                mock.patch.object(tcp2com_web, "preference_path", return_value=appearance_path), \
                mock.patch.object(webview, "create_window", side_effect=create_instrumented), \
                mock.patch.object(webview, "start", side_effect=start_and_verify), \
                mock.patch.object(tcp2com_web, "_show_error", side_effect=lambda message, *_: results.append(RuntimeError(message))):
            code = tcp2com_web.main([])
    finally:
        service._shutdown()
        directory.cleanup()
    report["errors"] = [str(result) for result in results if isinstance(result, Exception)]
    report["ok"] = code == 0 and not report["errors"] and not service._controller.thread.is_alive()
    if args.result_file:
        args.result_file.write_text(json.dumps(report, ensure_ascii=False), encoding="utf-8")
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
