@echo off
chcp 65001 >nul
set PYTHONIOENCODING=utf-8
cd /d "%~dp0"

if not exist logs mkdir logs

echo.
echo ============================================
echo   Quick Telegram Podcast (last 2 hours)
echo ============================================
echo.

echo [1/3] Fetching latest Telegram messages...
python main.py >> logs\run.log 2>&1
echo       Done.
echo.

echo [2/3] Checking dependencies and browser...
pip install -r requirements.txt --quiet --upgrade >> logs\run.log 2>&1
playwright install chromium >> logs\run.log 2>&1
echo       Done.
echo.

echo [3/3] Generating quick podcast (last 2 hours)...
echo ----------------------------------------
python quick_podcast.py
echo ----------------------------------------
echo.

echo Check your Telegram Saved Messages for the podcast.
echo.
pause
