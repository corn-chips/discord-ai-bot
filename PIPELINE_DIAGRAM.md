# Discord Grok Bot - Pipeline Architecture

## Overview

The Discord Grok Bot is an AI-powered conversational assistant that integrates Google's Gemini API with Discord. It provides contextual responses, image generation/editing, file processing (including PDFs), and natural language command handling through a modular service-oriented architecture.

**Core Technologies:**
- Discord.py (Bot framework)
- Google Gemini API (AI responses, image generation)
- PyMuPDF (PDF processing)
- Pillow (Image processing)
- Docker (Containerization)

---

## Architecture Components

### Core Services
1. **DiscordBot** - Main event handler and orchestrator
2. **GeminiClient** - AI response generation via Gemini API
3. **ContextCollector** - Message history and context management
4. **ImageProcessingService** - Image editing queue and processing
5. **EnhancedCommandHandler** - Natural language command parsing
6. **UserExperienceService** - Typing indicators, embeds, reactions
7. **MessageSplitter** - Response formatting and splitting
8. **ErrorManager** - Centralized error handling and logging

### Supporting Components
- **NanoBananaClient** - Gemini 2.5 Flash Image API integration
- **HelpSystem** - Interactive help and documentation
- **PerformanceLogger** - Metrics tracking and monitoring

---

## Data Flow Pipeline

