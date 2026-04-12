@echo off
REM Start script for Discord Bot (Windows)
REM Usage: start.bat
REM   Flags:
REM     --rebuild    Force recreate the venv from scratch

setlocal

cd /d "%~dp0"

set "REBUILD=0"
if "%~1"=="--rebuild" set "REBUILD=1"

REM If --rebuild or venv doesn't exist, create it
if "%REBUILD%"=="1" (
    echo Removing existing venv...
    if exist ".venv" rmdir /s /q ".venv"
)

if not exist ".venv\Scripts\python.exe" (
    echo Creating virtual environment...

    REM Clean up any stale caches
    echo Cleaning Python caches...
    for /d /r %%d in (__pycache__) do if exist "%%d" rmdir /s /q "%%d"
    if exist ".mypy_cache" rmdir /s /q ".mypy_cache"
    if exist ".pytest_cache" rmdir /s /q ".pytest_cache"
    del /s /q *.pyc >nul 2>&1

    python -m venv .venv
    if errorlevel 1 (
        echo Error: Failed to create virtual environment.
        echo Make sure Python 3.8+ is installed and on your PATH.
        exit /b 1
    )

    echo Installing dependencies...
    .venv\Scripts\python.exe -m pip install --upgrade pip
    .venv\Scripts\pip.exe install -r requirements.txt
    if errorlevel 1 (
        echo Error: Failed to install dependencies.
        exit /b 1
    )

    echo Virtual environment ready.
) else (
    echo Using existing virtual environment.
)

echo Starting bot...
.venv\Scripts\python.exe main.py
