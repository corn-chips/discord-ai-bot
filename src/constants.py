"""
Constants and configuration values for the Discord Grok Bot.

This module centralizes magic numbers and configuration constants
to improve code maintainability and readability.
"""

# Discord API Limits
DISCORD_MESSAGE_LIMIT = 2000
DISCORD_EMBED_DESCRIPTION_LIMIT = 4096
DISCORD_EMBED_FIELD_VALUE_LIMIT = 1024

# File Processing Limits
MAX_TEXT_FILE_SIZE_BYTES = 5 * 1024 * 1024  # 5MB
MAX_IMAGE_SIZE_MB_DEFAULT = 10
MAX_AUDIO_FILE_SIZE_MB = 25  # Discord's file size limit

# Supported File Types
SUPPORTED_IMAGE_FORMATS = {'image/png', 'image/jpeg', 'image/gif', 'image/webp'}
SUPPORTED_AUDIO_FORMATS = {
    'audio/mpeg', 'audio/mp3', 'audio/wav', 'audio/x-wav',
    'audio/aac', 'audio/mp4', 'audio/x-m4a', 'audio/ogg'
}
SUPPORTED_TEXT_EXTENSIONS = {
    '.txt', '.md', '.json', '.yaml', '.yml', '.xml', '.csv',
    '.log', '.ini', '.cfg', '.conf', '.py', '.js', '.ts',
    '.java', '.c', '.cpp', '.h', '.hpp', '.cs', '.go', '.rs',
    '.html', '.css', '.jsx', '.tsx', '.vue', '.php', '.rb',
    '.sh', '.bash', '.ps1', '.sql', '.r', '.m', '.swift',
    '.kt', '.scala', '.pl', '.lua', '.dart'
}

# Message Splitting
MESSAGE_SPLIT_SAFE_LENGTH = 1900  # Safe length with room for continuation indicators
CONTINUATION_INDICATOR_OVERHEAD = 50

# Timeouts (in seconds)
DEFAULT_RESPONSE_TIMEOUT = 30
EXTENDED_RESPONSE_TIMEOUT = 120  # For pro model or thinking mode
API_TIMEOUT_BUFFER = 10

# Rate Limiting
MAX_REQUESTS_PER_USER_PER_HOUR = 10
MAX_IMAGE_REQUESTS_PER_MINUTE = 30

# API Configuration
DEFAULT_ROUTER_MODEL = "gemini-2.5-flash-lite"
DEFAULT_MAIN_MODEL = "gemini-3.0-flash-preview"

# Token Limits
MIN_TOKEN_LENGTH_DISCORD = 50
MIN_TOKEN_LENGTH_GEMINI = 30

# Image Processing
PDF_RENDER_SCALE = 2.0  # 2x zoom for better quality
RGB_WHITE_BACKGROUND = (255, 255, 255)

# Logging
LOG_SEPARATOR = "=" * 80
