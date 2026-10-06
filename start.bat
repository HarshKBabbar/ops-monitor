@echo off
title Ops Monitor
cd /d "%~dp0"

if not exist ".venv\Scripts\python.exe" (
    echo First run: setting up Python environment...
    python -m venv .venv || (echo Python 3.11+ is required: https://www.python.org/downloads/ & pause & exit /b 1)
)

rem Install / update packages when requirements.txt changed (e.g. after a git pull).
rem If a "wheels" folder exists (made with make_wheels.bat), install offline from it.
fc /b requirements.txt ".venv\requirements.stamp" >nul 2>&1
if errorlevel 1 (
    echo Installing packages...
    if exist "wheels\" (
        ".venv\Scripts\python.exe" -m pip install --no-index --find-links wheels -r requirements.txt || (pause & exit /b 1)
    ) else (
        ".venv\Scripts\python.exe" -m pip install --upgrade pip
        ".venv\Scripts\python.exe" -m pip install -r requirements.txt || (pause & exit /b 1)
    )
    copy /y requirements.txt ".venv\requirements.stamp" >nul
)

echo.
echo  Ops Monitor is running.
echo  On this PC open:      http://localhost:8080
echo  Team (LAN / VPN):     http://%COMPUTERNAME%:8080
echo  Keep this window open. Close it to stop the app.
echo.
".venv\Scripts\python.exe" -m app.main
pause
