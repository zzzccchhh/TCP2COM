@echo off
setlocal
chcp 65001 >nul
cd /d "%~dp0"
rem Default matches the S3 factory AP and TCP server (UART or SPI).
rem Edit COM1 in tcp2com_config.json before use.
rem Profiles: s3-client, s3-spi-tcp, s3-spi-udp, s3-capture
set "T2C_PROFILE=s3-client"
py -3 -B "%~dp0tcp2com.py" --config "%~dp0tcp2com_config.json" --profile "%T2C_PROFILE%" %*
set "EXIT_CODE=%ERRORLEVEL%"
echo.
echo Bridge exited with code %EXIT_CODE%.
pause
exit /b %EXIT_CODE%
