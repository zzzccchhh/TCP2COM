"""Exercise the real HTTP client against a local setup API, without a board."""

import copy
import http.server
import json
from pathlib import Path
import socket
import sys
import threading
import time
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from c5_config import C5Client, ConfigError, SaveOutcomeUnknown, changed_config, public_config


def example_config():
    return {"config_source": "nvs", "mode": 0, "link_mode": 1, "band": 0, "baud": 1152000,
            "tcp_port": 8080, "ap_channel": 6, "ap_ssid": "测试热点", "ap_ip": "192.168.4.1",
            "ap_gateway": "192.168.4.1", "ap_netmask": "255.255.255.0",
            "pc": {"ssid": "PC", "static_ip": True, "ip": "192.168.137.200", "gateway": "192.168.137.1",
                   "netmask": "255.255.255.0", "dns": "192.168.137.1"},
            "shared": {"ssid": "WiFi", "static_ip": False, "ip": "192.168.1.200", "gateway": "192.168.1.1",
                       "netmask": "255.255.255.0", "dns": "192.168.1.1"}}


def wbr_config(device="c5", **changes):
    return {**example_config(), "config_api": "c5-wbr-1" if device == "c5" else "bridge-1",
            "revision": 23, "saved_revision": 21, "unsaved": False, "migration_requires_setup": False,
            "net_role": 0, "auto_reconnect": True, "remote_ip": "192.168.137.1", "remote_port": 8080,
            "local_port": 6060, "reconnect_ms": 1000, "connect_timeout_ms": 10000,
            "up_kb": 4096, "down_kb": 1024, **changes}


class ConfigModelTests(unittest.TestCase):
    def test_changed_fields_only_preserve_passwords_and_inactive_profiles(self):
        base = example_config()
        edited = copy.deepcopy(base)
        edited["mode"] = 2
        edited["shared"]["ssid"] = "新的 WiFi"
        patch = changed_config(base, edited, {"ap": "", "pc": "", "shared": ""})
        self.assertEqual(patch, {"mode": 2, "shared": {"ssid": "新的 WiFi"}})
        self.assertEqual(base, example_config())

    def test_password_preserve_replace_and_explicit_open_station(self):
        base = example_config()
        self.assertEqual(changed_config(base, base), {})
        self.assertEqual(changed_config(base, base, {"ap": "new-pass-123"}), {"ap_password": "new-pass-123"})
        self.assertEqual(changed_config(base, base, clear_passwords=["pc"]), {"pc": {"password": ""}})
        with self.assertRaises(ConfigError):
            changed_config(base, base, clear_passwords=["ap"])
        with self.assertRaises(ConfigError):
            changed_config(base, base, {"shared": "password"}, ["shared"])

    def test_public_snapshots_strip_all_passwords_and_readonly_metadata(self):
        base = example_config()
        base.update(ap_password="top-secret", future_password="secret")
        base["shared"]["password"] = "another-secret"
        result = public_config(base)
        serialized = json.dumps(result)
        self.assertNotIn("secret", serialized)
        self.assertNotIn("password", serialized)
        self.assertNotIn("config_source", result)

    def test_utf8_byte_limits_nul_and_password_minimum(self):
        base = example_config()
        for field, value in (("ap_ssid", "中" * 11), ("ap_ssid", "a\0b"), ("ap_ssid", "")):
            with self.subTest(value=value), self.assertRaises(ConfigError):
                public_config({**base, field: value})
        for password in ("short", "a" * 64, "中" * 22):
            with self.subTest(password=password), self.assertRaises(ConfigError):
                changed_config(base, base, {"pc": password})

    def test_s3_reset_configuration_and_legacy_field_absence(self):
        base = wbr_config(device="s3", link_mode=2, reset_pin=0)
        self.assertEqual(public_config(base, "s3")["reset_pin"], 0)
        edited = copy.deepcopy(base)
        edited["reset_pin"] = 1
        self.assertEqual(changed_config(base, edited, device="s3"), {"reset_pin": 1})
        edited["tcp_port"] = 8081
        with self.assertRaises(ConfigError):
            public_config(edited, "s3")
        old = wbr_config(device="s3")
        self.assertNotIn("reset_pin", public_config(old, "s3"))
        edited_old = copy.deepcopy(old)
        edited_old["link_mode"] = 0
        with self.assertRaisesRegex(ConfigError, "旧版 S3"):
            changed_config(old, edited_old, device="s3")

    def test_numeric_types_modes_and_channel_matching(self):
        base = example_config()
        for field, value in (("link_mode", 3), ("mode", True), ("band", 3), ("baud", 9599),
                             ("tcp_port", 1023), ("tcp_port", 65536), ("baud", 1.5), ("ap_channel", 36)):
            with self.subTest(field=field, value=value), self.assertRaises(ConfigError):
                public_config({**base, field: value})
        public_config({**base, "band": 1, "ap_channel": 149})
        with self.assertRaises(ConfigError):
            public_config({**base, "band": 2, "ap_channel": 149})

    def test_static_network_mask_gateway_and_dhcp_reservation(self):
        base = example_config()
        for patch in ({"ap_ip": "127.0.0.1"}, {"ap_netmask": "255.0.255.0"},
                      {"ap_ip": "192.168.004.1"}, {"ap_gateway": "192.168.008.1"},
                      {"ap_gateway": "192.168.5.1"}, {"ap_ip": "192.168.4.0"},
                      {"ap_netmask": "255.255.255.252"}):
            with self.subTest(patch=patch), self.assertRaises(ConfigError):
                public_config({**base, **patch})
        edited = copy.deepcopy(base)
        for octet in ("008", "010"):
            edited["pc"].update(ip=f"192.168.{octet}.200", gateway=f"192.168.{octet}.1", dns=f"192.168.{octet}.1")
            with self.assertRaises(ConfigError):
                public_config(edited)
        edited = copy.deepcopy(base)
        edited["pc"]["gateway"] = edited["pc"]["ip"]
        with self.assertRaises(ConfigError):
            public_config(edited)
        edited["pc"]["static_ip"] = False
        public_config(edited)
        edited["pc"]["static_ip"] = 0
        with self.assertRaises(ConfigError):
            public_config(edited)

    def test_selected_station_needs_ssid_and_missing_fields_rejected(self):
        base = example_config()
        base["shared"]["ssid"] = ""
        public_config(base)  # Inactive profile is still preserved.
        with self.assertRaises(ConfigError):
            public_config({**base, "mode": 2})
        del base["pc"]["dns"]
        with self.assertRaises(ConfigError):
            public_config(base)


