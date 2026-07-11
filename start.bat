@echo off
rem Inicia o painel local e abre no navegador. Duplo-clique para usar.
rem %~dp0 = a pasta deste arquivo, entao funciona mesmo se o projeto mudar de lugar.
cd /d "%~dp0"
start "Coach - painel" cmd /k venv\Scripts\python.exe -m coach.web
timeout /t 2 >nul
start "" http://127.0.0.1:8000
