"""Web adapter integration: real bridge/HTTP/USB workers, no physical devices."""

import copy
import http.server
import json
from pathlib import Path
import socket
import tempfile
import threading
import time
import unittest
from unittest import mock

from gui_state import save_state, VERSION
from gui_support import BridgeController
from web_service import WebService, config_to_draft, default_device_config
from test_c5_config import SetupHandler, wbr_config
from test_usb_config import SerialC5, wait_for


class WebServiceTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.path = Path(self.directory.name) / "state.json"
        self.service = WebService(state_path=self.path)

    def tearDown(self):
        self.service._shutdown()
        self.assertFalse(self.service._controller.thread.is_alive())
        self.directory.cleanup()

    def ok(self, response):
        self.assertTrue(response["ok"], response.get("error"))
        return response.get("data")

    def settings(self, **values):
        return self.ok(self.service.update_settings(values))

    def test_startup_does_not_open_connections_or_rewrite_state(self):
        self.assertFalse(self.ok(self.service.poll())["running"])
        self.assertIsNone(self.ok(self.service.poll())["usb"])
        self.assertFalse(self.path.exists())
        self.assertFalse(self.service.save_device(default_device_config("c5"), {}, [], 0)["ok"])
        self.assertFalse(self.service.bridge("unknown")["ok"])

    def test_incomplete_settings_and_device_drafts_persist_without_secrets(self):
        self.settings(port="", baud="12", host="192.168.")
        draft = default_device_config("c5")
        draft.update(ap_password="private-password", http_session="private-session", revision=42)
        draft["pc"]["password"] = "private-password"
        draft["baud"] = "115"
        self.ok(self.service.remember_draft("c5", draft))
        self.service._persist()
        raw = self.path.read_text(encoding="utf-8")
        self.assertNotIn("private", raw)
        self.assertNotIn("password", raw)
        self.assertNotIn("revision", raw)
        self.service._shutdown()
        self.service = WebService(state_path=self.path)
        state = self.ok(self.service.initial())
        self.assertEqual(state["settings"]["port"], "")
        self.assertEqual(state["config"]["baud"], 115)
        self.assertFalse(self.ok(self.service.poll())["valid"])
        self.assertFalse(self.ok(self.service.poll())["running"])

    def test_bad_settings_do_not_partially_apply(self):
        before = self.ok(self.service.initial())["settings"]
        self.assertFalse(self.service.update_settings({"device": "s3", "baud": False})["ok"])
        self.assertEqual(self.ok(self.service.initial())["settings"], before)

    def test_real_tcp_serial_loopback_and_unapplied_draft(self):
        with socket.socket() as temporary:
            temporary.bind(("127.0.0.1", 0))
            port = temporary.getsockname()[1]
        self.settings(device="c5", mode="tcp-server", listen="127.0.0.1", port=str(port), com="loop://")
        self.ok(self.service.bridge("start"))
        wait_for(lambda: self.ok(self.service.poll())["running"])
        with socket.create_connection(("127.0.0.1", port), timeout=2) as peer:
            peer.settimeout(2)
            payload = bytes(range(256)) * 4
            peer.sendall(payload)
            received = bytearray()
            while len(received) < len(payload):
                received.extend(peer.recv(4096))
            self.assertEqual(bytes(received), payload)
            state = self.ok(self.service.poll())
            self.assertTrue(state["connected"])
            self.assertTrue(state["applied"])
            self.settings(port="")
            self.assertEqual(self.service._controller.snapshot()[0].args.port, port)
            self.assertFalse(self.ok(self.service.poll())["applied"])
            self.assertFalse(self.service.bridge("restart")["ok"])
        self.ok(self.service.bridge("stop"))
        wait_for(lambda: not self.service._controller.snapshot()[4])
        self.assertFalse(self.ok(self.service.poll())["running"])

    def test_usb_only_ignores_invalid_payload_settings_and_session_expires(self):
        serial = SerialC5()
        serial.config = wbr_config()
        self.settings(device="c5", port="", com="", usb_port="COM88", config_transport="usb")
        with mock.patch("usb_diagnostics.serial.Serial", return_value=serial):
            self.ok(self.service.usb(True))
            wait_for(lambda: self.service._controller.snapshot()[1] is not None)
            wait_for(lambda: self.service._controller.snapshot()[1].status()["connected"])
            config = self.ok(self.service.read_device())
            self.assertEqual(config["revision"], 23)
            edited = {**config, "baud": 2000000}
            preview = self.ok(self.service.preview_save(edited, {"ap": "private-password"}, []))
            self.assertNotIn("private-password", json.dumps(preview))
            self.ok(self.service.save_device(edited, {"ap": "private-password"}, [], preview["epoch"]))
            self.assertEqual(serial.save_count, 1)
            self.assertFalse(self.service.save_device(edited, {}, [], preview["epoch"])["ok"])
            self.assertEqual(serial.save_count, 1)
            self.ok(self.service.read_device())
            self.ok(self.service.usb(False))
            wait_for(lambda: self.service._controller.snapshot()[1] is None)
            self.assertIsNone(self.ok(self.service.poll())["baseline"])

    def test_http_disconnect_usb_keeps_current_http_read(self):
        config = wbr_config()
        client = mock.Mock(identity=("http", "127.0.0.1", 80, "0123456789abcdef-1"))
        with mock.patch.object(self.service, "_client", return_value=client):
            self.service._baseline = config
            self.service._identity = client.identity
            self.ok(self.service.usb(False))
            self.assertEqual(self.ok(self.service.poll())["baseline"], config)

    def test_unknown_usb_save_requires_read_and_never_replays(self):
        serial = SerialC5()
        self.settings(usb_port="COM88", config_transport="usb")
        with mock.patch("usb_diagnostics.serial.Serial", return_value=serial):
            self.ok(self.service.usb(True))
            wait_for(lambda: self.service._controller.snapshot()[1] is not None)
            wait_for(lambda: self.service._controller.snapshot()[1].status()["connected"])
            config = self.ok(self.service.read_device())
            edited = {**config, "baud": 2000000}
            preview = self.ok(self.service.preview_save(edited, {}, []))
            serial.drop = "save"
            result = self.service.save_device(edited, {}, [], preview["epoch"])
            self.assertFalse(result["ok"])
            self.assertIn("待确认", result["error"])
            count = len([r for r in serial.requests if r["op"] == "save"])
            self.assertFalse(self.service.save_device(edited, {}, [], preview["epoch"])["ok"])
            self.assertEqual(len([r for r in serial.requests if r["op"] == "save"]), count)

    def test_logs_are_bounded_filterable_data_and_export_all_sources(self):
        self.ok(self.service.clear_logs())
        self.service._controller.ui.message("host event")
        self.service._controller.ui.usb_message("USB | \x1b[31mE (23) esp error\x1b[0m")
        state = self.ok(self.service.poll())
        self.assertEqual([row["source"] for row in state["logs"]], ["host", "usb"])
        self.assertEqual(state["logs"][-1]["level"], "E")
        self.assertNotIn("\x1b", state["logs"][-1]["text"])
        self.assertEqual(self.ok(self.service.poll(state["cursor"]))["logs"], [])
        for batch in range(7):
            for i in range(500):
                self.service._controller.ui.message(f"row {batch}-{i}")
            self.ok(self.service.poll())
        self.assertEqual(len(self.service._logs), 3000)
        self.assertIn("row 6-499", self.ok(self.service.export_logs()))
        self.ok(self.service.clear_logs())
        self.assertEqual(self.ok(self.service.poll())["logs"], [])

    def test_import_does_not_authorize_save_and_s3_rejects_5ghz(self):
        config = wbr_config(band=1, ap_channel=149)
        text = json.dumps({"format": "wifi-bridge-user-config-v1", "device": "c5", "config": config})
        imported = self.ok(self.service.import_config(text))
        self.assertFalse(self.service.preview_save(imported, {}, [])["ok"])
        self.settings(device="s3")
        self.assertFalse(self.service.import_config(text)["ok"])
        self.assertFalse(self.service.import_config("[1]")["ok"])

    def test_busy_request_blocks_replacement_but_disconnect_discards_old_read(self):
        started, finish = threading.Event(), threading.Event()
        client = mock.Mock(identity=("usb", "COM88", 1, "boot", 1))
        def read():
            started.set()
            finish.wait(3)
            return wbr_config()
        client.read.side_effect = read
        result = []
        with mock.patch.object(self.service, "_client", return_value=client):
            worker = threading.Thread(target=lambda: result.append(self.service.read_device()))
            worker.start()
            self.assertTrue(started.wait(2))
            self.assertFalse(self.service.update_settings({"device": "s3"})["ok"])
            self.assertFalse(self.service.usb(True)["ok"])
            self.ok(self.service.usb(False))
            finish.set()
            worker.join(3)
        self.assertFalse(result[0]["ok"])
        self.assertIsNone(self.ok(self.service.poll())["baseline"])

    def test_reset_context_does_not_accumulate_clicks_for_different_targets(self):
        with mock.patch("web_service.perform_reset") as reset:
            for _ in range(4):
                self.ok(self.service.reset_click("127.0.0.1", 8081))
            self.ok(self.service.reset_click("127.0.0.2", 8081))
            reset.assert_not_called()
            for _ in range(4):
                self.ok(self.service.reset_click("127.0.0.2", 8081))
            self.service._reset_thread.join(2)
            reset.assert_called_once()
        self.settings(device="s3")
        self.assertEqual(self.ok(self.service.reset_click("127.0.0.1"))["count"], 1)


