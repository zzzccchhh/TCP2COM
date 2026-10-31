@echo off
rem Console entry for first-time build and diagnostics. Use the VBS for daily launch.
cd /d "%~dp0"
py -3 -c "import webview, serial" >nul 2>&1
if errorlevel 1 (
    echo Run: py -3 -m pip install -r requirements-web.txt
    pause
    exit /b 1
)
if not exist "%~dp0frontend\dist\index.html" (
    where npm.cmd >nul 2>&1
    if errorlevel 1 (
        echo Node.js is required for the first build.
        pause
        exit /b 1
    )
    pushd "%~dp0frontend"
    call npm.cmd ci
    if errorlevel 1 goto build_failed
    call npm.cmd run build
    if errorlevel 1 goto build_failed
    popd
)
py -3 -B "%~dp0tcp2com_web.py"
if errorlevel 1 pause
exit /b
:build_failed
popd
pause
exit /b 1
