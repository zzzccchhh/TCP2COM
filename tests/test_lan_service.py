import json
from pathlib import Path
import tempfile
import threading
import time
import unittest
from urllib.request import Request, urlopen
from urllib.error import HTTPError

from lan_service import LANService, make_server
from web_service import WebService


class LANTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.service = WebService(state_path=Path(self.temp.name) / 'state.json')
        self.addCleanup(self.temp.cleanup)
        self.addCleanup(self.service._shutdown)
        self.adapter = LANService(self.service, ['https://zzzccchhh.github.io'])
        self.a = self.pair('电脑')
        self.b = self.pair('手机')

    def pair(self, name):
        request = self.adapter.request_pair(name, '127.0.0.1')
        self.assertEqual(self.adapter.pairing_status(request['request']), {'state': 'pending'})
        self.adapter.decide(request['id'], True)
        return self.adapter.pairing_status(request['request'])['token']

    def call(self, token, method, args=None, revision=None):
        return self.adapter.dispatch(token, method, args or [], self.adapter.revision if revision is None else revision)

    def test_shared_settings_and_stale_write_rejected(self):
        self.assertTrue(self.call(self.a, 'update_settings', [{'com': 'COM88'}])['ok'])
        state = self.call(self.b, 'poll')['data']
        self.assertEqual(state['settings']['com'], 'COM88')
        with self.assertRaises(ValueError):
            self.call(self.b, 'update_settings', [{'com': 'COM99'}], revision=0)
        self.assertEqual(self.call(self.a, 'initial')['data']['settings']['com'], 'COM88')

    def test_pair_expiry_throttle_and_revocation(self):
        for _ in range(5):
            self.adapter.request_pair('x', '127.0.0.2')
        with self.assertRaises(ValueError):
            self.adapter.request_pair('x', '127.0.0.2')
        peer = next(p for p in self.call(self.a, 'peers')['data'] if not p['self'])
        self.call(self.a, 'revoke', [peer['id']])
        with self.assertRaises(PermissionError):
            self.call(self.b, 'poll')
        request = self.adapter.request_pair('x', '127.0.0.3')
        self.adapter.pending[request['request']]['until'] = time.monotonic() - 1
        self.assertEqual(self.adapter.pairing_status(request['request'])['state'], 'expired')
        with self.assertRaises(ValueError):
            self.adapter.decide(request['id'], True)

    def test_pending_cancel_reject_and_secret_isolation(self):
        request = self.adapter.request_pair('new', '127.0.0.4')
        self.assertNotIn('token', self.adapter.pairing_status(request['request']))
        self.assertEqual(self.adapter.pairing_status(request['id'])['state'], 'expired')
        self.assertNotIn(request['request'], json.dumps(self.adapter.management_snapshot()))
        self.adapter.pairing_status(request['request'], cancel=True)
        with self.assertRaises(ValueError):
            self.adapter.decide(request['id'], True)
        request = self.adapter.request_pair('new', '127.0.0.4')
        self.adapter.decide(request['id'], False)
        self.assertEqual(self.adapter.pairing_status(request['request'])['state'], 'rejected')

    def test_approved_cancel_revokes_token_and_cannot_reapprove(self):
        request = self.adapter.request_pair('new', '127.0.0.4')
        self.adapter.decide(request['id'], True)
        token = self.adapter.pairing_status(request['request'])['token']
        self.adapter.pairing_status(request['request'], cancel=True)
        with self.assertRaises(PermissionError):
            self.call(token, 'poll')
        with self.assertRaises(ValueError):
            self.adapter.decide(request['id'], True)

    def test_allowlist_and_busy_commands(self):
        with self.assertRaises(ValueError):
            self.call(self.a, '_shutdown')
        self.adapter.command.acquire()
        try:
            with self.assertRaises(ValueError):
                self.call(self.a, 'bridge', ['stop'])
        finally:
            self.adapter.command.release()

    def test_drafts_and_config_receipts_are_per_terminal(self):
        self.call(self.a, 'remember_draft', ['c5', {'baud': 1234}])
        self.assertEqual(self.call(self.b, 'initial')['data']['drafts'], {})
        self.service._baseline = {'baud': 1152000}
        with self.assertRaises(ValueError):
            self.call(self.b, 'save_device', [{}, {}, [], self.service._epoch])

    def test_reset_clicks_do_not_accumulate_across_terminals(self):
        one = self.call(self.a, 'reset_click', ['192.168.4.1', 8081])
        two = self.call(self.b, 'reset_click', ['192.168.4.1', 8081])
        self.assertEqual(one['data']['count'], 1)
        self.assertEqual(two['data']['count'], 1)

    def test_http_cors_auth_and_static_root(self):
        Path(self.temp.name, 'index.html').write_text('<h1>test</h1>', encoding='utf-8')
        server = make_server('127.0.0.1', 0, self.adapter, self.temp.name)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        base = f'http://127.0.0.1:{server.server_port}'
        def request(path, body=None, origin=None, token=None):
            headers = {'Content-Type': 'application/json'}
            if origin: headers['Origin'] = origin
            if token: headers['Authorization'] = 'Bearer ' + token
            return urlopen(Request(base + path, data=json.dumps(body).encode() if body is not None else None, headers=headers), timeout=3)
        with request('/') as response:
            self.assertIn(b'<h1>', response.read())
        for path in ['/../lan_service.py', '/%2e%2e/lan_service.py', '/api/call']:
            with self.assertRaises(HTTPError) as caught:
                request(path, {'method': 'poll', 'args': []} if path == '/api/call' else None)
            caught.exception.close()
        with self.assertRaises(HTTPError) as error:
            request('/api/pair/request', {'name': 'x'}, 'https://evil.example')
        self.assertEqual(error.exception.code, 403)
        error.exception.close()
        for path in ['/api/pair', '/api/pair/approve', '/api/call']:
            with self.assertRaises(HTTPError) as caught:
                request(path, {'method': 'decide', 'args': []}, token=self.a)
            caught.exception.close()
        with request('/api/call', {'method': 'poll', 'args': []}, 'https://zzzccchhh.github.io', self.a) as response:
            self.assertEqual(response.headers['Access-Control-Allow-Origin'], 'https://zzzccchhh.github.io')
            self.assertTrue(json.load(response)['ok'])


if __name__ == '__main__':
    unittest.main()
