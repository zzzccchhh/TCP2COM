"""C5 setup API and password-free configuration snapshots (no automatic POST retry)."""

import copy
import http.client
import ipaddress
import json
import re
from urllib.parse import urlsplit

from bridge_settings import _unique_object


class ConfigError(ValueError):
    pass


class SaveOutcomeUnknown(ConfigError):
    """The device may have committed NVS; require a fresh read before another save."""


TOP_FIELDS = ("link_mode", "mode", "band", "baud", "tcp_port", "ap_channel",
              "ap_ssid", "ap_ip", "ap_gateway", "ap_netmask")
STATION_FIELDS = ("ssid", "static_ip", "ip", "gateway", "netmask", "dns")


def public_config(document):
    """Whitelist snapshots, so passwords and future private fields cannot leak to disk."""
    if not isinstance(document, dict):
        raise ConfigError("配置必须是 JSON 对象")
    try:
        result = {key: copy.deepcopy(document[key]) for key in TOP_FIELDS}
        for name in ("pc", "shared"):
            result[name] = {key: copy.deepcopy(document[name][key]) for key in STATION_FIELDS}
    except (KeyError, TypeError) as exc:
        raise ConfigError("配置缺少必要字段，请检查固件版本") from exc
    validate_config(result)
    return result


def _text(value, label, maximum, minimum=0):
    if not isinstance(value, str) or "\0" in value or not minimum <= len(value.encode("utf-8")) <= maximum:
        raise ConfigError(f"{label} 长度应为 {minimum}～{maximum} 个 UTF-8 字节，不能含 NUL")


def _address(value):
    try:
        if not isinstance(value, str) or len(value) > 15:
            raise ValueError
        parts = value.split(".")
        if len(parts) != 4 or any(not p.isascii() or not p.isdigit() or len(p) > 3
                                 or (len(p) > 1 and p[0] == "0") for p in parts):
            raise ValueError
        return int(ipaddress.IPv4Address(".".join(str(int(p)) for p in parts)))
    except ValueError as exc:
        raise ConfigError("请输入合法 IPv4 地址") from exc


def _network(ip, gateway, netmask, ap=False):
    ip, gateway, mask = map(_address, (ip, gateway, netmask))
    hosts = (~mask) & 0xffffffff
    if not mask or hosts < 3 or hosts & (hosts + 1):
        raise ConfigError("子网掩码必须连续，并保留足够主机地址")
    if ip >> 24 in (0, 127) or ip >> 24 >= 224 or ip & hosts in (0, hosts):
        raise ConfigError("IP 不能为网络、广播、回环或组播地址")
    if gateway & mask != ip & mask or gateway & hosts in (0, hosts):
        raise ConfigError("网关必须为同子网有效主机地址")
    if ap:
        if gateway >> 24 in (0, 127) or gateway >> 24 >= 224:
            raise ConfigError("AP 网关不能为保留、回环或组播地址")
        if not any(h + 1 < hosts and h not in (ip & hosts, gateway & hosts)
                   and h + 1 not in (ip & hosts, gateway & hosts) for h in range(1, 6)):
            raise ConfigError("AP 子网需为 DHCP 留出两个连续可用地址")
    elif gateway == ip:
        raise ConfigError("STA IP 不能与网关相同")


def validate_config(config):
    for key, low, high in (("link_mode", 0, 1), ("mode", 0, 2), ("band", 0, 2),
                           ("baud", 9600, 5000000), ("tcp_port", 1024, 65535), ("ap_channel", 1, 165)):
        if type(config.get(key)) is not int or not low <= config[key] <= high:
            raise ConfigError(f"{key} 必须为 {low}～{high} 的整数")
    _text(config.get("ap_ssid"), "AP SSID", 32, 1)
    if "ap_password" in config:
        _text(config["ap_password"], "AP 密码", 63, 8)
    channels = (36, 40, 44, 48, 149, 153, 157, 161, 165) if config["band"] == 1 else range(1, 14)
    if config["ap_channel"] not in channels:
        raise ConfigError("信道与频段不匹配：5 GHz 用 36/40/44/48/149/153/157/161/165；AUTO AP 用 2.4 GHz")
    _network(config.get("ap_ip"), config.get("ap_gateway"), config.get("ap_netmask"), ap=True)
    for index, name in enumerate(("pc", "shared"), 1):
        station = config.get(name)
        if not isinstance(station, dict) or type(station.get("static_ip")) is not bool:
            raise ConfigError(f"{name}.static_ip 必须是布尔值")
        _text(station.get("ssid"), f"{name} SSID", 32, int(config["mode"] == index))
        if "password" in station:
            password = station["password"]
            _text(password, f"{name} 密码", 63)
            if password and len(password.encode("utf-8")) < 8:
                raise ConfigError(f"{name} 密码应为空（开放网络）或 8～63 字节")
        for key in ("ip", "gateway", "netmask", "dns"):
            _text(station.get(key), f"{name}.{key}", 15)
        if station["static_ip"]:
            _network(station["ip"], station["gateway"], station["netmask"])
            _address(station["dns"])


