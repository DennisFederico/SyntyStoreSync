@echo off
cd /d "%~dp0"
echo Starting Synty Store login...
.\.venv\Scripts\python.exe scripts\login.py
pause
