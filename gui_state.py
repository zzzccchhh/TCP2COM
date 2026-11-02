"""Versioned, password-free GUI drafts; never open a port or validate runtime I/O."""

import json
from pathlib import Path

from bridge_settings import _unique_object
from gui_support import atomic_json

VERSION = 1
MAX_BYTES = 131072
FORM_FIELDS = {
    "network": ("device", "mode", "host", "listen", "port", "peer_port", "udp_payload"),
    "local": ("com", "baud", "bytesize", "parity", "stopbits", "flow"),
    "advanced": ("timeout", "write_timeout", "reconnect", "connect_timeout", "keepalive_idle",
                 "keepalive_interval", "keepalive_count", "bufsize", "stats_interval", "control_port",
                 "no_reset_ui", "verbose"),
    "usb_form": ("usb_port", "usb_baud", "usb_log"),
}
DRAFT_FIELDS = {
    "common": ("link_mode", "flow_pin", "reset_pin", "mode", "band", "baud", "tcp_port", "net_role", "auto_reconnect",
               "remote_ip", "remote_port", "local_port", "reconnect_ms", "connect_timeout_ms", "up_kb", "down_kb"),
    "ap": ("ap_ssid", "ap_channel", "ap_ip", "ap_gateway", "ap_netmask"),
    "pc": ("ssid", "static_ip", "ip", "gateway", "netmask", "dns"),
    "shared": ("ssid", "static_ip", "ip", "gateway", "netmask", "dns"),
}
CHOICES = {
    ("network", "device"): ("esp01s", "c5", "s3"),
    ("network", "mode"): ("tcp-client", "tcp-server", "udp"),
    ("local", "bytesize"): ("5", "6", "7", "8"),
    ("local", "parity"): ("N", "E", "O", "M", "S"),
    ("local", "stopbits"): ("1", "1.5", "2"),
    ("local", "flow"): ("none", "rtscts", "dsrdtr"),
    ("common", "link_mode"): (-1, 0, 1, 2, 3),
    ("common", "flow_pin"): (0, 1, 2, 3, 4),
    ("common", "reset_pin"): (0, 1, 2, 3, 4),
    ("common", "mode"): (0, 1, 2),
    ("common", "band"): (0, 1, 2),
    ("common", "net_role"): (0, 1, 2),
    ("profile", "path_base"): ("program", "absolute"),
}
BOOLEANS = {"no_reset_ui", "verbose", "auto_reconnect", "static_ip"}


def _group(document, fields, group, warnings, device=None):
    result = {}
    if not isinstance(document, dict):
        warnings.append(group)
        return result
    for key in fields:
        if key not in document:
            continue
        value = document[key]
        if device == "c5" and key == "link_mode" and value in (2, "2"):
            result[key] = -1
            warnings.append("common.link_mode（旧接口已移除，请重新选择）")
            continue
        choices = CHOICES.get((group, key))
        if choices:
            valid = type(value) is type(choices[0]) and value in choices
            if key == "flow_pin" and device != "c5":
                valid = False
            if key == "link_mode" and device != "c5" and value in (-1, 3):
                valid = False
            if device == "s3" and key == "band":
                valid = valid and value == 0
        elif key in BOOLEANS:
            valid = type(value) is bool
        else:
            valid = isinstance(value, str) and len(value) <= 4096 and "\0" not in value
        if valid:
            result[key] = value
        else:
            warnings.append(f"{group}.{key}")
    return result


