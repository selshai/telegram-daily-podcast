@echo off
chcp 65001 >nul
set PYTHONIOENCODING=utf-8
cd /d "%~dp0"

if not exist logs mkdir logs

echo.
echo ============================================
echo   Summarize with Nebius Gemma
echo ============================================
echo.

echo [1/2] Installing / updating dependencies...
pip install -r requirements.txt --quiet --upgrade >> logs\run.log 2>&1
echo       Done.
echo.

echo [2/2] Summarizing messages...
echo ----------------------------------------
python summarize.py
echo ----------------------------------------
echo.
pause
