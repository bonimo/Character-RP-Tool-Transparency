@echo off
cd /d "%~dp0"
start "" http://127.0.0.1:8000
"%~dp0.venv\Scripts\python.exe" -m uvicorn backend.app:app --host 127.0.0.1 --port 8000
pause
