# Docker Setup for Discord Grok Bot

Simple Docker setup for running the Discord Grok Bot.

## Quick Start

```bash
# Linux/macOS
./scripts/docker-build.sh

# Windows
scripts\docker-build.bat

# Or manually
docker-compose up --build -d
```

## Prerequisites

- Docker and Docker Compose
- Your `.env` file configured with Discord bot token and API keys

## Configuration

Make sure your `.env` file has the required tokens:

```bash
DISCORD_BOT_TOKEN=your_discord_bot_token
GEMINI_API_KEY=your_gemini_api_key
# NANO_BANANA_API_KEY is optional - defaults to GEMINI_API_KEY if not set
# Enables image generation/editing via Gemini 2.5 Flash Image model
```

## Common Commands

```bash
# Build and start
docker-compose up --build -d

# View logs
docker-compose logs -f discord-grok-bot

# Stop
docker-compose down

# Restart
docker-compose restart discord-grok-bot

# Check status
docker-compose ps
```

## Health Check

```bash
# Check if bot is healthy
docker-compose exec discord-grok-bot python scripts/health_check.py
```

## Features Enabled in Docker

✅ **Gemini 2.5 Flash** - Chat responses with context awareness
✅ **Image Generation** - Create images from text prompts (Gemini 2.5 Flash Image)
✅ **Image Editing** - Modify images with natural language (object removal, style transfer, etc.)
✅ **PDF Processing** - Automatic PDF to image conversion
✅ **File Upload Support** - Process 40+ file types
✅ **Health Checks** - Automatic monitoring and restart on failure

## Data Storage & Volumes

The bot uses several directories for different purposes:

- `./logs` - Bot log files (persistent, mounted from host)
- `./src` - Source code (mounted for live editing during development)
- `bot-temp` - Temporary files (Docker volume)
  - Image processing cache
  - File upload staging
  - PDF conversion workspace
- `bot-cache` - Cache data (Docker volume)
  - API response cache
  - Processed data cache

**Note**: Source code mounting enables live editing without rebuilding. Remove this mount in production for better security.

## Environment Variables

Required in your `.env` file:

```bash
# Required
DISCORD_BOT_TOKEN=your_discord_bot_token
GEMINI_API_KEY=your_gemini_api_key

# Optional - Image generation uses GEMINI_API_KEY by default
NANO_BANANA_API_KEY=your_separate_key_for_images

# Optional - Configuration
MAX_CONTEXT_MESSAGES=100
IMAGE_PROCESSING_TIMEOUT=60
MAX_CONCURRENT_IMAGE_EDITS=3
LOG_LEVEL=INFO
```

See `.env.example` for all available options.

## Troubleshooting

### Container Issues

```bash
# Check logs if something goes wrong
docker-compose logs discord-grok-bot

# Follow logs in real-time
docker-compose logs -f discord-grok-bot

# Check if container is running
docker-compose ps

# View container health status
docker inspect discord-grok-bot --format='{{.State.Health.Status}}'

# Restart if needed
docker-compose restart discord-grok-bot

# Rebuild if you made code changes
docker-compose up --build -d

# Full rebuild (no cache)
docker-compose build --no-cache
docker-compose up -d
```

### Common Problems

**Container keeps restarting:**
- Check if `.env` file exists with valid tokens
- View logs: `docker-compose logs discord-grok-bot`
- Verify GEMINI_API_KEY is valid

**Image generation not working:**
- Ensure GEMINI_API_KEY has access to image models
- Check logs for initialization messages:
  ```
  ✅ Gemini 2.5 Flash Image client initialized
  ✅ Image processing service initialized
  ```

**Out of disk space:**
- Clean up Docker volumes: `docker volume prune`
- Remove old images: `docker image prune -a`
- Check log file size: `ls -lh logs/bot.log`

**Permission issues:**
- The bot runs as non-root user `botuser`
- Ensure mounted directories are writable:
  ```bash
  chmod -R 755 logs/
  ```

### Development vs Production

**Development (hot-reload enabled):**
```yaml
volumes:
  - ./src:/app/src  # Live code editing
```

**Production (recommended):**
Remove the `./src:/app/src` mount for better security and performance.

## Advanced Usage

### Running Commands in Container

```bash
# Run health check
docker-compose exec discord-grok-bot python scripts/health_check.py

# Run monitor script
docker-compose exec discord-grok-bot python scripts/monitor.py --hours 24

# Access container shell
docker-compose exec discord-grok-bot bash

# View environment variables
docker-compose exec discord-grok-bot env | grep -E 'GEMINI|DISCORD'
```

### Resource Limits (Optional)

Add resource limits to `docker-compose.yml` for production:

```yaml
services:
  discord-grok-bot:
    deploy:
      resources:
        limits:
          cpus: '2'
          memory: 2G
        reservations:
          cpus: '1'
          memory: 1G
```

### Monitoring

View real-time resource usage:
```bash
docker stats discord-grok-bot
```

## Testing Image Generation in Docker

Once your bot is running, test the image features:

### Test Image Generation
```
@YourBot generate an image of a nano banana in a fancy restaurant
@YourBot create a cyberpunk city at sunset
```

### Test Image Editing
```
@YourBot [attach image] remove the background
@YourBot [attach image] make this look like a watercolor painting
```

### Verify Logs
```bash
# Check for successful initialization
docker-compose logs discord-grok-bot | grep "Image"

# Expected output:
# ✅ Gemini 2.5 Flash Image client initialized
# ✅ Image processing service initialized
```

## Build Information

**Image Size**: ~800MB (includes Python, system dependencies, and all packages)
**Build Time**: 2-5 minutes (first build, faster with cache)
**Runtime Memory**: 200-500MB (varies with image processing)

That's it! Your Discord bot is now running in Docker with full Gemini AI and image generation capabilities.