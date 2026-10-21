@echo off
setlocal
chcp 65001 >nul
cd /d "%~dp0"

rem TCP <-> COM bridge launcher. Keep this file ASCII-only to avoid
rem mojibake when Windows cmd.exe interprets a UTF-8 batch file.

rem ===== TCP server =====
set "T2C_HOST=192.168.137.200"
set "T2C_PORT=8080"
set "T2C_CONTROL_PORT=8081"

rem ===== Local serial port =====
set "T2C_COM=COM1"
set "T2C_BAUD=1152000"
set "T2C_FLOW=none"

rem ===== Network =====
set "T2C_RECONNECT=2"
set "T2C_KEEPALIVE_IDLE=3"
set "T2C_KEEPALIVE_INTERVAL=1"
set "T2C_KEEPALIVE_COUNT=5"

rem ===== Buffers and display =====
set "T2C_BUFSIZE=65536"
set "T2C_TIMEOUT=0.02"
set "T2C_WRITE_TIMEOUT=2"
set "T2C_CONNECT_TIMEOUT=4"
set "T2C_STATS_INTERVAL=0.25"
set "T2C_VERBOSE=-v"

echo ========================================
echo          TCP - COM BRIDGE
echo ========================================
echo.

echo Checking pyserial...
py -3 -c "import serial" >nul 2>&1
if errorlevel 1 (
    py -3 -m pip install pyserial
    if errorlevel 1 (
        echo ERROR: Could not install pyserial.
        pause
        exit /b 1
    )
)

echo.
echo Starting bridge...
echo TCP target: %T2C_HOST%:%T2C_PORT%
echo Serial port: %T2C_COM%
echo Baud rate: %T2C_BAUD%
echo.

py -3 "%~dp0tcp2com.py" ^
    --host "%T2C_HOST%" ^
    --port "%T2C_PORT%" ^
    --control-port "%T2C_CONTROL_PORT%" ^
    --com "%T2C_COM%" ^
    --baud "%T2C_BAUD%" ^
    --flow "%T2C_FLOW%" ^
    --reconnect "%T2C_RECONNECT%" ^
    --keepalive-idle "%T2C_KEEPALIVE_IDLE%" ^
    --keepalive-interval "%T2C_KEEPALIVE_INTERVAL%" ^
    --keepalive-count "%T2C_KEEPALIVE_COUNT%" ^
    --bufsize "%T2C_BUFSIZE%" ^
    --timeout "%T2C_TIMEOUT%" ^
    --write-timeout "%T2C_WRITE_TIMEOUT%" ^
    --connect-timeout "%T2C_CONNECT_TIMEOUT%" ^
    --stats-interval "%T2C_STATS_INTERVAL%" ^
    %T2C_VERBOSE%

set "EXIT_CODE=%ERRORLEVEL%"
echo.
echo Bridge exited with code %EXIT_CODE%.
pause
exit /b %EXIT_CODE%
