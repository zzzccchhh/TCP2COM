"""S3 capabilities, actual transports and firmware-compatible setup behavior."""
import contextlib
import io
from pathlib import Path
import socket
import sys
import unittest
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from c5_config import C5Client, ConfigError, SaveOutcomeUnknown, public_config, changed_config
from c5_usb_config import C5USBClient
from gui_support import load_profile, settings_to_args
from usb_diagnostics import USBMonitor
import tcp2com
import test_c5_config as config_tests
from test_usb_config import SerialC5, wait_for
from test_c5_bridge import QuietUI, free_port, receive


class S3CapabilityTests(unittest.TestCase):
    def test_s3_profiles_match_factory_and_preserve_c5(self):
        path = Path(__file__).resolve().parents[1] / "tcp2com_config.json"
        factory = load_profile(path, "s3-client")
        self.assertEqual((factory["device"], factory["mode"], factory["host"], factory["port"]),
                         ("s3", "tcp-client", "192.168.4.1", 8080))
        for name in ("s3-client", "s3-spi-tcp", "s3-spi-udp", "s3-capture"):
            self.assertTrue(settings_to_args(load_profile(path, name)).no_reset_ui)
        self.assertEqual(load_profile(path, "c5-spi-tcp")["device"], "c5")

    def test_s3_rejects_5g_and_auto_in_models_and_import_snapshots(self):
        base = config_tests.example_config()
        for band, channel in ((1, 36), (2, 6)):
            other = {**base, "band": band, "ap_channel": channel}
            public_config(other)  # Existing C5 behavior.
            with self.assertRaises(ConfigError):
                public_config(other, device="s3")
            with self.assertRaises(ConfigError):
                changed_config(base, other, device="s3")

    def test_s3_cli_usb_rules_and_setup_hint(self):
        args = tcp2com.parse_args(["--device", "s3", "--host", "127.0.0.1", "--com", "loop://", "--usb-port", "COM8"])
        self.assertTrue(args.no_reset_ui)
        with contextlib.redirect_stdout(io.StringIO()) as output, mock.patch("tcp2com.webbrowser.open", return_value=True):
            self.assertEqual(tcp2com.main(["--device", "s3", "--open-config"]), 0)
        self.assertIn("GPIO4", output.getvalue())
        self.assertIn("ESP32-S3-Setup", output.getvalue())
        self.assertNotIn("GPIO24", output.getvalue())
        with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
            tcp2com.parse_args(["--device", "s3", "--host", "127.0.0.1", "--com", "COM8", "--usb-port", "COM8"])

    def test_s3_reconnect_binary_loopback(self):
        args = tcp2com.parse_args(["--device", "s3", "--mode", "tcp-server", "--listen", "127.0.0.1",
                                  "--port", str(free_port()), "--com", "loop://", "--timeout", ".01"])
        app = tcp2com.App(args, QuietUI())
        self.addCleanup(app.stop)
        self.assertTrue(app.start())
        for sequence in range(3):
            with socket.create_connection(app.server.address, timeout=2) as peer:
                peer.settimeout(2)
                payload = bytes(range(256)) * (sequence + 1)
                peer.sendall(payload)
                self.assertEqual(receive(peer, len(payload)), payload)
            wait_for(lambda: app.conn.snapshot()[0] is None)


class S3HTTPTests(config_tests.ConfigHTTPTests):
    def setUp(self):
        super().setUp()
        self.client = C5Client(f"http://127.0.0.1:{self.server.server_port}/", timeout=.3, device="s3")
        self.client.bind_session(self.server.token)

    def test_unsupported_frequency_never_posts(self):
        self.client.read()
        for patch in ({"band": 1, "ap_channel": 36}, {"band": 2}, {"ap_channel": 149}):
            with self.assertRaises(ConfigError):
                self.client.save(patch)
        self.assertEqual(self.server.received, [])


class S3USBTests(unittest.TestCase):
    def setUp(self):
        self.device = SerialC5(model="ESP32-S3")
        patch = mock.patch("usb_diagnostics.serial.serial_for_url", return_value=self.device)
        patch.start()
        self.addCleanup(patch.stop)
        self.monitor = USBMonitor("COM8", 115200, lambda _: None, device="s3")
        self.monitor.start()
        self.addCleanup(self.monitor.close)
        wait_for(lambda: self.monitor.status()["connected"])

    def test_read_save_single_usb_owner_and_model(self):
        client = C5USBClient(self.monitor, device="s3")
        self.assertEqual(client.read(), config_tests.example_config())
        self.assertEqual(self.monitor.config_device(), "ESP32-S3")
        # GUI reconstructs the save client, preserving the verified model.
        self.assertTrue(C5USBClient(self.monitor, device="s3").save({"baud": 921600})["saved"])
        self.assertEqual(self.device.save_count, 1)

    def test_wrong_model_and_s3_frequency_never_save(self):
        with self.assertRaises(ConfigError):
            C5USBClient(self.monitor, device="c5").read()
        client = C5USBClient(self.monitor, device="s3")
        client.read()
        before = len(self.device.requests)
        for patch in ({"band": 1, "ap_channel": 36}, {"band": 2}, {"ap_channel": 36}):
            with self.assertRaises(ConfigError):
                client.save(patch)
        with self.assertRaises(ConfigError):
            C5USBClient(self.monitor, device="c5").save({"baud": 921600})
        self.assertEqual(len(self.device.requests), before)
        self.assertEqual(self.device.save_count, 0)
        self.device.model = "ESP32-C5"
        with self.assertRaises(ConfigError):
            client.read()

    def test_timeout_hint_uses_gpio4_and_not_c5(self):
        self.device.setup = False
        with self.assertRaisesRegex(ConfigError, "GPIO4") as error:
            C5USBClient(self.monitor, timeout=.05, device="s3").read()
        self.assertNotIn("GPIO24", str(error.exception))

    def test_uncertain_save_not_replayed_and_disconnect_clears_model(self):
        client = C5USBClient(self.monitor, timeout=.1, device="s3")
        client.read()
        self.device.drop = "save"
        with self.assertRaises(SaveOutcomeUnknown):
            client.save({"baud": 921600})
        self.assertEqual(self.device.save_count, 1)
        self.monitor.close()
        self.assertIsNone(self.monitor.config_device())


if __name__ == "__main__":
    unittest.main()
