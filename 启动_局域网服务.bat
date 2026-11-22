@echo off
cd /d "%~dp0"
if not exist "frontend\dist\index.html" (
    py -3 -B -X utf8 tools\install-frontend.py
    if errorlevel 1 (
        pause
        exit /b 1
    )
)
py -3 -B -X utf8 lan_manager.py %*
pause
