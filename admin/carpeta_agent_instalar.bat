@echo off
rem Doble clic aqui para dejar el ayudante de carpetas andando en este PC.
rem No necesita permiso de administrador.
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0carpeta_agent_instalar.ps1"
