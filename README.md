# Discord Grok Bot

A Discord bot that provides AI-powered conversational responses using Google's Gemini API — inspired by X's Grok. Responds when mentioned in guild channels and group DMs, and auto-responds in private DMs. Supports image generation, PDF processing, LaTeX rendering, and per-channel personality tuning.

## Recent Updates (v3.0)

- ✅ **Private DM Support**: Bot now auto-responds to all messages in private DMs — no @mention required
- ✅ **LaTeX & Table Rendering**: Block LaTeX (`$$...$$`) renders to PNG images; inline LaTeX (`$...$`) converts to Unicode; markdown tables display as formatted code blocks
- ✅ **Personality/Tone System**: Per-channel personality settings via `/personality` — choose from default, professional, casual, sarcastic, academic, or friendly
- ✅ **Per-User Preferences**: `/preferences` commands let users set their preferred AI model and response language, applied automatically on every request
- ✅ **Bug Fixes**:
  - Fixed bot crashing with AttributeError on `channel.name` in DMs
  - Fixed token usage not being tracked for DM conversations
  - Fixed audio transcription wiping all conversation context
  - Fixed long responses creating deeply nested reply chains (now sends subsequent parts as flat messages)
  - Removed faulty follow-up heuristic that triggered unwanted double-responses

## Previous Updates (v2.0)

- ✅ **Code Cleanup & Optimization**: Refactored codebase for better maintainability
- ✅ **Bug Fixes**: Fixed image handling for Gemini API, @everyone/@here spam, continuation indicators
- ✅ **Enhanced Stability**: All syntax checks pass, no diagnostic errors

## Todo List
 - [ ] (CANCELED, UNSAFE) Make model interpret user message to do actual actions in discord server (mute all, ban all, kick all, etc)
 - [x] **NEW!** Add image generation and editing via Gemini 2.5 Flash Image (nano-banana)
 - [x] Add functionality to read files in the discord message
 - [x] Add PDF support - PDFs are now converted to images and processed!
 - [x] LaTeX and table rendering in responses
 - [x] Private DM support (auto-respond without @mention)
 - [x] Per-channel personality/tone settings
 - [x] Per-user model and language preferences
 - [ ] Add feature to join vc and answer in real time
 - [ ] Add interactive image refinement (multiple edit iterations)
 - [ ] Add batch image processing

## Features

### Core Capabilities
- **Context-Aware Responses**: Analyzes recent message history for relevant context
- **Enhanced Reply Context**: Provides additional context when replying to specific messages
- **Smart Mention Detection**: Responds to direct @mentions and replies in guild/group channels; auto-responds in private DMs (no @mention needed)
- **Intelligent Message Splitting**: Preserves markdown formatting and code blocks across long messages; subsequent parts sent as flat channel messages (no nested chains)
- **Configurable Continuation Indicators**: Optional message part indicators (can be disabled)
- **LaTeX Rendering**: Block LaTeX expressions (`$$...$$`) rendered to PNG images via matplotlib; inline expressions (`$...$`) converted to Unicode with image fallback
- **Table Formatting**: Markdown tables automatically converted to fixed-width code blocks for proper Discord display

### AI & Media Processing
- **Image Analysis**: Supports image attachments for visual understanding
- **Image Generation & Editing**: 🎨 Create and edit images using Gemini 2.5 Flash Image (see [IMAGE_GENERATION.md](IMAGE_GENERATION.md))
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

### Personality & Preferences
- **Channel Personalities**: Set a per-channel AI tone with `/personality` — options: default, professional, casual, sarcastic, academic, friendly
- **User Preferences**: Each user can set their preferred Gemini model and response language via `/preferences`; settings persist across sessions and apply automatically

### Developer Tools
- **Developer Mode**: 🔧 Toggle detailed error output with `/dev` command (see [DEV_MODE.md](DEV_MODE.md))
  - Full stack traces when enabled
  - User-friendly messages when disabled
  - Perfect for debugging and troubleshooting
  - Admin-only control
- **Error Handling**: Graceful handling of API errors with user-friendly messages
- **Detailed Logging**: Track file processing, AI requests, and responses for troubleshooting
- **Per-User Token Tracking**: Precisely logs Gemini input/output tokens per request, stores them in SQLite, and powers an in-server `/token-leaderboard`

### Configuration & Monitoring
- **Highly Configurable**: Customizable message limits, timeouts, model selection, and more
- **Multiple AI Models**: Switch between Gemini 2.5 Flash, Flash-Lite, Pro, and 2.0 variants
- **Real-time Monitoring**: Built-in health checks and performance metrics
- **Clean Codebase**: Modular architecture with centralized utilities and proper error handling

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

