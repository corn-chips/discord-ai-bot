"""
Configuration management for Discord Grok Bot.

Loads all non-secret configuration from config.yaml and secrets from .env.
"""

import os
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional, Dict, List, Any

import yaml

from .constants import DISCORD_MESSAGE_LIMIT
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

        # Load YAML config
        if not config_path.exists():
            print(f"Config file not found at {config_path}")
            print("Copy config.yaml.example to config.yaml and edit it.")
            sys.exit(1)

        with open(config_path, 'r', encoding='utf-8') as f:
            cfg = yaml.safe_load(f) or {}

        # Helper to safely get nested values
        def get(section: str, key: str, default=None):
            return cfg.get(section, {}).get(key, default)

        models_cfg = cfg.get('models', {}) or {}
        available_models_cfg_raw = models_cfg.get('available')
        available_models_cfg = available_models_cfg_raw if isinstance(available_models_cfg_raw, list) else []
        normalized_available_models: List[Dict[str, str]] = []
        for item in available_models_cfg:
            if not isinstance(item, dict):
                continue
            model_value = str(item.get("value", "")).strip()
            if not model_value:
                continue
            model_name = str(item.get("name", model_value)).strip() or model_value
            normalized_available_models.append({
                "name": model_name,
                "value": model_value,
            })

        valid_models_raw = models_cfg.get('valid')
        if isinstance(valid_models_raw, list) and valid_models_raw:
            valid_models_cfg = [
                str(model_name).strip()
                for model_name in valid_models_raw
                if str(model_name).strip()
            ]
        else:
            valid_models_cfg = [item["value"] for item in normalized_available_models]
        valid_models_cfg = list(dict.fromkeys(valid_models_cfg))

        default_model_name = str(
            models_cfg.get('default') or (valid_models_cfg[0] if valid_models_cfg else "")
        ).strip()
        router_model_name = str(
            models_cfg.get('router') or default_model_name
        ).strip()

        raw_model_display_names = models_cfg.get('display_names')
        model_display_names_cfg: Dict[str, str] = {}
        if isinstance(raw_model_display_names, dict):
            model_display_names_cfg = {
                str(model_name).strip(): str(display_name).strip()
                for model_name, display_name in raw_model_display_names.items()
                if str(model_name).strip() and str(display_name).strip()
            }
        if not model_display_names_cfg:
            model_display_names_cfg = {
                item["value"]: item["name"]
                for item in normalized_available_models
            }

        raw_model_descriptions = models_cfg.get('descriptions')
        model_descriptions_cfg: Dict[str, str] = {}
        if isinstance(raw_model_descriptions, dict):
            model_descriptions_cfg = {
                str(model_name).strip(): str(description).strip()
                for model_name, description in raw_model_descriptions.items()
                if str(model_name).strip() and str(description).strip()
            }

        raw_model_thinking_backend = models_cfg.get('thinking_backend')
        model_thinking_backend_cfg: Dict[str, str] = {}
        if isinstance(raw_model_thinking_backend, dict):
            model_thinking_backend_cfg = {
                str(model_name).strip(): str(backend).strip().lower()
                for model_name, backend in raw_model_thinking_backend.items()
                if str(model_name).strip() and str(backend).strip()
            }
        # Final normalized complexity map used by runtime.
        raw_model_complexity_cfg = cfg.get('model_complexity')
        default_model_complexity_cfg = {
            "low": {"model": default_model_name, "thinking_level": "minimal"},
            "medium": {"model": default_model_name, "thinking_level": "low"},
            "high": {"model": default_model_name, "thinking_level": "high"},
        }
        model_complexity_cfg: Dict[str, Dict[str, str]] = {}
        for level in ("low", "medium", "high"):
            configured_level = raw_model_complexity_cfg.get(level) if isinstance(raw_model_complexity_cfg, dict) else {}
            if not isinstance(configured_level, dict):
                configured_level = {}
            model_name = str(configured_level.get("model", default_model_complexity_cfg[level]["model"]))
            thinking_level = str(
                configured_level.get(
                    "thinking_level",
                    default_model_complexity_cfg[level]["thinking_level"],
                )
            )
            model_complexity_cfg[level] = {
                "model": model_name,
                "thinking_level": thinking_level,
            }

        # Build config from YAML + env secrets
        config = cls(
            # Secrets from .env
            discord_token=os.getenv('DISCORD_BOT_TOKEN', ''),
            gemini_api_key=os.getenv('GEMINI_API_KEY', ''),
            nano_banana_api_key=os.getenv('NANO_BANANA_API_KEY', os.getenv('GEMINI_API_KEY', '')),

            # Bot
            dev_mode_enabled=get('bot', 'dev_mode', False),
            token_db_path=os.getenv('TOKEN_DB_PATH') or get('bot', 'token_db_path', 'data/token_usage.db'),

            # Reports
            report_web_enabled=get('reports', 'web_enabled', True),
            report_web_host=get('reports', 'web_host', '127.0.0.1'),
            report_web_port=int(get('reports', 'web_port', 8080)),

            # Logging
            log_level=get('logging', 'level', 'INFO'),
            log_file=os.getenv('LOG_FILE') or get('logging', 'file', None),
            enable_performance_logging=get('logging', 'enable_performance_logging', True),

            # Context
            max_context_messages=get('context', 'max_messages', 100),
            context_messages_low=get('context', 'context_messages_low', 10),
            context_messages_medium=get('context', 'context_messages_medium', 30),
            context_messages_high=get('context', 'context_messages_high', 50),
            reply_context_range=get('context', 'reply_range', 10),
            context_cutoff_hours=get('context', 'cutoff_hours', 24),
            max_context_images=get('context', 'max_images', 6),

            # Response
            response_timeout=get('response', 'timeout', 30),
            extended_timeout=get('response', 'extended_timeout', 120),
            api_timeout_buffer=get('response', 'api_timeout_buffer', 10),
            max_retries=get('response', 'max_retries', 3),

            # Messages
            message_split_length=get('messages', 'split_length', 2000),
            safe_split_length=get('messages', 'safe_split_length', 1900),
            continuation_overhead=get('messages', 'continuation_overhead', 50),
            preserve_code_blocks=get('messages', 'preserve_code_blocks', True),
            add_continuation_indicators=get('messages', 'add_continuation_indicators', True),

            # UX
            show_typing_indicators=get('ux', 'show_typing_indicators', True),
            use_rich_embeds=get('ux', 'use_rich_embeds', True),
            enable_reaction_feedback=get('ux', 'enable_reaction_feedback', True),
            command_suggestion_threshold=get('ux', 'command_suggestion_threshold', 0.7),

            # Models
            default_model=default_model_name,
            router_model_name=router_model_name,
            available_models=normalized_available_models,
            valid_models=valid_models_cfg,
            model_complexity=model_complexity_cfg,
            model_display_names=model_display_names_cfg,
            model_descriptions=model_descriptions_cfg,
            model_thinking_backend=model_thinking_backend_cfg,
            router_cache_size=get('models', 'router_cache_size', 256),
            router_cache_ttl=get('models', 'router_cache_ttl', 300),

            # Generation
            temperature=get('generation', 'temperature', 0.7),
            top_p=get('generation', 'top_p', 0.8),
            top_k=get('generation', 'top_k', 40),
            max_output_tokens=get('generation', 'max_output_tokens', 65536),
            max_output_tokens_low=get('generation', 'max_output_tokens_low', 4096),
            max_output_tokens_medium=get('generation', 'max_output_tokens_medium', 16384),
            max_output_tokens_high=get('generation', 'max_output_tokens_high', 65536),
            router_temperature=get('generation', 'router_temperature', 0.1),
            router_max_output_tokens=get('generation', 'router_max_output_tokens', 100),
            edit_detection_max_output_tokens=get('generation', 'edit_detection_max_output_tokens', 10),

            # Safety
            safety_harassment=get('safety', 'harassment', 'BLOCK_NONE'),
            safety_hate_speech=get('safety', 'hate_speech', 'BLOCK_NONE'),
            safety_sexually_explicit=get('safety', 'sexually_explicit', 'BLOCK_NONE'),
            safety_dangerous_content=get('safety', 'dangerous_content', 'BLOCK_NONE'),

            # Image Processing
            max_image_size_mb=get('image_processing', 'max_size_mb', 10),
            image_processing_timeout=get('image_processing', 'timeout', 60),
            max_concurrent_image_edits=get('image_processing', 'max_concurrent_edits', 3),

            # Rate Limiting
            max_requests_per_user_per_hour=get('rate_limiting', 'max_requests_per_user_per_hour', 10),
            max_image_requests_per_minute=get('rate_limiting', 'max_image_requests_per_minute', 30),
            text_rate_limit_per_minute=get('rate_limiting', 'text_rate_limit_per_minute', 10),
            text_rate_limit_per_hour=get('rate_limiting', 'text_rate_limit_per_hour', 60),

            # Nano Banana
            nano_banana_model=get('nano_banana', 'model', ''),
            nano_banana_timeout=get('nano_banana', 'timeout', 60),
            nano_banana_max_retries=get('nano_banana', 'max_retries', 3),
            nano_banana_retry_delay=get('nano_banana', 'retry_delay', 1.0),

            # Languages
            valid_languages=cfg.get('languages', None) or [
                "english", "spanish", "french", "german", "italian",
                "portuguese", "russian", "japanese", "korean", "chinese",
                "arabic", "hindi", "dutch", "swedish", "polish",
                "turkish", "vietnamese", "thai", "indonesian", "auto",
            ],

            # Personalities
            personalities=cfg.get('personalities', None) or {
                "default": "You are a helpful AI assistant. Respond naturally and informatively.",
            },

            # System Prompts
            system_prompt_high_complexity=cfg.get('system_prompts', {}).get('high_complexity', ''),
            system_prompt_low_complexity=cfg.get('system_prompts', {}).get('low_complexity', ''),
            system_prompt_medium_complexity=cfg.get('system_prompts', {}).get('medium_complexity', ''),
            system_prompt_thinking_addon=cfg.get('system_prompts', {}).get('thinking_mode_addon', ''),

            # Validation
            min_token_length_discord=get('validation', 'min_token_length_discord', 50),
            min_token_length_gemini=get('validation', 'min_token_length_gemini', 30),
            max_text_file_size_bytes=get('validation', 'max_text_file_size_bytes', 5 * 1024 * 1024),
            pdf_render_scale=get('validation', 'pdf_render_scale', 2.0),
            max_pdf_pages=get('validation', 'max_pdf_pages', 20),

            # Misc
            leaderboard_limit=get('misc', 'leaderboard_limit', 10),
            channel_history_limit=get('misc', 'channel_history_limit', 500),
            job_timeout=get('misc', 'job_timeout', 60),
            progress_update_threshold=get('misc', 'progress_update_threshold', 0.1),
        )

        return config

    def validate(self) -> list[str]:
        """Validate configuration and return list of errors."""
        errors = []

        # Required secrets
        if not self.discord_token:
            errors.append("DISCORD_BOT_TOKEN is required (set in .env)")
        if not self.gemini_api_key:
            errors.append("GEMINI_API_KEY is required (set in .env)")

        # Core settings
        if self.max_context_messages <= 0:
            errors.append("context.max_messages must be positive")
        if self.context_messages_low <= 0 or self.context_messages_medium <= 0 or self.context_messages_high <= 0:
            errors.append("context.context_messages_low/medium/high must all be positive")
        if not (self.context_messages_low <= self.context_messages_medium <= self.context_messages_high):
            errors.append("context.context_messages_low <= medium <= high is required")
        if not self.token_db_path or not self.token_db_path.strip():
            errors.append("bot.token_db_path must be a valid filesystem path")
        if not self.report_web_host or not str(self.report_web_host).strip():
            errors.append("reports.web_host must not be empty")
        if self.report_web_port <= 0 or self.report_web_port > 65535:
            errors.append("reports.web_port must be between 1 and 65535")
        if self.reply_context_range <= 0:
            errors.append("context.reply_range must be positive")
        if self.max_context_images < 0:
            errors.append("context.max_images must be zero or positive")
        if self.response_timeout <= 0:
            errors.append("response.timeout must be positive")
        if self.max_retries < 0:
            errors.append("response.max_retries must be non-negative")

        # Image processing
        if self.max_image_size_mb <= 0:
            errors.append("image_processing.max_size_mb must be positive")
        elif self.max_image_size_mb > 25:
            errors.append("image_processing.max_size_mb cannot exceed 25MB (Discord limit)")
        if self.image_processing_timeout <= 0:
            errors.append("image_processing.timeout must be positive")
        elif self.image_processing_timeout > 300:
            errors.append("image_processing.timeout should not exceed 300 seconds")
        if self.max_concurrent_image_edits <= 0:
            errors.append("image_processing.max_concurrent_edits must be positive")
        elif self.max_concurrent_image_edits > 10:
            errors.append("image_processing.max_concurrent_edits should not exceed 10")

        # Message formatting
        if self.message_split_length <= 0:
            errors.append("messages.split_length must be positive")
        if self.message_split_length > DISCORD_MESSAGE_LIMIT:
            errors.append(f"messages.split_length cannot exceed Discord's {DISCORD_MESSAGE_LIMIT} character limit")
        if self.message_split_length < 100:
            errors.append("messages.split_length should be at least 100 characters")

        # UX
        if not (0.0 <= self.command_suggestion_threshold <= 1.0):
            errors.append("ux.command_suggestion_threshold must be between 0.0 and 1.0")

        # Generation params
        if not (0.0 <= self.temperature <= 2.0):
            errors.append("generation.temperature must be between 0.0 and 2.0")
        if self.max_output_tokens <= 0:
            errors.append("generation.max_output_tokens must be positive")
        if self.max_output_tokens_low <= 0 or self.max_output_tokens_medium <= 0 or self.max_output_tokens_high <= 0:
            errors.append("generation.max_output_tokens_low/medium/high must all be positive")
        if not (self.max_output_tokens_low <= self.max_output_tokens_medium <= self.max_output_tokens_high):
            errors.append("generation.max_output_tokens_low <= medium <= high is required")
        if self.router_cache_size <= 0:
            errors.append("models.router_cache_size must be positive")
        if self.router_cache_ttl <= 0:
            errors.append("models.router_cache_ttl must be positive")
        if not self.valid_models:
            errors.append("models.valid must contain at least one model")
        if not self.default_model:
            errors.append("models.default must not be empty")
        elif self.default_model not in self.valid_models:
            errors.append("models.default must be one of models.valid")
        if not self.router_model_name:
            errors.append("models.router must not be empty")
        elif self.router_model_name not in self.valid_models:
            errors.append("models.router must be one of models.valid")

        for item in self.available_models:
            if not isinstance(item, dict):
                errors.append("models.available entries must be mappings with name/value")
                continue
            model_value = str(item.get("value", "")).strip()
            if not model_value:
                errors.append("models.available entries must include a non-empty value")
                continue
            if model_value not in self.valid_models:
                errors.append("models.available values must all exist in models.valid")

        valid_thinking_backends = {"thinking_level", "thinking_budget", "none"}
        for model_name, backend in self.model_thinking_backend.items():
            normalized_model = str(model_name).strip()
            normalized_backend = str(backend).strip().lower()
            if not normalized_model:
                errors.append("models.thinking_backend keys must not be empty")
                continue
            if normalized_model not in self.valid_models:
                errors.append("models.thinking_backend keys must be present in models.valid")
            if normalized_backend not in valid_thinking_backends:
                errors.append("models.thinking_backend values must be one of: thinking_level, thinking_budget, none")

        missing_backend_models = set(self.valid_models) - set(self.model_thinking_backend.keys())
        if missing_backend_models:
            missing = ", ".join(sorted(missing_backend_models))
            errors.append(f"models.thinking_backend is missing model entries: {missing}")
        required_complexity_keys = {"low", "medium", "high"}
        if not isinstance(self.model_complexity, dict):
            errors.append("model_complexity must be a mapping with low/medium/high keys")
        else:
            missing_complexity_keys = required_complexity_keys - set(self.model_complexity.keys())
            if missing_complexity_keys:
                missing = ", ".join(sorted(missing_complexity_keys))
                errors.append(f"model_complexity is missing required key(s): {missing}")

            valid_thinking_levels = {
                "default", "off", "minimal", "low", "medium", "high",
                # accepted aliases
                "none", "inherit", "disabled",
            }

            for level in sorted(required_complexity_keys):
                entry = self.model_complexity.get(level)
                if not isinstance(entry, dict):
                    errors.append(f"model_complexity.{level} must be a mapping with 'model' and 'thinking_level'")
                    continue

                model_name = str(entry.get("model", "")).strip()
                thinking_level = str(entry.get("thinking_level", "")).strip().lower()

                if not model_name:
                    errors.append(f"model_complexity.{level}.model must not be empty")
                elif model_name not in self.valid_models:
                    errors.append(
                        f"model_complexity.{level}.model must be one of models.valid"
                    )

                if thinking_level not in valid_thinking_levels:
                    errors.append(
                        f"model_complexity.{level}.thinking_level must be one of: default, off, minimal, low, medium, high"
                    )
        if not self.nano_banana_model or not self.nano_banana_model.strip():
            errors.append("nano_banana.model must not be empty")
        if self.text_rate_limit_per_minute <= 0 or self.text_rate_limit_per_hour <= 0:
            errors.append("rate_limiting.text_rate_limit_per_minute/hour must be positive")
        if self.text_rate_limit_per_minute > self.text_rate_limit_per_hour:
            errors.append("rate_limiting.text_rate_limit_per_minute cannot exceed text_rate_limit_per_hour")

        # System prompts
        if not self.system_prompt_high_complexity.strip():
            errors.append("system_prompts.high_complexity must not be empty")
        if not self.system_prompt_low_complexity.strip():
            errors.append("system_prompts.low_complexity must not be empty")
        if not self.system_prompt_medium_complexity.strip():
            errors.append("system_prompts.medium_complexity must not be empty")
        if self.max_pdf_pages <= 0:
            errors.append("validation.max_pdf_pages must be positive")

        return errors

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
