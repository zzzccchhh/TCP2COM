"""LAN HTTP adapter. Only this process owns serial ports and device sessions."""

import argparse
import hmac
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import ipaddress
import json
from pathlib import Path
import secrets
import threading
import time
from urllib.parse import urlsplit, unquote

from web_service import WebService
from lan_preferences import DEFAULT_PATH, Preferences, startup_settings

BASE = Path(__file__).resolve().parent
METHODS = frozenset("initial ports poll update_settings bridge usb remember_draft read_device preview_save save_device import_config backup_config clear_logs export_logs reset_click peers revoke".split())
READS = frozenset("initial ports poll peers export_logs".split())


class LANService:
    def __init__(self, service=None, origins=()):
        self.service = service or WebService()
        self.origins = set(origins)
        self.pending = {}
        self.clients = {}
        self.failures = {}
        self.lock = threading.RLock()
        self.command = threading.Lock()
        self.revision = 0
        self.reset_owner = None
        self.invite = secrets.token_urlsafe(24)
        self.join_attempts = {}
        self.revoked_joins = set()

    def rotate_invite(self):
        with self.lock:
            self.invite = secrets.token_urlsafe(24)
            return self.invite

    def connect(self, invite, name, address, join_id):
        """A native share link preauthorizes enrollment; never starts hardware."""
        with self.lock:
            if not isinstance(invite, str) or not hmac.compare_digest(invite, self.invite):
                raise PermissionError("连接链接已失效，请从服务端重新复制网址或扫码")
            if not isinstance(join_id, str) or not 24 <= len(join_id) <= 128:
                raise ValueError("配对连接标识不正确")
            if join_id in self.revoked_joins:
                raise PermissionError("终端已撤销，请重新打开服务端连接链接")
            if not isinstance(name, str) or not name.strip() or len(name) > 60:
                raise ValueError("配对终端名称应为 1～60 个字符")
            for token, client in self.clients.items():
                if client.get("join_id") == join_id:
                    return {"token": token}
            now = time.monotonic()
            self.join_attempts = {ip: times for ip, times in self.join_attempts.items() if times and now - times[-1] < 60}
            recent = [t for t in self.join_attempts.get(address, []) if now - t < 60]
            if len(recent) >= 20:
                raise ValueError("配对连接过于频繁，请稍后重试")
            if len(self.clients) >= 32:
                raise ValueError("已配对终端达到上限，请先撤销终端")
            self.join_attempts[address] = recent + [now]
            token = secrets.token_urlsafe(32)
            self.clients[token] = {"id": secrets.token_hex(8), "name": name.strip(), "address": address,
                                   "read_epoch": None, "join_id": join_id}
            return {"token": token}

    def _expire(self):
        now = time.monotonic()
        for key, item in list(self.pending.items()):
            if now > item["until"]:
                del self.pending[key]
        self.failures = {ip: times for ip, times in self.failures.items() if times and now - times[-1] < 60}

    def request_pair(self, name, address):
        with self.lock:
            self._expire()
            now = time.monotonic()
            recent = [t for t in self.failures.get(address, []) if now - t < 60]
            if len(recent) >= 5 or len(self.pending) >= 64:
                raise ValueError("配对申请过多，请一分钟后重试")
            if not isinstance(name, str) or not name.strip() or len(name) > 60:
                raise ValueError("配对终端名称应为 1～60 个字符")
            self.failures[address] = recent + [now]
            secret = secrets.token_urlsafe(32)
            item = {"id": secrets.token_hex(4).upper(), "name": name.strip(), "address": address,
                    "created": time.strftime("%H:%M:%S"), "until": now + 120, "state": "pending"}
            self.pending[secret] = item
            return {"request": secret, "id": item["id"], "expires_in": 120}

    def pairing_status(self, secret, cancel=False):
        with self.lock:
            self._expire()
            item = self.pending.get(secret)
            if not item:
                return {"state": "expired"}
            if cancel:
                if item.get("token"):
                    self.clients.pop(item["token"], None)
                item["state"] = "cancelled"
            return {"state": item["state"], **({"token": item["token"]} if item["state"] == "approved" else {})}

    def decide(self, request_id, allow):
        """Native supervisor only. This method is never exposed over HTTP."""
        with self.lock:
            self._expire()
            item = next((p for p in self.pending.values() if p["id"] == request_id), None)
            if not item or item["state"] != "pending":
                raise ValueError("申请已取消、过期或处理，请刷新列表")
            if allow:
                if len(self.clients) >= 32:
                    raise ValueError("已配对终端达到上限，请先撤销终端")
                token = secrets.token_urlsafe(32)
                self.clients[token] = {"id": secrets.token_hex(8), "name": item["name"], "address": item["address"], "read_epoch": None}
                item.update(state="approved", token=token)
            else:
                item["state"] = "rejected"

    def management_snapshot(self):
        with self.lock:
            self._expire()
            return ([{k: p[k] for k in ("id", "name", "address", "created")} for p in self.pending.values() if p["state"] == "pending"],
                    [{k: p[k] for k in ("id", "name", "address")} for p in self.clients.values()])

    def revoke_client(self, client_id):
        with self.lock:
            for key, value in list(self.clients.items()):
                if value["id"] == client_id:
                    del self.clients[key]
                    if value.get("join_id"):
                        self.revoked_joins.add(value["join_id"])
                    for item in self.pending.values():
                        if item.get("token") == key:
                            item["state"] = "cancelled"

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
                self.revoke_client(args[0])
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
            try:
                self.end_headers()
                self.wfile.write(body)
            except (ConnectionError, TimeoutError):
                pass  # A closed browser tab is not a service failure.

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
                if self.path == "/api/connect":
                    result = {"ok": True, "data": adapter.connect(data.get("invite"), data.get("name"), self.client_address[0], data.get("join_id"))}
                elif self.path == "/api/pair/request":
                    result = {"ok": True, "data": adapter.request_pair(data.get("name", ""), self.client_address[0])}
                elif self.path in ("/api/pair/status", "/api/pair/cancel"):
                    result = {"ok": True, "data": adapter.pairing_status(data.get("request", ""), self.path.endswith("cancel"))}
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
                self.reply(400, {"ok": False, "error": "请求失败，请同步状态并核对操作参数", "revision": adapter.revision})

        def do_GET(self):
            if not self.allowed():
                return self.reply(403, {"ok": False, "error": "来源未授权"})
            relative = unquote(urlsplit(self.path).path).lstrip("/") or "index.html"
            path = (root / relative).resolve()
            if not path.is_relative_to(root) or not path.is_file():
                return self.reply(404, {"ok": False, "error": "页面不存在，请先构建前端"})
            import mimetypes
            body = path.read_bytes()
            if path == root / "index.html":
                # Local pages work offline and can enroll directly on this service.
                bootstrap = '<script>window.__WIFI_BRIDGE__=' + json.dumps({"invite": adapter.invite}) + ';</script>'
                body = body.replace(b'<head>', b'<head>' + bootstrap.encode('utf-8'), 1)
            self.send_response(200)
            self.send_header("Content-Type", mimetypes.guess_type(path.name)[0] or "application/octet-stream")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store" if path == root / "index.html" else "no-cache")
            if path == root / "index.html":
                self.send_header("Referrer-Policy", "no-referrer")
                self.send_header("Content-Security-Policy", "frame-ancestors 'none'")
            self.send_header("X-Content-Type-Options", "nosniff")
            try:
                self.end_headers()
                self.wfile.write(body)
            except (ConnectionError, TimeoutError):
                pass

    server = ThreadingHTTPServer((host, port), Handler)
    server.daemon_threads = True
    return server


