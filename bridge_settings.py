"""Explicit JSON profiles; command line options take precedence."""

import argparse
import json
from pathlib import Path


def _unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"重复配置项：{key}")
        result[key] = value
    return result


def load_defaults(parser, argv):
    probe = type(parser)(add_help=False)
    probe.add_argument("--config")
    probe.add_argument("--profile")
    selection, _ = probe.parse_known_args(argv)
    if not selection.config:
        if selection.profile:
            parser.error("--profile 需要同时指定 --config")
        return
    try:
        path = Path(selection.config).resolve()
        document = json.loads(path.read_text(encoding="utf-8-sig"), object_pairs_hook=_unique_object)
        if not isinstance(document, dict) or set(document) != {"profiles"}:
            raise ValueError("配置根对象只能包含 profiles")
        profiles = document["profiles"]
        if isinstance(profiles, dict) and selection.profile in ("c5-capture", "s3-capture") and selection.profile not in profiles:
            raise ValueError("capture 配置已移除，请选择 COM 桥接配置")
        if not isinstance(profiles, dict) or not selection.profile or selection.profile not in profiles:
            raise ValueError("请用 --profile 选择存在的配置")
        settings = profiles[selection.profile]
        if not isinstance(settings, dict):
            raise ValueError("profile 必须为对象")
        actions = {action.dest: action for action in parser._actions
                   if action.dest not in {"help", "config", "profile", "list_coms", "open_config"}}
        for key, value in settings.items():
            if key == "capture":
                raise ValueError("capture 文件接收功能已移除，请删除该字段并配置 com")
            if key not in actions:
                raise ValueError(f"未知配置项：{key}")
            action = actions[key]
            if isinstance(action, argparse._StoreTrueAction):
                valid = type(value) is bool
            elif action.type is int:
                valid = type(value) is int
            elif action.type is float:
                valid = type(value) in (int, float)
            else:
                valid = isinstance(value, str)
            if not valid or (action.choices and value not in action.choices):
                raise ValueError(f"配置项类型或选项错误：{key}")
        settings = dict(settings)
        if settings.get("usb_log"):
            settings["usb_log"] = str((path.parent / settings["usb_log"]).resolve())
        parser.set_defaults(**settings)
    except (OSError, ValueError) as exc:
        parser.error(f"无法读取配置：{exc}")
