import copy
import unittest
from c5_config import ConfigError, public_config, public_draft_config, changed_config, validate_patch
from test_c5_config import wbr_config


class C5SPIWriteTests(unittest.TestCase):
    def test_mode4_roundtrip_and_s3_isolation(self):
        config = wbr_config(link_mode=4, flow_pin=1, reset_pin=1)
        self.assertEqual(public_config(config)["link_mode"], 4)
        self.assertEqual(changed_config(config, copy.deepcopy(config)), {})
        with self.assertRaises(ConfigError):
            public_config(wbr_config(device="s3", link_mode=4), "s3")

    def test_retired_spi_backups_require_selection(self):
        old = wbr_config(link_mode=1, remote_port=9123)
        with self.assertRaises(ConfigError):
            public_config(old)
        draft = public_draft_config(old)
        self.assertEqual((draft["link_mode"], draft["remote_port"]), (-1, 9123))

    def test_old_firmware_never_receives_new_interface_number(self):
        old = wbr_config(link_mode=0, config_api="c5-wbr-1")
        edited = {**old, "link_mode": 4}
        with self.assertRaises(ConfigError):
            changed_config(old, edited)
        with self.assertRaises(ConfigError):
            validate_patch({"link_mode": 4}, "c5", old)

    def test_old_spi_8081_backup_keeps_dormant_reset_and_network(self):
        for role in (0, 1):
            with self.subTest(role=role):
                old = wbr_config(config_api="c5-wbr-1", link_mode=1, net_role=role,
                                 reset_pin=1, flow_pin=1, tcp_port=8081, local_port=8081)
                untouched = copy.deepcopy(old)
                draft = public_draft_config(old)
                self.assertEqual(old, untouched)
                self.assertEqual((draft["link_mode"], draft["reset_pin"], draft["flow_pin"]), (-1, 1, 1))
                self.assertEqual((draft["tcp_port"], draft["local_port"], draft["net_role"]), (8081, 8081, role))
                base = wbr_config(link_mode=4, reset_pin=0, flow_pin=1)
                with self.assertRaises(ConfigError):
                    changed_config(base, draft)
                draft["link_mode"] = 4
                changed_config(base, draft)  # Reset/flow selections remain inactive in SPI.
                for link in (0, 3):
                    draft["link_mode"] = link
                    with self.assertRaises(ConfigError):
                        changed_config(base, draft)
                draft.update(link_mode=0, reset_pin=0)
                changed_config(base, draft)
