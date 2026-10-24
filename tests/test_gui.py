"""Real Tk lifecycle plus profile and bridge controller regression tests."""

import copy
import gc
import http.server
import json
from pathlib import Path
import socket
import sys
import tempfile
import threading
import time
import unittest
from unittest import mock

try:
    import tkinter as tk
except ImportError:
    tk = None

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from c5_config import C5Client, SaveOutcomeUnknown, public_config
from c5_usb_config import C5USBClient
from gui_support import BridgeController, EventUI, load_profile, save_profile, settings_to_args
if tk is not None:
    from tcp2com_gui import BridgeGUI
from test_c5_config import SetupHandler, example_config
from test_usb_config import SerialC5


def wait_for(predicate, update=None, timeout=4):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if update:
            update()
        if predicate():
            return
        time.sleep(.01)
    raise AssertionError("operation did not finish")


def free_port():
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


class ProfileTests(unittest.TestCase):
    def setUp(self):
        gc.collect()

    def test_atomic_profile_save_preserves_other_profiles_and_resolves_files(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "local.json"
            values = {"device": "c5", "mode": "tcp-server", "port": 8080, "capture": "raw.bin"}
            save_profile(path, "first", values)
            save_profile(path, "second", {**values, "capture": "next.bin"})
            self.assertEqual(set(json.loads(path.read_text())["profiles"]), {"first", "second"})
            self.assertEqual(load_profile(path, "first")["capture"], str(Path(directory) / "raw.bin"))
            before = path.read_bytes()
            with self.assertRaises(ValueError):
                save_profile(path, "bad", {**values, "port": -1})
            self.assertEqual(path.read_bytes(), before)
            self.assertEqual(len(list(Path(directory).iterdir())), 1)
            with self.assertRaises(ValueError):
                load_profile(path, "--invalid-profile")

    def test_gui_validation_uses_cli_limits_and_capabilities(self):
        base = {"device": "c5", "mode": "tcp-server", "com": "loop://"}
        self.assertTrue(settings_to_args(base).no_reset_ui)
        for patch in ({"port": 0}, {"mode": "udp", "host": ""}, {"stats_interval": "nan"},
                      {"usb_port": "loop://"}, {"capture": "data.bin"}, {"verbose": "true"}, {"unknown": 1}):
            with self.subTest(patch=patch), self.assertRaises(ValueError):
                settings_to_args({**base, **patch})

    def test_event_queue_stays_bounded_and_reports_overflow(self):
        ui = EventUI(limit=2)
        for i in range(100):
            ui.message(str(i))
        self.assertEqual(ui.events.qsize(), 2)
        self.assertEqual(ui.dropped, 98)


class ControllerTests(unittest.TestCase):
    def setUp(self):
        gc.collect()
        self.controller = BridgeController()

    def tearDown(self):
        self.controller.close()
        self.controller.thread.join(5)
        self.assertFalse(self.controller.thread.is_alive())
        gc.collect()

    def test_start_roundtrip_restart_and_release_ports(self):
        port = free_port()
        args = settings_to_args({"device": "c5", "mode": "tcp-server", "listen": "127.0.0.1",
                                 "port": port, "com": "loop://", "stats_interval": .1})
        self.assertTrue(self.controller.submit("start", args))
        wait_for(lambda: not self.controller.snapshot()[4])
        first = self.controller.snapshot()[0]
        self.assertIsNotNone(first)
        with socket.create_connection(("127.0.0.1", port), timeout=1) as peer:
            binary = bytes(range(256)) * 4
            peer.sendall(binary)
            received = bytearray()
            while len(received) < len(binary):
                received.extend(peer.recv(len(binary) - len(received)))
            self.assertEqual(received, binary)
        self.assertTrue(self.controller.submit("restart", args))
        wait_for(lambda: not self.controller.snapshot()[4])
        self.assertIsNot(self.controller.snapshot()[0], first)
        self.assertFalse(first.ser.is_open)
        self.assertTrue(self.controller.submit("stop"))
        wait_for(lambda: not self.controller.snapshot()[4])
        self.assertIsNone(self.controller.snapshot()[0])
        with socket.socket() as probe:
            probe.bind(("127.0.0.1", port))

    def test_usb_failure_is_independent_and_stop_releases_it(self):
        args = settings_to_args({"device": "c5", "mode": "tcp-server", "listen": "127.0.0.1",
                                 "port": free_port(), "com": "loop://"})
        self.controller.submit("start", args)
        wait_for(lambda: not self.controller.snapshot()[4])
        app = self.controller.snapshot()[0]
        args.usb_port = "missing://"
        self.controller.submit("usb", args)
        wait_for(lambda: not self.controller.snapshot()[4])
        self.assertIs(self.controller.snapshot()[0], app)
        monitor = self.controller.snapshot()[1]
        self.assertIsNotNone(monitor)
        self.assertFalse(monitor.status()["connected"])
        args.usb_port = None
        self.controller.submit("usb", args)
        wait_for(lambda: not self.controller.snapshot()[4])
        self.assertIs(self.controller.snapshot()[0], app)
        self.assertFalse(monitor.thread.is_alive())

    def test_failed_start_is_stopped_and_does_not_overwrite_capture(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "original.bin"
            path.write_bytes(b"original")
            args = settings_to_args({"device": "c5", "mode": "tcp-server", "port": free_port(), "capture": str(path)})
            self.controller.submit("start", args)
            wait_for(lambda: not self.controller.snapshot()[4])
            self.assertIsNone(self.controller.snapshot()[0])
            self.assertEqual(path.read_bytes(), b"original")


class GUITests(unittest.TestCase):
    def setUp(self):
        gc.collect()
        if tk is None:
            self.skipTest("Tk unavailable")
        try:
            self.root = tk.Tk()
        except tk.TclError:
            self.skipTest("Tk display unavailable")
        self.root.withdraw()
        self.root.geometry("+30000+30000")
        self.temporary = tempfile.TemporaryDirectory()
        self.gui = BridgeGUI(self.root, Path(self.temporary.name) / "profiles.json")
        self.root.update()

    def tearDown(self):
        if hasattr(self, "gui"):
            self.gui.close()
            wait_for(lambda: self.gui.destroyed, self.root.update, timeout=6)
            self.assertFalse(self.gui.controller.thread.is_alive())
            self.gui = None
            self.root = None
            self.temporary.cleanup()
        gc.collect()

    def read_fixture(self):
        config = example_config()
        self.gui.baseline = config
        client = C5Client(self.gui.config_url.get())
        client.bind_session("0123456789abcdef-1")
        self.gui.read_identity = client.identity
        self.gui.read_device = "c5"
        self.gui._fill_c5(config)
        self.gui._c5_enable()
        return config

    def test_mode_fields_device_capabilities_and_local_output(self):
        self.assertEqual(self.gui.network.get("device"), "c5")
        self.assertEqual(str(self.gui.reset_button["state"]), "disabled")
        self.gui.network.set("mode", "udp")
        self.assertEqual(str(self.gui.network.widgets["peer_port"]["state"]), "normal")
        self.gui.network.set("mode", "tcp-client")
        self.assertEqual(str(self.gui.network.widgets["listen"]["state"]), "disabled")
        self.gui.output.set("仅接收至文件")
        self.assertIsNone(self.gui.settings()["com"])
        self.gui.local.set("capture", "test.bin")
        self.assertEqual(str(self.gui.local.widgets["com"]["state"]), "disabled")
        self.read_fixture()
        self.gui.network.set("device", "esp01s")
        self.assertIsNone(self.gui.baseline)
        self.assertEqual(self.gui.tabs.tab(self.gui.c5_frame, "state"), "disabled")

    def test_local_save_does_not_write_device_or_change_running_connection(self):
        with mock.patch("tcp2com_gui.C5Client.save") as save_device:
            self.gui.save_profile()
            save_device.assert_not_called()
        self.assertTrue(Path(self.gui.profile_path.get()).exists())
        self.assertIsNone(self.gui.controller.snapshot()[0])

    def test_c5_undo_backup_import_never_persist_passwords(self):
        baseline = self.read_fixture()
        self.gui.c5_forms["ap"].set("ap_password", "private-secret")
        self.gui.c5_forms["pc"].set("ssid", "changed")
        path = str(Path(self.temporary.name) / "backup.json")
        with mock.patch("tcp2com_gui.filedialog.asksaveasfilename", return_value=path):
            self.gui.backup_c5()
        document = Path(path).read_text(encoding="utf-8")
        self.assertNotIn("password", document)
        self.assertNotIn("private-secret", document)
        self.gui.undo_c5()
        self.assertEqual(self.gui.edited_c5(), public_config(baseline))
        with mock.patch("tcp2com_gui.filedialog.askopenfilename", return_value=path):
            self.gui.import_c5()
        self.assertEqual(self.gui.c5_forms["pc"].get("ssid"), "changed")
        self.assertEqual(self.gui.c5_forms["ap"].get("ap_password"), "")
        self.assertEqual(self.gui.baseline, baseline)

    def test_device_save_requires_read_of_same_endpoint(self):
        self.read_fixture()
        self.gui.config_url.set("http://192.168.4.2/")
        with mock.patch.object(self.gui, "error") as error, mock.patch.object(self.gui, "_http") as http:
            self.gui.save_c5()
        self.assertIn("读取", str(error.call_args))
        http.assert_not_called()

    def test_s3_switch_clears_c5_read_and_passwords_and_limits_band(self):
        self.read_fixture()
        self.gui.c5_forms["ap"].set("ap_password", "private-unused")
        self.gui.network.set("device", "s3")
        self.assertIsNone(self.gui.baseline)
        self.assertIsNone(self.gui.read_identity)
        self.assertEqual(self.gui.c5_forms["ap"].get("ap_password"), "")
        self.assertEqual(self.gui.c5_forms["common"].options["band"], {"2.4 GHz": 0})
        self.assertIn("GPIO4", self.gui.setup_hint.get())
        self.assertIn("ESP32-S3", self.gui.c5_buttons[1]["text"])
        self.assertEqual(str(self.gui.usb_form.widgets["usb_port"]["state"]), "normal")
        with mock.patch.object(self.gui, "error") as error, mock.patch.object(self.gui, "_http") as send:
            self.gui.save_c5()
        self.assertIn("读取", str(error.call_args))
        send.assert_not_called()
        self.gui.network.set("device", "c5")
        self.assertEqual(set(self.gui.c5_forms["common"].options["band"].values()), {0, 1, 2})

    def test_s3_single_usb_read_and_save(self):
        self.gui.network.set("device", "s3")
        device = self.connect_usb_fixture(SerialC5(model="ESP32-S3"))
        self.gui.read_c5()
        wait_for(lambda: self.gui.baseline is not None, self.root.update)
        self.assertEqual(self.gui.read_device, "s3")
        self.gui.c5_forms["common"].set("baud", "921600")
        with mock.patch("tcp2com_gui.messagebox.askokcancel", return_value=True):
            self.gui.save_c5()
        wait_for(lambda: "已写入 NVS" in self.gui.c5_status.get(), self.root.update)
        self.assertEqual(device.save_count, 1)
        self.assertEqual(device.requests[-1]["patch"], {"baud": 921600})

    def test_s3_rejects_c5_usb_model(self):
        self.gui.network.set("device", "s3")
        device = self.connect_usb_fixture()
        self.gui.read_c5()
        wait_for(lambda: not self.gui.http_busy, self.root.update)
        self.assertIn("不是", self.gui.c5_status.get())
        self.assertIsNone(self.gui.baseline)
        self.assertEqual(device.save_count, 0)

    def test_s3_usb_read_result_after_disconnect_is_discarded(self):
        self.gui.network.set("device", "s3")
        self.connect_usb_fixture(SerialC5(model="ESP32-S3"))
        self.gui.read_c5()
        self.gui.http_thread.join(2)  # Leave the queued result for the GUI thread.
        self.assertFalse(self.gui.http_thread.is_alive())
        self.gui.controller.snapshot()[1].close()
        self.gui._http_results()
        self.assertIsNone(self.gui.baseline)
        self.assertIn("丢弃旧请求", self.gui.c5_status.get())

    def test_stale_result_discarded_after_switch_away_and_back(self):
        entered, release = threading.Event(), threading.Event()
        def delayed_read(_client):
            entered.set()
            release.wait(2)
            return example_config()
        with mock.patch("c5_config.C5Client.read", delayed_read):
            self.gui.read_c5()
            self.assertTrue(entered.wait(1))
            self.gui.network.set("device", "s3")
            self.gui.network.set("device", "c5")
            release.set()
            wait_for(lambda: not self.gui.http_busy, self.root.update)
        self.assertIsNone(self.gui.baseline)
        self.assertIn("丢弃旧请求", self.gui.c5_status.get())

    def test_s3_import_rejects_5g_and_backup_records_model_without_passwords(self):
        self.gui.network.set("device", "s3")
        config = example_config()
        self.gui._fill_c5(config)
        path = Path(self.temporary.name) / "s3-backup.json"
        self.gui.c5_forms["ap"].set("ap_password", "private-unused")
        with mock.patch("tcp2com_gui.filedialog.asksaveasfilename", return_value=str(path)):
            self.gui.backup_c5()
        backup = json.loads(path.read_text(encoding="utf-8"))
        self.assertEqual(backup["device"], "s3")
        self.assertNotIn("private-unused", path.read_text(encoding="utf-8"))
        path.write_text(json.dumps({"format": "c5-user-config-v1", "config": {**config, "band": 1, "ap_channel": 36}}), encoding="utf-8")
        with mock.patch("tcp2com_gui.filedialog.askopenfilename", return_value=str(path)), mock.patch.object(self.gui, "error") as error:
            self.gui.import_c5()
        error.assert_called_once()
        self.assertEqual(self.gui.c5_forms["common"].get("band"), 0)

    def test_save_sends_only_delta_and_clear_is_explicit(self):
        self.read_fixture()
        self.gui.c5_forms["common"].set("link_mode", 0)
        self.gui.c5_forms["shared"].set("clear_password", True)
        with mock.patch("tcp2com_gui.messagebox.askokcancel", return_value=True), mock.patch.object(self.gui, "_http") as http:
            self.gui.save_c5()
        http.assert_called_once_with("save", {"link_mode": 0, "shared": {"password": ""}})

    def test_http_worker_updates_main_thread_and_unknown_invalidates_baseline(self):
        with mock.patch("c5_config.C5Client.read", return_value=example_config()):
            self.gui.read_c5()
            wait_for(lambda: self.gui.baseline is not None, self.root.update)
        self.assertEqual(self.gui.baseline["config_source"], "nvs")
        self.gui.http_events.put(("save", self.gui.read_identity, None, SaveOutcomeUnknown("结果待确认")))
        wait_for(lambda: self.gui.baseline is None, self.root.update)
        self.assertIn("待确认", self.gui.c5_status.get())

    def test_apply_runtime_edits_and_minimize_keep_forwarding_until_close(self):
        self.gui.local.set("com", "loop://")
        self.gui.network.set("listen", "127.0.0.1")
        self.gui.network.set("port", str(free_port()))
        self.gui.start("start")
        wait_for(lambda: self.gui.controller.snapshot()[0] is not None, self.root.update)
        app = self.gui.controller.snapshot()[0]
        self.gui.root.iconify()
        self.gui.local.set("baud", "230400")
        wait_for(lambda: self.gui.applied.get() == "参数未应用", self.root.update)
        self.gui.save_profile()
        self.assertIs(self.gui.controller.snapshot()[0], app)
        self.assertEqual(app.args.baud, 1152000)
        self.assertFalse(app.close_event.is_set())
        self.gui.start("restart")
        wait_for(lambda: self.gui.controller.snapshot()[0] is not None and self.gui.controller.snapshot()[0] is not app,
                 self.root.update)
        self.assertEqual(self.gui.controller.snapshot()[0].args.baud, 230400)
        self.assertFalse(app.ser.is_open)

    def test_real_setup_api_read_save_and_restart_confirmation(self):
        server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), SetupHandler)
        server.status, server.delay, server.drop = 200, 0, False
        server.token = "0123456789abcdef-1"
        server.tokens = []
        server.body = json.dumps({**example_config(), "http_session": server.token}).encode()
        server.received, server.requests = [], 0
        thread = threading.Thread(target=server.serve_forever, kwargs={"poll_interval": .02}, daemon=True)
        thread.start()
        try:
            self.gui.config_url.set(f"http://127.0.0.1:{server.server_port}/")
            self.gui.read_c5()
            wait_for(lambda: self.gui.baseline is not None, self.root.update)
            self.gui.c5_forms["common"].set("mode", 2)
            server.body = b'{"saved":true,"restart":true}'
            with mock.patch("tcp2com_gui.messagebox.askokcancel", return_value=True):
                self.gui.save_c5()
            wait_for(lambda: "已写入 NVS" in self.gui.c5_status.get(), self.root.update)
            self.assertIsNone(self.gui.baseline)
            self.assertEqual(server.received, [("/api/config", "application/json", {"mode": 2})])
            self.assertEqual(server.tokens, [server.token])
        finally:
            server.shutdown()
            server.server_close()
            thread.join(1)

    def test_usb_stats_show_stale_and_offline_state(self):
        monitor = mock.Mock()
        monitor.port = "COM8"
        monitor.status.return_value = {"connected": False, "updated_at": time.monotonic() - 20,
                                       "fields": {"RSSI": "-58", "spi_error": "2"}}
        self.gui._usb_status(monitor, time.monotonic())
        self.assertIn("离线", self.gui.usb_status.get())
        self.assertIn("已过期", self.gui.usb_status.get())
        self.assertEqual(len(self.gui.usb_tree.get_children()), 2)

    def test_usb_apply_ignores_unapplied_invalid_network_parameters(self):
        self.gui.network.set("port", "invalid")
        self.gui.usb_form.set("usb_port", "missing://")
        with mock.patch.object(self.gui, "error") as error:
            self.gui.apply_usb()
        error.assert_not_called()
        wait_for(lambda: not self.gui.controller.snapshot()[4], self.root.update)
        self.assertIsNotNone(self.gui.controller.snapshot()[1])

    def connect_usb_fixture(self, device=None):
        device = device or SerialC5()
        patch = mock.patch("usb_diagnostics.serial.serial_for_url", return_value=device)
        patch.start()
        self.addCleanup(patch.stop)
        self.gui.config_transport.set("USB")
        self.gui.usb_form.set("usb_port", "COM8")
        self.gui.connect_c5_usb()
        wait_for(lambda: self.gui.controller.snapshot()[1] is not None
                 and self.gui.controller.snapshot()[1].status()["connected"], self.root.update)
        return device

    def test_usb_only_configuration_ignores_payload_settings_and_saves(self):
        self.gui.network.set("port", "invalid")
        self.gui.local.set("com", "COM8")  # not actually open: must not block USB-only setup
        device = self.connect_usb_fixture()
        self.gui.read_c5()
        wait_for(lambda: self.gui.baseline is not None, self.root.update)
        self.assertIsNone(self.gui.controller.snapshot()[0])
        self.assertEqual(self.gui.read_identity[0], "usb")
        self.assertEqual(str(self.gui.url_entry["state"]), "disabled")
        self.gui.c5_forms["common"].set("baud", "921600")
        with mock.patch("tcp2com_gui.messagebox.askokcancel", return_value=True):
            self.gui.save_c5()
        wait_for(lambda: "已写入 NVS" in self.gui.c5_status.get(), self.root.update)
        self.assertEqual(device.requests[-1]["patch"], {"baud": 921600})
        self.assertIsNone(self.gui.baseline)

    def test_usb_selection_or_transport_change_requires_another_read(self):
        self.connect_usb_fixture()
        self.gui.read_c5()
        wait_for(lambda: self.gui.baseline is not None, self.root.update)
        for change in (lambda: self.gui.usb_form.set("usb_port", "COM9"),
                       lambda: self.gui.config_transport.set("Wi-Fi HTTP")):
            change()
            with mock.patch.object(self.gui, "error") as error, mock.patch.object(self.gui, "_http") as send:
                self.gui.save_c5()
            self.assertIn("读取", str(error.call_args))
            send.assert_not_called()

    def test_usb_save_timeout_invalidates_read_without_replaying(self):
        device = self.connect_usb_fixture()
        self.gui.read_c5()
        wait_for(lambda: self.gui.baseline is not None, self.root.update)
        self.gui.c5_forms["ap"].set("ap_password", "private-gui-secret")
        device.drop = "save"
        with mock.patch("tcp2com_gui.messagebox.askokcancel", return_value=True), \
                mock.patch("tcp2com_gui.C5USBClient", side_effect=lambda m, **kw: C5USBClient(m, timeout=.1, **kw)):
            self.gui.save_c5()
        wait_for(lambda: "待确认" in self.gui.c5_status.get(), self.root.update)
        self.assertIsNone(self.gui.baseline)
        self.assertEqual(device.save_count, 1)
        self.assertEqual(self.gui.c5_forms["ap"].get("ap_password"), "")

    def test_usb_normal_mode_read_prompts_setup_without_enabling_save(self):
        device = SerialC5()
        device.setup = False
        self.connect_usb_fixture(device)
        with mock.patch("tcp2com_gui.C5USBClient", side_effect=lambda m, **kw: C5USBClient(m, timeout=.1, **kw)):
            self.gui.read_c5()
        wait_for(lambda: "超时" in self.gui.c5_status.get(), self.root.update)
        self.assertIn("配网", self.gui.c5_status.get())
        self.assertIsNone(self.gui.baseline)
        self.assertEqual(device.save_count, 0)

    def test_usb_setup_reentry_invalidates_gui_read_before_saving(self):
        device = self.connect_usb_fixture()
        self.gui.read_c5()
        wait_for(lambda: self.gui.baseline is not None, self.root.update)
        device.session += 1
        self.gui.c5_forms["common"].set("baud", "921600")
        with mock.patch("tcp2com_gui.messagebox.askokcancel", return_value=True):
            self.gui.save_c5()
        wait_for(lambda: "会话" in self.gui.c5_status.get(), self.root.update)
        self.assertIsNone(self.gui.baseline)
        self.assertEqual(device.save_count, 0)
        self.assertNotIn("save", [r["op"] for r in device.requests])

    def test_pending_configuration_prevents_usb_replacement_and_closes_cleanly(self):
        device = self.connect_usb_fixture()
        device.drop = "hello"
        self.gui.read_c5()
        wait_for(lambda: device.requests, self.root.update)
        original = self.gui.controller.snapshot()[1]
        with mock.patch.object(self.gui, "error") as error:
            self.gui.apply_usb()
            self.gui.start("start")
            self.gui.stop()
        self.assertEqual(error.call_count, 3)
        self.assertIs(original, self.gui.controller.snapshot()[1])
        self.gui.close()
        wait_for(lambda: self.gui.destroyed, self.root.update)
        self.assertFalse(original.thread.is_alive())

    def test_usb_transport_profile_roundtrip_without_device_write(self):
        self.gui.config_transport.set("USB")
        self.gui.usb_form.set("usb_port", "COM8")
        self.gui.save_profile()
        self.gui.config_transport.set("Wi-Fi HTTP")
        self.gui.load_profile()
        self.assertEqual(self.gui.config_transport.get(), "USB")
        self.assertEqual(self.gui.usb_form.get("usb_port"), "COM8")
        self.assertIsNone(self.gui.controller.snapshot()[1])

    def test_close_waits_for_inflight_http_without_worker_holding_tk(self):
        entered, release = threading.Event(), threading.Event()
        def delayed_read(_client):
            entered.set()
            release.wait(2)
            return example_config()
        with mock.patch("c5_config.C5Client.read", delayed_read):
            self.gui.read_c5()
            self.assertTrue(entered.wait(1))
            self.gui.close()
            self.root.update()
            self.assertFalse(self.gui.destroyed)
            self.assertTrue(self.gui.http_thread.is_alive())
            release.set()
            wait_for(lambda: self.gui.destroyed, self.root.update)
            self.assertFalse(self.gui.http_thread.is_alive())

    def test_dpi_layout_and_bounded_filtered_logs(self):
        self.root.tk.call("tk", "scaling", 2.0)
        self.root.deiconify()
        self.root.update()
        for i in range(3500):
            self.gui.logs.append(f"{i} USB example")
        self.gui.filter_text.set("3499")
        self.gui._logs()
        self.assertEqual(len(self.gui.logs), 3000)
        self.assertIn("3499", self.gui.log_text.get("1.0", "end"))
        self.assertNotIn("3498", self.gui.log_text.get("1.0", "end"))
        self.assertLess(self.gui.tabs.winfo_y(), self.root.winfo_height())
        self.assertGreater(self.gui.tabs.winfo_height(), 150)


if __name__ == "__main__":
    unittest.main()
