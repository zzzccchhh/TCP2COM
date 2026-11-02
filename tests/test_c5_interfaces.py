"""Current C5 schema, revision ownership and network settings over HTTP/USB."""

import copy
import json
import sys
from pathlib import Path
import unittest
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from c5_config import C5Client, ConfigError, NETWORK_FIELDS, changed_config, public_config
from c5_usb_config import C5USBClient
from gui_state import clean_state
import test_c5_config as config_tests
from test_usb_config import SerialC5, wait_for
from usb_diagnostics import USBMonitor, parse_stats


class CurrentC5ModelTests(unittest.TestCase):
    def test_three_links_and_three_roles_preserve_all_new_fields_and_status(self):
        for link in (0, 4, 3):
            for role in range(3):
                document = config_tests.wbr_config(link_mode=link, flow_pin=1, net_role=role)
                result = public_config(document)
                self.assertEqual(result, {k: v for k, v in document.items() if k != "config_source"})
                self.assertEqual(changed_config(document, result), {})

    def test_full_patch_keeps_new_fields_and_excludes_revision_and_status(self):
        baseline = config_tests.wbr_config(flow_pin=0)
        changes = dict(link_mode=3, flow_pin=1, band=1, ap_channel=36, net_role=2, auto_reconnect=False,
                       remote_ip="192.168.137.99", remote_port=9000, local_port=0,
                       reconnect_ms=60000, connect_timeout_ms=100, up_kb=2048, down_kb=2048)
        edited = {**baseline, **changes, "revision": 999, "unsaved": True}
        self.assertEqual(changed_config(baseline, edited), changes)
        self.assertEqual(baseline["revision"], 23)

    def test_invalid_network_values_are_rejected_before_transmission(self):
        document = config_tests.wbr_config()
        cases = (("net_role", 3), ("net_role", True), ("auto_reconnect", 1),
                 ("remote_ip", "0.0.0.0"), ("remote_ip", "192.168.001.1"),
                 ("remote_ip", "127.0.0.1"), ("remote_ip", "224.0.0.1"),
                 ("remote_port", 0), ("remote_port", 65536), ("local_port", -1),
                 ("reconnect_ms", 99), ("connect_timeout_ms", 60001),
                 ("up_kb", 63), ("up_kb", 6144), ("down_kb", 2049), ("up_kb", True))
        for key, value in cases:
            with self.subTest(key=key, value=value), self.assertRaises(ConfigError):
                public_config({**document, key: value})
        public_config({**document, "up_kb": 4096, "down_kb": 2048, "local_port": 0})

    def test_current_schema_requires_revision_complete_fields_and_correct_model(self):
        for field in ("revision", *NETWORK_FIELDS):
            broken = config_tests.wbr_config()
            del broken[field]
            with self.subTest(field=field), self.assertRaises(ConfigError):
                public_config(broken)
        for changes in ({"revision": True}, {"revision": -1}, {"revision": 0x100000000},
                        {"saved_revision": "1"}, {"unsaved": 1}, {"config_api": "bridge-1"},
                        {"config_api": "c5-wbr-99"}):
            with self.subTest(changes=changes), self.assertRaises(ConfigError):
                public_config(config_tests.wbr_config(**changes))
        with self.assertRaises(ConfigError):
            public_config(config_tests.wbr_config(), "s3")

    def test_drafts_keep_c5_link_3_network_fields_but_never_identity_or_secrets(self):
        common = dict(link_mode=3, flow_pin=1, band=1, net_role=2, auto_reconnect=False,
                      remote_ip="192.168.137.99", remote_port="9000", local_port="0",
                      reconnect_ms="1000", connect_timeout_ms="10000", up_kb="4096", down_kb="1024",
                      revision=100, http_session="secret", config_api="c5-wbr-1")
        state, warnings = clean_state({"version": 1, "device_drafts": {"c5": {"common": common}}})
        self.assertFalse(warnings)
        result = state["device_drafts"]["c5"]["common"]
        self.assertEqual(result["link_mode"], 3)
        self.assertEqual(result["remote_port"], "9000")
        for key in ("revision", "http_session", "config_api"):
            self.assertNotIn(key, result)

    def test_current_usb_stats_include_current_links_and_udp_totals(self):
        for link in ("UART0", "UART-GPIO-FLOW", "SPI-WBR"):
            fields = parse_stats(f"I stats: link={link} SPI rx/tx=12/34 protocol_errors=5 (network counters include UDP)")
            self.assertEqual(fields, {"link": link, "spi.rx": "12", "spi.tx": "34", "protocol_errors": "5"})


