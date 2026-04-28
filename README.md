# Discord Grok Bot

A Discord bot that provides AI-powered conversational responses using Google's Gemini API — inspired by X's Grok. Responds when mentioned in guild channels and group DMs, and auto-responds in private DMs. Supports image generation, PDF processing, LaTeX rendering, per-channel personality tuning, and deep research.

## Recent Updates (v4.0)

- **New Models**: Default is now Gemini Flash 3 Preview; added Gemini 3.1 Pro Preview for advanced tasks; Gemini 2.5 Flash-Lite remains as fast router
- **Thinking Mode**: `/config thinking` toggles Chain-of-Thought reasoning — bot shows its thought process before answering
- **DeepSearch**: `/config deepsearch` forces Google Search on every query for real-time information
- **Deep Research**: `/deepresearch` performs a comprehensive research task and generates a formatted report
- **Summarize**: `/summarize` condenses the current conversation into a concise summary
- **Config Refactor**: Non-secret settings moved from `.env` to `config.yaml`; `.env` now holds only API keys and tokens
- **New `/config` Subcommand Group**: Model switching, thinking mode, deepsearch, and debug logging are now under `/config`

## Previous Updates (v3.0)

- **Private DM Support**: Bot now auto-responds to all messages in private DMs — no @mention required
- **LaTeX & Table Rendering**: Block LaTeX (`$$...$$`) renders to PNG images; inline LaTeX (`$...$`) converts to Unicode; markdown tables display as formatted code blocks
- **Personality/Tone System**: Per-channel personality settings via `/personality`
- **Per-User Preferences**: `/preferences` commands let users set their preferred AI model and response language

## Todo List

- [ ] (CANCELED, UNSAFE) Make model interpret user message to do actual actions in discord server
- [x] Add image generation and editing via Gemini Image model
- [x] Add functionality to read files in the discord message
- [x] Add PDF support
- [x] LaTeX and table rendering in responses
- [x] Private DM support (auto-respond without @mention)
- [x] Per-channel personality/tone settings
- [x] Per-user model and language preferences
- [x] Thinking Mode (Chain of Thought)
- [x] DeepSearch (force Google Search)
- [x] Deep Research command
- [ ] Add feature to join vc and answer in real time
- [ ] Add interactive image refinement (multiple edit iterations)
- [ ] Add batch image processing

## Features

### Core Capabilities
- **Context-Aware Responses**: Analyzes recent message history for relevant context
- **Enhanced Reply Context**: Provides additional context when replying to specific messages
- **Smart Mention Detection**: Responds to direct @mentions and replies in guild/group channels; auto-responds in private DMs (no @mention needed)
- **Intelligent Message Splitting**: Preserves markdown formatting and code blocks across long messages; subsequent parts sent as flat channel messages (no nested chains)
- **LaTeX Rendering**: Block LaTeX expressions (`$$...$$`) rendered to PNG images via matplotlib; inline expressions (`$...$`) converted to Unicode with image fallback
- **Table Formatting**: Markdown tables automatically converted to fixed-width code blocks for proper Discord display
- **Thinking Mode**: Optional Chain-of-Thought mode where the bot shows its reasoning process
- **DeepSearch**: Force Google Search grounding on every query for up-to-date answers

### AI & Media Processing
- **Image Analysis**: Supports image attachments for visual understanding
- **Image Generation & Editing**: Create and edit images using Gemini Image model (see [IMAGE_GENERATION.md](IMAGE_GENERATION.md))
  - Generate images from text descriptions
  - Edit existing images with natural language prompts
  - Object removal, background changes, style transfer, and more
- **PDF Support**: Full PDF support — automatically converts PDFs to images for AI analysis (see [PDF_SUPPORT.md](PDF_SUPPORT.md))
  - Multi-page support, high-quality conversion (2x resolution)
- **File Upload Support**: Reads and processes uploaded text files, code, configurations, logs, and more (see [FILE_UPLOAD_FEATURE.md](FILE_UPLOAD_FEATURE.md))
  - Supports 40+ file types including all major programming languages
- **Deep Research**: `/deepresearch` performs multi-step research and generates a structured report

### Personality & Preferences
- **Channel Personalities**: Set a per-channel AI tone with `/personality` — options: default, professional, casual, sarcastic, academic, friendly
- **User Preferences**: Each user can set their preferred Gemini model and response language via `/preferences`; settings persist across sessions and apply automatically

### Developer Tools
- **Developer Mode**: Toggle detailed error output with `/dev` command
- **Debug Logging**: Toggle verbose debug logging with `/config debug`
- **Error Handling**: Graceful handling of API errors with user-friendly messages
- **Detailed Logging**: Track file processing, AI requests, and responses for troubleshooting
- **Per-User Token Tracking**: Logs Gemini input/output tokens per request, stores them in SQLite, and powers `/token-leaderboard`

### Configuration & Monitoring
- **Highly Configurable**: Settings managed in `config.yaml`; secrets in `.env`
- **Multiple AI Models**: Gemini Flash 3 Preview (default), Gemini 2.5 Flash-Lite (fast), Gemini 3.1 Pro Preview (advanced)
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

