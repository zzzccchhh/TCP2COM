import copy
import json
from pathlib import Path
import socket
import tempfile
import threading
import time
import unittest

from network_log import NetworkLogMonitor, log_target
from c5_config import public_config, changed_config, ConfigError
from gui_state import clean_state
from web_service import WebService, default_device_config, config_to_draft, draft_to_config


def wait_for(check):
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        if check():
            return
        time.sleep(.01)
    raise AssertionError("condition timed out")


class NetworkLogTests(unittest.TestCase):
    def test_fragmented_utf8_long_line_recovery_stats_reconnect_and_close(self):
        with socket.socket() as listener:
            listener.bind(("127.0.0.1", 0))
            listener.listen()
            listener.settimeout(5)
            received = []
            monitor = NetworkLogMonitor("127.0.0.1", listener.getsockname()[1], received.append)
            monitor.start()
            try:
                with listener.accept()[0] as client:
                    data = "I (3) stats: IP=192.168.4.1 ch=6 RSSI=-50 session=1 local->net=10 net->local=20\nE (4) 中文日志\n".encode()
                    for b in data:
                        client.sendall(bytes([b]))
                    client.sendall(b"x" * 10000 + b"\nC5CFG private\nI (5) recovered\n")
                    wait_for(lambda: "I (5) recovered" in received)
                    self.assertIn("E (4) 中文日志", received)
                    self.assertFalse(any("private" in line for line in received))
                    self.assertEqual(monitor.status()["fields"]["IP"], "192.168.4.1")
                    self.assertEqual(monitor.status()["dropped"], 1)
                with listener.accept()[0] as client:
                    client.sendall(b"W (6) reconnected\n")
                    wait_for(lambda: "W (6) reconnected" in received)
                    self.assertEqual(monitor.status()["fields"], {})
            finally:
                monitor.close()
            self.assertFalse(monitor.thread.is_alive())

    def test_config_whitelist_deltas_validation_and_drafts(self):
        base = {**default_device_config("c5"), "log_enabled": True, "log_port": 8082}
        self.assertEqual(public_config(base)["log_port"], 8082)
        edited = {**base, "log_port": 19082}
        self.assertEqual(changed_config(base, edited), {"log_port": 19082})
        for patch in ({"log_port": True}, {"log_port": 65536}, {"log_port": 8081},
                      {"log_port": 8080}, {"log_enabled": 1}, {"log_port": "8082"}):
            with self.assertRaises(ConfigError):
                public_config({**base, **patch})
        public_config({**base, "net_role": 2, "local_port": 8082})
        with self.assertRaises(ConfigError):
            public_config({**base, "net_role": 1, "local_port": 8082})
        old = default_device_config("c5")
        self.assertEqual(changed_config(base, old), {})
        with self.assertRaises(ConfigError):
            changed_config(old, base)
        draft = config_to_draft(edited)
        cleaned, warnings = clean_state({"version": 1, "forms": {}, "device_drafts": {"c5": draft}})
        self.assertFalse(warnings)
        self.assertEqual(draft_to_config(cleaned["device_drafts"]["c5"], "c5")["log_port"], 19082)

    def test_service_independent_connection_sources_export_and_restore(self):
        with tempfile.TemporaryDirectory() as directory, socket.socket() as listener:
            listener.bind(("127.0.0.1", 0))
            listener.listen()
            listener.settimeout(4)
            path = Path(directory) / "state.json"
            service = WebService(state_path=path)
            try:
                service._baseline = {**default_device_config("c5"), "log_enabled": False, "log_port": 19082}
                imported = service.import_config(json.dumps({"format": "wifi-bridge-user-config-v1", "config": default_device_config("c5")}))
                self.assertTrue(imported["ok"])
                self.assertEqual(imported["data"]["log_port"], 19082)
                self.assertIs(imported["data"]["log_enabled"], False)
                service._baseline = None
                self.assertTrue(service.update_settings({"net_log_host": "127.0.0.1", "net_log_port": str(listener.getsockname()[1])})["ok"])
                self.assertTrue(service.network_log(True)["ok"])
                with listener.accept()[0] as client:
                    client.sendall(b"E (1) netlog regression\n")
                    wait_for(lambda: any(row[2].endswith("netlog regression") for row in list(service._controller.ui.events.queue)))
                    state = service.poll()["data"]
                    self.assertFalse(state["running"])
                    self.assertIsNone(state["usb"])
                    self.assertTrue(state["net_log"]["connected"])
                    self.assertTrue(any(row["source"] == "net" and row["level"] == "E" for row in state["logs"]))
                    self.assertIn("网络 / ESP", service.export_logs()["data"])
                    self.assertTrue(service.clear_logs()["ok"])
                    self.assertFalse(service.poll()["data"]["logs"])
            finally:
                service._shutdown()
            restored = WebService(state_path=path)
            try:
                self.assertEqual(restored._settings["net_log_port"], str(listener.getsockname()[1]))
                self.assertIsNone(restored.poll()["data"]["net_log"])
            finally:
                restored._shutdown()

    def test_bad_target_is_rejected_before_connection(self):
        for host, port in (("invalid", "8082"), ("127.0.0.1", "1"), ("127.0.0.1", "8082.1")):
            with self.assertRaises(ValueError):
                log_target(host, port)