class SetupHandler(http.server.BaseHTTPRequestHandler):
    def log_message(self, *_):
        pass

    def do_GET(self):
        self.respond()

    def do_POST(self):
        raw = self.rfile.read(int(self.headers.get("Content-Length", 0)))
        self.server.received.append((self.path, self.headers.get("Content-Type"), json.loads(raw)))
        self.server.tokens.append(self.headers.get("X-C5-Session"))
        if self.server.tokens[-1] != self.server.token:
            self.send_response(503)
            self.send_header("Content-Length", "0")
            self.end_headers()
            return
        self.respond()

    def respond(self):
        self.server.requests += 1
        if self.server.drop:
            self.connection.shutdown(socket.SHUT_RDWR)
            return
        if self.server.delay:
            time.sleep(self.server.delay)
        self.send_response(self.server.status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(self.server.body)))
        self.end_headers()
        try:
            self.wfile.write(self.server.body)
        except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
            pass


class ConfigHTTPTests(unittest.TestCase):
    def setUp(self):
        self.server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), SetupHandler)
        self.server.status = 200
        self.server.token = "0123456789abcdef-1"
        self.server.tokens = []
        self.server.body = json.dumps({**example_config(), "http_session": self.server.token}, ensure_ascii=False).encode()
        self.server.received = []
        self.server.requests = 0
        self.server.delay = 0
        self.server.drop = False
        self.thread = threading.Thread(target=self.server.serve_forever, kwargs={"poll_interval": .02}, daemon=True)
        self.thread.start()
        self.client = C5Client(f"http://127.0.0.1:{self.server.server_port}/", timeout=.3)
        self.client.bind_session(self.server.token)

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(1)

    def test_get_and_post_match_firmware_api(self):
        self.assertEqual(self.client.read(), example_config())
        self.server.body = b'{"saved":true,"restart":true}'
        self.client.save({"pc": {"password": "private-new"}})
        self.assertEqual(self.server.received, [("/api/config", "application/json", {"pc": {"password": "private-new"}})])
        self.assertEqual(self.server.tokens, [self.server.token])

    def test_save_requires_read_identity_and_rejects_old_setup_or_boot(self):
        fresh = C5Client(f"http://127.0.0.1:{self.server.server_port}/")
        with self.assertRaisesRegex(ConfigError, "先.*读取"):
            fresh.save({"mode": 1})
        self.assertEqual(self.server.requests, 0)
        old = self.client.identity
        for token in ("0123456789abcdef-2", "fedcba9876543210-1"):
            self.server.token = token
            with self.assertRaisesRegex(ConfigError, "会话已失效") as caught:
                self.client.save({"mode": 1})
            self.assertNotIsInstance(caught.exception, SaveOutcomeUnknown)
            self.server.body = json.dumps({**example_config(), "http_session": token}).encode()
            self.client.read()
            self.assertNotEqual(old, self.client.identity)
            old = self.client.identity

    def test_read_rejects_missing_identity_and_clears_previous_binding(self):
        self.server.body = json.dumps(example_config()).encode()
        with self.assertRaisesRegex(ConfigError, "会话"):
            self.client.read()
        self.assertIsNone(self.client.http_session)

    def test_403_and_503_have_actionable_explanations_and_no_retry(self):
        for status, hint in ((403, f"GPIO{self.client.capabilities.setup_button}"), (503, "重启")):
            self.server.status = status
            with self.subTest(status=status), self.assertRaisesRegex(ConfigError, hint) as failure:
                self.client.save({"mode": 1})
            self.assertNotIsInstance(failure.exception, SaveOutcomeUnknown)
        self.assertEqual(self.server.requests, 2)

    def test_disconnect_after_post_is_unknown_and_never_retried(self):
        self.server.drop = True
        with self.assertRaisesRegex(SaveOutcomeUnknown, "待确认"):
            self.client.save({"ap_password": "private-secret"})
        self.assertEqual(self.server.requests, 1)

    def test_post_timeout_is_unknown_without_retry(self):
        self.server.delay = .1
        self.client.timeout = .02
        with self.assertRaises(SaveOutcomeUnknown):
            self.client.save({"mode": 1})
        self.assertEqual(self.server.requests, 1)

    def test_invalid_or_unconfirmed_save_reply_is_unknown(self):
        for body in (b"invalid private-secret", b'{}', b'{"saved":true,"saved":false}', b"x" * 8193):
            self.server.body = body
            with self.subTest(body=body[:30]), self.assertRaises(SaveOutcomeUnknown) as failure:
                self.client.save({"mode": 1})
            self.assertNotIn("private-secret", str(failure.exception))

    def test_invalid_get_response_and_redirect_are_rejected(self):
        for body in (b"<html>error</html>", b"[]", b"{}", b"x" * 8193):
            self.server.body = body
            with self.subTest(body=body[:20]), self.assertRaises(ConfigError):
                self.client.read()
        self.server.status = 302
        with self.assertRaisesRegex(ConfigError, "302"):
            self.client.read()

    def test_http_rejection_does_not_echo_secret_response(self):
        self.server.status = 400
        self.server.body = b"private-secret"
        with self.assertRaises(ConfigError) as failure:
            self.client.save({"ap_password": "private-secret"})
        self.assertNotIn("private-secret", str(failure.exception))

    def test_address_validation_and_connection_failure_before_send(self):
        for url in ("file:///tmp/config", "http://user:secret@127.0.0.1/", "http://127.0.0.1/other", "http://127.0.0.1/?password=x"):
            with self.subTest(url=url), self.assertRaises(ConfigError):
                C5Client(url)
        with socket.socket() as occupied:
            occupied.bind(("127.0.0.1", 0))  # Bound, not listening.
            client = C5Client(f"http://127.0.0.1:{occupied.getsockname()[1]}/", timeout=.1)
            with self.assertRaises(ConfigError) as failure:
                client.save({"mode": 1})
            self.assertNotIsInstance(failure.exception, SaveOutcomeUnknown)


if __name__ == "__main__":
    unittest.main()
