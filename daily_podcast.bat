@echo off
chcp 65001 >nul
set PYTHONIOENCODING=utf-8
cd /d "%~dp0"

if not exist logs mkdir logs

echo.
echo ============================================
echo   Daily Telegram Podcast
echo ============================================
echo.

echo [1/4] Installing / updating dependencies...
pip install -r requirements.txt --quiet --upgrade >> logs\run.log 2>&1
echo       Done.
echo.

echo [2/4] Fetching unread Telegram messages...
echo ----------------------------------------
python main.py --no-podcast
echo ----------------------------------------
echo.

echo [3/4] Summarizing with Nebius Gemma...
echo ----------------------------------------
python summarize.py
echo ----------------------------------------
echo.

echo [4/4] Generating podcast...
echo ----------------------------------------
python daily_podcast.py
echo ----------------------------------------
echo.

echo Check your Telegram Saved Messages for the podcast.
echo.
