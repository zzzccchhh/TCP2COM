"""LAN HTTP adapter. Only this process owns serial ports and device sessions."""

import argparse
import hmac
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import ipaddress
import json
from pathlib import Path
import secrets
import socket
import threading
import time
from urllib.parse import urlsplit, unquote

from web_service import WebService

BASE = Path(__file__).resolve().parent
METHODS = frozenset("initial ports poll update_settings bridge usb remember_draft read_device preview_save save_device import_config backup_config clear_logs export_logs reset_click peers revoke".split())
READS = frozenset("initial ports poll peers export_logs".split())


class LANService:
    def __init__(self, service=None, origins=()):
        self.service = service or WebService()
        self.origins = set(origins)
        self.code = secrets.token_hex(4).upper()
        self.code_until = time.monotonic() + 600
        self.clients = {}
        self.failures = {}
        self.lock = threading.RLock()
        self.command = threading.Lock()
        self.revision = 0
        self.reset_owner = None

    def pair(self, code, name, address):
        with self.lock:
            now = time.monotonic()
            attempts, retry = self.failures.get(address, (0, 0))
            if retry > now:
                raise ValueError("配对尝试过多，请一分钟后重试")
            if now > self.code_until or not hmac.compare_digest(str(code), self.code):
                self.failures[address] = (attempts + 1, now + 60 if attempts >= 4 else 0)
                raise ValueError("配对码不正确或已过期，请在服务电脑查看新码")
            if len(self.clients) >= 32:
                raise ValueError("已配对终端达到上限，请撤销不用的终端")
            self.failures.pop(address, None)
            token = secrets.token_urlsafe(32)
            self.clients[token] = {"id": secrets.token_hex(8), "name": str(name)[:60] or "浏览器", "read_epoch": None}
            return {"token": token}

    def dispatch(self, token, method, args, revision=None):
        with self.lock:
            client = self.clients.get(token)
            if not client:
                raise PermissionError("配对已失效，请重新配对")
        if method not in METHODS or not isinstance(args, list):
            raise ValueError("未知操作")
        mutating = method not in READS
        acquired = self.command.acquire(timeout=.25 if mutating else 30)
        if not acquired:
            raise ValueError("其他终端正在操作，请等待完成后重试")
        try:
            with self.lock:
                if token not in self.clients:
                    raise PermissionError("终端已撤销")
                if mutating and revision != self.revision:
                    raise ValueError("其他终端已更新设置，请同步后检查并重试")
            if method == "peers":
                with self.lock:
                    result = {"ok": True, "data": [{"id": c["id"], "name": c["name"], "self": t == token} for t, c in self.clients.items()]}
            elif method == "revoke":
                with self.lock:
                    for key, value in list(self.clients.items()):
                        if value["id"] == args[0]:
                            del self.clients[key]
                result = {"ok": True, "data": True}
            elif method == "remember_draft":
                # Each browser keeps its own in-memory draft; never overwrite another terminal's draft.
                result = {"ok": True, "data": True}
            else:
                if method in ("preview_save", "save_device"):
                    with self.service._lock:
                        if client["read_epoch"] != self.service._epoch or self.service._baseline is None:
                            raise ValueError("请在本终端重新读取设备，再检查修改")
                if method == "reset_click":
                    with self.service._lock:
                        if self.reset_owner != token:
                            if self.service._gate.busy:
                                raise ValueError("复位正在进行")
                            self.service._gate.finish()
                            self.reset_owner = token
                result = getattr(self.service, method)(*args)
                if method == "read_device" and result["ok"]:
                    client["read_epoch"] = self.service._epoch
                if method == "initial" and result["ok"]:
                    result["data"]["drafts"] = {}
                if method == "poll" and result["ok"]:
                    initial = self.service.initial()
                    result["data"]["settings"] = initial["data"]["settings"]
            with self.lock:
                if mutating:
                    self.revision += 1
                result["revision"] = self.revision
            return result
        finally:
            self.command.release()