3. Configure secrets:
```bash
cp .env.example .env
# Edit .env with your Discord token and Gemini API key
```

4. Configure settings:
```bash
cp config.yaml.example config.yaml
# Edit config.yaml for non-secret settings (models, context limits, etc.)
```

5. Run the bot:
```bash
python main.py
```

#### Option 2: Docker Installation (Recommended)

1. Clone the repository:
```bash
git clone <repository-url>
cd discord-grok-bot
```

2. Configure secrets:
```bash
cp .env.example .env
# Edit .env with your Discord token and Gemini API key
```

3. Configure settings:
```bash
cp config.yaml.example config.yaml
# Edit config.yaml for non-secret settings
```

4. Ensure local persistence folders exist:
```bash
mkdir -p data logs
```

5. Quick start with Docker:
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

After the initial build, subsequent restarts only require:
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

# Remove and rebuild everything
docker-compose down
docker-compose build --no-cache
docker-compose up -d

# Clean up Docker resources
docker system prune -a
```

### Discord Bot Setup

1. Go to the [Discord Developer Portal](https://discord.com/developers/applications)
2. Create a new application and bot
3. Copy the bot token to your `.env` file
4. Enable the following bot permissions:
   - Send Messages
   - Read Message History
   - Use Application Commands (required for slash commands)
5. Enable the **Message Content Intent** in the Bot section
6. Invite the bot to your server using the OAuth2 URL generator with `bot` and `applications.commands` scopes

### Google Gemini API Setup

1. Visit [Google AI Studio](https://makersuite.google.com/app/apikey)
2. Create a new API key
3. Copy the API key to your `.env` file

## Configuration

Configuration is split into two files:

### `.env` — Secrets only
```env
DISCORD_BOT_TOKEN=your_discord_token
GEMINI_API_KEY=your_gemini_api_key
NANO_BANANA_API_KEY=your_image_api_key   # optional, defaults to GEMINI_API_KEY
```

### `config.yaml` — All other settings

Copy `config.yaml.example` to `config.yaml` and edit as needed. Key sections:

**Bot:**
- `dev_mode`: Enable detailed error output (default: false)
- `token_db_path`: SQLite database path (default: `data/token_usage.db`)

**Context & Response:**
- `context.max_messages`: Maximum messages to retrieve as context candidates before relevance selection (default: 100)
- `context.context_messages_low/medium/high`: Maximum selected messages sent to the final model by task complexity
- `context.reply_range`: Messages before/after replied message (default: 10)
- `context.cutoff_hours`: Message history cutoff in hours (default: 24)
- `response.timeout`: API response timeout in seconds (default: 30)
- `response.max_retries`: Maximum retry attempts (default: 3)

**Message Formatting:**
- `messages.split_length`: Maximum length for each message part (default: 2000)
- `messages.preserve_code_blocks`: Preserve code block formatting across splits (default: true)
- `messages.add_continuation_indicators`: Add "continued" text between parts (default: true)

**Models:**
- `models.default`: Default model ID (default: `gemini-3.0-flash-preview`)
- `models.router`: Fast router model (default: `gemini-2.5-flash-lite`)
- `models.available`: List of available models shown in `/config model`

**Image Processing:**
- `image_processing.max_size_mb`: Maximum image size (default: 10)
- `image_processing.timeout`: Processing timeout in seconds (default: 60)
- `image_processing.max_concurrent_edits`: Concurrent image operations (default: 3)

**Logging:**
- `logging.level`: DEBUG, INFO, WARNING, or ERROR (default: INFO)
- `logging.file`: Optional log file path
- `logging.enable_performance_logging`: Enable performance metrics (default: true)

## Usage

### Basic Usage
1. Invite the bot to your Discord server with appropriate permissions
2. Mention the bot in any channel: `@YourBot Hello, how are you?`
3. The bot will respond with an AI-generated message based on the context

### Slash Commands

**General:**
- `/ping` — Check bot status and latency
- `/stats` — View usage statistics and token consumption
- `/token-leaderboard` — Display the top token users in the current guild
- `/api-usage` — Real-time API usage and rate limits
- `/usage-report` — Downloadable usage report (CSV + Markdown)
- `/features` — Discover all available bot features
- `/dev` — Toggle developer mode for detailed error output

**Configuration (`/config` group):**
- `/config model <model>` — Switch AI model (Flash 3 Preview, 2.5 Flash-Lite, 3.1 Pro Preview)
- `/config thinking <true/false>` — Toggle Thinking Mode (Chain of Thought)
- `/config deepsearch <true/false>` — Toggle DeepSearch (force Google Search on every query)
- `/config debug <true/false>` — Toggle verbose debug logging
- `/config info` — View current configuration and feature status

**AI Tools:**
- `/deepresearch <topic>` — Perform deep research and generate a structured report
- `/summarize` — Summarize the current conversation

**Personality & Tone:**
- `/personality <style>` — Set the bot's tone for this channel (default, professional, casual, sarcastic, academic, friendly)
- `/personality-info` — Show the current personality and all available styles

**User Preferences:**
- `/preferences model <model>` — Set your preferred Gemini model
- `/preferences language <language>` — Set your preferred response language
- `/preferences show` — View your current preferences
- `/preferences clear` — Reset all preferences to defaults

**Image Processing (if configured):**
- `/edit-image <image> <instruction>` — Edit an uploaded image using AI
- `/image-queue` — Check the image processing queue status

**Admin Only:**
- `/clear-cache` — Clear statistics cache

## Deployment

### Local Development

```bash
python main.py
```

The bot will start and connect to Discord. Press `Ctrl+C` to stop gracefully.

### Production Deployment

#### Using Docker (Recommended)

```bash
docker-compose build --no-cache
docker-compose up -d
```

#### Environment Variables for Production

For production deployments, set in `config.yaml`:
- `logging.level: WARNING` or `ERROR` to reduce log verbosity
- `logging.enable_performance_logging: false` if not needed
- `logging.file: /var/log/discord-grok-bot.log` for centralized logging

## Common Operations

### Viewing Logs

**Docker:**
```bash
docker-compose logs -f discord-grok-bot
docker-compose logs --tail=100 discord-grok-bot
docker-compose logs discord-grok-bot | grep -i error
```

**Local:**
```bash
tail -f logs/bot.log
grep -i error logs/bot.log
```

### Restarting the Bot

**Docker:**
```bash
docker-compose restart discord-grok-bot
docker-compose up --build -d   # after code changes
```

**Local:**
```bash
# Stop with Ctrl+C, then:
python main.py
```

### Updating the Bot

**Docker:**
```bash
git pull origin main
docker-compose down
docker-compose build --no-cache
docker-compose up -d
```

**Local:**
```bash
git pull origin main
pip install -r requirements.txt --upgrade
python main.py
```

### Checking Status

```bash
docker-compose ps
docker inspect discord-grok-bot --format='{{.State.Health.Status}}'
docker stats discord-grok-bot --no-stream
```

Or use bot commands: `/ping`, `/config info`, `/stats`

## Monitoring and Health Checks

```bash
# Docker
docker-compose exec discord-grok-bot python scripts/health_check.py

