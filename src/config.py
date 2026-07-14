"""
Configuration management for Discord Grok Bot.

Loads all non-secret configuration from config.yaml and secrets from .env.
"""

import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional, Dict, List

import yaml

from .config_helpers import load_yaml_config, parse_config_values, validate_config
from .utils.logging_config import setup_logging


# Path to the config file (project root)
CONFIG_FILE_PATH = Path(__file__).parent.parent / "config.yaml"


@dataclass
class BotConfig:
    """Configuration class for the Discord Grok Bot."""

    # === Secrets (from .env) ===
    discord_token: str = ""
    gemini_api_key: str = ""
    nano_banana_api_key: str = ""

    # === Bot ===
    dev_mode_enabled: bool = False
    token_db_path: str = "data/token_usage.db"

    # === Reports ===
    report_web_enabled: bool = True
    report_web_host: str = "127.0.0.1"
    report_web_port: int = 8080

    # === Logging ===
    log_level: str = "INFO"
    log_file: Optional[str] = None
    enable_performance_logging: bool = True

    # === Context ===
    max_context_messages: int = 100
    context_messages_low: int = 10
    context_messages_medium: int = 30
    context_messages_high: int = 50
    reply_context_range: int = 10
    context_cutoff_hours: int = 24
    max_context_images: int = 6

    # === Hybrid Message RAG ===
    rag_enabled: bool = True
    rag_database_path: str = "data/message_rag.db"
    rag_embedding_model: str = "gemini-embedding-2"
    rag_embedding_dimensions: int = 768
    rag_cross_channel_enabled: bool = False
    rag_index_bot_responses: bool = True
    rag_backfill_limit: int = 0
    rag_gating_enabled: bool = True
    rag_lexical_candidates: int = 30
    rag_semantic_candidates: int = 24
    rag_rerank_candidates: int = 12
    rag_rerank_min_boundary_margin: float = 0.15
    rag_recency_half_life_hours: int = 72
    rag_max_context_messages_low: int = 4
    rag_max_context_messages_medium: int = 6
    rag_max_context_messages_high: int = 8
    rag_embedding_min_words: int = 2
    rag_embedding_min_alphanumeric_chars: int = 12
    rag_vector_cache_enabled: bool = True

    # === Response ===
    response_timeout: int = 30
    extended_timeout: int = 120
    api_timeout_buffer: int = 10
    max_retries: int = 3

    # === Messages ===
    message_split_length: int = 2000
    safe_split_length: int = 1900
    continuation_overhead: int = 50
    preserve_code_blocks: bool = True
    add_continuation_indicators: bool = True

    # === UX ===
    show_typing_indicators: bool = True
    use_rich_embeds: bool = True
    enable_reaction_feedback: bool = True
    command_suggestion_threshold: float = 0.7

    # === Models ===
    default_model: str = ""
    router_model_name: str = ""
    available_models: List[Dict[str, str]] = field(default_factory=list)
    valid_models: List[str] = field(default_factory=list)
    model_complexity: Dict[str, Dict[str, str]] = field(default_factory=lambda: {
        "low": {"model": "", "thinking_level": "minimal"},
        "medium": {"model": "", "thinking_level": "low"},
        "high": {"model": "", "thinking_level": "high"},
    })
    model_display_names: Dict[str, str] = field(default_factory=dict)
    model_descriptions: Dict[str, str] = field(default_factory=dict)
    model_thinking_backend: Dict[str, str] = field(default_factory=dict)
    router_cache_size: int = 256
    router_cache_ttl: int = 300

    # === Generation Parameters ===
    temperature: float = 0.7
    top_p: float = 0.8
    top_k: int = 40
    max_output_tokens: int = 65536
    max_output_tokens_low: int = 4096
    max_output_tokens_medium: int = 16384
    max_output_tokens_high: int = 65536
    router_temperature: float = 0.1
    router_max_output_tokens: int = 100
    edit_detection_max_output_tokens: int = 10

    # === Safety Settings ===
    safety_harassment: str = "BLOCK_NONE"
    safety_hate_speech: str = "BLOCK_NONE"
    safety_sexually_explicit: str = "BLOCK_NONE"
    safety_dangerous_content: str = "BLOCK_NONE"

    # === Image Processing ===
    max_image_size_mb: int = 10
    image_processing_timeout: int = 60
    max_concurrent_image_edits: int = 3

    # === Rate Limiting ===
    max_requests_per_user_per_hour: int = 10
    max_image_requests_per_minute: int = 30
    text_rate_limit_per_minute: int = 10
    text_rate_limit_per_hour: int = 60

    # === Nano Banana ===
    nano_banana_timeout: int = 60
    nano_banana_max_retries: int = 3
    nano_banana_retry_delay: float = 1.0
    nano_banana_model: str = ""

    # === Languages ===
    valid_languages: List[str] = field(default_factory=lambda: [
        "english", "spanish", "french", "german", "italian",
        "portuguese", "russian", "japanese", "korean", "chinese",
        "arabic", "hindi", "dutch", "swedish", "polish",
        "turkish", "vietnamese", "thai", "indonesian", "auto",
    ])

    # === Personalities ===
    personalities: Dict[str, str] = field(default_factory=lambda: {
        "default": "You are a helpful AI assistant. Respond naturally and informatively.",
        "professional": "Respond in a professional, formal tone. Be precise, structured, and business-appropriate. Avoid slang and humor.",
        "casual": "Respond in a casual, friendly tone with humor. Use conversational language, contractions, and feel free to joke around.",
        "sarcastic": "Respond with witty sarcasm and dry humor, but still be helpful. Think of yourself as a clever friend who can't resist a good quip.",
        "academic": "Respond in an academic, scholarly tone. Use precise terminology, cite reasoning, and structure responses like a knowledgeable professor.",
        "friendly": "Respond in a warm, encouraging, and supportive tone. Be enthusiastic and uplifting, like a cheerful friend who genuinely wants to help.",
    })

    # === System Prompts ===
    system_prompt_high_complexity: str = ""
    system_prompt_low_complexity: str = ""
    system_prompt_medium_complexity: str = ""
    system_prompt_thinking_addon: str = ""

    # === Validation ===
    min_token_length_discord: int = 50
    min_token_length_gemini: int = 30
    max_text_file_size_bytes: int = 5 * 1024 * 1024
    pdf_render_scale: float = 2.0
    max_pdf_pages: int = 20

    # === Misc ===
    leaderboard_limit: int = 10
    channel_history_limit: int = 500
    job_timeout: int = 60
    progress_update_threshold: float = 0.1

    @classmethod
    def from_yaml(cls, config_path: Path = None) -> 'BotConfig':
        """Create BotConfig from config.yaml + .env secrets."""
        if config_path is None:
            config_path = CONFIG_FILE_PATH

        config_data = load_yaml_config(config_path)
        return cls(**parse_config_values(config_data))

    def validate(self) -> list[str]:
        """Validate configuration and return list of errors."""
        return validate_config(self)

    def validate_tokens(self) -> tuple[bool, list[str]]:
        """Validate Discord and Gemini API tokens."""
        errors = []

        if self.discord_token:
            if len(self.discord_token) < self.min_token_length_discord:
                errors.append("DISCORD_BOT_TOKEN appears to be invalid (too short)")

        if self.gemini_api_key:
            if len(self.gemini_api_key) < self.min_token_length_gemini:
                errors.append("GEMINI_API_KEY appears to be invalid (too short)")

        if self.nano_banana_api_key:
            if len(self.nano_banana_api_key) < 20:
                errors.append("NANO_BANANA_API_KEY appears to be invalid (too short)")

        return len(errors) == 0, errors

    async def validate_service_connectivity(self) -> tuple[bool, dict[str, str]]:
        """Validate connectivity to external services."""
        service_status = {}
        all_ok = True

        if self.nano_banana_api_key:
            service_status['gemini_image'] = "Configured (uses Gemini SDK)"
        else:
            service_status['gemini_image'] = "Not configured"

        return all_ok, service_status

    def get_feature_availability(self) -> dict[str, bool]:
        """Get availability status of optional features."""
        return {
            'report_tracking': True,
            'image_generation': bool(self.nano_banana_api_key),
            'enhanced_ux': self.show_typing_indicators or self.use_rich_embeds or self.enable_reaction_feedback,
            'message_splitting': self.preserve_code_blocks or self.add_continuation_indicators,
            'command_suggestions': self.command_suggestion_threshold > 0.0,
            'performance_logging': self.enable_performance_logging
        }

    def get_safety_threshold(self, category: str) -> str:
        """Get the safety threshold string for a given category."""
        mapping = {
            'harassment': self.safety_harassment,
            'hate_speech': self.safety_hate_speech,
            'sexually_explicit': self.safety_sexually_explicit,
            'dangerous_content': self.safety_dangerous_content,
        }
        return mapping.get(category, 'BLOCK_NONE')


