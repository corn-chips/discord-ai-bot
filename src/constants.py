"""
Platform constants for the Discord Grok Bot.

These are fixed values defined by external APIs (Discord, etc.) or
immutable data definitions. Tunable settings live in config.yaml.
"""

# Discord API Limits (fixed by Discord)
DISCORD_MESSAGE_LIMIT = 2000
DISCORD_EMBED_DESCRIPTION_LIMIT = 4096
DISCORD_EMBED_FIELD_VALUE_LIMIT = 1024

# The rest of the embed ceilings. None of them is enforced by discord.py:
# `Embed.add_field` accepts 30 fields with 300-character names and
# 2,000-character values and `to_dict()` passes every one of them through, so
# the first thing that notices is a 400 on send.
#
# The total is the one that surprises. It is 6,000 characters summed across the
# title, the description, every field name and value, the footer text and the
# author name -- and a /pins listing at the permitted 25 pins reaches 5,996 with
# 8-character display names and 6,046 with 9-character ones. The field-count
# ceiling never binds first for that command, so it cannot be the only check.
DISCORD_EMBED_TOTAL_LIMIT = 6000
DISCORD_EMBED_FIELD_COUNT_LIMIT = 25
DISCORD_EMBED_FIELD_NAME_LIMIT = 256

# discord.ui.View raises ValueError("maximum number of children exceeded") on
# the 26th component. That is worse than a 400: it is raised while building the
# reply, so the interaction is never acknowledged, and this bot registers no
# on_app_command_error handler to notice.
DISCORD_VIEW_CHILD_LIMIT = 25

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

# Gemini safety thresholds (fixed by the google-genai SDK's HarmBlockThreshold)
#
# The only values config.yaml's `safety:` block may take. Listed here rather
# than read off the SDK because config has to load, validate and be reported on
# before any provider client exists, and importing google.genai to validate a
# string is the wrong dependency. tests/test_safety_thresholds.py asserts this
# set is exactly `types.HarmBlockThreshold`'s, so a future SDK adding or
# renaming a member is a test failure rather than a silent divergence.
SAFETY_THRESHOLD_NAMES = frozenset(
    {
        "HARM_BLOCK_THRESHOLD_UNSPECIFIED",
        "BLOCK_LOW_AND_ABOVE",
        "BLOCK_MEDIUM_AND_ABOVE",
        "BLOCK_ONLY_HIGH",
        "BLOCK_NONE",
        "OFF",
    }
)

# Names this bot has accepted that the SDK never defined.
#
# BLOCK_HIGH_AND_ABOVE was invented by the threshold map in gemini_client and
# keyed to the real BLOCK_ONLY_HIGH. It is kept as an alias, not deleted,
# because config.yaml's own comment advertised it -- and listed neither
# BLOCK_ONLY_HIGH nor OFF, both of which that map silently turned into
# BLOCK_NONE. An operator following the shipped documentation wrote the
# invented name and it worked; removing it would turn their working config
# into a boot error for no gain.
SAFETY_THRESHOLD_ALIASES = {"BLOCK_HIGH_AND_ABOVE": "BLOCK_ONLY_HIGH"}

# What to send when a configured threshold cannot be resolved at request time.
#
# The strictest value the SDK offers. Validation rejects an unresolvable
# threshold at boot, so this is unreachable from a config file and exists for
# a BotConfig built in code; when it is reached, the operator's intent is
# unknown and the safe reading of "I asked for some filtering and mistyped it"
# is more filtering, not none. The previous default was BLOCK_NONE, which meant
# every typo -- and the real names BLOCK_ONLY_HIGH and OFF -- silently disabled
# the filter the operator was trying to configure.
SAFETY_THRESHOLD_FALLBACK = "BLOCK_LOW_AND_ABOVE"
