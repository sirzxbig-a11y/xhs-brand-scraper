@echo off
cd /d "%~dp0"
echo Starting local backend at http://127.0.0.1:8000 ...
python run.py
pause