class CurrentC5HTTPTests(unittest.TestCase):
    # Reuse only setup/cleanup; inherited tests would exercise the old schema.
    setUp = config_tests.ConfigHTTPTests.setUp
    tearDown = config_tests.ConfigHTTPTests.tearDown

    def read_current(self, **changes):
        self.server.body = json.dumps({**config_tests.wbr_config(**changes),
                                       "http_session": self.server.token}).encode()
        return self.client.read()

    def test_reconstructed_http_client_uses_c5_header_and_current_revision(self):
        baseline = self.read_current(link_mode=3, flow_pin=1, band=1, ap_channel=36)
        client = C5Client(f"http://127.0.0.1:{self.server.server_port}/", device="c5")
        client.bind_session(self.client.http_session)
        client.baseline = copy.deepcopy(baseline)
        self.server.body = b'{"saved":true,"restart":true}'
        patch = dict(net_role=2, remote_ip="192.168.137.100", remote_port=9000, local_port=6061,
                     auto_reconnect=False, reconnect_ms=2000, connect_timeout_ms=5000, up_kb=2048, down_kb=512)
        client.save(patch)
        self.assertEqual(self.server.received[-1][2], {**patch, "revision": 23})
        self.assertEqual(self.server.tokens, [self.server.token])

    def test_invalid_c5_frequency_or_queues_do_not_post(self):
        self.read_current()
        for patch in ({"band": 1}, {"up_kb": 6144}, {"net_role": 3}, {"remote_port": 0}):
            with self.subTest(patch=patch), self.assertRaises(ConfigError):
                self.client.save(patch)
        self.assertEqual(self.server.received, [])

    def test_rejected_current_read_clears_prior_session_and_cannot_save(self):
        self.read_current()
        bad = config_tests.wbr_config()
        del bad["revision"]
        self.server.body = json.dumps({**bad, "http_session": self.server.token}).encode()
        with self.assertRaises(ConfigError):
            self.client.read()
        self.assertIsNone(self.client.baseline)
        self.assertIsNone(self.client.http_session)
        with self.assertRaises(ConfigError):
            self.client.save({"net_role": 1})
        self.assertEqual(self.server.received, [])


class CurrentC5USBTests(unittest.TestCase):
    def setUp(self):
        self.device = SerialC5()
        self.device.config = config_tests.wbr_config(link_mode=3, flow_pin=1, band=1, ap_channel=149)
        patch = mock.patch("usb_diagnostics.serial.serial_for_url", return_value=self.device)
        patch.start()
        self.addCleanup(patch.stop)
        self.monitor = USBMonitor("COM8", 115200, lambda _: None, device="c5")
        self.monitor.start()
        self.addCleanup(self.monitor.close)
        wait_for(lambda: self.monitor.status()["connected"])

    def test_reconstructed_usb_save_preserves_revision_and_network_patch(self):
        baseline = C5USBClient(self.monitor, device="c5").read()
        client = C5USBClient(self.monitor, device="c5")
        client.baseline = baseline
        patch = dict(link_mode=4, net_role=1, remote_port=9000, up_kb=2048)
        client.save(patch)
        self.assertEqual(self.device.save_count, 1)
        self.assertEqual([r["patch"] for r in self.device.requests[-2:]], [{**patch, "revision": 23}] * 2)
        self.assertTrue(self.device.writes[-1].startswith(b"\nC5CFG "))

    def test_mismatched_model_and_missing_revision_never_save(self):
        with self.assertRaises(ConfigError):
            C5USBClient(self.monitor, device="s3").read()
        del self.device.config["revision"]
        with self.assertRaises(ConfigError):
            C5USBClient(self.monitor, device="c5").read()
        self.assertEqual(self.device.save_count, 0)


if __name__ == "__main__":
    unittest.main()
