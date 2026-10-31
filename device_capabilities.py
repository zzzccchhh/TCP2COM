"""Board capabilities shared by CLI, GUI and setup clients."""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class DeviceCapabilities:
    label: str
    setup_button: int | None
    setup_ssid: str | None
    bands: tuple[int, ...]
    usb: bool = False
    reset: bool = False

    @property
    def setup_hint(self):
        return (f"长按 GPIO{self.setup_button} ≥3 秒进入配网；"
                f"HTTP：连接 {self.setup_ssid}。USB：使用原生 USB，无需连接 Wi-Fi 或启动桥接。")


DEVICES = {
    "esp01s": DeviceCapabilities("ESP-01S", None, None, (0,), reset=True),
    "c5": DeviceCapabilities("ESP32-C5", 24, "ESP32-C5-Setup", (0, 1, 2), usb=True),
    "s3": DeviceCapabilities("ESP32-S3", 4, "ESP32-S3-Setup", (0,), usb=True),
}
BANDS = {"2.4 GHz": 0, "5 GHz": 1, "自动（AP 使用 2.4 GHz）": 2}


def capabilities(device):
    try:
        return DEVICES[device]
    except (KeyError, TypeError) as error:
        raise ValueError(f"未知设备：{device}") from error
