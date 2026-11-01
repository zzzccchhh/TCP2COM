"""Isolated browser test fixture: loopback only, temporary settings, no devices opened."""
from pathlib import Path
import sys
import tempfile
import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from lan_service import LANService, make_server
from web_service import WebService

if __name__ == '__main__':
    with tempfile.TemporaryDirectory() as directory:
        service = WebService(state_path=Path(directory) / 'state.json')
        adapter = LANService(service, ['http://127.0.0.1:4173'])
        server = make_server('127.0.0.1', 8766, adapter)
        # Test driver only. Production has no web approval route; the native manager calls decide().
        class TestSupervisor(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def do_POST(self):
                data = json.loads(self.rfile.read(int(self.headers['Content-Length'])))
                pending, _ = adapter.management_snapshot()
                item = next(p for p in pending if p['name'] == data['name'])
                if data.get('expire'):
                    with adapter.lock:
                        for request in adapter.pending.values():
                            if request['id'] == item['id']:
                                request['until'] = 0
                else:
                    adapter.decide(item['id'], data.get('allow', True))
                self.send_response(200)
                self.end_headers()

        supervisor = ThreadingHTTPServer(('127.0.0.1', 8767), TestSupervisor)
        threading.Thread(target=supervisor.serve_forever, daemon=True).start()
        try:
            server.serve_forever()
        finally:
            supervisor.shutdown()
            supervisor.server_close()
            server.server_close()
            service._shutdown()
