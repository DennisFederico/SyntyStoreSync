@echo off
cd /d "%~dp0"
echo Starting SyntyStoreSync on http://127.0.0.1:8765 ...
start "" "http://127.0.0.1:8765"
.\.venv\Scripts\python.exe -m uvicorn app.main:app --host 127.0.0.1 --port 8765 --reload
pause
