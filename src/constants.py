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

# Ceiling on the combined LaTeX figure, in inches. The renderer stacks one row
# per expression with no upper bound on how many a response may contain, so the
# computed height is attacker-influenced: at 150 dpi a 400-expression response
# asks for a 1500x42060 pixel canvas (measured: 5.9 s, 568 MB resident).
#
# 40 in is 6000 px tall at the renderer's dpi -- taller than any figure a reader
# would scroll through, and roughly 57 single-line expressions. Beyond it the
# renderer degrades to inline code blocks instead of allocating.
#
# Deliberately not a config setting: this is a resource guard rather than an
# operator preference, and no legitimate response should approach it.
MAX_LATEX_FIGURE_HEIGHT_INCHES = 40.0

# Ceiling on the rasterised size of a single PDF page, in pixels.
#
# A PDF declares its own page geometry, so the render size is attacker-chosen:
# a 520-byte file declaring an 8000x8000 pt page rasterises at scale 2.0 to
# 16000x16000 = 256 MP. Measured on that input: MuPDF allocates 792 MB and
# succeeds, the PNG encode peaks at 1,525 MB, and only Pillow's decompression
# bomb check then refuses it -- after ~5 s of CPU, and returning zero images.
#
# 40 MP is roughly twenty times an A4 page at the shipped render scale, so no
# legitimate document comes near it, and it sits well under Pillow's own
# ~89 MP default so that guard is never the one that fires. Oversized pages are
# rendered at a reduced scale rather than dropped.
MAX_PDF_PAGE_PIXELS = 40_000_000

# Logging
LOG_SEPARATOR = "=" * 80
