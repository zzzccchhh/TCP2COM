"""Windowed startup failures and the real Windows Script Host -> pythonw route."""

from contextlib import redirect_stderr
import io
import ctypes
import importlib.util
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from unittest import mock

import tcp2com_web as launcher

BASE = Path(__file__).resolve().parents[1]


class StartupTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.base = Path(self.directory.name)
        self.log = self.base / "local" / "WiFiBridge" / "logs" / "startup.log"
        self.stack = mock.patch.dict(os.environ, {"LOCALAPPDATA": str(self.base / "local")})
        self.stack.start()
        self.base_patch = mock.patch.object(launcher, "BASE", self.base)
        self.base_patch.start()

    def tearDown(self):
        self.base_patch.stop()
        self.stack.stop()
        self.directory.cleanup()

    def build_entry(self):
        entry = self.base / "frontend" / "dist" / "index.html"
        entry.parent.mkdir(parents=True)
        entry.write_text("<html></html>", encoding="utf-8")

    def test_missing_frontend_reports_windowed_instructions_and_logs(self):
        with mock.patch.object(launcher, "_show_error") as show, mock.patch.object(launcher, "_runtime") as runtime:
            self.assertEqual(launcher.main(["--windowed"]), 1)
        runtime.assert_not_called()
        message, windowed, log_path = show.call_args.args
        self.assertIn("前端尚未构建", message)
        self.assertTrue(windowed)
        self.assertEqual(log_path, self.log)
        self.assertIn("前端尚未构建", self.log.read_text(encoding="utf-8"))

    def test_missing_python_dependency_has_actionable_popup(self):
        self.build_entry()
        with mock.patch.object(launcher.importlib, "import_module", side_effect=ModuleNotFoundError("missing")), \
                mock.patch.object(launcher, "_show_error") as show:
            self.assertEqual(launcher.main(["--windowed"]), 1)
        self.assertIn("pip install -r requirements-web.txt", show.call_args.args[0])
        self.assertTrue(show.call_args.args[1])

    def test_pythonw_missing_streams_are_safe_and_windowed_errors_are_detected(self):
        seen = []
        def start(_args):
            sys.stdout.write("discarded stdout")
            sys.stderr.write("discarded stderr")
            self.assertEqual(sys.stdin.read(), "")
            seen.append(sys.stdout.isatty())
            raise launcher.StartupError("startup failure")
        with mock.patch.multiple(sys, stdin=None, stdout=None, stderr=None), \
                mock.patch.object(launcher, "_launch", side_effect=start), \
                mock.patch.object(launcher, "_show_error") as show:
            self.assertEqual(launcher.main([]), 1)
            self.assertIsNone(sys.stdout)
            self.assertIsNone(sys.stderr)
            self.assertIsNone(sys.stdin)
        self.assertEqual(seen, [False])
        self.assertTrue(show.call_args.args[1])

    def test_console_diagnostic_entry_keeps_errors_in_stderr(self):
        output = io.StringIO()
        with redirect_stderr(output):
            self.assertEqual(launcher.main([]), 1)
        self.assertIn("前端尚未构建", output.getvalue())
        self.assertIn(str(self.log), output.getvalue())

    def test_window_creation_failure_cleans_up_service_and_records_traceback(self):
        self.build_entry()
        webview = mock.Mock(settings={})
        webview.create_window.side_effect = RuntimeError("window creation failed")
        with mock.patch.object(launcher, "_runtime", return_value=webview), \
                mock.patch("web_service.WebService") as factory, mock.patch.object(launcher, "_show_error") as show:
            self.assertEqual(launcher.main(["--windowed"]), 1)
        factory.return_value._shutdown.assert_called_once()
        webview.start.assert_not_called()
        self.assertTrue(show.call_args.args[1])
        self.assertIn("RuntimeError: window creation failed", self.log.read_text(encoding="utf-8"))

    def test_successful_start_keeps_webview_behavior_and_closes_service(self):
        self.build_entry()
        webview = mock.MagicMock(settings={})
        with mock.patch.object(launcher, "_runtime", return_value=webview), \
                mock.patch("web_service.WebService") as factory, mock.patch.object(launcher, "_show_error") as show:
            self.assertEqual(launcher.main(["--windowed"]), 0)
        webview.create_window.assert_called_once()
        webview.start.assert_called_once_with(gui="edgechromium", debug=False, http_server=True)
        factory.return_value._shutdown.assert_called_once()
        show.assert_not_called()

    def test_unwritable_log_directory_falls_back_to_temporary_directory(self):
        blocked = self.base / "blocked"
        blocked.write_text("file", encoding="utf-8")
        fallback = self.base / "fallback"
        with mock.patch.dict(os.environ, {"LOCALAPPDATA": str(blocked)}), \
                mock.patch.object(launcher.tempfile, "gettempdir", return_value=str(fallback)), \
                mock.patch.object(launcher, "_show_error") as show:
            self.assertEqual(launcher.main(["--windowed"]), 1)
        path = show.call_args.args[2]
        self.assertEqual(path, fallback / "WiFiBridge" / "logs" / "startup.log")
        self.assertTrue(path.is_file())

    def test_worker_exception_is_logged_and_hook_is_restored(self):
        original_hook = threading.excepthook
        def start(_args):
            def failure():
                raise RuntimeError("worker failure")
            worker = threading.Thread(target=failure, name="startup-test-worker")
            worker.start()
            worker.join(2)
        with mock.patch.object(launcher, "_launch", side_effect=start), redirect_stderr(io.StringIO()):
            self.assertEqual(launcher.main(["--windowed"]), 0)
        self.assertIs(threading.excepthook, original_hook)
        text = self.log.read_text(encoding="utf-8")
        self.assertIn("startup-test-worker", text)
        self.assertIn("RuntimeError: worker failure", text)

    @unittest.skipUnless(sys.platform == "win32", "Native error dialog is Windows-only")
    def test_error_popup_uses_native_message_box_without_a_tk_window(self):
        with mock.patch.object(launcher.ctypes.windll.user32, "MessageBoxW", return_value=1) as message:
            launcher._show_error("启动失败", True, self.log)
        message.assert_called_once()
        self.assertIn(str(self.log), message.call_args.args[1])


