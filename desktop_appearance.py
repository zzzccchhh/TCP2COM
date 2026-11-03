"""Local desktop appearance preference, separate from bridge/device settings."""

import json
import os
from pathlib import Path
import tempfile

CHOICES = ("light", "dark", "system")
BACKGROUNDS = {"light": "#F1F0EA", "dark": "#08080A"}


def preference_path():
    return Path(os.environ.get("LOCALAPPDATA") or tempfile.gettempdir()) / "WiFiBridge" / "appearance.json"


def load_preference(path):
    try:
        document = json.loads(Path(path).read_text(encoding="utf-8"))
        value = document.get("appearance") if isinstance(document, dict) else None
        return value if value in CHOICES else "light"
    except (OSError, ValueError):
        return "light"


def save_preference(path, value):
    if value not in CHOICES:
        raise ValueError("请选择浅色、深色或跟随系统")
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=path.parent, delete=False) as stream:
            temporary = Path(stream.name)
            json.dump({"appearance": value}, stream)
        os.replace(temporary, path)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def resolved_theme(value):
    if value != "system":
        return value if value in CHOICES else "light"
    if os.name == "nt":
        try:
            import winreg
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER,
                               r"Software\Microsoft\Windows\CurrentVersion\Themes\Personalize") as key:
                return "light" if winreg.QueryValueEx(key, "AppsUseLightTheme")[0] else "dark"
        except OSError:
            pass
    return "light"
