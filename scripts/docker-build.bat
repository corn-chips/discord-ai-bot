@echo off
REM Simple Docker build script for Discord Grok Bot (Windows)
REM Usage: scripts\docker-build.bat

echo Building and starting Discord Grok Bot...

REM Check if Docker is running
docker info >nul 2>&1
if errorlevel 1 (
    echo Error: Docker is not running. Please start Docker and try again.
    exit /b 1
)

REM Stop existing containers
echo Stopping existing containers...
docker-compose down

REM Build and start
echo Building and starting bot...
docker-compose up --build -d

REM Show status
echo Bot started! Check status with:
echo   docker-compose logs -f discord-grok-bot
echo   docker-compose ps