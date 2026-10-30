"""Pair real host USB client/worker with the production C parser over stdio.

Point --fixture at an existing firmware build/host/usb_config_fixture.exe. Only serial I/O, NVS and timers are adapted;
this does not test the ESP USB driver or physical re-enumeration.
"""

import argparse
import copy
import json
from pathlib import Path
import queue
import subprocess
import sys
import threading
import time
from unittest import mock


def run_pair(args, link_mode=1, net_role=0, drop_save=False):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    from c5_usb_config import C5USBClient as NativeUSBClient
    def C5USBClient(monitor, timeout=4):
        return NativeUSBClient(monitor, timeout, device=args.device)
    from c5_config import ConfigError, SaveOutcomeUnknown
    from usb_diagnostics import USBMonitor

    process = subprocess.Popen([str(args.fixture.resolve())], stdin=subprocess.PIPE, stdout=subprocess.PIPE)
    incoming = queue.Queue()
    requests = []
    saved_replies = []
    def collect():
        for line in iter(process.stdout.readline, b""):
            if line.startswith((b"C5CFG ", b"BRIDGECFG ")):
                response = json.loads(line.split(b" ", 1)[1])
                if response.get("result", {}).get("saved") is True:
                    saved_replies.append(response)
                    if drop_save:
                        continue
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
            requests.append(json.loads(packet.strip().split(b" ", 1)[1]))
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
            if args.device == "c5":
                assert config["config_api"] == "c5-wbr-1" and config["revision"] > 0
                assert config["saved_revision"] == 0 and not config["unsaved"]
                assert not config["migration_requires_setup"]
            # Exit/re-enter setup without resetting the process/boot identifier.
            process.stdin.write(b"!setup off\n!setup on\n")
            process.stdin.flush()
            try:
                client.save({"baud": 921600})
                raise AssertionError("old setup session accepted")
            except ConfigError as error:
                assert "会话" in str(error)
            client.read()
            if args.device == "c5":
                # Corrupt the editing revision, keeping the USB session current.
                client.baseline = copy.deepcopy(client.baseline)
                client.baseline["revision"] += 1
                try:
                    client.save({"net_role": 1})
                    raise AssertionError("stale edit revision accepted")
                except ConfigError:
                    assert not any(r["op"] == "save" for r in requests)
                client.read()
                frame = monitor.request("validate", {"revision": client.baseline["revision"],
                                                      "up_kb": 6144, "down_kb": 64},
                                        boot=client.identity[3], generation=client.identity[2],
                                        session=client.identity[4])
                assert frame["ok"] is False and frame["code"] == "invalid"
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
            if args.device == "c5":
                if "reset_pin" in config:
                    patch["reset_pin"] = link_mode + 1
                band, channel = ((0, 6), (1, 36), (2, 6))[link_mode]
                patch.update(link_mode=link_mode, band=band, ap_channel=channel, net_role=net_role,
                             auto_reconnect=False, remote_ip="192.168.137.99", remote_port=9000,
                             local_port=0, reconnect_ms=2000, connect_timeout_ms=5000,
                             up_kb=2048, down_kb=512)
            expected_revision = client.baseline.get("revision")
            try:
                assert client.save(patch) == {"saved": True, "restart": True}
                assert not drop_save
            except SaveOutcomeUnknown:
                assert drop_save
            saves = [r for r in requests if r["op"] == "save"]
            assert len(saves) == len(saved_replies) == 1
            if expected_revision is not None:
                assert saves[0]["patch"] == {**patch, "revision": expected_revision}
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
    print(f"PASS {args.device} link={link_mode} role={net_role} lost_ack={drop_save}: real Python + production C, setup/session/revision, validation/save, restart, no replay/secrets")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--device", choices=("c5", "s3"), required=True)
    parser.add_argument("--fixture", type=Path, required=True)
    args = parser.parse_args()
    if args.device == "c5":
        for link_mode in range(3):
            run_pair(args, link_mode=link_mode, net_role=link_mode)
        run_pair(args, link_mode=2, net_role=2, drop_save=True)
    else:
        run_pair(args)


if __name__ == "__main__":
    main()
