@echo off
chcp 65001 >nul
set PYTHONIOENCODING=utf-8
cd /d "%~dp0"

:: Ensure logs folder exists
if not exist logs mkdir logs

:: Install / upgrade dependencies quietly
pip install -r requirements.txt --quiet --upgrade >> logs\run.log 2>&1

:: Run the main script
python main.py >> logs\run.log 2>&1
