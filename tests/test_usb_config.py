"""USB configuration uses one real worker; simulate only the serial device."""

import copy
import json
from pathlib import Path
import queue
import tempfile
import threading
import time
import unittest
from unittest import mock

from c5_config import ConfigError, SaveOutcomeUnknown
from c5_usb_config import C5USBClient
from usb_diagnostics import USBMonitor
from test_c5_config import example_config


def wait_for(predicate, timeout=3):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return
        time.sleep(.005)
    raise AssertionError("USB operation did not finish")


class SerialC5:
    """Fragment replies and mix logs; assert exclusive handle and worker ownership."""
    def __init__(self):
        self.incoming = queue.Queue()
        self.boot = "0123456789abcdef"
        self.config = example_config()
        self.requests = []
        self.writes = []
        self.drop = None
        self.bad = None
        self.bad_operation = None
        self.short = False
        self.unplugged = False
        self.opened = False
        self.closed = False
        self.save_count = 0
        self.saving = False
        self.worker = None

    def open(self):
        if self.dtr or self.rts:
            raise AssertionError("control lines must remain inactive")
        self.opened = True

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.closed = True

    def _owner(self):
        if self.worker is None:
            self.worker = threading.get_ident()
        if self.worker != threading.get_ident():
            raise AssertionError("multiple serial owners")

    def read(self, _size):
        self._owner()
        if self.unplugged:
            raise OSError("removed")
        try:
            return self.incoming.get(timeout=.01)
        except queue.Empty:
            return b""

    def inject(self, content):
        for i in range(0, len(content), 19):
            self.incoming.put(content[i:i + 19])

    def write(self, packet):
        self._owner()
        self.writes.append(packet)
        if self.short:
            return len(packet) // 2
        request = json.loads(packet.strip()[6:])
        self.requests.append(request)
        operation = request["op"]
        response = {"v": 1, "id": request["id"], "boot": self.boot, "ok": True}
        if operation == "hello":
            result = {"device": "ESP32-C5", "protocol": 1}
        elif operation == "read":
            result = copy.deepcopy(self.config)
        else:
            result = {"valid": True} if operation == "validate" else {"saved": True, "restart": True}
            if request.get("boot") != self.boot:
                response.update(ok=False, code="stale")
            elif self.saving:
                response.update(ok=False, code="busy")
            elif operation == "save":
                self.save_count += 1
                self.saving = True
        response["result"] = result
        if self.bad and (self.bad_operation is None or self.bad_operation == operation):
            response.update(self.bad)
        if self.drop != operation:
            # Stale replies and stats must not satisfy the active request.
            stale = {**response, "id": "old-request"}
            content = (b"I (1) stats: RSSI=-42\nC5CFG " + json.dumps(stale).encode() + b"\nC5CFG "
                       + json.dumps(response, ensure_ascii=False).encode() + b"\r\n")
            self.inject(content)
        return len(packet)


class USBConfigTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.logfile = Path(self.temporary.name) / "usb.log"
        self.messages = []
        self.device = SerialC5()
        self.patch = mock.patch("usb_diagnostics.serial.serial_for_url", return_value=self.device)
        self.factory = self.patch.start()
        self.monitor = USBMonitor("COM8", 115200, self.messages.append, str(self.logfile))
        self.monitor.start()
        wait_for(lambda: self.monitor.status()["connected"])

    def tearDown(self):
        self.monitor.close()
        self.assertFalse(self.monitor.thread.is_alive())
        self.patch.stop()
        self.temporary.cleanup()

    def test_read_and_save_share_logs_single_handle_and_preserve_secrets(self):
        client = C5USBClient(self.monitor, timeout=.5)
        document = client.read()
        self.assertEqual(document, example_config())
        self.assertEqual(client.identity, self.monitor.config_identity())
        self.assertEqual(self.monitor.snapshot()["RSSI"], "-42")
        patch = {"baud": 921600, "pc": {"password": "private-usb-secret"}}
        self.assertTrue(client.save(patch)["saved"])
        self.assertEqual(self.device.save_count, 1)
        self.assertEqual([r["op"] for r in self.device.requests], ["hello", "read", "validate", "save"])
        self.assertEqual(self.device.requests[-1]["patch"], patch)
        self.factory.assert_called_once_with("COM8", do_not_open=True)
        self.assertNotIn("private-usb-secret", self.logfile.read_text(encoding="utf-8"))
        self.assertNotIn("C5CFG", self.logfile.read_text(encoding="utf-8"))
        self.assertNotIn("private-usb-secret", "\n".join(self.messages))
        self.assertEqual(self.device.write_timeout, .5)

    def test_old_firmware_times_out_without_ever_sending_save(self):
        self.device.drop = "hello"
        with self.assertRaises(ConfigError):
            C5USBClient(self.monitor, timeout=.08).read()
        self.assertEqual([r["op"] for r in self.device.requests], ["hello"])
        with self.assertRaises(ConfigError):
            C5USBClient(self.monitor).save({"baud": 921600})
        self.assertEqual(self.device.save_count, 0)

    def test_save_ack_loss_is_unknown_and_never_replayed(self):
        client = C5USBClient(self.monitor, timeout=.08)
        client.read()
        self.device.drop = "save"
        with self.assertRaises(SaveOutcomeUnknown):
            client.save({"baud": 921600})
        time.sleep(.05)
        self.assertEqual(self.device.save_count, 1)
        self.assertEqual(sum(r["op"] == "save" for r in self.device.requests), 1)
        self.assertIsNone(self.monitor.pending)

    def test_failed_validation_never_sends_save(self):
        client = C5USBClient(self.monitor, timeout=.2)
        client.read()
        self.device.bad = {"ok": False, "code": "invalid", "error": "private-secret"}
        with self.assertRaises(ConfigError) as error:
            client.save({"baud": 1})
        self.assertNotIn("private-secret", str(error.exception))
        self.assertEqual(self.device.save_count, 0)

    def test_unconfirmed_or_invalid_save_replies_are_unknown(self):
        for bad in ({"result": {}}, {"result": {"saved": True, "restart": False}},
                    {"ok": False, "code": "memory"}, {"v": 2}):
            with self.subTest(reply=bad):
                self.device.bad = None
                self.device.saving = False
                client = C5USBClient(self.monitor, timeout=.1)
                client.read()
                self.device.bad, self.device.bad_operation = bad, "save"
                before = self.device.save_count
                with self.assertRaises(SaveOutcomeUnknown):
                    client.save({"baud": 921600})
                self.assertEqual(self.device.save_count, before + 1)

    def test_boot_change_rejects_edits_based_on_old_read(self):
        client = C5USBClient(self.monitor, timeout=.2)
        client.read()
        old = client.identity
        self.device.boot = "fedcba9876543210"
        with self.assertRaisesRegex(ConfigError, "重启"):
            client.save({"baud": 921600})
        self.assertNotEqual(old, self.monitor.config_identity())
        self.assertEqual(self.device.save_count, 0)

    def test_disconnect_cancels_waiter_and_stale_generation_never_writes(self):
        client = C5USBClient(self.monitor, timeout=.3)
        client.read()
        self.monitor._disconnected()
        with self.assertRaises(ConfigError):
            client.save({"baud": 921600})
        with self.monitor.lock:
            self.monitor.connected = True
            self.monitor.generation = "new-connection"
        with self.assertRaises(ConfigError):
            client.save({"baud": 921600})
        self.assertEqual(self.device.save_count, 0)

    def test_partial_save_write_is_unknown_and_not_replayed(self):
        client = C5USBClient(self.monitor, timeout=.3)
        client.read()
        original = self.device.write
        def write(packet):
            if b'"op":"save"' in packet:
                self.device.short = True
            return original(packet)
        self.device.write = write
        with self.assertRaises(SaveOutcomeUnknown):
            client.save({"baud": 921600})
        self.assertEqual(sum(b'"op":"save"' in p for p in self.device.writes), 1)

    def test_bad_protocol_and_oversized_echo_never_enter_log(self):
        self.device.inject(b'C5CFG {"password":"never-log-this"}\n')
        self.device.inject(b'C5CFG ' + b'x' * 17000 + b'\n')
        client = C5USBClient(self.monitor, timeout=.5)
        client.read()
        self.assertNotIn("never-log-this", self.logfile.read_text(encoding="utf-8"))
        self.assertNotIn("never-log-this", "\n".join(self.messages))

    def test_close_releases_waiter_and_no_queued_command_survives(self):
        self.device.drop = "hello"
        errors = []
        def read():
            try:
                C5USBClient(self.monitor, timeout=2).read()
            except ConfigError as error:
                errors.append(error)
        thread = threading.Thread(target=read)
        thread.start()
        wait_for(lambda: self.device.requests)
        self.monitor.close()
        thread.join(1)
        self.assertFalse(thread.is_alive())
        self.assertEqual(len(errors), 1)
        self.assertIsNone(self.monitor.pending)
        self.assertTrue(self.device.closed)


if __name__ == "__main__":
    unittest.main()
