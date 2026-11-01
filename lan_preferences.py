"""Local LAN launcher preferences; never stores pairing or device credentials."""

import copy
import ipaddress
import json
import os
from pathlib import Path
import tempfile
from urllib.parse import urlsplit

DEFAULT_PATH = Path(__file__).resolve().parent / "lan_service_state.json"
DEFAULTS = {"version": 1, "listen": "0.0.0.0", "port": 8765,
            "origins": ["https://zzzccchhh.github.io"], "preferred_interface": {}}


def clean(document):
    if not isinstance(document, dict) or type(document.get("version")) is not int or document.get("version") != 1:
        raise ValueError("服务设置版本不正确")
    result = copy.deepcopy(DEFAULTS)
    host = document.get("listen", result["listen"])
    if not isinstance(host, str):
        raise ValueError("服务监听地址不正确")
    result["listen"] = "127.0.0.1" if host == "localhost" else str(ipaddress.IPv4Address(host))
    port = document.get("port", result["port"])
    if type(port) is not int or not 1 <= port <= 65535:
        raise ValueError("服务端口应为 1～65535")
    result["port"] = port
    origins = document.get("origins", result["origins"])
    if not isinstance(origins, list) or not 1 <= len(origins) <= 32:
        raise ValueError("网页来源不正确")
    result["origins"] = []
    for origin in origins:
        if not isinstance(origin, str) or len(origin) > 512:
            raise ValueError("网页来源不正确")
        url = urlsplit(origin)
        if url.scheme not in ("http", "https") or not url.hostname or url.username or url.password or url.path not in ("", "/") or url.query or url.fragment:
            raise ValueError("网页来源不正确")
        if url.port is not None and not 1 <= url.port <= 65535:
            raise ValueError("网页来源端口不正确")
        result["origins"].append(f"{url.scheme}://{url.netloc}")
    preferred = document.get("preferred_interface", {})
    if not isinstance(preferred, dict):
        raise ValueError("首选网卡设置不正确")
    if preferred:
        if not all(isinstance(preferred.get(key), str) and 0 < len(preferred[key]) <= 512 for key in ("id", "name", "ip")):
            raise ValueError("首选网卡设置不正确")
        result["preferred_interface"] = {key: preferred[key] for key in ("id", "name", "ip")}
        result["preferred_interface"]["ip"] = str(ipaddress.IPv4Address(preferred["ip"]))
    return result


class Preferences:
    def __init__(self, path=None):
        self.path = Path(path) if path else None
        self.data = copy.deepcopy(DEFAULTS)
        self.warning = ""
        if self.path:
            try:
                with self.path.open("rb") as stream:
                    raw = stream.read(65537)
                if len(raw) > 65536:
                    raise ValueError("服务设置文件过大")
                self.data = clean(json.loads(raw.decode("utf-8-sig")))
            except FileNotFoundError:
                pass
            except (ValueError, OSError, TypeError):
                self.warning = "无法读取服务设置，已使用默认值；选择网卡后会重新保存。"

    def update(self, **values):
        self.data = clean({**self.data, **values})
        if self.path:
            temporary = None
            try:
                with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=self.path.parent, prefix=self.path.name + ".", suffix=".tmp", delete=False) as stream:
                    temporary = Path(stream.name)
                    json.dump(self.data, stream, ensure_ascii=False, indent=2)
                    stream.write("\n")
                    stream.flush()
                    os.fsync(stream.fileno())
                os.replace(temporary, self.path)
            except OSError:
                self.warning = "无法保存服务设置，请检查目录权限；当前选择仍然有效。"
                return False
            finally:
                if temporary and temporary.exists():
                    try:
                        temporary.unlink(missing_ok=True)
                    except OSError:
                        pass
        self.warning = ""
        return True


def startup_settings(saved, listen=None, port=None, origins=None):
    """Command-line values override stored values; omitted flags retain preferences."""
    values = copy.deepcopy(saved)
    if listen is not None:
        values["listen"] = listen
    if port is not None:
        values["port"] = port
    if origins is not None:
        values["origins"] = list(dict.fromkeys(DEFAULTS["origins"] + origins))
    return clean(values)
