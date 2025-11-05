# Discord Grok Bot

A Discord bot that provides AI-powered conversational responses using Google's Gemini API. The bot mimics Grok's functionality from Twitter, offering contextual responses when mentioned in Discord channels.

## Todo List
 - [ ] (CANCELED, UNSAFE) Make model interpret user message to do actual actions in discord server (mute all, ban all, kick all, etc)
 - [x] **NEW!** Add image generation and editing via Gemini 2.5 Flash Image (nano-banana)
 - [x] Add functionality to read files in the discord message
 - [x] Add PDF support - PDFs are now converted to images and processed!
 - [ ] Make it so that markdown is consistent through split messages
 - [ ] Add feature to join vc and answer in real time
 - [ ] Add interactive image refinement (multiple edit iterations)
 - [ ] Add batch image processing

## Features

- **Context-Aware Responses**: Analyzes recent message history for relevant context
- **Enhanced Reply Context**: Provides additional context when replying to specific messages
- **Image Analysis**: Supports image attachments for visual understanding
- **Image Generation & Editing**: 🎨 **NEW!** Create and edit images using Gemini 2.5 Flash Image (see [IMAGE_GENERATION.md](IMAGE_GENERATION.md))
  - Generate images from text descriptions
  - Edit existing images with natural language prompts
  - Object removal, background changes, style transfer, and more
  - Uses your Gemini API key automatically
- **PDF Support**: 🎉 Full PDF support - automatically converts PDFs to images for AI analysis (see [PDF_SUPPORT.md](PDF_SUPPORT.md))
  - Multi-page support - processes all pages
  - High-quality conversion (2x resolution)
  - Works with assignments, documents, reports, and more
- **File Upload Support**: Reads and processes uploaded text files, code, configurations, logs, and more (see [FILE_UPLOAD_FEATURE.md](FILE_UPLOAD_FEATURE.md))
  - Supports 40+ file types including all major programming languages
  - Comprehensive logging for debugging (see [FILE_UPLOAD_DEBUGGING.md](FILE_UPLOAD_DEBUGGING.md))
- **Developer Mode**: 🔧 **NEW!** Toggle detailed error output with `/dev` command (see [DEV_MODE.md](DEV_MODE.md))
  - Full stack traces when enabled
  - User-friendly messages when disabled
  - Perfect for debugging and troubleshooting
  - Admin-only control
- **Error Handling**: Graceful handling of API errors with user-friendly messages
- **Configurable**: Customizable message limits, timeouts, and other settings
- **Detailed Logging**: Track file processing, AI requests, and responses for troubleshooting

## Setup

### Prerequisites

