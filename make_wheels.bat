@echo off
rem Use this only if the office PC cannot download Python packages (pip blocked by the proxy).
rem Run it on a PC with internet, then copy the "wheels" folder next to start.bat on the office PC.
rem Set PYVER to the office PC's Python version (python --version  ->  3.12.x = 312).
set PYVER=312
cd /d "%~dp0"
python -m pip download -r requirements.txt -d wheels --only-binary=:all: --python-version %PYVER% --platform win_amd64
echo.
echo Done. Copy the "wheels" folder to the office PC, next to start.bat.
pause