def clean_state(document):
    """Whitelist both reads and writes; sessions, passwords and clear intents vanish."""
    if not isinstance(document, dict) or type(document.get("version")) is not int or document["version"] != VERSION:
        raise ValueError("上次设置的版本或格式不支持")
    warnings = []
    result = {"version": VERSION, "forms": {}, "device_drafts": {}}
    forms = document.get("forms", {})
    if not isinstance(forms, dict):
        forms = {}
        warnings.append("forms")
    for group, fields in FORM_FIELDS.items():
        result["forms"][group] = _group(forms.get(group, {}), fields, group, warnings)
    if "profile" in document:
        result["profile"] = _group(document["profile"], ("path", "name", "path_base"), "profile", warnings)
    for key, choices in (("config_transport", ("http", "usb")),):
        if key in document:
            if document[key] in choices:
                result[key] = document[key]
            else:
                warnings.append(key)
    if "config_url" in document:
        result.update(_group(document, ("config_url",), "connection", warnings))
    drafts = document.get("device_drafts", {})
    if isinstance(drafts, dict):
        for device in ("c5", "s3"):
            if device not in drafts:
                continue
            if not isinstance(drafts[device], dict):
                warnings.append("device_drafts")
                continue
            result["device_drafts"][device] = {
                group: _group(drafts[device].get(group, {}), fields, group, warnings, device)
                for group, fields in DRAFT_FIELDS.items()}
    else:
        warnings.append("device_drafts")
    return result, warnings


def profile_selection(path, name, program_dir):
    """Keep bundled profiles portable and distinguish explicit external files."""
    if not path:
        return {"path": "", "name": name, "path_base": "program"}
    base = Path(program_dir).resolve()
    target = (base / path).resolve()
    try:
        relative = target.relative_to(base)
    except ValueError:
        return {"path": str(target), "name": name, "path_base": "absolute"}
    return {"path": relative.as_posix(), "name": name, "path_base": "program"}


def restore_profile_path(selection, program_dir, default_path):
    """Resolve against the program, recovering legacy absolute paths after a move.

    Legacy records cannot distinguish the bundled default from an external file
    with the same name. Prefer the local default for that legacy case; new
    records explicitly distinguish external files and always keep their path.
    """
    base = Path(program_dir).resolve()
    default = Path(default_path).resolve()
    stored = selection.get("path")
    if not stored:
        return default, None
    path = Path(stored)
    if selection.get("path_base") == "program":
        target = (base / path).resolve()
        target.relative_to(base)  # A program-relative reference must stay inside it.
        return target, None
    if selection.get("path_base") == "absolute":
        if not path.is_absolute():
            raise ValueError("外部配置文件需要绝对路径")
        return path, None
    if not path.is_absolute():
        return (base / path).resolve(), None
    if path == default:
        return default, None
    if Path(path.name) == Path(default.name) and default.is_file():
        return default, "已将旧配置路径迁移到当前程序目录"
    if not path.exists():
        local = base / path.name
        if local.is_file():
            return local, "旧配置路径已失效，已找到当前程序目录中的同名配置"
        if default.is_file():
            return default, "旧配置路径已失效，已改用当前程序目录的默认配置文件"
    return path, None


def load_state(path):
    try:
        with Path(path).open("rb") as stream:
            raw = stream.read(MAX_BYTES + 1)
        if len(raw) > MAX_BYTES:
            raise ValueError("上次设置文件过大")
        document = json.loads(raw.decode("utf-8-sig"), object_pairs_hook=_unique_object)
        result, warnings = clean_state(document)
        if document.get("output") == "capture":
            return result, "文件接收功能已移除；已恢复其他设置，请选择业务 COM"
        return result, "已忽略上次设置中无效的字段" if warnings else None
    except FileNotFoundError:
        return None, None
    except (OSError, ValueError):
        # Do not echo file contents or values, which may have been hand-edited.
        return None, "无法读取上次设置，已加载默认参数；后续编辑或关闭时将重新保存"


def save_state(path, document):
    cleaned, _ = clean_state(document)
    cleaned.pop("profile", None)
    if len(json.dumps(cleaned, ensure_ascii=False, indent=2).encode("utf-8")) + 1 > MAX_BYTES:
        raise ValueError("界面设置过大")
    atomic_json(path, cleaned)
    return cleaned
