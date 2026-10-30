# Repository Guidelines

## Project Structure & Module Organization

This repository contains a Python TCP/UDP-to-serial bridge for ESP-01S, ESP32-C5, and ESP32-S3. `tcp2com.py` provides the bridge and CLI; `tcp2com_gui.py` provides the Tkinter interface. Supporting modules separate network transport, device capabilities, configuration, USB diagnostics, reset control, and GUI state. `tests/` contains host-side regression tests; `tools/test-firmware-config.py` pairs the USB client with a separately built firmware fixture. Windows launchers are named `启动_*.bat`. See `readme.md` and `README_tcp2com.txt` for device setup and usage.

## Build, Test, and Development Commands

Run these commands from the repository root on Windows with Python 3:

```powershell
py -3 -m pip install -r requirements-tcp2com.txt
py -3 tcp2com.py --list-coms
py -3 tcp2com_gui.py
py -3 -B -X utf8 -m unittest discover -s tests -v
```

These install pyserial, list serial ports, launch the GUI, and run the test suite. Use `启动_tcp2com.bat` for the CLI launcher or `启动_图形界面.bat` for the GUI. Set the host, port, COM port, and baud rate to match your hardware before connecting.

## Coding Style & Naming Conventions

Use four-space Python indentation, `snake_case` for functions and variables, `PascalCase` for classes, and uppercase constants. Follow existing module boundaries and preserve UTF-8 text. Keep batch-file contents ASCII-only so Windows command parsing remains reliable. No formatter or linter is configured.

## Testing Guidelines

Use the standard library `unittest` framework; name files `tests/test_*.py` and methods `test_*`. Add focused regression tests for behavior changes, especially reconnection, serial I/O, device configuration, and GUI state. Tests use mocks, local sockets, and serial loopback; some GUI or Windows-specific tests may skip when their environment is unavailable. No coverage threshold is configured. Report skips and distinguish host tests from physical device validation.

## Commit & Pull Request Guidelines

Recent commits use short, descriptive Chinese subjects, such as `增加接收队列和串口写入诊断`; follow that concise style. PRs should describe the behavior changed, affected devices and configuration, test results, and any hardware validation. Link related issues and include screenshots for visible GUI changes.

## Configuration & Security

Do not commit Wi-Fi passwords or personal device settings. `tcp2com_last_state.json` and captured data are local runtime artifacts. For C5/S3 configuration saves, use the current device revision and session; if a save outcome is uncertain, re-read the device before trying again.
