"""C5/S3 setup API and password-free snapshots (no automatic POST retry)."""

import copy
import http.client
import ipaddress
import json
import re
from urllib.parse import urlsplit

from bridge_settings import _unique_object
from device_capabilities import capabilities


class ConfigError(ValueError):
    pass


class SaveOutcomeUnknown(ConfigError):
    """The device may have committed NVS; require a fresh read before another save."""


TOP_FIELDS = ("link_mode", "mode", "band", "baud", "tcp_port", "ap_channel",
              "ap_ssid", "ap_ip", "ap_gateway", "ap_netmask")
NETWORK_FIELDS = ("net_role", "auto_reconnect", "remote_ip", "remote_port", "local_port",
                  "reconnect_ms", "connect_timeout_ms", "up_kb", "down_kb")
CONFIG_APIS = {"c5": "c5-wbr-1", "s3": "bridge-1"}
STATION_FIELDS = ("ssid", "static_ip", "ip", "gateway", "netmask", "dns")


def public_config(document, device="c5"):
    """Whitelist snapshots, so passwords and future private fields cannot leak to disk."""
    if not isinstance(document, dict):
        raise ConfigError("配置必须是 JSON 对象")
    try:
        result = {key: copy.deepcopy(document[key]) for key in TOP_FIELDS}
        for name in ("pc", "shared"):
            result[name] = {key: copy.deepcopy(document[name][key]) for key in STATION_FIELDS}
    except (KeyError, TypeError) as exc:
        raise ConfigError("配置缺少必要字段，请检查固件版本") from exc
    if device == "c5" and "reset_pin" in document:
        result["reset_pin"] = copy.deepcopy(document["reset_pin"])
    for key in NETWORK_FIELDS:
        if key in document:
            result[key] = copy.deepcopy(document[key])
    api = document.get("config_api")
    if api is not None:
        if api != CONFIG_APIS.get(device):
            raise ConfigError("设备配置接口与所选型号不匹配或版本不支持，请重新选择并读取")
        if "revision" not in document or any(key not in document for key in NETWORK_FIELDS):
            raise ConfigError("新固件配置缺少修订号或网络字段，请重新读取并检查固件版本")
        result["config_api"] = api
    for key in ("revision", "saved_revision"):
        if key in document:
            if type(document[key]) is not int or not 0 <= document[key] <= 0xffffffff:
                raise ConfigError(f"{key} 必须为有效的配置修订号，请重新读取")
            result[key] = document[key]
    for key in ("unsaved", "migration_requires_setup"):
        if key in document:
            if type(document[key]) is not bool:
                raise ConfigError("设备配置状态格式不正确，请重新读取")
            result[key] = document[key]
    validate_config(result, device)
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


def validate_config(config, device="c5"):
    for key, low, high in (("link_mode", 0, 2), ("mode", 0, 2), ("band", 0, 2),
                           ("baud", 9600, 5000000), ("tcp_port", 1024, 65535), ("ap_channel", 1, 165)):
        if type(config.get(key)) is not int or not low <= config[key] <= high:
            raise ConfigError(f"{key} 必须为 {low}～{high} 的整数")
    if "reset_pin" in config and (device != "c5" or type(config["reset_pin"]) is not int or not 0 <= config["reset_pin"] <= 4):
        raise ConfigError("复位线必须为 0～4")
    if config.get("reset_pin", 0) and config["link_mode"] != 1 and (
            (config.get("net_role", 0) == 0 and config["tcp_port"] == 8081) or
            (config.get("net_role", 0) == 1 and config.get("local_port") == 8081)):
        raise ConfigError("复位控制 TCP 端口 8081 与业务端口冲突")
    for key, low, high in (("net_role",0,2),("remote_port",1,65535),("local_port",0,65535),
                           ("reconnect_ms",100,60000),("connect_timeout_ms",100,60000),
                           ("up_kb",64,6144),("down_kb",64,2048)):
        if key in config and (type(config[key]) is not int or not low <= config[key] <= high):
            raise ConfigError(f"{key} 必须为 {low}～{high} 的整数")
    if "auto_reconnect" in config and type(config["auto_reconnect"]) is not bool:
        raise ConfigError("auto_reconnect 必须为布尔值")
    if "remote_ip" in config:
        ip = _address(config["remote_ip"])
        if ip >> 24 in (0,127) or ip >> 24 >= 224:
            raise ConfigError("目标 IP 无效")
    if config.get("up_kb",64) + config.get("down_kb",64) > 6144:
        raise ConfigError("缓冲总容量不得超过 6144 KiB")
    _text(config.get("ap_ssid"), "AP SSID", 32, 1)
    if config["band"] not in capabilities(device).bands:
        raise ConfigError(f"{capabilities(device).label} 仅支持 2.4 GHz，频段必须为 0")
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


