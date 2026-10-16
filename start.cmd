@echo off
rem Arranca el karaoke y abre la pantalla principal en el navegador.
rem   start.cmd           arrancar
rem   start.cmd --check   diagnosticar la instalacion (no arranca nada)
cd /d "%~dp0"

rem Recarga el PATH del sistema: si setup acaba de instalar uv, esta ventana quiza no lo veria todavia.
for /f "usebackq delims=" %%P in (`powershell -NoProfile -Command "[Environment]::GetEnvironmentVariable('Path','Machine') + ';' + [Environment]::GetEnvironmentVariable('Path','User')"`) do set "PATH=%%P"

where uv >nul 2>nul
if errorlevel 1 (
  echo No se encuentra uv. Ejecuta primero setup.cmd
  pause
  exit /b 1
)

if "%~1"=="" (
  uv run python -m server --open
) else (
  uv run python -m server %*
)
if errorlevel 1 pause