- Python 3.8 or higher
- Discord Bot Token ([Get one here](https://discord.com/developers/applications))
- Google Gemini API Key ([Get one here](https://makersuite.google.com/app/apikey))

### Installation

#### Option 1: Standard Python Installation

1. Clone the repository:
```bash
git clone <repository-url>
cd discord-grok-bot
```

2. Install dependencies:
```bash
pip install -r requirements.txt
```

3. Configure environment variables:
```bash
cp .env.example .env
# Edit .env with your actual tokens and configuration
```

4. Run the bot:
```bash
python main.py
```

#### Option 2: Docker Installation (Recommended)

1. Clone the repository:
```bash
git clone <repository-url>
cd discord-grok-bot
```

2. Configure environment variables:
```bash
cp .env.example .env
# Edit .env with your actual tokens and configuration
```

3. Quick start with Docker:
```bash
# Linux/Mac
./scripts/docker-build.sh

# Windows
scripts\docker-build.bat
```

Or manually:
```bash
docker-compose up --build -d
```

**Essential Docker Commands:**
```bash
# View logs in real-time
docker-compose logs -f discord-grok-bot

# View last 100 lines of logs
docker-compose logs --tail=100 discord-grok-bot

# Restart the bot (quick restart)
docker-compose restart discord-grok-bot

# Stop the bot
docker-compose down

# Start the bot (after stopping)
docker-compose up -d

# Rebuild and restart (after code changes)
docker-compose up --build -d

# Full rebuild (no cache)
docker-compose build --no-cache
docker-compose up -d

# Check status
docker-compose ps

# Check health status
docker inspect discord-grok-bot --format='{{.State.Health.Status}}'

# View resource usage
docker stats discord-grok-bot

# Health check script
docker-compose exec discord-grok-bot python scripts/health_check.py

# Access container shell
docker-compose exec discord-grok-bot bash

# View environment variables
docker-compose exec discord-grok-bot env | grep -E 'GEMINI|DISCORD'
```

**Troubleshooting Commands:**
```bash
# Check for errors in logs
docker-compose logs discord-grok-bot | grep -i error

# Check initialization status
docker-compose logs discord-grok-bot | grep "Image"

# Remove and rebuild everything
docker-compose down
docker-compose build --no-cache
docker-compose up -d

# Clean up Docker resources
docker system prune -a
```

**Quick Build Script:**
```bash
# Linux/Mac
./scripts/docker-build.sh

# Windows
scripts\docker-build.bat
```

**Docker Benefits:**
- Isolated environment with all dependencies included
- Automatic health checks and restart on failure
- Easy deployment and scaling
- Consistent behavior across different systems

### Discord Bot Setup

1. Go to the [Discord Developer Portal](https://discord.com/developers/applications)
2. Create a new application and bot
3. Copy the bot token to your `.env` file
4. Enable the following bot permissions:
   - Send Messages
   - Read Message History
   - Use Application Commands (Required for slash commands)
5. Enable the **Message Content Intent** in the Bot section
6. Invite the bot to your server using the OAuth2 URL generator with `bot` and `applications.commands` scopes

### Google Gemini API Setup

1. Visit [Google AI Studio](https://makersuite.google.com/app/apikey)
2. Create a new API key
3. Copy the API key to your `.env` file

## Configuration

The bot uses environment variables for configuration. Copy `.env.example` to `.env` and fill in your values:

### Required Variables
- `DISCORD_BOT_TOKEN`: Your Discord bot token
- `GEMINI_API_KEY`: Your Google Gemini API key

### Optional Variables
- `MAX_CONTEXT_MESSAGES`: Maximum messages to include in context (default: 100)
- `REPLY_CONTEXT_RANGE`: Messages before/after replied message (default: 10)
- `RESPONSE_TIMEOUT`: API response timeout in seconds (default: 30)
- `MAX_RETRIES`: Maximum retry attempts for API calls (default: 3)
- `LOG_LEVEL`: Logging level - DEBUG, INFO, WARNING, ERROR (default: INFO)
- `LOG_FILE`: Optional log file path (default: logs to console and bot.log)
- `ENABLE_PERFORMANCE_LOGGING`: Enable performance metrics logging (default: true)
- `NANO_BANANA_API_KEY`: API key for image generation/editing (uses Gemini 2.5 Flash Image model, defaults to GEMINI_API_KEY)

## Usage

### Basic Usage
1. Invite the bot to your Discord server with appropriate permissions
2. Mention the bot in any channel: `@YourBot Hello, how are you?`
3. The bot will respond with an AI-generated message based on the context

### Slash Commands
The bot includes powerful slash commands for configuration and monitoring:

- `/ping` - Check bot status and latency
- `/model` - Switch between Gemini Flash models (2.5, 2.5-Lite, 2.0, 2.0-Lite)
- `/stats` - View usage statistics and token consumption
- `/config` - View current configuration
- `/help` - Show help information
- `/dev` - Toggle developer mode for detailed error output (Admin only)
- `/clear-cache` - Clear statistics cache (Admin only)

📖 See [SLASH_COMMANDS.md](SLASH_COMMANDS.md) for detailed command documentation.

## Deployment

### Local Development

For local development, simply run:
```bash
python main.py
```

The bot will start and connect to Discord. Press `Ctrl+C` to stop the bot gracefully.

### Production Deployment

#### Using Docker (Recommended)

Quick build and start:
```bash
# Build the image
docker-compose build --no-cache

# Run the bot
docker-compose up -d
```

#### Environment Variables for Production

For production deployments, ensure you set:
- `LOG_LEVEL=WARNING` or `ERROR` to reduce log verbosity
- `ENABLE_PERFORMANCE_LOGGING=false` if not needed
- `LOG_FILE=/var/log/discord-grok-bot.log` for centralized logging

## Common Operations

### Viewing Logs

**Docker:**
```bash
# Real-time logs (follow mode)
docker-compose logs -f discord-grok-bot

# Last 100 lines
docker-compose logs --tail=100 discord-grok-bot

# Save logs to file
docker-compose logs discord-grok-bot > bot-logs.txt

# Search for errors
docker-compose logs discord-grok-bot | grep -i error

# Check image generation status
docker-compose logs discord-grok-bot | grep "Image"
```

**Local:**
```bash
# View log file
tail -f logs/bot.log

# Last 100 lines
tail -n 100 logs/bot.log

# Search for errors
grep -i error logs/bot.log
```

### Restarting the Bot

**Docker:**
```bash
# Quick restart (keeps container)
docker-compose restart discord-grok-bot

# Full restart (recreates container)
docker-compose down
docker-compose up -d

# Restart after code changes
docker-compose up --build -d
```

**Local:**
```bash
# Stop with Ctrl+C, then:
python main.py
```

**Systemd (Linux):**
```bash
# Restart
sudo systemctl restart discord-grok-bot

# Stop
sudo systemctl stop discord-grok-bot

# Start
sudo systemctl start discord-grok-bot

# Check status
sudo systemctl status discord-grok-bot
```

### Updating the Bot

**Docker:**
```bash
# Pull latest code
git pull origin main

# Rebuild and restart
docker-compose down
docker-compose build --no-cache
docker-compose up -d

# Verify it's running
docker-compose ps
docker-compose logs -f discord-grok-bot
```

**Local:**
```bash
# Pull latest code
git pull origin main

# Update dependencies
pip install -r requirements.txt --upgrade

# Restart bot
python main.py
```

### Checking Status

**Docker:**
```bash
# Container status
docker-compose ps

# Health status
docker inspect discord-grok-bot --format='{{.State.Health.Status}}'

# Resource usage
docker stats discord-grok-bot --no-stream

# Detailed info
docker inspect discord-grok-bot
```

**Bot Commands:**
```
/ping - Check bot responsiveness
/config - View current configuration
/stats - View usage statistics
```

### Troubleshooting

**Bot not responding:**
```bash
# Check if running
docker-compose ps

# Check logs for errors
docker-compose logs --tail=50 discord-grok-bot | grep -i error

# Restart
docker-compose restart discord-grok-bot
```

**Image generation not working:**
```bash
# Check initialization
docker-compose logs discord-grok-bot | grep -E "Image|Gemini"

# Expected output:
# ✅ Gemini 2.5 Flash Image client initialized
# ✅ Image processing service initialized

# Verify API key
docker-compose exec discord-grok-bot env | grep GEMINI_API_KEY
```

**High memory usage:**
```bash
# Check memory
docker stats discord-grok-bot --no-stream

# Restart to clear cache
docker-compose restart discord-grok-bot
```

**Configuration changes not applying:**
```bash
# Rebuild completely
docker-compose down
docker-compose build --no-cache
docker-compose up -d
```

## Monitoring and Health Checks

### Health Checks
```bash
# Docker
docker-compose exec discord-grok-bot python scripts/health_check.py

# Or directly
python scripts/health_check.py
```

### Monitoring
Use the bot's built-in slash commands:
- `/api-usage` - Real-time API usage and rate limits
- `/usage-report` - Downloadable usage report (CSV + Markdown)
- `/stats` - Bot statistics and performance metrics

See [SLASH_COMMANDS.md](SLASH_COMMANDS.md) for details.

### Docker Health Checks
When running with Docker, the container includes automatic health checks:
```bash
docker ps  # Shows health status
docker-compose logs -f  # View real-time logs
```

## Project Structure

```
discord-grok-bot/
├── src/
│   ├── bot/          # Discord bot implementation
│   ├── models/       # Data models and structures
│   ├── services/     # External service integrations
│   ├── utils/        # Utility functions
│   └── config.py     # Configuration management
├── main.py           # Application entry point
├── requirements.txt  # Python dependencies
├── .env.example      # Environment configuration template
└── README.md         # This file
```

## Development

This project is currently under development. See the implementation tasks in `.kiro/specs/discord-grok-bot/tasks.md` for the development roadmap.

## Quick Reference

### Most Used Commands

**Start Bot:**
```bash
# Docker
docker-compose up -d

# Local
python main.py
```

**View Logs:**
```bash
# Docker (real-time)
docker-compose logs -f discord-grok-bot

# Local
tail -f logs/bot.log
```

**Restart Bot:**
```bash
# Docker
docker-compose restart discord-grok-bot

# After code changes
docker-compose up --build -d
```

**Stop Bot:**
```bash
# Docker
docker-compose down

# Local
Ctrl+C
```

**Health Check:**
```bash
# Docker
docker-compose exec discord-grok-bot python scripts/health_check.py

# Local
python scripts/health_check.py
```

**Troubleshooting:**
```bash
# Check errors
docker-compose logs discord-grok-bot | grep -i error

# Check status
docker-compose ps

# Full rebuild
docker-compose down && docker-compose build --no-cache && docker-compose up -d
```

### Environment Files

- `.env` - Your configuration (not in git)
- `.env.example` - Configuration template
- `logs/bot.log` - Application logs
- `docker-compose.yml` - Docker configuration

### Documentation

- [SLASH_COMMANDS.md](SLASH_COMMANDS.md) - Slash commands guide
- [IMAGE_GENERATION.md](IMAGE_GENERATION.md) - Image generation/editing guide
- [PDF_SUPPORT.md](PDF_SUPPORT.md) - PDF processing guide
- [FILE_UPLOAD_FEATURE.md](FILE_UPLOAD_FEATURE.md) - File upload guide
- [DOCKER.md](DOCKER.md) - Detailed Docker setup

## License

[Add your license information here]