@echo off
setlocal
chcp 65001 >nul
cd /d "%~dp0"
title SQL Assessment - Servidor local
echo SQL Assessment - Preparando entorno local...
if not defined PORT set "PORT=5002"

if exist ".venv\Scripts\python.exe" goto dependencias
where py >nul 2>nul
if not errorlevel 1 (
    py -3 -m venv .venv
) else (
    python -m venv .venv
)
if errorlevel 1 goto errorpython

:dependencias
".venv\Scripts\python.exe" -c "import sys; assert sys.version_info >= (3,10); import flask, waitress" >nul 2>nul
if not errorlevel 1 goto arrancar
echo Instalando dependencias desde paquetes locales, sin internet...
".venv\Scripts\python.exe" -m pip install --no-index --find-links=wheelhouse -r requirements.txt
if errorlevel 1 goto errordependencias

:arrancar
".venv\Scripts\python.exe" run.py --lan --abrir
if errorlevel 1 goto errorservidor
goto fin

:errorpython
echo.
echo No se pudo crear el entorno. Instala Python de 64 bits, version 3.10 a 3.14.
echo Activa la opcion "Add python.exe to PATH" del instalador y vuelve a intentar.
pause
exit /b 1

:errordependencias
echo.
echo No se pudieron instalar las dependencias locales.
echo Verifica que exista la carpeta wheelhouse y que uses Python 3.10 a 3.14 de 64 bits.
echo Consulta README.md para instalar o preparar paquetes en otro equipo.
pause
exit /b 1

:errorservidor
echo.
echo El servidor no pudo arrancar. Revisa el mensaje anterior y README.md.
echo Si el puerto seleccionado esta ocupado, configura PORT con otro puerto libre.
echo Ejemplo: set "PORT=5003" y vuelve a ejecutar iniciar.bat desde esta terminal.
pause
exit /b 1

:fin
endlocal