class ConfigurationError(Exception):
    """Exception raised for configuration-related errors."""
    pass


def load_and_validate_config() -> BotConfig:
    """
    Load configuration from config.yaml and .env, then validate it.
    Exits the application with clear error messages if configuration is invalid.
    """
    try:
        config = BotConfig.from_yaml()
    except yaml.YAMLError as e:
        print(f"Config file syntax error: {e}")
        print("Please check config.yaml for YAML syntax errors.")
        sys.exit(1)
    except ValueError as e:
        print(f"Configuration Error: Invalid value - {e}")
        sys.exit(1)

    # Validate required fields
    validation_errors = config.validate()
    if validation_errors:
        print("Configuration Error: Missing or invalid configuration:")
        for error in validation_errors:
            print(f"   - {error}")
        print("\nSecrets go in .env:")
        print("   DISCORD_BOT_TOKEN - Your Discord bot token")
        print("   GEMINI_API_KEY - Your Google Gemini API key")
        print("   NANO_BANANA_API_KEY - (optional, defaults to GEMINI_API_KEY)")
        print("\nAll other settings go in config.yaml")
        sys.exit(1)

    # Validate token formats
    token_valid, token_errors = config.validate_tokens()
    if not token_valid:
        print("Configuration Error: Invalid API tokens:")
        for error in token_errors:
            print(f"   - {error}")
        print("\nPlease verify your API tokens in .env:")
        print("   Discord Bot Token: https://discord.com/developers/applications")
        print("   Gemini API Key: https://makersuite.google.com/app/apikey")
        sys.exit(1)

    # Set up logging
    setup_logging(
        log_level=config.log_level,
        log_file=config.log_file,
        enable_console=True,
        enable_performance_logging=config.enable_performance_logging
    )

    # Display feature availability
    features = config.get_feature_availability()
    print("Configuration loaded successfully from config.yaml")
    print("Feature availability:")
    for feature, available in features.items():
        status = "Enabled" if available else "Disabled"
        print(f"   - {feature.replace('_', ' ').title()}: {status}")

    return config


async def validate_startup_connectivity(config: BotConfig) -> bool:
    """Validate connectivity to external services during startup."""
    print("Checking service connectivity...")

    try:
        all_ok, service_status = await config.validate_service_connectivity()

        print("Service connectivity status:")
        for service, status in service_status.items():
            print(f"   - {service.replace('_', ' ').title()}: {status}")

        if not all_ok:
            print("Some services are unavailable. The bot will start with reduced functionality.")

        return True

    except Exception as e:
        print(f"Error checking service connectivity: {e}")
        print("Continuing startup without connectivity validation...")
        return True