def main():
    parser = argparse.ArgumentParser(description="Wi-Fi Bridge 局域网多端服务")
    parser.add_argument("--listen", help="监听 IPv4 地址；未指定时恢复上次设置")
    parser.add_argument("--port", type=int, help="监听端口；未指定时恢复上次设置")
    parser.add_argument("--origin", action="append", help="允许的网页来源；未指定时恢复上次设置")
    parser.add_argument("--headless", action="store_true", help="只运行服务，不显示管理窗口")
    args = parser.parse_args()
    if not (BASE / "frontend" / "dist" / "index.html").is_file():
        raise ValueError("本地网页尚未构建。请在 frontend 目录运行 npm ci 和 npm run build，再启动局域网服务。")
    preferences = Preferences(DEFAULT_PATH)
    startup_warning = preferences.warning
    settings = startup_settings(preferences.data, args.listen, args.port, args.origin)
    adapter = LANService(origins=settings["origins"])
    try:
        server = make_server(settings["listen"], settings["port"], adapter)
    except OSError:
        adapter.service._shutdown()
        raise
    server.preferences = preferences
    server.startup_warning = startup_warning
    if preferences.warning:
        print(preferences.warning, flush=True)
    preferences.update(listen=settings["listen"], port=settings["port"], origins=settings["origins"])
    if preferences.warning:
        print(preferences.warning, flush=True)
    from lan_network import discover_addresses
    addresses, warning = discover_addresses(server.server_address[0])
    if warning:
        print(warning, flush=True)
    for address in addresses:
        print(f"{address.name}：{address.url(server.server_port)}", flush=True)
    print("在线工作台：https://zzzccchhh.github.io/TCP2COM/\n使用服务管理窗口的打开网页、复制网址或二维码即可自动连接。", flush=True)
    try:
        if args.headless:
            server.serve_forever()
        else:
            from lan_manager import run_manager
            run_manager(server, adapter)
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
        adapter.service._shutdown()


if __name__ == "__main__":
    main()
