@echo off
echo Iniciando Panel de Administración...
echo.
cd /d "%~dp0"
start http://localhost:5001
python admin/server.py
pause
