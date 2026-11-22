@echo off
rem Console entry for first-time asset installation and diagnostics. Use the VBS for daily launch.
cd /d "%~dp0"
py -3 -c "import webview, serial" >nul 2>&1
if errorlevel 1 (
    echo Run: py -3 -m pip install -r requirements-web.txt
    pause
    exit /b 1
)
if not exist "frontend\dist\index.html" (
    py -3 -B -X utf8 tools\install-frontend.py
    if errorlevel 1 (
        pause
        exit /b 1
    )
)
py -3 -B "%~dp0tcp2com_web.py"
if errorlevel 1 pause
exit /b
