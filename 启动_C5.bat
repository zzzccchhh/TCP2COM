@echo off
setlocal
chcp 65001 >nul
cd /d "%~dp0"
rem Edit tcp2com_config.json first. COM1 is only an example.
rem Profiles: c5-spi-tcp, c5-client, c5-spi-udp, c5-capture
set "T2C_PROFILE=c5-spi-tcp"
py -3 -B "%~dp0tcp2com.py" --config "%~dp0tcp2com_config.json" --profile "%T2C_PROFILE%" %*
set "EXIT_CODE=%ERRORLEVEL%"
echo.
echo Bridge exited with code %EXIT_CODE%.
pause
exit /b %EXIT_CODE%