def changed_config(baseline, edited, passwords=None, clear_passwords=()):
    """Omitted/blank passwords preserve the existing secret; clearing is explicit."""
    base = public_config(baseline)
    candidate = public_config(edited)
    passwords = passwords or {}
    for name in ("ap", "pc", "shared"):
        value = passwords.get(name, "")
        if name in clear_passwords:
            if name == "ap" or value:
                raise ConfigError("清空密码只能用于 STA 开放网络，且不能同时填写新密码")
            value = ""
        elif not value:
            continue
        if name == "ap":
            candidate["ap_password"] = value
        else:
            candidate[name]["password"] = value
    validate_config(candidate)
    patch = {key: candidate[key] for key in TOP_FIELDS if candidate[key] != base[key]}
    if "ap_password" in candidate:
        patch["ap_password"] = candidate["ap_password"]
    for name in ("pc", "shared"):
        delta = {key: value for key, value in candidate[name].items() if base[name].get(key) != value}
        if delta:
            patch[name] = delta
    if len(json.dumps(patch, ensure_ascii=False, separators=(",", ":")).encode("utf-8")) > 2048:
        raise ConfigError("修改内容超过固件 2048 字节限制，请分批保存")
    return patch


class C5Client:
    def __init__(self, url, timeout=4):
        parts = urlsplit(url)
        if (parts.scheme not in ("http", "https") or not parts.hostname or parts.username
                or parts.password or parts.query or parts.fragment or parts.path not in ("", "/", "/api/config")):
            raise ConfigError("配置地址应为 http://设备IP/，不能含账号、查询参数或其他路径")
        self.scheme = parts.scheme
        self.host = parts.hostname
        try:
            self.port = parts.port or (443 if self.scheme == "https" else 80)
        except ValueError as exc:
            raise ConfigError("配置地址端口无效") from exc
        self.timeout = timeout
        self.identity = (self.scheme, self.host.casefold(), self.port)
        self.http_session = None

    def bind_session(self, token):
        if not isinstance(token, str) or not re.fullmatch(r"[0-9a-f]{16}-[1-9][0-9]{0,9}", token):
            raise ConfigError("缺少有效的 HTTP 配网会话，请更新固件并重新读取")
        self.http_session = token
        self.identity = self.identity[:3] + (token,)

    def _request(self, method, patch=None):
        cls = http.client.HTTPSConnection if self.scheme == "https" else http.client.HTTPConnection
        connection = cls(self.host, self.port, timeout=self.timeout)
        sent = False
        try:
            connection.connect()  # Connection failure proves no POST has been transmitted.
            body = None if patch is None else json.dumps(patch, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
            if body is not None and not 1 <= len(body) <= 2048:
                raise ConfigError("请求体必须为 1～2048 字节")
            sent = method == "POST"
            headers = {"Content-Type": "application/json", "Connection": "close"}
            if method == "POST":
                headers["X-C5-Session"] = self.http_session
            connection.request(method, "/api/config", body=body, headers=headers)
            response = connection.getresponse()
            if response.status == 403:
                raise ConfigError("403：长按 GPIO24 至少 3 秒后松开，连接 ESP32-C5-Setup，再读取配置")
            if response.status == 503:
                raise ConfigError("503：配网会话已失效或设备正在重启，请重新进入配网并读取")
            if response.status != 200:
                # Do not echo device bodies: they could contain submitted credentials.
                if method == "POST" and response.status >= 500:
                    raise SaveOutcomeUnknown(f"HTTP {response.status}：保存结果待确认，请重新进入配置热点读取")
                raise ConfigError(f"HTTP {response.status}：配置请求被拒绝，请检查配置和固件")
            raw = response.read(8193)
            if len(raw) > 8192:
                raise ValueError("响应过大")
            result = json.loads(raw.decode("utf-8"), object_pairs_hook=_unique_object)
            if not isinstance(result, dict):
                raise ValueError("响应不是对象")
            return result
        except ConfigError:
            raise
        except (OSError, http.client.HTTPException, ValueError, UnicodeError) as exc:
            if sent:
                raise SaveOutcomeUnknown("保存结果待确认：请求可能已写入 NVS；请重新进入配置热点读取，勿直接重发") from exc
            raise ConfigError("无法读取配置：连接超时、断开或响应格式不正确") from exc
        finally:
            connection.close()

    def read(self):
        self.http_session = None
        self.identity = self.identity[:3]
        document = self._request("GET")
        result = public_config(document)
        self.bind_session(document.get("http_session"))
        result["config_source"] = str(document.get("config_source", "unknown"))
        return result

    def save(self, patch):
        if self.http_session is None:
            raise ConfigError("请先从当前设备读取配置，再保存")
        result = self._request("POST", patch)
        if result.get("saved") is not True or result.get("restart") is not True:
            raise SaveOutcomeUnknown("保存结果待确认：未收到 saved/restart 确认，请重新进入配置热点读取")
        return result