3. Ensure local persistence folders exist (Docker bind mounts create them if missing, but creating ahead of time avoids permission issues):
```bash
mkdir -p data logs
```

4. Quick start with Docker:
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

After the initial build completes successfully, subsequent restarts only require:
```bash
docker compose up -d
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

**Context & Response Settings:**
- `MAX_CONTEXT_MESSAGES`: Maximum messages to include in context (default: 100)
- `REPLY_CONTEXT_RANGE`: Messages before/after replied message (default: 10)
- `RESPONSE_TIMEOUT`: API response timeout in seconds (default: 30)
- `MAX_RETRIES`: Maximum retry attempts for API calls (default: 3)

**Message Formatting:**
- `MESSAGE_SPLIT_LENGTH`: Maximum length for each message part (default: 2000)
- `PRESERVE_CODE_BLOCKS`: Preserve code block formatting across splits (default: true)
- `ADD_CONTINUATION_INDICATORS`: Add "continued" text between message parts (default: true)

**User Experience:**
- `SHOW_TYPING_INDICATORS`: Show typing indicator while processing (default: true)
- `USE_RICH_EMBEDS`: Use rich Discord embeds for responses (default: true)
- `ENABLE_REACTION_FEEDBACK`: Add reaction emojis for feedback (default: true)

**Logging & Monitoring:**
- `LOG_LEVEL`: Logging level - DEBUG, INFO, WARNING, ERROR (default: INFO)
- `LOG_FILE`: Optional log file path (default: logs to console and bot.log)
- `ENABLE_PERFORMANCE_LOGGING`: Enable performance metrics logging (default: true)
- `DEV_MODE_ENABLED`: Enable detailed error output (default: false)

**Image Processing:**
- `NANO_BANANA_API_KEY`: API key for image generation/editing (uses Gemini 2.5 Flash Image model, defaults to GEMINI_API_KEY)
- `MAX_IMAGE_SIZE_MB`: Maximum image size in MB (default: 10)
- `IMAGE_PROCESSING_TIMEOUT`: Image processing timeout in seconds (default: 60)
- `MAX_CONCURRENT_IMAGE_EDITS`: Maximum concurrent image operations (default: 3)

**Data Storage:**
- `TOKEN_DB_PATH`: Filesystem path for storing the SQLite token usage database (default: `data/token_usage.db`)

## Usage

### Basic Usage
1. Invite the bot to your Discord server with appropriate permissions
2. Mention the bot in any channel: `@YourBot Hello, how are you?`
3. The bot will respond with an AI-generated message based on the context

### Slash Commands
The bot includes powerful slash commands for configuration and monitoring:

**General:**
- `/ping` - Check bot status and latency
- `/model` - Switch between Gemini Flash models (2.5, 2.5-Lite, 2.0, 2.0-Lite)
- `/stats` - View usage statistics and token consumption
- `/token-leaderboard` - Display the top 10 token users in the current guild
- `/config` - View current configuration
- `/help` - Show help information
- `/dev` - Toggle developer mode for detailed error output (Admin only)
- `/clear-cache` - Clear statistics cache (Admin only)

**Personality & Tone:**
- `/personality <style>` - Set the bot's tone for this channel (default, professional, casual, sarcastic, academic, friendly)
- `/personality-info` - Show the current personality and all available styles

**User Preferences:**
- `/preferences model <model>` - Set your preferred Gemini model
- `/preferences language <language>` - Set your preferred response language
- `/preferences show` - View your current preferences
- `/preferences clear` - Reset all preferences to defaults

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
│   ├── bot/                    # Discord bot implementation
│   │   ├── discord_bot.py      # Main bot class and event handlers
│   │   ├── commands.py         # Slash command definitions
│   │   └── enhanced_command_handler.py  # Image editing command handler
│   ├── models/                 # Data models and structures
│   │   └── data_models.py      # Core data models (MessageContext, APIResponse, etc.)
│   ├── services/               # External service integrations
│   │   ├── gemini_client.py    # Gemini API client for text generation
│   │   ├── nano_banana_client.py  # Gemini image generation client
│   │   ├── context_collector.py   # Message context collection
│   │   ├── message_splitter.py    # Intelligent message splitting
│   │   ├── image_processing_service.py  # Image processing queue
│   │   ├── user_experience_service.py   # UX enhancements
│   │   ├── token_tracker.py    # Token usage tracking
│   │   ├── content_renderer.py # LaTeX-to-image and table formatting
│   │   ├── channel_settings_service.py  # Per-channel personality settings
│   │   ├── user_preferences_service.py  # Per-user model/language preferences
│   │   └── help_system.py      # Help and command suggestions
│   ├── utils/                  # Utility functions
│   │   ├── error_manager.py    # Centralized error handling
│   │   ├── logging_config.py   # Logging configuration
│   │   ├── markdown_utils.py   # Markdown parsing
│   │   ├── image_utils.py      # Image validation
│   │   └── token_extraction.py # Shared token usage extraction
│   ├── config.py               # Configuration management
│   └── constants.py            # Application constants
├── data/                       # Data storage
│   └── token_usage.db          # SQLite database for token tracking
├── logs/                       # Log files
│   └── bot.log                 # Application logs
├── scripts/                    # Utility scripts
│   ├── health_check.py         # Health check script
│   ├── docker-build.sh         # Docker build script (Linux/Mac)
│   └── docker-build.bat        # Docker build script (Windows)
├── grok-prompts/               # AI prompt templates
├── main.py                     # Application entry point
├── requirements.txt            # Python dependencies
├── docker-compose.yml          # Docker configuration
├── Dockerfile                  # Docker image definition
├── .env.example                # Environment configuration template
└── README.md                   # This file
```

