"""Last GUI settings are local drafts, not permission to reconnect or save ESP."""

import copy
import json
from pathlib import Path
import shutil
import tempfile
import unittest
from unittest import mock

from gui_state import (MAX_BYTES, clean_state, load_state, profile_selection,
                       restore_profile_path, save_state)
from test_gui import tk, wait_for
from test_c5_config import example_config
if tk is not None:
    from tcp2com_gui import BridgeGUI


def snapshot():
    return {"version": 1, "profile": {"path": "missing.json", "name": "上次连接"},
            "forms": {"network": {"device": "s3", "mode": "udp", "port": "", "host": "192.168."},
                      "local": {"com": "COM99", "baud": "5."},
                      "advanced": {"verbose": True}, "usb_form": {"usb_port": "COM88"}},
            "config_transport": "usb", "config_url": "http://192.168.",
            "device_drafts": {"s3": {"common": {"link_mode": 2, "mode": 2, "band": 0},
                                     "ap": {"ap_ssid": "my AP"}}}}


class StateFileTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.path = Path(self.temporary.name) / "last.json"

    def test_roundtrip_keeps_incomplete_text_and_inactive_fields(self):
        saved = save_state(self.path, snapshot())
        loaded, warning = load_state(self.path)
        self.assertEqual(loaded, saved)
        self.assertIsNone(warning)
        self.assertEqual(loaded["forms"]["local"]["baud"], "5.")
        self.assertEqual(loaded["forms"]["network"]["port"], "")
        self.assertEqual(loaded["forms"]["local"]["com"], "COM99")
        self.assertEqual(loaded["device_drafts"]["s3"]["common"]["link_mode"], 2)

    def test_passwords_clear_intents_sessions_and_unknown_fields_never_persist(self):
        document = snapshot()
        document.update(baseline={"password": "private-baseline"}, read_identity=["private-session"])
        draft = document["device_drafts"]["s3"]
        draft["ap"].update(ap_password="private-ap", http_session="private-token")
        draft["pc"] = {"ssid": "PC AP", "password": "private-pc", "clear_password": True}
        draft["common"].update(revision=998, config_source="private-source")
        document["forms"]["usb_form"]["boot"] = "private-boot"
        save_state(self.path, document)
        text = self.path.read_text(encoding="utf-8")
        self.assertNotIn("private-", text)
        for field in ("password", "clear_password", "revision", "read_identity", "baseline", "http_session"):
            self.assertNotIn(field, text)

    def test_bad_fields_do_not_discard_valid_settings_or_cross_device_capabilities(self):
        document = snapshot()
        document["forms"]["network"].update(device="unknown", port="9000")
        document["device_drafts"]["s3"]["common"]["band"] = 1
        document["device_drafts"]["c5"] = {"common": {"link_mode": 2, "band": 1}}
        self.path.write_text(json.dumps(document), encoding="utf-8")
        loaded, warning = load_state(self.path)
        self.assertTrue(warning)
        self.assertNotIn("device", loaded["forms"]["network"])
        self.assertEqual(loaded["forms"]["network"]["port"], "9000")
        self.assertNotIn("band", loaded["device_drafts"]["s3"]["common"])
        self.assertEqual(loaded["device_drafts"]["c5"]["common"]["link_mode"], 2)
        self.assertEqual(loaded["device_drafts"]["c5"]["common"]["band"], 1)

    def test_missing_damaged_duplicate_new_version_and_oversized_files(self):
        self.assertEqual(load_state(self.path), (None, None))
        for text in ("{broken", '{"version":1,"version":1}', '{"version":2}',
                     '{"version":true}', "x" * (MAX_BYTES + 1)):
            with self.subTest(text=text[:30]):
                self.path.write_text(text, encoding="utf-8")
                loaded, warning = load_state(self.path)
                self.assertIsNone(loaded)
                self.assertTrue(warning)

    def test_failed_atomic_replace_preserves_previous_file_and_cleans_temporary(self):
        save_state(self.path, snapshot())
        before = self.path.read_bytes()
        changed = snapshot()
        changed["forms"]["network"]["mode"] = "tcp-client"
        with mock.patch("gui_support.os.replace", side_effect=PermissionError), self.assertRaises(OSError):
            save_state(self.path, changed)
        self.assertEqual(self.path.read_bytes(), before)
        self.assertEqual(list(Path(self.temporary.name).iterdir()), [self.path])

    def test_legacy_capture_state_drops_removed_fields_and_preserves_other_drafts(self):
        document = snapshot()
        document["output"] = "capture"
        document["forms"]["local"]["capture"] = "old-recording.bin"
        self.path.write_text(json.dumps(document), encoding="utf-8")
        before = self.path.read_bytes()
        loaded, warning = load_state(self.path)
        self.assertIn("已移除", warning)
        self.assertEqual(loaded["forms"]["local"]["com"], "COM99")
        self.assertEqual(loaded["forms"]["network"], document["forms"]["network"])
        self.assertNotIn("capture", loaded["forms"]["local"])
        self.assertNotIn("output", loaded)
        self.assertEqual(self.path.read_bytes(), before)
        save_state(self.path, document)
        self.assertNotIn("output", json.loads(self.path.read_text(encoding="utf-8")))


class ProfilePathTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.original = Path(self.temporary.name) / "原目录"
        self.copied = Path(self.temporary.name) / "新电脑 中文目录"
        self.original.mkdir()
        self.copied.mkdir()
        self.default = self.copied / "tcp2com_config.json"
        self.default.write_text('{"profiles":{}}', encoding="utf-8")

    def test_bundled_nested_reference_moves_with_program_independently_of_cwd(self):
        selected = self.original / "配置" / "custom.json"
        selection = profile_selection(str(selected), "我的配置", self.original)
        self.assertEqual(selection, {"path": "配置/custom.json", "name": "我的配置", "path_base": "program"})
        with mock.patch("os.getcwd", return_value=str(self.original)):
            restored, notice = restore_profile_path(selection, self.copied, self.default)
        self.assertEqual(restored, self.copied / "配置" / "custom.json")
        self.assertIsNone(notice)

    def test_explicit_external_default_filename_is_preserved_even_when_missing(self):
        external = self.original / self.default.name
        selection = profile_selection(str(external), "external", self.copied)
        self.assertEqual(selection["path_base"], "absolute")
        restored, notice = restore_profile_path(selection, self.copied, self.default)
        self.assertEqual(restored, external)
        self.assertIsNone(notice)
        external.write_text('{"profiles":{}}', encoding="utf-8")
        self.assertEqual(restore_profile_path(selection, self.copied, self.default)[0], external)

    def test_legacy_default_prefers_copied_file_even_if_original_still_exists(self):
        original = self.original / self.default.name
        original.write_text('{"profiles":{"old":{}}}', encoding="utf-8")
        restored, notice = restore_profile_path({"path": str(original)}, self.copied, self.default)
        self.assertEqual(restored, self.default)
        self.assertIn("迁移", notice)

    def test_missing_legacy_path_finds_same_name_then_local_default(self):
        old_path = self.original / "custom.json"
        local = self.copied / old_path.name
        local.write_text('{"profiles":{}}', encoding="utf-8")
        selection = {"path": str(old_path)}
        restored, notice = restore_profile_path(selection, self.copied, self.default)
        self.assertEqual(restored, local)
        self.assertIn("同名", notice)
        local.unlink()
        restored, notice = restore_profile_path(selection, self.copied, self.default)
        self.assertEqual(restored, self.default)
        self.assertIn("默认配置", notice)

    def test_legacy_windows_path_on_another_drive_recovers_local_default(self):
        if not self.copied.drive:
            self.skipTest("Windows drive letters")
        drive = "Z:" if self.copied.drive.upper() != "Z:" else "Y:"
        old_path = Path(drive + "/旧电脑/WiFiBridge/" + self.default.name)
        restored, notice = restore_profile_path({"path": str(old_path)}, self.copied, self.default)
        self.assertEqual(restored, self.default)
        self.assertIn("迁移", notice)

    def test_legacy_existing_external_file_keeps_its_location(self):
        external = self.original / "external.json"
        external.write_text('{"profiles":{}}', encoding="utf-8")
        self.assertEqual(restore_profile_path({"path": str(external)}, self.copied, self.default), (external, None))

    def test_program_reference_cannot_escape_and_external_reference_must_be_absolute(self):
        for selection in ({"path": "../outside.json", "path_base": "program"},
                          {"path": str(self.original / "outside.json"), "path_base": "program"},
                          {"path": "relative.json", "path_base": "absolute"}):
            with self.subTest(selection=selection), self.assertRaises(ValueError):
                restore_profile_path(selection, self.copied, self.default)


class GUIStateTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.profile = Path(self.temporary.name) / "profiles.json"
        self.state = self.profile.parent / "tcp2com_last_state.json"
        self.gui = None
        self.root = None
        if tk is None:
            self.temporary.cleanup()
            self.skipTest("Tk unavailable")
        self.open_gui()

    def open_gui(self):
        try:
            self.root = tk.Tk()
        except tk.TclError:
            self.temporary.cleanup()
            self.skipTest("Tk display unavailable")
        self.root.withdraw()
        self.root.geometry("+30000+30000")
        self.gui = BridgeGUI(self.root, self.profile)
        self.root.update()

    def close_gui(self):
        if self.gui:
            self.gui.close()
            wait_for(lambda: self.gui.destroyed, self.root.update, timeout=6)
            self.gui = None
            self.root = None

    def tearDown(self):
        self.close_gui()
        self.temporary.cleanup()

    def test_restart_restores_all_connection_modes_and_keeps_inactive_parameters(self):
        for index, mode in enumerate(("tcp-client", "tcp-server", "udp")):
            self.gui.network.set("device", "s3")
            self.gui.network.set("mode", mode)
            self.gui.network.set("host", "192.168.")
            self.gui.network.set("port", "unfinished")
            self.gui.network.set("peer_port", "7000")
            self.gui.local.set("com", "COM99")
            self.gui.local.set("baud", "5.")
            self.gui.usb_form.set("usb_port", "COM88")
            self.gui.usb_form.set("usb_log", "last-usb.log")
            self.gui.config_url.set("http://192.168.")
            self.gui.config_transport.set("USB" if index % 2 == 0 else "Wi-Fi HTTP")
            self.close_gui()  # Flush even when the debounce hasn't fired.
            with mock.patch("tcp2com_gui.BridgeController.submit", side_effect=AssertionError("auto start")), \
                    mock.patch("usb_diagnostics.serial.serial_for_url", side_effect=AssertionError("auto USB")), \
                    mock.patch("c5_config.C5Client.save", side_effect=AssertionError("auto save")):
                self.open_gui()
                self.assertEqual(self.gui.network.get("device"), "s3")
                self.assertEqual(self.gui.network.get("mode"), mode)
                self.assertEqual(self.gui.network.get("port"), "unfinished")
                self.assertEqual(self.gui.local.get("com"), "COM99")
                self.assertEqual(self.gui.local.get("baud"), "5.")
                self.assertEqual(self.gui.usb_form.get("usb_port"), "COM88")
                self.assertEqual(self.gui.config_transport.get(), "USB" if index % 2 == 0 else "Wi-Fi HTTP")
                self.assertIsNone(self.gui.baseline)
                self.assertIsNone(self.gui.controller.snapshot()[0])
                self.assertIsNone(self.gui.controller.snapshot()[1])

    def test_old_capture_selection_reports_removal_without_switching_device_or_opening_ports(self):
        self.close_gui()
        shipped = Path(__file__).resolve().parents[1] / "tcp2com_config.json"
        self.profile.write_bytes(shipped.read_bytes())
        document = snapshot()
        document["profile"] = {"path": self.profile.name, "name": "s3-capture", "path_base": "program"}
        recording = self.profile.parent / "old-recording.bin"
        recording.write_bytes(b"existing data")
        document["output"] = "capture"
        document["forms"]["local"]["capture"] = str(recording)
        self.state.write_text(json.dumps(document), encoding="utf-8")
        before = self.state.read_bytes()
        with mock.patch("tcp2com_gui.BridgeController.submit", side_effect=AssertionError("auto start")), \
                mock.patch("usb_diagnostics.serial.serial_for_url", side_effect=AssertionError("auto USB")):
            self.open_gui()
        self.assertEqual(self.gui.network.get("device"), "s3")
        self.assertEqual(self.gui.local.get("com"), "COM99")
        self.assertIn("已移除", self.gui.state_status.get())
        self.assertNotIn("capture", self.gui._state_snapshot()["forms"]["local"])
        self.assertNotIn("output", self.gui._state_snapshot())
        self.assertEqual(self.state.read_bytes(), before)
        self.assertEqual(recording.read_bytes(), b"existing data")

    def test_debounce_saves_once_without_overwriting_old_profiles(self):
        self.profile.write_text('{"profiles":{}}', encoding="utf-8")
        original = self.profile.read_bytes()
        with mock.patch("tcp2com_gui.save_state", wraps=save_state) as writer:
            for value in ("8081", "8082", "8083"):
                self.gui.network.set("port", value)
            wait_for(lambda: self.state.exists(), self.root.update)
            self.assertEqual(writer.call_count, 1)
        saved, _ = load_state(self.state)
        self.assertEqual(saved["forms"]["network"]["port"], "8083")
        self.assertNotIn("profile", saved)
        self.assertEqual(self.profile.read_bytes(), original)

    def test_existing_state_restores_without_reading_legacy_profile(self):
        self.close_gui()
        document = snapshot()
        document["profile"] = {"path": "missing.json", "name": "old", "path_base": "program"}
        self.state.write_text(json.dumps(document), encoding="utf-8")
        before = self.state.read_bytes()
        with mock.patch("tcp2com_gui.load_profile", side_effect=AssertionError("unexpected profile read")):
            self.open_gui()
        self.assertEqual(self.gui.network.get("mode"), "udp")
        self.assertEqual(self.gui.local.get("com"), "COM99")
        self.assertEqual(self.state.read_bytes(), before)
        self.assertNotIn("profile", self.gui._state_snapshot())

    def test_migrates_default_profile_once_when_state_missing(self):
        self.close_gui()
        self.state.unlink()
        self.profile.write_text(json.dumps({"profiles": {"c5-spi-tcp": {
            "device": "s3", "mode": "tcp-client", "host": "192.168.4.1",
            "port": 8123, "com": "COM5", "baud": 1152000}}}), encoding="utf-8")
        original = self.profile.read_bytes()
        with mock.patch("tcp2com_gui.BridgeController.submit", side_effect=AssertionError("auto start")):
            self.open_gui()
        self.assertEqual(self.gui.network.get("device"), "s3")
        self.assertEqual(self.gui.network.get("port"), "8123")
        self.assertIn("迁移", self.gui.state_status.get())
        self.assertTrue(self.state.exists())
        self.assertEqual(self.profile.read_bytes(), original)
        self.close_gui()
        self.profile.unlink()
        self.open_gui()
        self.assertEqual(self.gui.network.get("port"), "8123")

    def test_device_drafts_are_separate_and_require_a_fresh_read(self):
        self.gui.c5_forms["common"].set("link_mode", 2)
        self.gui.c5_forms["common"].set("net_role", 2)
        self.gui.c5_forms["common"].set("remote_port", "9000")
        self.gui.c5_forms["common"].set("up_kb", "2048")
        self.gui.c5_forms["common"].set("band", 1)
        self.gui.c5_forms["common"].set("mode", 1)
        self.gui.c5_forms["ap"].set("ap_ssid", "C5 draft")
        self.gui.network.set("device", "s3")
        self.gui.c5_forms["common"].set("link_mode", 2)
        self.gui.c5_forms["common"].set("mode", 2)
        self.gui.c5_forms["ap"].set("ap_ssid", "S3 draft")
        self.gui.c5_forms["ap"].set("ap_password", "never-store-ap")
        self.gui.c5_forms["pc"].set("password", "never-store-sta")
        self.gui.c5_forms["pc"].set("clear_password", True)
        self.close_gui()
        text = self.state.read_text(encoding="utf-8")
        self.assertNotIn("never-store", text)
        self.assertNotIn("clear_password", text)
        self.open_gui()
        self.assertEqual(self.gui.c5_forms["common"].get("link_mode"), 2)
        self.assertEqual(self.gui.c5_forms["common"].get("mode"), 2)
        self.assertEqual(self.gui.c5_forms["ap"].get("ap_ssid"), "S3 draft")
        self.assertEqual(self.gui.c5_forms["ap"].get("ap_password"), "")
        self.assertFalse(self.gui.c5_forms["pc"].get("clear_password"))
        self.assertIsNone(self.gui.read_identity)
        self.assertEqual(str(self.gui.c5_buttons[2]["state"]), "disabled")
        with mock.patch.object(self.gui, "error") as error, mock.patch.object(self.gui, "_http") as request:
            self.gui.save_c5()
            error.assert_called_once()
            request.assert_not_called()
        self.gui.network.set("device", "c5")
        self.assertEqual(self.gui.c5_forms["common"].get("link_mode"), 2)
        self.assertEqual(self.gui.c5_forms["common"].get("net_role"), 2)
        self.assertEqual(self.gui.c5_forms["common"].get("remote_port"), "9000")
        self.assertEqual(self.gui.c5_forms["common"].get("up_kb"), "2048")
        self.assertEqual(self.gui.c5_forms["common"].get("band"), 1)
        self.assertEqual(self.gui.c5_forms["ap"].get("ap_ssid"), "C5 draft")
        self.gui.network.set("device", "s3")
        self.assertEqual(self.gui.c5_forms["common"].get("link_mode"), 2)

    def test_fresh_device_read_does_not_erase_saved_draft_or_restore_session(self):
        self.gui.c5_forms["common"].set("baud", "draft-incomplete")
        draft = copy.deepcopy(self.gui._device_drafts["c5"])
        self.gui.baseline = example_config()
        self.gui._fill_c5(self.gui.baseline)
        self.assertEqual(self.gui.c5_forms["common"].get("baud"), "1152000")
        self.assertEqual(self.gui._device_drafts["c5"], draft)
        self.gui.restore_device_draft()
        self.assertEqual(self.gui.c5_forms["common"].get("baud"), "draft-incomplete")
        self.assertIsNone(self.gui.read_identity)
        self.gui.undo_c5()
        self.assertEqual(self.gui._device_drafts["c5"]["common"]["baud"], "1152000")

    def test_missing_profile_and_bad_state_fall_back_without_opening_connections(self):
        self.close_gui()
        save_state(self.state, snapshot())
        self.open_gui()
        self.assertEqual(self.gui.network.get("mode"), "udp")
        self.assertEqual(self.gui.c5_forms["common"].get("link_mode"), 2)
        self.close_gui()
        self.state.write_text("invalid json", encoding="utf-8")
        self.open_gui()
        self.assertIn("无法读取", self.gui.state_status.get())
        self.assertEqual(self.gui.network.get("device"), "c5")
        self.assertEqual(self.state.read_text(encoding="utf-8"), "invalid json")

    def test_write_failure_does_not_block_close_or_leave_pending_timer(self):
        self.gui.network.set("mode", "udp")
        with mock.patch("tcp2com_gui.save_state", side_effect=PermissionError):
            self.gui._save_last_state()
            self.assertIn("无法保存", self.gui.state_status.get())
            self.assertIsNone(self.gui._state_job)
            self.close_gui()


if __name__ == "__main__":
    unittest.main()
