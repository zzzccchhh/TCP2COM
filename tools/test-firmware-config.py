"""Pair real host USB client/worker with the production C parser over stdio.

Point --fixture at an existing firmware build/host/usb_config_fixture.exe. Only serial I/O, NVS and timers are adapted;
this does not test the ESP USB driver or physical re-enumeration.
"""

import argparse
from pathlib import Path
import queue
import subprocess
import sys
import threading
import time
from unittest import mock


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--device", choices=("c5", "s3"), required=True)
    parser.add_argument("--fixture", type=Path,
                        required=True)
    args = parser.parse_args()
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    from c5_usb_config import C5USBClient as NativeUSBClient
    def C5USBClient(monitor, timeout=4):
        return NativeUSBClient(monitor, timeout, device=args.device)
    from c5_config import ConfigError
    from usb_diagnostics import USBMonitor

    process = subprocess.Popen([str(args.fixture.resolve())], stdin=subprocess.PIPE, stdout=subprocess.PIPE)
    incoming = queue.Queue()
    def collect():
        for line in iter(process.stdout.readline, b""):
            incoming.put(line)
    reader = threading.Thread(target=collect, daemon=True)
    reader.start()

    class Device:
        def open(self):
            assert not self.dtr and not self.rts
        def read(self, _size):
            try:
                return incoming.get(timeout=.02)
            except queue.Empty:
                return b""
        def write(self, packet):
            process.stdin.write(packet)
            process.stdin.flush()
            return len(packet)
        def __enter__(self):
            return self
        def __exit__(self, *_):
            pass

    messages = []
    monitor = USBMonitor("fixture", 115200, messages.append, device=args.device)
    try:
        with mock.patch("usb_diagnostics.serial.serial_for_url", return_value=Device()):
            monitor.start()
            deadline = time.monotonic() + 2
            while not monitor.status()["connected"]:
                if time.monotonic() > deadline:
                    raise AssertionError("fixture connection timeout")
                time.sleep(.01)
            client = C5USBClient(monitor)
            try:
                C5USBClient(monitor, timeout=.15).read()
                raise AssertionError("normal mode accepted USB configuration")
            except ConfigError as error:
                assert "配网" in str(error)
            process.stdin.write(b"!setup on\n")
            process.stdin.flush()
            config = client.read()
            assert config["config_source"] == "defaults"
            # Exit/re-enter setup without resetting the process/boot identifier.
            process.stdin.write(b"!setup off\n!setup on\n")
            process.stdin.flush()
            try:
                client.save({"baud": 921600})
                raise AssertionError("old setup session accepted")
            except ConfigError as error:
                assert "会话" in str(error)
            client.read()
            try:
                client.save({"baud": 1})
                raise AssertionError("invalid configuration accepted")
            except ConfigError:
                pass
            assert client.read()["baud"] == config["baud"]
            if args.device == "s3":
                for band, channel in ((1, 36), (2, 6)):
                    try:
                        client.save({"band": band, "ap_channel": channel})
                        raise AssertionError("S3 client accepted unsupported Wi-Fi band")
                    except ConfigError:
                        pass
                    # Bypass the local validator to also exercise production C.
                    frame = monitor.request("validate", {"band": band, "ap_channel": channel},
                                            boot=client.identity[3], generation=client.identity[2],
                                            session=client.identity[4])
                    assert frame["ok"] is False and frame["code"] == "invalid"
                assert client.read()["band"] == 0
            patch = {"baud": 921600, "link_mode": 1, "mode": 2,
                     "shared": {"ssid": "USB-pair-test", "password": "pair-test-secret", "static_ip": False}}
            assert client.save(patch) == {"saved": True, "restart": True}
            try:
                client.read()
                raise AssertionError("old startup config returned after save")
            except ConfigError:
                pass
            assert "pair-test-secret" not in "\n".join(messages)
            monitor.close()
    finally:
        monitor.close()
        process.stdin.close()
        process.wait(timeout=3)
        reader.join(1)
        process.stdout.close()
    assert process.returncode == 0
    print(f"PASS {args.device}: real Python + production C: normal-mode silence, setup/re-entry, stale session, validation/save, restart, secrets")


if __name__ == "__main__":
    main()
