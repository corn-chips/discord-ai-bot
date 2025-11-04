# Slash Commands Guide

This document describes all available slash commands for the Discord Grok Bot.

## Available Commands

### `/ping` - Check Bot Status
**Description:** Check if the bot is responsive and view its latency.

**Usage:**
```
/ping
```

**Response:**
- Bot status (✅ Operational)
- Current latency in milliseconds

**Permissions:** None required (Everyone)

---

### `/model` - Switch AI Model
**Description:** Switch between different Gemini Flash models for different performance characteristics.

**Usage:**
```
/model model_name:<choice>
```

**Available Models:**
- **Gemini 2.5 Flash** (Recommended) - Latest generation with best overall performance and advanced features
- **Gemini 2.5 Flash-Lite** - Ultra-fast lightweight variant optimized for maximum speed
- **Gemini 2.0 Flash** - Stable 2.0 generation with reliable performance
- **Gemini 2.0 Flash-Lite** - Lightweight 2.0 variant for quick simple tasks

**Example:**
```
/model model_name:Gemini 2.5 Flash (Latest, Recommended)
```

**Permissions:** None required (Everyone)

---

### `/stats` - View Statistics
**Description:** View detailed bot statistics including token usage, performance metrics, and API call data.

**Usage:**
```
/stats
```

**Information Displayed:**
- **Bot Info:**
  - Uptime
  - Connected guilds
  - Current latency
  
- **Message Statistics:**
  - Total messages processed
  - Average response time
  - Success rate percentage
  
- **API Statistics:**
  - Total API calls made
  - Average API call duration
  - Number of failures
  
- **Token Usage (Estimated):**
  - Total tokens consumed
  - Input tokens
  - Output tokens
  
- **Configuration:**
  - Current model in use
  - Max context messages
  - Response timeout

**Permissions:** None required (Everyone)

---

### `/config` - View Configuration
**Description:** Display the current bot configuration and settings.

**Usage:**
```
/config
```

**Information Displayed:**
- Current AI model and description
- Context settings (max messages, reply range)
- Performance settings (timeout, retries)
- Bot permissions in the current channel
- Logging level

**Permissions:** None required (Everyone)

---

### `/help` - Show Help
**Description:** Display help information about the bot and how to use it.

**Usage:**
```
/help
```

**Information Displayed:**
- How to use the bot (mention syntax)
- List of all available commands
- Bot features
- Links to documentation and issue tracker

**Permissions:** None required (Everyone)

---

### `/clear-cache` - Clear Cache (Admin Only)
**Description:** Clear the bot's internal caches including performance metrics and statistics. Useful for resetting counters or freeing memory.

**Usage:**
```
/clear-cache
```

**What Gets Cleared:**
- Performance metrics cache
- Internal statistics counters
- Token usage counts

**Permissions:** Administrator only

---

## Usage Tips

### Viewing Statistics
- Use `/stats` regularly to monitor your bot's token usage
- Track performance metrics to identify if response times are increasing
- Monitor success rate to detect API issues

### Model Selection
- **Use Gemini 2.5 Flash** for most conversations (default) - latest and best overall
- Try **Gemini 2.5 Flash-Lite** for maximum speed when you need instant responses
- Use **Gemini 2.0 Flash** for stable reliable performance
- Use **Gemini 2.0 Flash-Lite** for simple tasks where speed is critical
- Models can be switched anytime and take effect immediately

### Performance Monitoring
- `/config` shows current settings
- `/ping` checks bot responsiveness
- `/stats` shows detailed performance data

### Administrative Tasks
- Use `/clear-cache` to reset statistics after maintenance
- Only administrators can clear the cache

---

## Examples

### Check bot status quickly:
```
/ping
```

### Switch to ultra-fast model:
```
/model model_name:Gemini 2.5 Flash-Lite (Ultra Fast)
```

### View token usage after heavy use:
```
/stats
```

### Get help with commands:
```
/help
```

### Reset statistics (admin):
```
/clear-cache
```

---

## Troubleshooting

### Slash commands not appearing
1. Make sure the bot has `applications.commands` permission
2. Wait a few minutes for Discord to sync commands
3. Try restarting your Discord client

### Permission errors
- `/clear-cache` requires Administrator permission
- Other commands are available to everyone

### Statistics showing zeros
- The bot needs to process some messages first
- Statistics accumulate over time
- Use `/clear-cache` if you want to reset counters

---

## Token Usage Information

Token usage is estimated based on:
- **Input tokens**: ~1 token per 4 characters of context
- **Output tokens**: ~1 token per 4 characters of response
- Average context message: ~50 tokens

**Note:** These are estimates. Actual token usage from Gemini API may vary.

---

## Integration with Mentions

All slash commands are supplementary to the main bot functionality:

**Main Usage:** 
```
@grok your message here
```

**Slash Commands:** 
- Configuration and management
- Statistics and monitoring
- Help and information

---

## Future Commands (Planned)

- `/set-temperature` - Adjust model creativity
- `/set-timeout` - Customize response timeout
- `/export-logs` - Export bot logs
- `/usage-report` - Generate usage report

---

Last Updated: November 4, 2025