```mermaid
graph TB
    %% Entry Points
    User[Discord User] -->|@mention or /command| Discord[Discord Gateway]
    Discord -->|Event| Bot[DiscordBot Main Handler]
    
    %% Command Routing
    Bot -->|on_message event| MentionCheck{Is Bot<br/>Mentioned?}
    Bot -->|slash command| SlashHandler[Slash Command Handler]
    
    MentionCheck -->|No| Ignore[Ignore Message]
    MentionCheck -->|Yes| HelpCheck{Help<br/>Request?}
    
    HelpCheck -->|Yes| HelpRedirect[Redirect to /help]
    HelpCheck -->|No| EnhancedHandler[Enhanced Command Handler]
    
    %% Enhanced Command Processing
    EnhancedHandler -->|Intent Detection| IntentCheck{Command<br/>Type?}
    
    IntentCheck -->|Image Edit| ImageEdit[Image Edit Request]
    IntentCheck -->|Image Gen| ImageGen[Image Generation]
    IntentCheck -->|Standard| StandardFlow[Standard AI Response]
    
    %% Image Processing Path
    ImageEdit --> AttachCheck{Has<br/>Attachment?}
    AttachCheck -->|Yes| FileType{File<br/>Type?}
    AttachCheck -->|No| ErrorImg[Error: No Image]
    
    FileType -->|Image| ImageQueue[Image Processing Queue]
    FileType -->|PDF| PDFConvert[PDF to Images Converter]
    FileType -->|Text| TextExtract[Text File Extractor]
    
    PDFConvert --> ImageQueue
    ImageQueue -->|Rate Limited| ImageService[Image Processing Service]
    ImageService -->|API Call| NanoBanana[NanoBanana Client<br/>Gemini 2.5 Flash Image]
    NanoBanana -->|Result| ImageResult[Image Edit Result]
    ImageResult --> SendImage[Send to Discord]
    
    %% Standard AI Response Path
    StandardFlow --> ExtractPrompt[Extract User Prompt]
    ExtractPrompt --> ReplyCheck{Is<br/>Reply?}
    
    ReplyCheck -->|Yes| ReplyContext[Get Reply Context<br/>±10 messages]
    ReplyCheck -->|No| ChannelContext[Get Channel Context<br/>Max 100 messages]
    
    ReplyContext --> MergeContext[Merge Contexts]
    ChannelContext --> MergeContext
    
    MergeContext --> ContextFilter[Filter Context<br/>24hr limit, no bots]
    ContextFilter --> AttachmentCheck{Has<br/>Attachments?}
    
    AttachmentCheck -->|Yes| ProcessAttach[Process Attachments]
    AttachmentCheck -->|No| BuildPrompt[Build Prompt]
    
    ProcessAttach --> FileType2{File<br/>Type?}
    FileType2 -->|Image/PDF| ExtractImages[Extract Images]
    FileType2 -->|Text Files| ExtractText[Extract Text Content]
    
    ExtractImages --> BuildPrompt
    ExtractText --> BuildPrompt
    
    %% Gemini API Processing
    BuildPrompt --> PromptMode{Prompt<br/>Mode?}
    PromptMode -->|Short| ShortPrompt[Concise System Prompt]
    PromptMode -->|Thinking| ThinkPrompt[Detailed System Prompt]
    
    ShortPrompt --> GeminiAPI[Gemini Client]
    ThinkPrompt --> GeminiAPI
    ThinkPrompt --> ResetMode[Reset to Short Mode]
    
    GeminiAPI -->|Model Selection| ModelCheck{Model<br/>Type?}
    ModelCheck -->|2.5 Flash| Gemini25[Gemini 2.5 Flash]
    ModelCheck -->|2.5 Lite| Gemini25Lite[Gemini 2.5 Flash-Lite]
    ModelCheck -->|2.0 Flash| Gemini20[Gemini 2.0 Flash]
    
    Gemini25 --> APICall[Google Gemini API]
    Gemini25Lite --> APICall
    Gemini20 --> APICall
    
    APICall -->|Response| ResponseCheck{Success?}
    
    ResponseCheck -->|Yes| ProcessResponse[Process AI Response]
    ResponseCheck -->|No| ErrorHandler[Error Manager]
    
    %% Response Handling
    ProcessResponse --> LengthCheck{Response<br/>>2000 chars?}
    
    LengthCheck -->|Yes| MessageSplit[Message Splitter<br/>Preserve Code Blocks]
    LengthCheck -->|No| FormatResponse[Format Response]
    
    MessageSplit --> SendMultiple[Send Multiple Messages]
    FormatResponse --> UXService[User Experience Service]
    
    UXService -->|Typing Indicator| ShowTyping[Show Typing Status]
    UXService -->|Rich Embed| CreateEmbed[Create Discord Embed]
    UXService -->|Reaction| AddReaction[Add Reaction Feedback]
    
    ShowTyping --> SendResponse[Send to Discord Channel]
    CreateEmbed --> SendResponse
    AddReaction --> SendResponse
    SendMultiple --> SendResponse
    
    %% Error Handling
    ErrorHandler --> DevMode{Dev Mode<br/>Enabled?}
    DevMode -->|Yes| DetailedError[Full Stack Trace]
    DevMode -->|No| FriendlyError[User-Friendly Message]
    
    DetailedError --> SendError[Send Error Message]
    FriendlyError --> SendError
    SendError --> LogError[Performance Logger]
    
    %% Slash Commands
    SlashHandler --> CommandType{Command<br/>Type?}
    CommandType -->|/ping| PingCmd[Check Bot Status]
    CommandType -->|/model| ModelCmd[Switch Gemini Model]
    CommandType -->|/prompt-mode| PromptCmd[Switch Response Mode]
    CommandType -->|/stats| StatsCmd[Show Bot Statistics]
    CommandType -->|/help| HelpCmd[Show Help System]
    CommandType -->|/dev| DevCmd[Toggle Dev Mode]
    
    PingCmd --> RespondEphemeral[Ephemeral Response]
    ModelCmd --> RespondEphemeral
    PromptCmd --> RespondEphemeral
    StatsCmd --> RespondEphemeral
    HelpCmd --> RespondEphemeral
    DevCmd --> RespondEphemeral
    
    %% Monitoring
    LogError --> Metrics[Performance Metrics]
    SendResponse --> Metrics
    Metrics --> Dashboard[Statistics Dashboard]
    
    style Bot fill:#4a90e2,stroke:#2e5c8a,stroke-width:3px
    style GeminiAPI fill:#34a853,stroke:#1e7e34,stroke-width:2px
    style ImageService fill:#ea4335,stroke:#c5221f,stroke-width:2px
    style ErrorHandler fill:#fbbc04,stroke:#f9ab00,stroke-width:2px
    style SendResponse fill:#34a853,stroke:#1e7e34,stroke-width:2px
```

---

## Feature Breakdown

### 1. Message Processing
**Trigger:** User mentions bot (@bot) in Discord  
**Flow:**
- Extract user prompt (remove mentions)
- Check for help keywords → redirect to `/help`
- Detect command intent (standard response vs. image operation)
- Set complexity-based model selection

### 2. Context Collection
**Trigger:** Standard message or reply detection  
**Flow:**
- **Standard Message:** Retrieve last 100 messages from channel (24hr limit)
- **Reply Message:** Get ±10 messages around replied message + channel context
- Filter: Remove bot messages, empty content, old messages
- Merge and deduplicate contexts

