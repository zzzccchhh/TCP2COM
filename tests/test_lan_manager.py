from pathlib import Path
import gc
import tempfile
import tkinter as tk
import unittest
from unittest import mock
from urllib.parse import parse_qs, urlsplit

from lan_manager import Manager, ONLINE_URL, pairing_link, service_addresses, run_manager
from lan_service import LANService, make_server
from web_service import WebService


class SharingTests(unittest.TestCase):
    def tearDown(self):
        gc.collect()

    def test_share_link_only_contains_address_in_fragment(self):
        address = 'http://192.168.1.9:8765/'
        url = urlsplit(pairing_link(address))
        self.assertEqual(url.query, '')
        self.assertEqual(parse_qs(url.fragment), {'service': [address]})
        self.assertTrue(pairing_link(address).startswith(ONLINE_URL))

    def test_addresses_respect_bind_and_include_local_fallback(self):
        self.assertEqual(service_addresses('127.0.0.1', 9000), ['http://127.0.0.1:9000/'])
        self.assertEqual(service_addresses('192.168.1.9', 9000), ['http://192.168.1.9:9000/'])
        with mock.patch('lan_manager.socket.getaddrinfo', return_value=[(None, None, None, None, ('192.168.1.9', 0))]):
            self.assertEqual(service_addresses('0.0.0.0', 9000)[0], 'http://192.168.1.9:9000/')

    def test_closing_manager_stops_server_and_releases_service(self):
        try:
            import qrcode
            root = tk.Tk()
        except (ImportError, tk.TclError) as exc:
            self.skipTest(str(exc))
        root.withdraw()
        with tempfile.TemporaryDirectory() as directory:
            service = WebService(state_path=Path(directory) / 'state.json')
            adapter = LANService(service)
            server = make_server('127.0.0.1', 0, adapter)
            def create(root, server, adapter):
                manager = Manager(root, server, adapter)
                root.after(100, manager.close)
                return manager
            try:
                with mock.patch('lan_manager.tk.Tk', return_value=root), mock.patch('lan_manager.Manager', side_effect=create):
                    run_manager(server, adapter)
                self.assertTrue(service._closing)
                self.assertFalse(service._controller.thread.is_alive())
            finally:
                server.server_close()
                service._shutdown()


class ManagerTests(unittest.TestCase):
    def setUp(self):
        try:
            import qrcode
            self.root = tk.Tk()
        except (ImportError, tk.TclError) as exc:
            self.skipTest(str(exc))
        self.root.withdraw()
        self.addCleanup(self.destroy_ui)
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.service = WebService(state_path=Path(self.temp.name) / 'state.json')
        self.addCleanup(self.service._shutdown)
        self.adapter = LANService(self.service)
        self.server = make_server('127.0.0.1', 0, self.adapter)
        self.addCleanup(self.server.server_close)
        self.manager = Manager(self.root, self.server, self.adapter)
        self.root.update()

    def destroy_ui(self):
        self.root.destroy()
        self.manager = None
        self.root = None
        gc.collect()

    def test_copy_share_and_qr_follow_selected_address(self):
        self.manager.copy(ONLINE_URL)
        self.assertEqual(self.root.clipboard_get(), ONLINE_URL)
        self.manager.address.set('http://192.168.1.9:8765/')
        self.manager.update_qr()
        self.assertEqual(self.manager.link.get(), pairing_link('http://192.168.1.9:8765/'))
        self.assertGreater(self.manager.photo.width(), 100)
        self.manager.copy(self.manager.link.get())
        self.assertEqual(self.root.clipboard_get(), self.manager.link.get())

    def test_small_window_can_scroll_to_footer_and_revoke(self):
        self.root.geometry('860x600+3500+100')
        self.root.deiconify()
        self.root.update()
        canvas = self.manager.body.master
        canvas.yview_moveto(1)
        self.root.update()
        footer = self.manager.body.winfo_children()[-1]
        self.assertTrue(footer.winfo_ismapped())
        self.assertLessEqual(footer.winfo_rooty() + footer.winfo_height(), canvas.winfo_rooty() + canvas.winfo_height())

    def test_approve_reject_and_revoke_through_window_actions(self):
        request = self.adapter.request_pair('测试手机', '192.168.1.22')
        rejected = self.adapter.request_pair('另一终端', '192.168.1.23')
        self.manager.refresh(schedule=False)
        self.manager.pending.selection_set(request['id'])
        self.manager.decide(True)
        token = self.adapter.pairing_status(request['request'])['token']
        self.assertEqual(len(self.manager.peers.get_children()), 1)
        self.manager.pending.selection_set(rejected['id'])
        self.manager.decide(False)
        self.assertEqual(self.adapter.pairing_status(rejected['request'])['state'], 'rejected')
        self.manager.peers.selection_set(self.manager.peers.get_children()[0])
        self.manager.revoke()
        with self.assertRaises(PermissionError):
            self.adapter.dispatch(token, 'poll', [])
        self.assertEqual(self.manager.pending.get_children(), ())
        self.assertEqual(self.manager.peers.get_children(), ())


if __name__ == '__main__':
    unittest.main()
