@echo off
rem Ejecuta uninstall.ps1 sin tener que cambiar la política de ejecución de PowerShell.
rem Uso: uninstall.cmd [-RemoveData] [-Yes] [-DryRun]
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0uninstall.ps1" %*
set CODE=%ERRORLEVEL%
if "%~1"=="" pause
exit /b %CODE%