### 3. File & Attachment Processing
**Trigger:** Message contains attachments  
**Supported Types:**
- **Images:** PNG, JPG, GIF, WebP → PIL Image objects
- **PDFs:** Convert to images (PyMuPDF, 2x resolution) → PIL Image objects
- **Text Files:** 40+ types (.py, .js, .txt, .md, .json, etc.) → Extracted text content
- **Size Limit:** 5MB for text files, 10MB for images

**Flow:**
1. Check attachment type
2. Process accordingly:
   - Images/PDFs → Image extraction
   - Text files → Content extraction
3. Include in Gemini API prompt

### 4. AI Response Generation
**Trigger:** After context collection  
**Models Available:**
- Gemini 2.5 Flash (default, recommended)
- Gemini 2.5 Flash-Lite (ultra-fast)
- Gemini 2.0 Flash (stable)
- Gemini 2.0 Flash-Lite (lightweight)

**Prompt Modes:**
- **Short Mode:** Concise responses (default, persistent)
- **Thinking Mode:** Detailed analysis (auto-reverts after 1 use)

**Configuration:**
- Max output tokens: 65,536
- Temperature: 0.7
- Safety filters: All disabled (BLOCK_NONE)

### 5. Image Generation & Editing
**Trigger:** Natural language detection (e.g., "generate image of...", "remove the...", "change background to...")  
**Edit Types:**
- Object Removal
- Background Replacement
- Style Transfer
- Color Adjustment
- General Editing

**Flow:**
1. Intent detection via keyword matching + Gemini analysis
2. Queue job (rate limited: 10 requests/user/hour, 3 concurrent)
3. Process via NanoBananaClient (Gemini 2.5 Flash Image API)
4. Return edited image to Discord

### 6. Response Formatting
**Trigger:** AI response received  
**Features:**
- Message splitting (2000 char limit)
- Code block preservation
- Markdown formatting
- Continuation indicators
- Rich embeds (configurable)
- Typing indicators

### 7. Error Handling
**Trigger:** Any exception during processing  
**Modes:**
- **Dev Mode OFF:** User-friendly messages
- **Dev Mode ON:** Full stack traces (admin only)

**Error Types:**
- Discord API errors (Forbidden, HTTPException)
- Gemini API errors (Rate limits, invalid key)
- Timeout errors
- Validation errors

### 8. Slash Commands
**Available Commands:**

| Command | Description | Ephemeral |
|---------|-------------|-----------|
| `/ping` | Check bot responsiveness & latency | Yes |
| `/model` | Switch Gemini models | Yes |
| `/prompt-mode` | Toggle short/thinking mode | Yes |
| `/stats` | View bot statistics & uptime | Yes |
| `/help` | Interactive help system | Yes |
| `/dev` | Toggle dev mode (admin only) | Yes |

**Trigger:** Direct slash command invocation  
**Sync:** Global sync on bot startup

---

## External Services & Configuration

### API Integrations
1. **Discord Bot API**
   - Token: `DISCORD_BOT_TOKEN` (required)
   - Permissions: Read messages, send messages, read message history, use slash commands

2. **Google Gemini API**
   - Token: `GEMINI_API_KEY` (required)
   - Used for: AI responses, image generation/editing
   - Fallback: `NANO_BANANA_API_KEY` (optional, defaults to `GEMINI_API_KEY`)

### Environment Variables
**Required:**
- `DISCORD_BOT_TOKEN` - Discord bot authentication
- `GEMINI_API_KEY` - Google Gemini API key

**Optional:**
- `MAX_CONTEXT_MESSAGES` (default: 100)
- `REPLY_CONTEXT_RANGE` (default: 10)
- `RESPONSE_TIMEOUT` (default: 30s)
- `MAX_RETRIES` (default: 3)
- `LOG_LEVEL` (default: INFO)
- `MESSAGE_SPLIT_LENGTH` (default: 2000)
- `MAX_IMAGE_SIZE_MB` (default: 10)
- `IMAGE_PROCESSING_TIMEOUT` (default: 60s)
- `MAX_CONCURRENT_IMAGE_EDITS` (default: 3)
- `DEV_MODE_ENABLED` (default: false)

### Storage & Persistence
- **Logs:** `/app/logs` (mounted volume)
- **Temp Files:** `/app/temp` (volume for image processing)
- **Cache:** `/app/cache` (volume for API responses)

---

## Monitoring & Health

### Health Checks
**Endpoint:** `scripts/health_check.py`  
**Interval:** 30s  
**Checks:**
- Discord connection status
- Gemini API availability
- Image processing service responsiveness

