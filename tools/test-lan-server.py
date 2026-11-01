"""Isolated browser test fixture: loopback only, temporary settings, no devices opened."""
from pathlib import Path
import sys
import tempfile

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from lan_service import LANService, make_server
from web_service import WebService

if __name__ == '__main__':
    with tempfile.TemporaryDirectory() as directory:
        service = WebService(state_path=Path(directory) / 'state.json')
        adapter = LANService(service, ['http://127.0.0.1:4173'])
        adapter.code = 'TEST1234'
        server = make_server('127.0.0.1', 8766, adapter)
        try:
            server.serve_forever()
        finally:
            server.server_close()
            service._shutdown()