def make_server(host, port, adapter, dist=None):
    root = (Path(dist) if dist else BASE / "frontend" / "dist").resolve()

    class Handler(BaseHTTPRequestHandler):
        def setup(self):
            super().setup()
            self.connection.settimeout(10)

        def log_message(self, *args):
            pass  # Requests can carry credentials; never log request bodies or headers.

        def allowed(self):
            try:
                if not ipaddress.ip_address(self.client_address[0]).is_private:
                    return False
            except ValueError:
                return False
            authority = self.headers.get("Host", "")
            if not authority or any(c in authority for c in "/\\@#?"):
                return False
            try:
                name = urlsplit("http://" + authority).hostname
                local_host = name == "localhost" or ipaddress.ip_address(name).is_private
            except ValueError:
                local_host = False
            origin = self.headers.get("Origin")
            return local_host and (not origin or origin == "http://" + authority or origin in adapter.origins)

        def reply(self, status, data):
            body = json.dumps(data, ensure_ascii=False).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Cache-Control", "no-store")
            origin = self.headers.get("Origin")
            if origin and self.allowed():
                self.send_header("Access-Control-Allow-Origin", origin)
                self.send_header("Vary", "Origin")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_OPTIONS(self):
            if not self.allowed():
                return self.reply(403, {"ok": False, "error": "来源未授权"})
            self.send_response(204)
            self.send_header("Access-Control-Allow-Origin", self.headers.get("Origin", ""))
            self.send_header("Access-Control-Allow-Methods", "POST, OPTIONS")
            self.send_header("Access-Control-Allow-Headers", "Content-Type, Authorization")
            self.send_header("Access-Control-Allow-Private-Network", "true")
            self.end_headers()

        def do_POST(self):
            if not self.allowed():
                return self.reply(403, {"ok": False, "error": "来源未授权"})
            self.connection.settimeout(10)
            try:
                length = int(self.headers.get("Content-Length", "0"))
                if not 0 < length <= 262144 or self.headers.get_content_type() != "application/json":
                    raise ValueError("请求格式不正确或过大")
                data = json.loads(self.rfile.read(length))
                if not isinstance(data, dict):
                    raise ValueError("请求格式不正确")
                if self.path == "/api/pair":
                    result = {"ok": True, "data": adapter.pair(data.get("code", ""), data.get("name", ""), self.client_address[0])}
                elif self.path == "/api/call":
                    auth = self.headers.get("Authorization", "")
                    token = auth[7:] if auth.startswith("Bearer ") else ""
                    result = adapter.dispatch(token, data.get("method"), data.get("args"), data.get("revision"))
                else:
                    return self.reply(404, {"ok": False, "error": "接口不存在"})
                self.reply(200, result)
            except PermissionError as exc:
                self.reply(401, {"ok": False, "error": str(exc)})
            except ValueError as exc:
                self.reply(400, {"ok": False, "error": str(exc) if str(exc).startswith(("配对", "其他终端", "请在本终端", "已配对", "未知操作", "复位正在")) else "请求格式不正确", "revision": adapter.revision})
            except (TypeError, KeyError, IndexError, OSError):
                # Do not expose hardware paths, submitted passwords or raw parser exceptions.
                self.reply(400, {"ok": False, "error": "请求失败，请同步状态并核对配对码或操作参数" , "revision": adapter.revision})

        def do_GET(self):
            if not self.allowed():
                return self.reply(403, {"ok": False, "error": "来源未授权"})
            relative = unquote(urlsplit(self.path).path).lstrip("/") or "index.html"
            path = (root / relative).resolve()
            if not path.is_relative_to(root) or not path.is_file():
                return self.reply(404, {"ok": False, "error": "页面不存在，请先构建前端"})
            import mimetypes
            body = path.read_bytes()
            self.send_response(200)
            self.send_header("Content-Type", mimetypes.guess_type(path.name)[0] or "application/octet-stream")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-cache")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.end_headers()
            self.wfile.write(body)

    server = ThreadingHTTPServer((host, port), Handler)
    server.daemon_threads = True
    return server


def main():
    parser = argparse.ArgumentParser(description="Wi-Fi Bridge 局域网多端服务")
    parser.add_argument("--listen", default="0.0.0.0")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--origin", action="append", default=["https://zzzccchhh.github.io"])
    args = parser.parse_args()
    adapter = LANService(origins=args.origin)
    server = make_server(args.listen, args.port, adapter)
    print(f"本机页面：http://127.0.0.1:{args.port}/", flush=True)
    for address in sorted({x[4][0] for x in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET)}):
        print(f"局域网页面：http://{address}:{args.port}/", flush=True)
    print(f"配对码（10 分钟有效）：{adapter.code}\n服务重启后所有终端需重新配对。Ctrl+C 停止。", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
        adapter.service._shutdown()


if __name__ == "__main__":
    main()
