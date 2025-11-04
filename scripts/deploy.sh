#!/bin/bash

# Discord Grok Bot Deployment Script
# This script helps deploy the bot in various environments

set -e  # Exit on any error

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

# Function to print colored output
print_status() {
    echo -e "${BLUE}[INFO]${NC} $1"
}

print_success() {
    echo -e "${GREEN}[SUCCESS]${NC} $1"
}

print_warning() {
    echo -e "${YELLOW}[WARNING]${NC} $1"
}

print_error() {
    echo -e "${RED}[ERROR]${NC} $1"
}

# Function to check if command exists
command_exists() {
    command -v "$1" >/dev/null 2>&1
}

# Function to check environment file
check_env_file() {
    if [ ! -f ".env" ]; then
        print_error ".env file not found!"
        print_status "Creating .env from .env.example..."
        if [ -f ".env.example" ]; then
            cp .env.example .env
            print_warning "Please edit .env file with your actual tokens before running the bot"
            return 1
        else
            print_error ".env.example not found either!"
            return 1
        fi
    fi
    
    # Check if required variables are set
    if ! grep -q "DISCORD_BOT_TOKEN=your_discord_bot_token_here" .env; then
        print_success ".env file appears to be configured"
        return 0
    else
        print_warning ".env file contains example values. Please update with real tokens."
        return 1
    fi
}

# Function to deploy with Docker
deploy_docker() {
    print_status "Deploying with Docker..."
    
    if ! command_exists docker; then
        print_error "Docker is not installed!"
        exit 1
    fi
    
    if ! command_exists docker-compose; then
        print_error "Docker Compose is not installed!"
        exit 1
    fi
    
    # Check environment
    if ! check_env_file; then
        print_error "Please configure .env file before deploying"
        exit 1
    fi
    
    # Build and start
    print_status "Building Docker image..."
    docker-compose build
    
    print_status "Starting bot container..."
    docker-compose up -d
    
    print_success "Bot deployed with Docker!"
    print_status "Use 'docker-compose logs -f' to view logs"
    print_status "Use 'docker-compose down' to stop the bot"
}

# Function to deploy locally
deploy_local() {
    print_status "Deploying locally..."
    
    if ! command_exists python3; then
        print_error "Python 3 is not installed!"
        exit 1
    fi
    
    # Check environment
    if ! check_env_file; then
        print_error "Please configure .env file before deploying"
        exit 1
    fi
    
    # Install dependencies
    print_status "Installing Python dependencies..."
    if [ -f "requirements.txt" ]; then
        python3 -m pip install -r requirements.txt
    else
        print_error "requirements.txt not found!"
        exit 1
    fi
    
    print_success "Dependencies installed!"
    print_status "You can now run the bot with: python3 main.py"
}

# Function to create systemd service
create_systemd_service() {
    print_status "Creating systemd service..."
    
    if [ "$EUID" -ne 0 ]; then
        print_error "Please run with sudo to create systemd service"
        exit 1
    fi
    
    BOT_DIR=$(pwd)
    BOT_USER=$(logname)
    
    cat > /etc/systemd/system/discord-grok-bot.service << EOF
[Unit]
Description=Discord Grok Bot
After=network.target

[Service]
Type=simple
User=$BOT_USER
WorkingDirectory=$BOT_DIR
Environment=PYTHONPATH=$BOT_DIR
EnvironmentFile=$BOT_DIR/.env
ExecStart=/usr/bin/python3 main.py
Restart=always
RestartSec=10

[Install]
WantedBy=multi-user.target
EOF

    systemctl daemon-reload
    systemctl enable discord-grok-bot
    
    print_success "Systemd service created!"
    print_status "Start with: sudo systemctl start discord-grok-bot"
    print_status "Check status with: sudo systemctl status discord-grok-bot"
    print_status "View logs with: sudo journalctl -u discord-grok-bot -f"
}

# Function to run health check
run_health_check() {
    print_status "Running health check..."
    
    if [ -f "scripts/health_check.py" ]; then
        python3 scripts/health_check.py
    else
        print_error "Health check script not found!"
        exit 1
    fi
}

# Function to show monitoring
show_monitoring() {
    print_status "Showing monitoring report..."
    
    if [ -f "scripts/monitor.py" ]; then
        python3 scripts/monitor.py --hours 24
    else
        print_error "Monitor script not found!"
        exit 1
    fi
}

# Main script logic
case "${1:-help}" in
    "docker")
        deploy_docker
        ;;
    "local")
        deploy_local
        ;;
    "systemd")
        create_systemd_service
        ;;
    "health")
        run_health_check
        ;;
    "monitor")
        show_monitoring
        ;;
    "help"|*)
        echo "Discord Grok Bot Deployment Script"
        echo ""
        echo "Usage: $0 [command]"
        echo ""
        echo "Commands:"
        echo "  docker    - Deploy using Docker Compose"
        echo "  local     - Set up for local development"
        echo "  systemd   - Create systemd service (requires sudo)"
        echo "  health    - Run health check"
        echo "  monitor   - Show monitoring report"
        echo "  help      - Show this help message"
        echo ""
        echo "Examples:"
        echo "  $0 docker     # Deploy with Docker"
        echo "  $0 local      # Set up locally"
        echo "  $0 health     # Check bot health"
        ;;
esac