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

### `/prompt-mode` - Switch Response Style
**Description:** Switch between concise responses and one-time detailed “thinking” mode.

**Usage:**
```
/prompt-mode mode:<choice>
```

**Available Modes:**
- **Short** — Concise and to the point (default)
- **Thinking** — One detailed, comprehensive response; then auto-reverts to Short

**Notes:**
- Thinking mode is single-use and reverts automatically after one reply

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

### `/api-usage` - API Usage & Rate Limits
**Description:** Show real-time API usage, free-tier rate limits, model capabilities, and performance metrics.

**Usage:**
```
/api-usage
```

**Information Displayed:**
- **Rate Limits (Free Tier):** Requests/min, Requests/day, Tokens/min
- **Model Capabilities:** Max input/output tokens, context window
- **API Usage:** Total calls, average API time, failures, success rate
- **Token Usage (Estimated):** Input/output/total tokens
- **Message & Image Processing:** Counts, averages, success rates
- **Service Health (Image Gen):** nano-banana status and rate-limit remaining

**Visibility:** Public (message is visible to everyone in the channel)

**Notes:** Image generation requires a paid API key. Free tier image edits are unavailable.

**Permissions:** None required (Everyone)

📖 See [API_USAGE_COMMAND.md](API_USAGE_COMMAND.md) for a deeper breakdown.

---

### `/usage-report` - Generate Usage Report
**Description:** Generate a downloadable usage report since bot startup. Attaches both Markdown and CSV files and posts a summary embed.

**Usage:**
```
/usage-report
```

**Contents:**
- Markdown report with sections: Overview, API usage, Token usage (estimated), Message processing, Image processing (if available), Image queue snapshot
- CSV with key metrics (uptime, counts, averages, token estimates, free-tier limits)

**Visibility:** Public (message and attachments are visible to everyone in the channel)

**Notes:** Token counts are estimated (~4 chars ≈ 1 token). Image generation/editing requires paid API access.

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

### `/features` - Discover Capabilities
**Description:** Show a guided overview of everything the bot can do, including AI, UX, and image features.

**Usage:**
```
/features
```

**Permissions:** None required (Everyone)

---

### `/edit-image` - Edit an Image with AI
**Description:** Upload an image and describe the edit you want (object removal, background change, style, etc.).

**Usage:**
```
/edit-image image:<attachment> instruction:"your edit" [edit_type:<choice>]
```

**Examples:**
- Remove background: “remove the background”
- Replace background: “replace the background with a beach”
- Remove objects: “remove the person in red”
- Style transfer: “make this look like a watercolor painting”

**Notes:**
- Large images may be rejected based on max size in config
- Image processing runs asynchronously and returns the edited image when ready
- Requires paid API key for image generation/editing

**Permissions:** None required (Everyone)

---

### `/image-queue` - Check Image Queue
**Description:** Show the current image processing queue size, stats, and your per-minute quota.

**Usage:**
```
/image-queue
```

**Visibility:** Public (channel-wide)

**Permissions:** None required (Everyone)

---

### `/clear-cache` - Clear Cache
**Description:** Clear the bot's internal caches including performance metrics and statistics. Useful for resetting counters or freeing memory. Recommended for administrators.

**Usage:**
```
/clear-cache
```

**What Gets Cleared:**
- Performance metrics cache
- Internal statistics counters
- Token usage counts

**Permissions:** Server-managed (recommended: Administrator)

---

### `/dev` - Toggle Developer Mode
**Description:** Toggle developer mode to control error message verbosity. When enabled, shows full error details including stack traces for debugging. When disabled, shows user-friendly error messages only. Recommended for administrators.

**Usage:**
```
/dev
```

**When Dev Mode is Enabled (ON):**
- Full error messages displayed
- Complete stack traces shown (up to 1500 characters)
- Error type information included
- Useful for debugging API issues and troubleshooting
- ⚠️ May expose technical implementation details

**When Dev Mode is Disabled (OFF - Default):**
- User-friendly error messages only
- Simplified notifications
- No stack traces or technical details
- Better experience for regular users

**Use Cases:**
- Debugging API connection issues
- Troubleshooting image processing errors
- Testing new features or updates
- Investigating user-reported issues

**Security Note:** Dev mode may expose internal error details. Only enable when actively debugging, and disable immediately after. Server admins can restrict access via Discord command permissions.

**Permissions:** Server-managed (recommended: Administrator)

📖 See [DEV_MODE.md](DEV_MODE.md) for complete developer mode documentation.

---

## Usage Tips

### Viewing Statistics
- Use `/stats` regularly to monitor your bot's token usage
- Use `/api-usage` to view API quotas, rate limits, and model capabilities
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
- `/stats` shows detailed performance data (ephemeral)
- `/api-usage` shows quotas and service health (public)

### Administrative Tasks
- Use `/clear-cache` to reset statistics after maintenance
- Use `/dev` to toggle detailed error output for debugging
- Server admins can restrict who can run `/clear-cache` and `/dev` via Discord permissions

### Debugging and Development
- Enable `/dev` mode when troubleshooting issues
- Full error details help diagnose API problems
- Remember to disable after debugging for better UX

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

### Inspect API usage and quotas:
```
/api-usage
```

### Get help with commands:
```
/help
```

### Switch to Thinking mode for one detailed reply:
```
/prompt-mode mode:Thinking
```

### Edit an image (remove background):
```
/edit-image image:<attach a file> instruction:"remove the background"
```

### Enable developer mode for debugging (admin):
```
/dev
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
- `/clear-cache` and `/dev` require Administrator permission
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
- (Moved) `/usage-report` is now available

---

Last Updated: November 4, 2025