class WebHTTPTests(unittest.TestCase):
    def test_real_http_revision_session_save_and_endpoint_change(self):
        with tempfile.TemporaryDirectory() as directory:
            server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), SetupHandler)
            server.status, server.token = 200, "0123456789abcdef-1"
            server.body = json.dumps({**wbr_config(), "http_session": server.token}).encode()
            server.received, server.tokens, server.requests = [], [], 0
            server.delay, server.drop = 0, False
            thread = threading.Thread(target=server.serve_forever, kwargs={"poll_interval": .02}, daemon=True)
            thread.start()
            service = WebService(state_path=Path(directory) / "state.json")
            try:
                result = service.update_settings({"config_transport": "http", "config_url": f"http://127.0.0.1:{server.server_port}/"})
                self.assertTrue(result["ok"], result)
                result = service.read_device()
                self.assertTrue(result["ok"], result)
                edited = {**result["data"], "baud": 2000000}
                preview = service.preview_save(edited, {}, [])
                self.assertTrue(preview["ok"], preview)
                server.body = b'{"saved":true,"restart":true}'
                saved = service.save_device(edited, {}, [], preview["data"]["epoch"])
                self.assertTrue(saved["ok"], saved)
                self.assertEqual(server.received[-1][2], {"baud": 2000000, "revision": 23})
                self.assertEqual(server.tokens[-1], server.token)
                self.assertFalse(service.save_device(edited, {}, [], preview["data"]["epoch"])["ok"])
                self.assertEqual(len(server.received), 1)
            finally:
                service._shutdown()
                server.shutdown()
                server.server_close()
                thread.join(2)


if __name__ == "__main__":
    unittest.main()
