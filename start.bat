@echo off
REM Start script for Discord Bot (Windows)
REM Usage: start.bat
REM   Flags:
REM     --rebuild    Force recreate the venv from scratch

setlocal

cd /d "%~dp0"

set "REBUILD=0"
if "%~1"=="--rebuild" set "REBUILD=1"

set "NEEDS_CREATE=0"
if "%REBUILD%"=="1" set "NEEDS_CREATE=1"
if not exist ".venv\Scripts\python.exe" set "NEEDS_CREATE=1"

REM Find and validate a creation interpreter before changing an existing venv.
set "PYTHON_CMD="
if "%NEEDS_CREATE%"=="1" (
    py -3.12 -c "import sys; sys.exit(0 if sys.version_info[:2] == (3, 12) else 1)" >nul 2>&1
    if not errorlevel 1 set "PYTHON_CMD=py -3.12"
)
if "%NEEDS_CREATE%"=="1" if not defined PYTHON_CMD (
    python -c "import sys; sys.exit(0 if sys.version_info[:2] == (3, 12) else 1)" >nul 2>&1
    if not errorlevel 1 set "PYTHON_CMD=python"
)
if "%NEEDS_CREATE%"=="1" if not defined PYTHON_CMD (
    echo Error: Python 3.12 was not found through py -3.12 or python.
    exit /b 1
)

REM If --rebuild, remove the venv only after a replacement interpreter is ready.
if "%REBUILD%"=="1" (
    echo Removing existing venv...
    if exist ".venv" rmdir /s /q ".venv"
    if exist ".venv" (
        echo Error: Failed to remove the existing virtual environment.
        exit /b 1
    )
)

if not exist ".venv\Scripts\python.exe" (
    echo Creating virtual environment...

    REM Clean up any stale caches
    echo Cleaning Python caches...
    for /d /r %%d in (__pycache__) do if exist "%%d" rmdir /s /q "%%d"
    if exist ".mypy_cache" rmdir /s /q ".mypy_cache"
    if exist ".pytest_cache" rmdir /s /q ".pytest_cache"
    del /s /q *.pyc >nul 2>&1

    %PYTHON_CMD% -m venv .venv
    if errorlevel 1 (
        echo Error: Failed to create virtual environment.
        echo Make sure the selected Python 3.12 installation can create virtual environments.
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

.venv\Scripts\python.exe -c "import sys; sys.exit(0 if sys.version_info[:2] == (3, 12) else 1)"
if errorlevel 1 (
    echo Error: The existing virtual environment does not use Python 3.12.
    echo Run start.bat --rebuild after installing Python 3.12.
    exit /b 1
)

echo Starting bot...
.venv\Scripts\python.exe main.py
