@echo off
cd /d "%~dp0"
py -3 -c "import tkinter, serial" >nul 2>&1
if errorlevel 1 (
    echo Python 3 with tkinter and pyserial is required.
    echo Run: py -3 -m pip install -r requirements-tcp2com.txt
    pause
    exit /b 1
)
start "" pyw -3 -B "%~dp0tcp2com_gui.py"