### Code Organization

The codebase follows a modular architecture:

- **Bot Layer** (`src/bot/`): Discord integration and command handling
- **Service Layer** (`src/services/`): Business logic and external API integrations
- **Model Layer** (`src/models/`): Data structures and validation
- **Utility Layer** (`src/utils/`): Shared utilities and helpers
- **Configuration** (`src/config.py`): Centralized configuration management

Key improvements in v2.0:
- Centralized token extraction utility (eliminates duplication)
- Proper module exports for cleaner imports
- Separated concerns (e.g., image processing has its own service)
- Shared error handling and logging utilities

## Development

### Code Quality

The codebase maintains high quality standards:
- ✅ All syntax checks pass
- ✅ No diagnostic errors
- ✅ Modular architecture with clear separation of concerns
- ✅ Comprehensive error handling
- ✅ Detailed logging for debugging
- ✅ Type hints for better IDE support

### Recent Improvements (v2.0)

**Code Cleanup:**
- Removed duplicate token extraction code (~80 lines eliminated)
- Removed unused classes and imports
- Centralized shared utilities
- Improved module organization

**Bug Fixes:**
- Fixed PIL Image handling for Gemini API (images now properly converted to bytes)
- Fixed @everyone/@here spam (bot now only responds to direct mentions)
- Fixed continuation indicators config (now properly honors user settings)

**Architecture:**
- Better separation of concerns
- Proper module exports
- Centralized error handling
- Shared utility functions

### Contributing

When contributing to this project:
1. Follow the existing code structure
2. Add type hints to function signatures
3. Include docstrings for classes and methods
4. Test changes with both Docker and local installations
5. Update documentation for new features
6. Run syntax checks: `python -m py_compile <file>`

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

## Technical Details

### Architecture Highlights

**Modular Design:**
- Clean separation between Discord integration, AI services, and utilities
- Each service has a single, well-defined responsibility
- Shared utilities eliminate code duplication

**Error Handling:**
- Centralized error manager with user-friendly messages
- Automatic retry logic with exponential backoff
- Graceful degradation when services are unavailable

**Performance:**
- Intelligent message splitting preserves formatting
- Async/await throughout for non-blocking operations
- Connection pooling and rate limiting
- Token usage tracking for cost monitoring

**Image Processing:**
- Queue-based processing with configurable concurrency
- Proper PIL Image to bytes conversion for Gemini API
- Support for both generation and editing operations
- Rate limiting per user to prevent abuse

**Message Handling:**
- Smart mention detection (ignores broadcast mentions; auto-responds in private DMs)
- Context-aware responses with conversation history
- Markdown preservation across message splits; parts sent as flat messages, not nested chains
- LaTeX rendering via matplotlib (block → PNG image attachments; inline → Unicode)
- Markdown table conversion to fixed-width code blocks
- Per-channel personality injected into system prompt
- Per-user model/language preferences applied per request

### API Integration

**Gemini Text API:**
- Supports multiple models (Flash, Flash-Lite, Pro)
- Automatic model selection based on complexity
- Search grounding for up-to-date information
- Token usage tracking and reporting

**Gemini Image API:**
- Uses Gemini 2.5 Flash Image model
- Proper image format conversion (PIL → bytes → types.Part)
- Support for both generation and editing
- Natural language instruction parsing

### Data Storage

**SQLite Database (`data/token_usage.db`):**
- `token_usage` — per-user input/output/total tokens per request; powers `/token-leaderboard`
- `channel_settings` — per-channel personality setting; populated by `/personality`
- `user_preferences` — per-user preferred model and language; populated by `/preferences`
- All tables auto-initialized on first run

**File System:**
- Logs stored in `logs/` directory
- Token database in `data/` directory
- Configurable paths via environment variables

## License

[Add your license information here]