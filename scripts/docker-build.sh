#!/bin/bash

# Simple Docker build script for Discord Grok Bot
# Usage: ./scripts/docker-build.sh

set -e

echo "Building and starting Discord Grok Bot..."

# Check if Docker is running
if ! docker info >/dev/null 2>&1; then
    echo "Error: Docker is not running. Please start Docker and try again."
    exit 1
fi

# Stop existing containers
echo "Stopping existing containers..."
docker-compose down

# Build and start
echo "Building and starting bot..."
docker-compose up --build -d

# Show status
echo "Bot started! Check status with:"
echo "  docker-compose logs -f discord-grok-bot"
echo "  docker-compose ps"