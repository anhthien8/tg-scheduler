@echo off
setlocal
cd /d "%~dp0"
rem Phase 1 split launcher: worker owns Telethon + watchers; web serves API/UI only.
rem Web mode returns HTTP 503 (fail-closed) for Telegram-mutating routes until Phase 2 IPC ships.
if exist ".venv\Scripts\python.exe" (set "PY=.venv\Scripts\python.exe") else (set "PY=python")

start "TG Scheduler Worker" cmd /k ""%PY%" telegram_worker.py"
start "TG Scheduler Web" cmd /k "set TG_RUNTIME_MODE=web&& "%PY%" main.py"
endlocal
