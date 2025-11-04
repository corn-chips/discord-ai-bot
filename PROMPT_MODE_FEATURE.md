# Prompt Mode Feature

## Overview

The Discord AI Bot now supports switching between two distinct system prompt modes, allowing you to control the style and length of the AI's responses.

## Available Modes

### 1. **Short Mode** (Default)
- **Purpose**: Quick, concise responses
- **Best For**: 
  - Quick questions
  - Simple queries
  - When you need fast answers
  - Casual conversations
- **Characteristics**:
  - Direct and to-the-point
  - Minimal elaboration
  - Friendly and conversational tone
  - Efficient communication

### 2. **Thinking Mode** (Single-Use)
- **Purpose**: Detailed, comprehensive analysis
- **Best For**: 
  - Complex topics
  - In-depth explanations
  - Research questions
  - When you need thorough understanding
- **Characteristics**:
  - Comprehensive and detailed
  - Provides context and background
  - Thorough reasoning and analysis
  - Professional, academic tone
  - Includes nuance and multiple perspectives
- **⚡ Special Behavior**: 
  - **Automatically reverts to Short mode after one use**
  - Perfect for getting one detailed answer without staying in verbose mode
  - Need another detailed response? Just switch back to Thinking mode again## How to Use

### Switching Prompt Modes

Use the `/prompt-mode` slash command in Discord:

1. Type `/prompt-mode` in any channel
2. Select either:
   - **Short - Concise & Direct Responses**
   - **Thinking - Detailed & Comprehensive Analysis**
3. The bot will confirm the mode change

### Checking Current Mode

Use the `/config` command to see:
- Current AI model
- **Current prompt mode** (Short or Thinking)
- Other configuration settings

### Example Usage

```
/prompt-mode mode:Short
```
Bot switches to short, concise response mode (default).

```
/prompt-mode mode:Thinking
```
Bot switches to detailed, comprehensive analysis mode.
**Note:** After the bot responds once in Thinking mode, it will automatically revert to Short mode.

## Implementation Details

### Technical Changes

1. **GeminiClient Class** (`src/services/gemini_client.py`):
   - Added `_prompt_mode` attribute (defaults to "short")
   - Added `_thinking_single_use` flag (set to True for auto-revert behavior)
   - Added `get_prompt_mode()` method
   - Added `set_prompt_mode(mode)` method
   - Modified `format_prompt()` to use different system instructions based on mode
   - Added auto-revert logic in `generate_response()` that switches back to "short" after a successful response in "thinking" mode

2. **Commands** (`src/bot/commands.py`):
   - Added `/prompt-mode` slash command
   - Updated `/help` command to include the new command
   - Updated `/config` command to display current prompt mode

### System Prompts

#### Short Mode System Prompt
```
### **System Prompt: The Concise Expert**

You are a helpful AI assistant focused on providing clear, accurate, 
and concise responses. You communicate efficiently while maintaining 
accuracy and relevance.

Key directives:
- Be Concise
- Prioritize Clarity
- Stay Accurate
- Be Conversational
- Format for Readability
- Answer Directly
```

#### Thinking Mode System Prompt
```
### **System Prompt: The Grounded Expert**

You are a grounded, well-informed expert AI assistant. Your primary 
function is to provide users with accurate, verifiable, and comprehensive 
information sourced from reliable data.

Key directives:
- Factuality is Paramount
- Mandatory Grounding & Sourcing
- Distinguish Fact from Opinion/Analysis
- Professional & Direct Tone
- Acknowledge Limits and Nuance
- Structure and Clarity
- Strive for Comprehensive Depth
```

## Benefits

1. **Flexibility**: Choose the response style that fits your needs
2. **Efficiency**: Get quick answers when you need them
3. **Depth**: Get comprehensive analysis when required
4. **User Control**: You decide how the AI responds
5. **Smart Auto-Revert**: Thinking mode automatically returns to Short mode after one use, preventing unnecessary verbose responses
6. **No Mode Lock-In**: Never get stuck in verbose mode - perfect for occasional detailed analysis

## Default Behavior

- The bot starts in **Short Mode** by default
- This provides quick, efficient responses for most use cases
- Switch to Thinking Mode when you need more detailed analysis
- **Thinking Mode is single-use**: After generating one detailed response, the bot automatically reverts to Short Mode
- This prevents the bot from staying in verbose mode unnecessarily

## Commands Summary

| Command | Description |
|---------|-------------|
| `/prompt-mode` | Switch between Short and Thinking modes |
| `/config` | View current mode and configuration |
| `/help` | See all available commands |
| `/model` | Switch between AI models |
| `/stats` | View bot statistics |
| `/ping` | Check bot responsiveness |

## Notes

- The prompt mode is global and affects all users
- Mode changes are logged for troubleshooting
- **Thinking mode automatically reverts to Short after one response**
- Short mode persists until manually changed to Thinking
- Works with all AI models (Gemini 2.5 Flash, 2.0 Flash, etc.)
- The auto-revert behavior helps maintain efficiency while allowing detailed responses when needed
