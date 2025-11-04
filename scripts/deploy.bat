@echo off
REM Discord Grok Bot Deployment Script for Windows
REM This script helps deploy the bot in various environments

setlocal enabledelayedexpansion

REM Main script logic - handle commands first
if "%1"=="docker" goto deploy_docker
if "%1"=="local" goto deploy_local
if "%1"=="health" goto run_health_check
if "%1"=="monitor" goto show_monitoring
goto show_help

REM Function to deploy with Docker
:deploy_docker
echo [INFO] Deploying with Docker...

where docker >nul 2>&1
if errorlevel 1 (
    echo [ERROR] Docker is not installed!
    echo [INFO] Please install Docker Desktop from: https://www.docker.com/products/docker-desktop/
    exit /b 1
)

where docker-compose >nul 2>&1
if errorlevel 1 (
    echo [ERROR] Docker Compose is not installed!
    echo [INFO] Docker Compose should come with Docker Desktop
    exit /b 1
)

REM Check environment file
if not exist ".env" (
    echo [ERROR] .env file not found!
    if exist ".env.example" (
        echo [INFO] Creating .env from .env.example...
        copy .env.example .env >nul
        echo [WARNING] Please edit .env file with your actual tokens before running the bot
        exit /b 1
    ) else (
        echo [ERROR] .env.example not found either!
        exit /b 1
    )
)

REM Check if tokens are configured
findstr /C:"your_discord_bot_token_here" .env >nul
if not errorlevel 1 (
    echo [WARNING] .env file contains example values. Please update with real tokens.
    exit /b 1
)

echo [SUCCESS] .env file appears to be configured

REM Build and start
echo [INFO] Building Docker image...
docker-compose build
if errorlevel 1 (
    echo [ERROR] Failed to build Docker image
    exit /b 1
)

echo [INFO] Starting bot container...
docker-compose up -d
if errorlevel 1 (
    echo [ERROR] Failed to start container
    exit /b 1
)

echo [SUCCESS] Bot deployed with Docker!
echo [INFO] Use 'docker-compose logs -f' to view logs
echo [INFO] Use 'docker-compose down' to stop the bot
exit /b 0

REM Function to deploy locally
:deploy_local
echo [INFO] Setting up for local deployment...

where python >nul 2>&1
if errorlevel 1 (
    echo [ERROR] Python is not installed!
    echo [INFO] Please install Python from: https://www.python.org/downloads/
    exit /b 1
)

REM Check environment file
if not exist ".env" (
    echo [ERROR] .env file not found!
    if exist ".env.example" (
        echo [INFO] Creating .env from .env.example...
        copy .env.example .env >nul
        echo [WARNING] Please edit .env file with your actual tokens before running the bot
        exit /b 1
    ) else (
        echo [ERROR] .env.example not found either!
        exit /b 1
    )
)

REM Check if tokens are configured
findstr /C:"your_discord_bot_token_here" .env >nul
if not errorlevel 1 (
    echo [WARNING] .env file contains example values. Please update with real tokens.
    exit /b 1
)

echo [SUCCESS] .env file appears to be configured

REM Install dependencies
echo [INFO] Installing Python dependencies...
if not exist "requirements.txt" (
    echo [ERROR] requirements.txt not found!
    exit /b 1
)

python -m pip install -r requirements.txt
if errorlevel 1 (
    echo [ERROR] Failed to install dependencies
    exit /b 1
)

echo [SUCCESS] Dependencies installed successfully!
echo [INFO] You can now run the bot with: python main.py
exit /b 0

REM Function to run health check
:run_health_check
echo [INFO] Running health check...

if not exist "scripts\health_check.py" (
    echo [ERROR] Health check script not found!
    exit /b 1
)

python scripts\health_check.py
exit /b %errorlevel%

REM Function to show monitoring
:show_monitoring
echo [INFO] Showing monitoring report...

if not exist "scripts\monitor.py" (
    echo [ERROR] Monitor script not found!
    exit /b 1
)

python scripts\monitor.py --hours 24
exit /b %errorlevel%

REM Show help
:show_help
echo Discord Grok Bot Deployment Script for Windows
echo.
echo Usage: %0 [command]
echo.
echo Commands:
echo   docker    - Deploy using Docker Compose
echo   local     - Set up for local development
echo   health    - Run health check
echo   monitor   - Show monitoring report
echo   help      - Show this help message
echo.
echo Examples:
echo   %0 docker     # Deploy with Docker
echo   %0 local      # Set up locally
echo   %0 health     # Check bot health
echo.
exit /b 0