# Local
python scripts/health_check.py
```

Use the bot's built-in slash commands for real-time monitoring:
- `/api-usage` — Real-time API usage and rate limits
- `/usage-report` — Downloadable usage report
- `/stats` — Bot statistics and performance metrics

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
├── config.yaml                 # Non-secret configuration
├── docker-compose.yml          # Docker configuration
├── Dockerfile                  # Docker image definition
├── .env                        # Secrets (not in git)
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

### Data Storage

**SQLite Database (`data/token_usage.db`):**
- `token_usage` — per-user input/output/total tokens per request; powers `/token-leaderboard`
- `channel_settings` — per-channel personality setting; populated by `/personality`
- `user_preferences` — per-user preferred model and language; populated by `/preferences`
- All tables auto-initialized on first run

**File System:**
- Logs stored in `logs/` directory
- Token database in `data/` directory
- Configurable paths via `config.yaml`

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
- Supports multiple models (Flash 3 Preview, Flash-Lite, Pro 3.1 Preview)
- Intelligent routing — simple queries use the fast lite model; complex ones use the default
- Thinking Mode (Chain of Thought) for deeper reasoning
- DeepSearch grounding via Google Search
- Token usage tracking and reporting

**Gemini Image API:**
- Uses Gemini Image model for generation and editing
- Proper image format conversion (PIL → bytes → types.Part)
- Natural language instruction parsing

## Quick Reference

**Start Bot:**
```bash
docker-compose up -d        # Docker
python main.py              # Local
```

**View Logs:**
```bash
docker-compose logs -f discord-grok-bot   # Docker (real-time)
tail -f logs/bot.log                       # Local
```

**Restart Bot:**
```bash
docker-compose restart discord-grok-bot   # Docker
docker-compose up --build -d              # After code changes
```

**Stop Bot:**
```bash
docker-compose down   # Docker
Ctrl+C                # Local
```

**Troubleshooting:**
```bash
docker-compose logs discord-grok-bot | grep -i error
docker-compose ps
docker-compose down && docker-compose build --no-cache && docker-compose up -d
```

### Key Files

- `config.yaml` — Bot settings (not secrets)
- `.env` — API keys and tokens (not in git)
- `logs/bot.log` — Application logs
- `docker-compose.yml` — Docker configuration

### Documentation

- [SLASH_COMMANDS.md](SLASH_COMMANDS.md) — Slash commands guide
- [IMAGE_GENERATION.md](IMAGE_GENERATION.md) — Image generation/editing guide
- [PDF_SUPPORT.md](PDF_SUPPORT.md) — PDF processing guide
- [FILE_UPLOAD_FEATURE.md](FILE_UPLOAD_FEATURE.md) — File upload guide
- [DOCKER.md](DOCKER.md) — Detailed Docker setup

## License

[Add your license information here]
