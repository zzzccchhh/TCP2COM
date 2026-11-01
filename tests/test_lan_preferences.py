import copy
import json
import os
from pathlib import Path
import tempfile
import io
from contextlib import redirect_stdout
import unittest
from unittest import mock

from lan_preferences import Preferences, DEFAULTS, startup_settings
from lan_network import InterfaceAddress, choose_address, discover_addresses, windows_addresses


class PreferenceTests(unittest.TestCase):
    def test_atomic_save_whitelists_fields_and_cli_precedence(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'state.json'
            preferences = Preferences(path)
            selected = {'id': 'windows:guid', 'name': 'WLAN', 'ip': '192.168.1.80'}
            self.assertTrue(preferences.update(listen='127.0.0.1', port=9000, preferred_interface=selected,
                                               token='must-not-save', invite='must-not-save'))
            saved = Preferences(path).data
            self.assertEqual(saved['preferred_interface'], selected)
            self.assertNotIn('must-not-save', path.read_text(encoding='utf-8'))
            self.assertEqual(startup_settings(saved)['port'], 9000)
            explicit = startup_settings(saved, '0.0.0.0', 9100, ['https://other.example'])
            self.assertEqual((explicit['listen'], explicit['port']), ('0.0.0.0', 9100))
            self.assertIn('https://other.example', explicit['origins'])
            self.assertEqual(startup_settings(saved)['preferred_interface'], selected)

    def test_corrupt_oversized_and_invalid_documents_use_defaults(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'state.json'
            for raw in ('bad JSON', 'x' * 65537, json.dumps({'version': 9}), json.dumps({**DEFAULTS, 'port': True}),
                        json.dumps({**DEFAULTS, 'origins': ['https://user:password@example.com']})):
                path.write_text(raw, encoding='utf-8')
                preferences = Preferences(path)
                self.assertEqual(preferences.data, DEFAULTS)
                self.assertTrue(preferences.warning)
            self.assertTrue(preferences.update(port=9000))
            self.assertEqual(Preferences(path).data['port'], 9000)

    def test_failed_write_keeps_previous_file_and_warns(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'state.json'
            preferences = Preferences(path)
            preferences.update(port=9000)
            with mock.patch('lan_preferences.os.replace', side_effect=OSError('locked')):
                self.assertFalse(preferences.update(port=9100))
            self.assertEqual(Preferences(path).data['port'], 9000)
            self.assertIn('无法保存', preferences.warning)
            self.assertEqual(list(Path(directory).glob('*.tmp')), [])


class NetworkTests(unittest.TestCase):
    def test_identity_wins_over_order_and_same_ip_on_another_card(self):
        a = InterfaceAddress('A', 'WLAN', '192.168.1.9')
        b = InterfaceAddress('B', 'Ethernet', '192.168.1.9')
        changed = InterfaceAddress('A', 'Renamed WLAN', '192.168.1.80')
        preference = a.preference()
        selected, warning = choose_address([b, changed], preference)
        self.assertEqual(selected, changed)
        self.assertTrue(warning)
        selected, _ = choose_address([changed, a, b], preference)
        self.assertEqual(selected, a)
        self.assertEqual(preference, a.preference())

    def test_disconnected_card_falls_back_without_modifying_preference(self):
        preferred = {'id': 'A', 'name': 'WLAN', 'ip': '192.168.1.9'}
        before = copy.deepcopy(preferred)
        loop = InterfaceAddress('loopback', '本机专用', '127.0.0.1')
        selected, warning = choose_address([InterfaceAddress('B', 'Ethernet', '192.168.2.9'), loop], preferred)
        self.assertEqual(selected, loop)
        self.assertIn('暂不可用', warning)
        self.assertEqual(before, preferred)

    def test_discovery_respects_binding_and_failed_scan(self):
        a = InterfaceAddress('A', 'WLAN', '192.168.1.9')
        with mock.patch('lan_network.os.name', 'nt'), mock.patch('lan_network.windows_addresses', return_value=[a]):
            rows, _ = discover_addresses('0.0.0.0')
            self.assertEqual(rows[0], a)
            self.assertEqual(rows[-1].ip, '127.0.0.1')
            self.assertEqual(discover_addresses('192.168.1.9')[0], [a])
            self.assertEqual(discover_addresses('127.0.0.1')[0][0].ip, '127.0.0.1')
        with mock.patch('lan_network.os.name', 'nt'), mock.patch('lan_network.windows_addresses', side_effect=OSError()):
            rows, warning = discover_addresses('0.0.0.0')
            self.assertEqual(len(rows), 1)
            self.assertTrue(warning)

    @unittest.skipUnless(os.name == 'nt', 'Windows native discovery')
    def test_windows_native_interface_data_is_readable(self):
        rows = windows_addresses()
        for row in rows:
            self.assertTrue(row.id.startswith('windows:'))
            self.assertTrue(row.name)
            self.assertTrue(row.ip)


class StartupTests(unittest.TestCase):
    def test_launcher_restores_values_and_only_persists_successful_overrides(self):
        import lan_service
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            dist = base / 'frontend' / 'dist'
            dist.mkdir(parents=True)
            (dist / 'index.html').write_text('<head></head>', encoding='utf-8')
            path = base / 'state.json'
            preferences = Preferences(path)
            preferred = {'id': 'windows:WLAN', 'name': 'WLAN', 'ip': '192.168.1.9'}
            preferences.update(port=9000, preferred_interface=preferred)
            for flags, expected in [([], ('0.0.0.0', 9000)), (['--listen', '127.0.0.1', '--port', '9100'], ('127.0.0.1', 9100))]:
                server = mock.MagicMock()
                server.server_address = expected
                server.server_port = expected[1]
                server.serve_forever.side_effect = KeyboardInterrupt
                with mock.patch.object(lan_service, 'BASE', base), mock.patch.object(lan_service, 'DEFAULT_PATH', path), \
                     mock.patch('sys.argv', ['lan_service.py', '--headless'] + flags), \
                     mock.patch.object(lan_service, 'LANService') as adapter, \
                     mock.patch.object(lan_service, 'make_server', return_value=server) as factory, \
                     redirect_stdout(io.StringIO()):
                    lan_service.main()
                    self.assertEqual(factory.call_args.args[:2], expected)
                    adapter.return_value.service._shutdown.assert_called_once()
                    server.server_close.assert_called_once()
                saved = Preferences(path).data
                self.assertEqual((saved['listen'], saved['port']), expected)
                self.assertEqual(saved['preferred_interface'], preferred)
            before = path.read_bytes()
            with mock.patch.object(lan_service, 'BASE', base), mock.patch.object(lan_service, 'DEFAULT_PATH', path), \
                 mock.patch('sys.argv', ['lan_service.py', '--headless', '--port', '9200']), \
                 mock.patch.object(lan_service, 'LANService') as adapter, \
                 mock.patch.object(lan_service, 'make_server', side_effect=OSError('occupied')):
                with self.assertRaises(OSError):
                    lan_service.main()
                adapter.return_value.service._shutdown.assert_called_once()
            self.assertEqual(path.read_bytes(), before)


if __name__ == '__main__':
    unittest.main()
