@echo off
rem Starts the local dashboard and opens it in the browser. Double-click to use.
rem %~dp0 = this file's folder, so it still works if the project is moved.
cd /d "%~dp0"
start "Coach - painel" cmd /k venv\Scripts\python.exe -m coach.web
timeout /t 2 >nul
start "" http://127.0.0.1:8000
