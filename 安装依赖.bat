@echo off
cd /d "%~dp0"
echo ============================================
echo  Xiaohongshu Brand Scraper - Setup
echo ============================================
echo.

python --version >nul 2>&1
if errorlevel 1 (
  echo [ERROR] Python not found. Install Python 3.10+ first: https://www.python.org/downloads/
  pause
  exit /b 1
)

echo [1/2] Installing Python dependencies...
python -m pip install -r requirements.txt
if errorlevel 1 goto fail

echo.
echo [2/2] Installing Playwright Chromium...
python -m playwright install chromium
if errorlevel 1 goto fail

echo.
echo ============================================
echo  Done!
echo  Next steps:
echo   1. Double-click  启动调试Chrome.bat  then log in to Xiaohongshu
echo   2. Double-click  启动服务.bat
echo   3. Open http://127.0.0.1:8000 and fill API keys
echo ============================================
pause
exit /b 0

:fail
echo.
echo [ERROR] Installation failed. Check the output above.
pause
exit /b 1