@unittest.skipUnless(sys.platform == "win32" and shutil.which("cscript") and shutil.which("pyw"),
                     "Requires Windows Script Host and pyw launcher")
class WindowedScriptTests(unittest.TestCase):
    def wait_for_processes(self, pids):
        # Async launchers may still own their working directory after writing a result.
        kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel.OpenProcess.argtypes = [ctypes.c_ulong, ctypes.c_int, ctypes.c_ulong]
        kernel.OpenProcess.restype = ctypes.c_void_p
        kernel.WaitForSingleObject.argtypes = [ctypes.c_void_p, ctypes.c_ulong]
        kernel.CloseHandle.argtypes = [ctypes.c_void_p]
        for pid in pids:
            handle = kernel.OpenProcess(0x100000, False, pid)
            if handle:
                try:
                    self.assertEqual(kernel.WaitForSingleObject(handle, 10000), 0)
                finally:
                    kernel.CloseHandle(handle)

    def test_real_vbs_launches_pythonw_without_console_from_a_unicode_path(self):
        with tempfile.TemporaryDirectory() as directory:
            folder = Path(directory) / "中文 program folder"
            folder.mkdir()
            script = folder / "启动_新版界面.vbs"
            shutil.copyfile(BASE / script.name, script)
            (folder / "tcp2com_web.py").write_text(
                "import ctypes,json,os,sys\nfrom pathlib import Path\n"
                "data={'console':int(ctypes.windll.kernel32.GetConsoleWindow()),"
                "'pid':os.getpid(),'parent':os.getppid(),'executable':sys.executable,'args':sys.argv[1:]}\n"
                "Path(__file__).with_name('result.json').write_text(json.dumps(data),encoding='utf-8')\n",
                encoding="utf-8")
            result = subprocess.run([shutil.which("cscript"), "//B", "//Nologo", str(script)],
                                    cwd=directory, capture_output=True, timeout=10,
                                    creationflags=subprocess.CREATE_NO_WINDOW)
            self.assertEqual(result.returncode, 0, result.stderr)
            path = folder / "result.json"
            deadline = time.monotonic() + 10
            while not path.is_file() and time.monotonic() < deadline:
                time.sleep(.05)
            self.assertTrue(path.is_file(), "Windowed Python did not execute the portable entry")
            data = json.loads(path.read_text(encoding="utf-8"))
            self.wait_for_processes((data["pid"], data["parent"]))
            self.assertEqual(data["console"], 0)
            self.assertEqual(Path(data["executable"]).name.lower(), "pythonw.exe")
            self.assertEqual(data["args"], ["--windowed"])

    @unittest.skipUnless(importlib.util.find_spec("webview"), "Visible desktop integration requires pywebview")
    def test_real_vbs_displays_production_workspace_without_console(self):
        with tempfile.TemporaryDirectory() as directory:
            folder = Path(directory) / "中文 visible workspace"
            folder.mkdir()
            script = folder / "启动_新版界面.vbs"
            shutil.copyfile(BASE / script.name, script)
            report = folder / "result.json"
            tool = BASE / "tools" / "test-webview.py"
            # Use the actual production entry/service with temporary settings; do not hide its window.
            (folder / "tcp2com_web.py").write_text(
                f"import runpy,sys\nsys.argv=[{str(tool)!r},'--visible','--result-file',{str(report)!r}]\n"
                f"runpy.run_path({str(tool)!r},run_name='__main__')\n", encoding="utf-8")
            result = subprocess.run([shutil.which("cscript"), "//B", "//Nologo", str(script)],
                                    cwd=directory, capture_output=True, timeout=10,
                                    creationflags=subprocess.CREATE_NO_WINDOW)
            self.assertEqual(result.returncode, 0, result.stderr)
            deadline = time.monotonic() + 45
            while not report.is_file() and time.monotonic() < deadline:
                time.sleep(.05)
            self.assertTrue(report.is_file(), "Visible desktop did not produce a report")
            data = json.loads(report.read_text(encoding="utf-8"))
            self.wait_for_processes((data["pid"], data["parent"]))
            self.assertTrue(data["ok"], data["errors"])
            self.assertEqual(data["console"], 0)
            self.assertEqual(data["window_count"], 1)
            self.assertTrue(data["visible"], "The real production window must be visible")


if __name__ == "__main__":
    unittest.main()
