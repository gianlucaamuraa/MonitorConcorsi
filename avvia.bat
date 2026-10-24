@echo off
rem Apre l'interfaccia web di MonitorConcorsi (http://127.0.0.1:8765)
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" (
    echo Ambiente Python non trovato. Leggi la sezione "Installazione" del README.
    pause
    exit /b 1
)
".venv\Scripts\python.exe" main.py serve
pause
