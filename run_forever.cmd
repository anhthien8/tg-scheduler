@echo off
rem Keeps the server up: when main.py exits for any reason, start it again after 5s.
rem To restart the server on purpose, kill only the python main.py process; this loop brings it back.
cd /d "%~dp0"
:loop
echo %date% %time% start >> data\restarts.log
.venv\Scripts\python.exe main.py >> data\server_out.log 2>> data\server_err.log
echo %date% %time% exited with code %errorlevel% >> data\restarts.log
ping -n 6 127.0.0.1 >nul
goto loop
