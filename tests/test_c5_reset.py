import pathlib
import socket
import sys
import threading
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
from c5_config import ConfigError, changed_config, public_config
from reset_control import ResetError, perform_reset
from test_c5_config import wbr_config


class C5ResetTests(unittest.TestCase):
    def test_optional_configuration_and_bounds(self):
        old = wbr_config(link_mode=0)
        self.assertNotIn("reset_pin", public_config(old))
        for pin in range(5):
            config = {**old, "reset_pin": pin}
            self.assertEqual(public_config(config)["reset_pin"], pin)
            self.assertEqual(changed_config({**old, "reset_pin": 0}, config),
                             {} if pin == 0 else {"reset_pin": pin})
        for pin in (-1, 5, True):
            with self.assertRaises(ConfigError): public_config({**old, "reset_pin": pin})
        with self.assertRaises(ConfigError):
            public_config({**old, "reset_pin": 1, "net_role": 0, "tcp_port": 8081})

    def test_c5_handshake_and_single_request(self):
        listener = socket.socket(); listener.bind(("127.0.0.1", 0)); listener.listen(1)
        requests = []
        def server():
            with listener:
                conn, _ = listener.accept()
                with conn:
                    conn.sendall(b"C5-RESET/1 READY\n")
                    data = bytearray()
                    while not data.endswith(b"\n"): data.extend(conn.recv(128))
                    requests.append(bytes(data))
                    ident = data.decode().strip().split()[1]
                    conn.sendall(f"STARTED {ident}\nDONE {ident}\n".encode())
        worker = threading.Thread(target=server); worker.start()
        progress = []
        perform_reset("127.0.0.1", listener.getsockname()[1], progress.append,
                      threading.Event(), 2, device="c5")
        worker.join(3)
        self.assertFalse(worker.is_alive())
        self.assertEqual(len(requests), 1)
        self.assertIn("数据灯", progress[0])

    def test_c5_never_sends_to_esp01s_greeting(self):
        listener = socket.socket(); listener.bind(("127.0.0.1", 0)); listener.listen(1)
        received = []
        def server():
            with listener:
                conn, _ = listener.accept()
                with conn:
                    conn.sendall(b"ESP01S-RESET/1 READY\n")
                    received.append(conn.recv(128))
        worker = threading.Thread(target=server); worker.start()
        with self.assertRaises(ResetError):
            perform_reset("127.0.0.1", listener.getsockname()[1], lambda _: None,
                          threading.Event(), 2, device="c5")
        worker.join(3)
        self.assertEqual(received, [b""])
