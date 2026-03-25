"""
Platform constants for the Discord Grok Bot.

These are fixed values defined by external APIs (Discord, etc.) or
immutable data definitions. Tunable settings live in config.yaml.
"""

# Discord API Limits (fixed by Discord)
DISCORD_MESSAGE_LIMIT = 2000
DISCORD_EMBED_DESCRIPTION_LIMIT = 4096
DISCORD_EMBED_FIELD_VALUE_LIMIT = 1024

# Supported File Types (protocol-level data definitions)
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

# Rendering Constants
RGB_WHITE_BACKGROUND = (255, 255, 255)

# Logging
LOG_SEPARATOR = "=" * 80