### Performance Metrics
**Tracked:**
- Total messages processed
- Average response time
- Success/failure rates
- Token usage (Gemini API)
- Queue sizes (image processing)
- Uptime & latency

**Access:** `/stats` slash command

### Logging
**Format:** JSON logs (Docker)  
**Levels:** DEBUG, INFO, WARNING, ERROR  
**Rotation:** 10MB max, 3 files  
**Contextual:** User ID, Guild ID, Channel ID, Message ID

---

## Deployment

### Docker
**Build:**
```bash
# Windows
scripts\docker-build.bat

# Linux/Mac
./scripts/docker-build.sh
```

**Run:**
```bash
docker-compose up -d
```

**Restart Policy:** `unless-stopped`

### Standard Python
```bash
pip install -r requirements.txt
python main.py
```

---

## Key Design Patterns

1. **Service-Oriented Architecture:** Modular services for each concern (context, AI, images, errors, UX)
2. **Event-Driven:** Discord event handlers trigger processing pipelines
3. **Async Processing:** Full async/await pattern for concurrent operations
4. **Rate Limiting:** Queue-based rate limiting for image processing
5. **Graceful Degradation:** Optional services (image processing) fail gracefully
6. **Context-Aware:** Message history and reply chains enhance AI understanding
7. **Error Recovery:** Retry logic, timeouts, and user-friendly error messages

---

## Development Quick Start

1. **Clone repository**
2. **Set up `.env` file** with `DISCORD_BOT_TOKEN` and `GEMINI_API_KEY`
3. **Install dependencies:** `pip install -r requirements.txt`
4. **Run:** `python main.py`
5. **Invite bot to server** with required permissions
6. **Mention bot in channel** or use slash commands

**Testing:**
- `/ping` - Verify bot is online
- `@bot hello` - Test basic response
- `@bot [attach image] describe this` - Test image processing
- `/help` - Explore all features

---

## Architecture Diagram (Simplified)

```
┌─────────────────────────────────────────────────────────────┐
│                        Discord User                          │
└──────────────────────────┬──────────────────────────────────┘
                           │
                           ▼
┌─────────────────────────────────────────────────────────────┐
│                    Discord Gateway (Events)                  │
└──────────────────────────┬──────────────────────────────────┘
                           │
                           ▼
┌─────────────────────────────────────────────────────────────┐
│                      DiscordBot (Core)                       │
│  ┌──────────────┐  ┌──────────────┐  ┌──────────────┐      │
│  │ Message      │  │ Slash        │  │ Error        │      │
│  │ Handler      │  │ Commands     │  │ Manager      │      │
│  └──────────────┘  └──────────────┘  └──────────────┘      │
└──────────────────────────┬──────────────────────────────────┘
                           │
        ┌──────────────────┼──────────────────┐
        │                  │                  │
        ▼                  ▼                  ▼
┌──────────────┐  ┌──────────────┐  ┌──────────────┐
│ Context      │  │ Enhanced     │  │ Image        │
│ Collector    │  │ Command      │  │ Processing   │
└──────┬───────┘  │ Handler      │  │ Service      │
       │          └──────┬───────┘  └──────┬───────┘
       │                 │                  │
       │                 │                  │
       ▼                 ▼                  ▼
┌──────────────┐  ┌──────────────┐  ┌──────────────┐
│ Message      │  │ Gemini       │  │ NanoBanana   │
│ History      │  │ Client       │  │ Client       │
└──────────────┘  └──────┬───────┘  └──────┬───────┘
                         │                  │
                         ▼                  ▼
                  ┌──────────────────────────────┐
                  │    Google Gemini API         │
                  │  (AI + Image Generation)     │
                  └──────────────────────────────┘
```

---

## Notes for New Developers

- **Entry Point:** `main.py` → loads config, starts bot
- **Core Logic:** `src/bot/discord_bot.py` → event handlers
- **AI Integration:** `src/services/gemini_client.py` → API calls
- **Command System:** `src/bot/commands.py` → slash commands
- **Image Editing:** `src/services/image_processing_service.py` → queue management
- **Tests:** `tests/` directory → pytest-based unit tests

**Common Tasks:**
- Add slash command → `src/bot/commands.py`
- Modify AI prompt → `src/services/gemini_client.py`
- Add image edit type → `src/models/data_models.py` + `enhanced_command_handler.py`
- Change context limits → `src/config.py`

**Debugging:**
- Enable dev mode: `/dev` command (requires admin)
- Check logs: `logs/bot.log`
- View stats: `/stats` command