def changed_config(baseline, edited, passwords=None, clear_passwords=(), device="c5"):
    """Omitted/blank passwords preserve the existing secret; clearing is explicit."""
    base = public_config(baseline, device)
    candidate = public_config(edited, device)
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
    validate_config(candidate, device)
    patch = {key: candidate[key] for key in TOP_FIELDS + NETWORK_FIELDS + ("reset_pin",)
             if key in candidate and candidate[key] != base.get(key)}
    if "ap_password" in candidate:
        patch["ap_password"] = candidate["ap_password"]
    for name in ("pc", "shared"):
        delta = {key: value for key, value in candidate[name].items() if base[name].get(key) != value}
        if delta:
            patch[name] = delta
    if len(json.dumps(patch, ensure_ascii=False, separators=(",", ":")).encode("utf-8")) > 2048:
        raise ConfigError("修改内容超过固件 2048 字节限制，请分批保存")
    return patch


def validate_patch(patch, device, baseline=None):
    if not isinstance(patch, dict):
        raise ConfigError("修改内容必须是 JSON 对象")
    if "band" in patch and (type(patch["band"]) is not int or patch["band"] not in capabilities(device).bands):
        raise ConfigError(f"{capabilities(device).label} 不支持该频段")
    if device == "s3" and "ap_channel" in patch and (
            type(patch["ap_channel"]) is not int or not 1 <= patch["ap_channel"] <= 13):
        raise ConfigError("S3 的 2.4 GHz 信道必须为 1～13")
    if baseline is not None:
        merged = copy.deepcopy(baseline)
        for key, value in patch.items():
            if key in ("pc", "shared"):
                if not isinstance(value, dict):
                    raise ConfigError(f"{key} 必须是对象")
                merged[key].update(value)
            else:
                merged[key] = value
        validate_config(merged, device)


class C5Client:
    def __init__(self, url, timeout=4, *, device="c5"):
        self.device = device
        self.capabilities = capabilities(device)
        self.baseline = None
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
        self.session_header = "X-C5-Session"

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
                if self.baseline and self.baseline.get("config_api") == "bridge-1":
                    self.session_header = "X-Bridge-Session"
                headers[self.session_header] = self.http_session
            connection.request(method, "/api/config", body=body, headers=headers)
            response = connection.getresponse()
            if response.status == 403:
                raise ConfigError(f"403：{self.capabilities.setup_hint}")
            if response.status == 503:
                raise ConfigError("503：配网会话已失效、配置修订号已变化或设备正在重启，请重新进入配网并读取")
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
        self.baseline = None
        self.http_session = None
        self.identity = self.identity[:3]
        document = self._request("GET")
        self.session_header = "X-Bridge-Session" if document.get("config_api") == "bridge-1" else "X-C5-Session"
        result = public_config(document, self.device)
        self.bind_session(document.get("http_session"))
        result["config_source"] = str(document.get("config_source", "unknown"))
        self.baseline = copy.deepcopy(result)
        return result

    def save(self, patch):
        if self.http_session is None:
            raise ConfigError("请先从当前设备读取配置，再保存")
        validate_patch(patch, self.device, self.baseline)
        patch = copy.deepcopy(patch)
        if self.baseline and "revision" in self.baseline:
            patch["revision"] = self.baseline["revision"]
        result = self._request("POST", patch)
        if result.get("saved") is not True or result.get("restart") is not True:
            raise SaveOutcomeUnknown("保存结果待确认：未收到 saved/restart 确认，请重新进入配置热点读取")
        return result
