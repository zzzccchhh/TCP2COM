import copy
import unittest
from c5_config import ConfigError, changed_config, public_config, validate_patch
from test_c5_config import wbr_config
from gui_state import clean_state


class C5FlowTests(unittest.TestCase):
    def test_new_mode_and_pin_selection(self):
        for pin in range(1, 5):
            base = wbr_config(link_mode=0, flow_pin=0, reset_pin=0)
            edited = {**base, "link_mode": 3, "flow_pin": pin}
            self.assertEqual(public_config(edited)["flow_pin"], pin)
            patch = changed_config(base, edited)
            self.assertEqual(patch, {"link_mode": 3, "flow_pin": pin})
            validate_patch(patch, "c5", base)
            with self.assertRaises(ConfigError):
                public_config({**edited, "reset_pin": pin})
        for pin in (0, 5, True, "1"):
            with self.assertRaises(ConfigError):
                public_config(wbr_config(link_mode=3, flow_pin=pin))

    def test_old_firmware_and_s3_remain_compatible(self):
        self.assertEqual(public_config(wbr_config(link_mode=2))["link_mode"], 2)
        self.assertEqual(public_config(wbr_config(device="s3", link_mode=2), "s3")["link_mode"], 2)
        with self.assertRaises(ConfigError):
            validate_patch({"link_mode": 3, "flow_pin": 1}, "c5", wbr_config())
        with self.assertRaises(ConfigError):
            public_config(wbr_config(link_mode=2, flow_pin=1))
        with self.assertRaises(ConfigError):
            public_config(wbr_config(device="s3", link_mode=3, flow_pin=1), "s3")

    def test_recovery_sends_explicit_interface_even_unchanged(self):
        base = wbr_config(link_mode=3, flow_pin=2, migration_requires_setup=True)
        self.assertEqual(changed_config(base, copy.deepcopy(base)), {"link_mode": 3, "flow_pin": 2})
        base["link_mode"] = 0
        self.assertEqual(changed_config(base, base), {"link_mode": 0})

    def test_drafts_keep_c5_flow_but_reject_s3_mode(self):
        state, _ = clean_state({"version": 1, "device_drafts": {
            "c5": {"common": {"link_mode": 3, "flow_pin": 4}},
            "s3": {"common": {"link_mode": 3, "flow_pin": 4}}}})
        self.assertEqual(state["device_drafts"]["c5"]["common"], {"link_mode": 3, "flow_pin": 4})
        self.assertEqual(state["device_drafts"]["s3"]["common"], {})
