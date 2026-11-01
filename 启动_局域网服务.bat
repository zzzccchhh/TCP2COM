@echo off
cd /d "%~dp0"
if not exist frontend\dist\index.html (
  echo Build frontend first: cd frontend ^&^& npm ci ^&^& npm run build
  pause
  exit /b 1
)
py -3 -B -X utf8 lan_manager.py %*
pause
