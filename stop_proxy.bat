@echo off
setlocal EnableExtensions
REM 停止本机代理，并禁止 keepalive 再次拉起（与 stop_proxy.sh 同逻辑）

set "PORT=8766"
set "PROXY_DIR=%~dp0proxy"
set "DISABLED=%PROXY_DIR%\.keepalive_disabled"
set "FOUND=0"

if not exist "%PROXY_DIR%" mkdir "%PROXY_DIR%"
type nul > "%DISABLED%"
echo Keepalive disabled: %DISABLED%
echo Run start_proxy again to clear this flag.

for /f "tokens=5" %%p in ('netstat -ano ^| findstr ":%PORT%" ^| findstr "LISTENING"') do (
  set "FOUND=1"
  echo Stopping PID %%p on port %PORT%...
  taskkill /PID %%p /F >nul 2>&1
)

if "%FOUND%"=="0" (
  echo No proxy listening on port %PORT%.
  exit /b 0
)

timeout /t 2 /nobreak >nul

for /f "tokens=5" %%p in ('netstat -ano ^| findstr ":%PORT%" ^| findstr "LISTENING"') do (
  echo Port %PORT% still in use by PID %%p.
  exit /b 1
)

echo Proxy stopped.
exit /b 0
