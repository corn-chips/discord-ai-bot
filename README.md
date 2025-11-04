# Discord Grok Bot

A Discord bot that provides AI-powered conversational responses using Google's Gemini API. The bot mimics Grok's functionality from Twitter, offering contextual responses when mentioned in Discord channels.

## Features

- **Context-Aware Responses**: Analyzes recent message history for relevant context
- **Enhanced Reply Context**: Provides additional context when replying to specific messages
- **Error Handling**: Graceful handling of API errors with user-friendly messages
- **Configurable**: Customizable message limits, timeouts, and other settings

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

3. Build and run with Docker Compose:
```bash
docker-compose up -d
```

The bot will automatically start in the background. To view logs:
```bash
docker-compose logs -f
```

To stop the bot:
```bash
docker-compose down
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

Use the deployment script for easy setup:
```bash
# Linux/Mac
./scripts/deploy.sh docker

# Windows
scripts\deploy.bat docker
```

Or manually:
1. Build the Docker image:
```bash
docker build -t discord-grok-bot .
```

2. Run with Docker Compose:
```bash
docker-compose up -d
```

#### Using systemd (Linux)

Use the deployment script:
```bash
sudo ./scripts/deploy.sh systemd
```

This will automatically create and enable the systemd service.

#### Environment Variables for Production

For production deployments, ensure you set:
- `LOG_LEVEL=WARNING` or `ERROR` to reduce log verbosity
- `ENABLE_PERFORMANCE_LOGGING=false` if not needed
- `LOG_FILE=/var/log/discord-grok-bot.log` for centralized logging

## Monitoring and Health Checks

The bot includes built-in monitoring and health check capabilities:

### Health Checks
```bash
# Linux/Mac
./scripts/deploy.sh health

# Windows
scripts\deploy.bat health

# Or directly
python scripts/health_check.py
```

### Monitoring
```bash
# Linux/Mac
./scripts/deploy.sh monitor

# Windows  
scripts\deploy.bat monitor

# Or directly with options
python scripts/monitor.py --hours 24 --json
```

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

## License

[Add your license information here]