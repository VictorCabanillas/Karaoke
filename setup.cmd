@echo off
rem Ejecuta setup.ps1 sin tener que cambiar la política de ejecución de PowerShell.
rem Uso: setup.cmd [-NoProcessing] [-NoCerts] [-DryRun]
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0setup.ps1" %*
set CODE=%ERRORLEVEL%
if "%~1"=="" pause
exit /b %CODE%
