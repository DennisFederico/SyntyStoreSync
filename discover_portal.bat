@echo off
cd /d "%~dp0"
echo Starting Synty Store portal DISCOVERY (developer tool, captures personal data)...
.\.venv\Scripts\python.exe scripts\discover_portal.py
pause
