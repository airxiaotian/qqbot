@echo off
setlocal EnableExtensions
REM 停止本机代理（与 start_proxy 同端口 8766）

set "PORT=8766"
set "FOUND=0"

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
