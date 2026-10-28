@echo off
cd /d "%~dp0"
set "GIT=C:\Program Files\Git\cmd\git.exe"
if not exist "%GIT%" set "GIT=git"

echo Syncing data/ (bloggers + results) to GitHub...
"%GIT%" add data/
"%GIT%" commit -m "sync: update bloggers and results"
"%GIT%" pull --rebase origin main
"%GIT%" push
echo.
echo Done. The next GitHub Actions run will use the updated blogger list.
pause
