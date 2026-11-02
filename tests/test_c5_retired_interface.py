import copy
import unittest
from c5_config import ConfigError, changed_config, public_config, public_draft_config, validate_patch
from gui_state import clean_state
from web_service import draft_to_config
from test_c5_config import wbr_config


class RetiredC5InterfaceTests(unittest.TestCase):
    def test_old_mode_is_rejected_with_or_without_capability_fields(self):
        for extra in ({}, {"flow_pin": 1}):
            with self.assertRaisesRegex(ConfigError, "升级固件"):
                public_config(wbr_config(link_mode=2, **extra))
        for mode in (2, -1, True, "2"):
            with self.assertRaises(ConfigError):
                validate_patch({"link_mode": mode}, "c5")
        self.assertEqual(public_config(wbr_config(device="s3", link_mode=2), "s3")["link_mode"], 2)

    def test_old_backup_keeps_network_but_requires_explicit_selection(self):
        original = wbr_config(link_mode=2, remote_port=9000)
        original["ap_password"] = "never-import-this"
        draft = public_draft_config(original)
        self.assertEqual((draft["link_mode"], draft["remote_port"]), (-1, 9000))
        self.assertNotIn("ap_password", draft)
        self.assertEqual(original["link_mode"], 2)
        baseline = wbr_config(link_mode=3, flow_pin=1)
        with self.assertRaises(ConfigError):
            changed_config(baseline, draft)
        draft["link_mode"] = 0
        self.assertEqual(changed_config(baseline, draft), {"link_mode": 0, "remote_port": 9000})

    def test_state_migration_is_stable_and_does_not_change_s3(self):
        for old_mode in (2, "2"):
            old = {"version": 1, "device_drafts": {
                "c5": {"common": {"link_mode": old_mode, "remote_port": "9000"}},
                "s3": {"common": {"link_mode": 2}}}}
            untouched = copy.deepcopy(old)
            migrated, warnings = clean_state(old)
            self.assertTrue(warnings)
            self.assertEqual(old, untouched)
            self.assertEqual(migrated["device_drafts"]["c5"]["common"], {"link_mode": -1, "remote_port": "9000"})
            self.assertEqual(migrated["device_drafts"]["s3"]["common"]["link_mode"], 2)
            again, warnings = clean_state(migrated)
            self.assertFalse(warnings)
            self.assertEqual(again, migrated)
            self.assertEqual(draft_to_config(old["device_drafts"]["c5"], "c5")["link_mode"], -1)
