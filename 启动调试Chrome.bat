@echo off
cd /d "%~dp0"
powershell -ExecutionPolicy Bypass -File "%~dp0scripts\start_chrome_debug.ps1"
echo.
echo Chrome started with remote-debugging-port 9222.
echo Log in to Xiaohongshu in that Chrome window,
echo then click "Open browser login" at http://127.0.0.1:8000
echo.
echo (Closing this window will NOT close Chrome.)
pause
