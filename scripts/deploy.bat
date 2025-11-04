@echo off
REM Discord Grok Bot Deployment Script for Windows
REM This script helps deploy the bot in various environments

setlocal enabledelayedexpansion

REM Function to print colored output (basic version for Windows)
:print_status
echo [INFO] %~1
goto :eof

:print_success
echo [SUCCESS] %~1
goto :eof

:print_warning
echo [WARNING] %~1
goto :eof

:print_error
echo [ERROR] %~1
goto :eof

REM Function to check if command exists
:command_exists
where %1 >nul 2>&1
goto :eof

REM Function to check environment file
:check_env_file
if not exist ".env" (
    call :print_error ".env file not found!"
    call :print_status "Creating .env from .env.example..."
    if exist ".env.example" (
        copy .env.example .env >nul
        call :print_warning "Please edit .env file with your actual tokens before running the bot"
        exit /b 1
    ) else (
        call :print_error ".env.example not found either!"
        exit /b 1
    )
)

REM Check if required variables are set
findstr /C:"DISCORD_BOT_TOKEN=your_discord_bot_token_here" .env >nul
if !errorlevel! neq 0 (
    call :print_success ".env file appears to be configured"
    exit /b 0
) else (
    call :print_warning ".env file contains example values. Please update with real tokens."
    exit /b 1
)

REM Function to deploy with Docker
:deploy_docker
call :print_status "Deploying with Docker..."

call :command_exists docker
if !errorlevel! neq 0 (
    call :print_error "Docker is not installed!"
    exit /b 1
)

call :command_exists docker-compose
if !errorlevel! neq 0 (
    call :print_error "Docker Compose is not installed!"
    exit /b 1
)

REM Check environment
call :check_env_file
if !errorlevel! neq 0 (
    call :print_error "Please configure .env file before deploying"
    exit /b 1
)

REM Build and start
call :print_status "Building Docker image..."
docker-compose build

call :print_status "Starting bot container..."
docker-compose up -d

call :print_success "Bot deployed with Docker!"
call :print_status "Use 'docker-compose logs -f' to view logs"
call :print_status "Use 'docker-compose down' to stop the bot"
goto :eof

REM Function to deploy locally
:deploy_local
call :print_status "Deploying locally..."

call :command_exists python
if !errorlevel! neq 0 (
    call :print_error "Python is not installed!"
    exit /b 1
)

REM Check environment
call :check_env_file
if !errorlevel! neq 0 (
    call :print_error "Please configure .env file before deploying"
    exit /b 1
)

REM Install dependencies
call :print_status "Installing Python dependencies..."
if exist "requirements.txt" (
    python -m pip install -r requirements.txt
) else (
    call :print_error "requirements.txt not found!"
    exit /b 1
)

call :print_success "Dependencies installed!"
call :print_status "You can now run the bot with: python main.py"
goto :eof

REM Function to run health check
:run_health_check
call :print_status "Running health check..."

if exist "scripts\health_check.py" (
    python scripts\health_check.py
) else (
    call :print_error "Health check script not found!"
    exit /b 1
)
goto :eof

REM Function to show monitoring
:show_monitoring
call :print_status "Showing monitoring report..."

if exist "scripts\monitor.py" (
    python scripts\monitor.py --hours 24
) else (
    call :print_error "Monitor script not found!"
    exit /b 1
)
goto :eof

REM Main script logic
if "%1"=="docker" (
    call :deploy_docker
) else if "%1"=="local" (
    call :deploy_local
) else if "%1"=="health" (
    call :run_health_check
) else if "%1"=="monitor" (
    call :show_monitoring
) else (
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
)