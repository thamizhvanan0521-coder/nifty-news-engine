@echo off
title NIFTY News Engine
cd /d "%~dp0"
set PYTHONIOENCODING=utf-8
:loop
.venv\Scripts\python.exe main.py run
echo Engine stopped. Restarting in 30 seconds... (close this window to stop)
timeout /t 30 /nobreak >nul
goto loop
