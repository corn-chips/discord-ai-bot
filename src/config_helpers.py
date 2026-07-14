"""Internal parsing and validation helpers for :mod:`src.config`."""

import os
import sys
from pathlib import Path
from typing import Any, Dict, List

import yaml

from .constants import DISCORD_MESSAGE_LIMIT


DEFAULT_LANGUAGES = [
    "english", "spanish", "french", "german", "italian",
    "portuguese", "russian", "japanese", "korean", "chinese",
    "arabic", "hindi", "dutch", "swedish", "polish",
    "turkish", "vietnamese", "thai", "indonesian", "auto",
]

DEFAULT_PERSONALITIES = {
    "default": "You are a helpful AI assistant. Respond naturally and informatively.",
}


def load_yaml_config(config_path: Path) -> Any:
    """Load a YAML document, preserving the existing missing-file behavior."""
    if not config_path.exists():
        print(f"Config file not found at {config_path}")
        print("Copy config.yaml.example to config.yaml and edit it.")
        sys.exit(1)

    with open(config_path, "r", encoding="utf-8") as config_file:
        return yaml.safe_load(config_file) or {}


def get_config_value(
    config_data: Dict[str, Any],
    section: str,
    key: str,
    default: Any = None,
) -> Any:
    """Return one nested value using the legacy missing-key semantics."""
    return config_data.get(section, {}).get(key, default)


def _normalize_available_models(
    models_config: Dict[str, Any],
) -> List[Dict[str, str]]:
    available_models_raw = models_config.get("available")
    available_models = (
        available_models_raw if isinstance(available_models_raw, list) else []
    )
    normalized_models: List[Dict[str, str]] = []
    for item in available_models:
        if not isinstance(item, dict):
            continue
        model_value = str(item.get("value", "")).strip()
        if not model_value:
            continue
        model_name = str(item.get("name", model_value)).strip() or model_value
        normalized_models.append({"name": model_name, "value": model_value})
    return normalized_models


def _normalize_valid_models(
    models_config: Dict[str, Any],
    available_models: List[Dict[str, str]],
) -> List[str]:
    valid_models_raw = models_config.get("valid")
    if isinstance(valid_models_raw, list) and valid_models_raw:
        valid_models = [
            str(model_name).strip()
            for model_name in valid_models_raw
            if str(model_name).strip()
        ]
    else:
        valid_models = [item["value"] for item in available_models]
    return list(dict.fromkeys(valid_models))


def _normalize_model_metadata(
    models_config: Dict[str, Any],
    available_models: List[Dict[str, str]],
) -> tuple[Dict[str, str], Dict[str, str], Dict[str, str]]:
    raw_display_names = models_config.get("display_names")
    display_names: Dict[str, str] = {}
    if isinstance(raw_display_names, dict):
        display_names = {
            str(model_name).strip(): str(display_name).strip()
            for model_name, display_name in raw_display_names.items()
            if str(model_name).strip() and str(display_name).strip()
        }
    if not display_names:
        display_names = {
            item["value"]: item["name"]
            for item in available_models
        }

    raw_descriptions = models_config.get("descriptions")
    descriptions: Dict[str, str] = {}
    if isinstance(raw_descriptions, dict):
        descriptions = {
            str(model_name).strip(): str(description).strip()
            for model_name, description in raw_descriptions.items()
            if str(model_name).strip() and str(description).strip()
        }

    raw_thinking_backend = models_config.get("thinking_backend")
    thinking_backend: Dict[str, str] = {}
    if isinstance(raw_thinking_backend, dict):
        thinking_backend = {
            str(model_name).strip(): str(backend).strip().lower()
            for model_name, backend in raw_thinking_backend.items()
            if str(model_name).strip() and str(backend).strip()
        }
    return display_names, descriptions, thinking_backend


def _normalize_model_complexity(
    config_data: Dict[str, Any],
    default_model: str,
) -> Dict[str, Dict[str, str]]:
    complexity_defaults = {
        "low": {"model": default_model, "thinking_level": "minimal"},
        "medium": {"model": default_model, "thinking_level": "low"},
        "high": {"model": default_model, "thinking_level": "high"},
    }
    raw_complexity = config_data.get("model_complexity")
    model_complexity: Dict[str, Dict[str, str]] = {}
    for level in ("low", "medium", "high"):
        configured_level = (
            raw_complexity.get(level) if isinstance(raw_complexity, dict) else {}
        )
        if not isinstance(configured_level, dict):
            configured_level = {}
        model_complexity[level] = {
            "model": str(
                configured_level.get("model", complexity_defaults[level]["model"])
            ),
            "thinking_level": str(
                configured_level.get(
                    "thinking_level",
                    complexity_defaults[level]["thinking_level"],
                )
            ),
        }
    return model_complexity


def _parse_model_values(config_data: Dict[str, Any]) -> Dict[str, Any]:
    models_config = config_data.get("models", {}) or {}
    available_models = _normalize_available_models(models_config)
    valid_models = _normalize_valid_models(models_config, available_models)
    default_model = str(
        models_config.get("default")
        or (valid_models[0] if valid_models else "")
    ).strip()
    router_model = str(models_config.get("router") or default_model).strip()
    display_names, descriptions, thinking_backend = _normalize_model_metadata(
        models_config,
        available_models,
    )

    return {
        "default_model": default_model,
        "router_model_name": router_model,
        "available_models": available_models,
        "valid_models": valid_models,
        "model_complexity": _normalize_model_complexity(
            config_data,
            default_model,
        ),
        "model_display_names": display_names,
        "model_descriptions": descriptions,
        "model_thinking_backend": thinking_backend,
        "router_cache_size": get_config_value(
            config_data, "models", "router_cache_size", 256
        ),
        "router_cache_ttl": get_config_value(
            config_data, "models", "router_cache_ttl", 300
        ),
    }


def _parse_secret_reporting_values(
    config_data: Dict[str, Any],
) -> Dict[str, Any]:
    return {
        "discord_token": os.getenv("DISCORD_BOT_TOKEN", ""),
        "gemini_api_key": os.getenv("GEMINI_API_KEY", ""),
        "nano_banana_api_key": os.getenv(
            "NANO_BANANA_API_KEY", os.getenv("GEMINI_API_KEY", "")
        ),
        "dev_mode_enabled": get_config_value(config_data, "bot", "dev_mode", False),
        "token_db_path": os.getenv("TOKEN_DB_PATH")
        or get_config_value(config_data, "bot", "token_db_path", "data/token_usage.db"),
        "report_web_enabled": get_config_value(
            config_data, "reports", "web_enabled", True
        ),
        "report_web_host": get_config_value(
            config_data, "reports", "web_host", "127.0.0.1"
        ),
        "report_web_port": int(
            get_config_value(config_data, "reports", "web_port", 8080)
        ),
        "log_level": get_config_value(config_data, "logging", "level", "INFO"),
        "log_file": os.getenv("LOG_FILE")
        or get_config_value(config_data, "logging", "file", None),
        "enable_performance_logging": get_config_value(
            config_data, "logging", "enable_performance_logging", True
        ),
    }


def _parse_context_response_values(
    config_data: Dict[str, Any],
) -> Dict[str, Any]:
    return {
        "max_context_messages": get_config_value(
            config_data, "context", "max_messages", 100
        ),
        "context_messages_low": get_config_value(
            config_data, "context", "context_messages_low", 10
        ),
        "context_messages_medium": get_config_value(
            config_data, "context", "context_messages_medium", 30
        ),
        "context_messages_high": get_config_value(
            config_data, "context", "context_messages_high", 50
        ),
        "reply_context_range": get_config_value(
            config_data, "context", "reply_range", 10
        ),
        "context_cutoff_hours": get_config_value(
            config_data, "context", "cutoff_hours", 24
        ),
        "max_context_images": get_config_value(
            config_data, "context", "max_images", 6
        ),
        "response_timeout": get_config_value(config_data, "response", "timeout", 30),
        "extended_timeout": get_config_value(
            config_data, "response", "extended_timeout", 120
        ),
        "api_timeout_buffer": get_config_value(
            config_data, "response", "api_timeout_buffer", 10
        ),
        "max_retries": get_config_value(config_data, "response", "max_retries", 3),
    }


def _parse_message_ux_values(config_data: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "message_split_length": get_config_value(
            config_data, "messages", "split_length", 2000
        ),
        "safe_split_length": get_config_value(
            config_data, "messages", "safe_split_length", 1900
        ),
        "continuation_overhead": get_config_value(
            config_data, "messages", "continuation_overhead", 50
        ),
        "preserve_code_blocks": get_config_value(
            config_data, "messages", "preserve_code_blocks", True
        ),
        "add_continuation_indicators": get_config_value(
            config_data, "messages", "add_continuation_indicators", True
        ),
        "show_typing_indicators": get_config_value(
            config_data, "ux", "show_typing_indicators", True
        ),
        "use_rich_embeds": get_config_value(config_data, "ux", "use_rich_embeds", True),
        "enable_reaction_feedback": get_config_value(
            config_data, "ux", "enable_reaction_feedback", True
        ),
        "command_suggestion_threshold": get_config_value(
            config_data, "ux", "command_suggestion_threshold", 0.7
        ),
    }


def _parse_rag_values(config_data: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "rag_enabled": get_config_value(config_data, "rag", "enabled", True),
        "rag_embedding_model": str(
            get_config_value(
                config_data, "rag", "embedding_model", "gemini-embedding-2"
            )
        ).strip()
        or "gemini-embedding-2",
        "rag_embedding_dimensions": int(
            get_config_value(config_data, "rag", "embedding_dimensions", 768)
        ),
        "rag_cross_channel_enabled": get_config_value(
            config_data, "rag", "cross_channel_enabled", False
        ),
        "rag_index_bot_responses": get_config_value(
            config_data, "rag", "index_bot_responses", True
        ),
        "rag_backfill_limit": int(
            get_config_value(config_data, "rag", "backfill_limit", 0)
        ),
        "rag_lexical_candidates": int(
            get_config_value(config_data, "rag", "lexical_candidates", 40)
        ),
        "rag_semantic_candidates": int(
            get_config_value(config_data, "rag", "semantic_candidates", 40)
        ),
        "rag_rerank_candidates": int(
            get_config_value(config_data, "rag", "rerank_candidates", 30)
        ),
        "rag_recency_half_life_hours": int(
            get_config_value(config_data, "rag", "recency_half_life_hours", 72)
        ),
        "rag_max_context_messages_low": int(
            get_config_value(config_data, "rag", "max_context_messages_low", 4)
        ),
        "rag_max_context_messages_medium": int(
            get_config_value(config_data, "rag", "max_context_messages_medium", 8)
        ),
        "rag_max_context_messages_high": int(
            get_config_value(config_data, "rag", "max_context_messages_high", 12)
        ),
    }


def _parse_generation_values(config_data: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "temperature": get_config_value(config_data, "generation", "temperature", 0.7),
        "top_p": get_config_value(config_data, "generation", "top_p", 0.8),
        "top_k": get_config_value(config_data, "generation", "top_k", 40),
        "max_output_tokens": get_config_value(
            config_data, "generation", "max_output_tokens", 65536
        ),
        "max_output_tokens_low": get_config_value(
            config_data, "generation", "max_output_tokens_low", 4096
        ),
        "max_output_tokens_medium": get_config_value(
            config_data, "generation", "max_output_tokens_medium", 16384
        ),
        "max_output_tokens_high": get_config_value(
            config_data, "generation", "max_output_tokens_high", 65536
        ),
        "router_temperature": get_config_value(
            config_data, "generation", "router_temperature", 0.1
        ),
        "router_max_output_tokens": get_config_value(
            config_data, "generation", "router_max_output_tokens", 100
        ),
        "edit_detection_max_output_tokens": get_config_value(
            config_data, "generation", "edit_detection_max_output_tokens", 10
        ),
        "safety_harassment": get_config_value(
            config_data, "safety", "harassment", "BLOCK_NONE"
        ),
        "safety_hate_speech": get_config_value(
            config_data, "safety", "hate_speech", "BLOCK_NONE"
        ),
        "safety_sexually_explicit": get_config_value(
            config_data, "safety", "sexually_explicit", "BLOCK_NONE"
        ),
        "safety_dangerous_content": get_config_value(
            config_data, "safety", "dangerous_content", "BLOCK_NONE"
        ),
    }


def _parse_image_rate_values(config_data: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "max_image_size_mb": get_config_value(
            config_data, "image_processing", "max_size_mb", 10
        ),
        "image_processing_timeout": get_config_value(
            config_data, "image_processing", "timeout", 60
        ),
        "max_concurrent_image_edits": get_config_value(
            config_data, "image_processing", "max_concurrent_edits", 3
        ),
        "max_requests_per_user_per_hour": get_config_value(
            config_data, "rate_limiting", "max_requests_per_user_per_hour", 10
        ),
        "max_image_requests_per_minute": get_config_value(
            config_data, "rate_limiting", "max_image_requests_per_minute", 30
        ),
        "text_rate_limit_per_minute": get_config_value(
            config_data, "rate_limiting", "text_rate_limit_per_minute", 10
        ),
        "text_rate_limit_per_hour": get_config_value(
            config_data, "rate_limiting", "text_rate_limit_per_hour", 60
        ),
        "nano_banana_model": get_config_value(
            config_data, "nano_banana", "model", ""
        ),
        "nano_banana_timeout": get_config_value(
            config_data, "nano_banana", "timeout", 60
        ),
        "nano_banana_max_retries": get_config_value(
            config_data, "nano_banana", "max_retries", 3
        ),
        "nano_banana_retry_delay": get_config_value(
            config_data, "nano_banana", "retry_delay", 1.0
        ),
    }


def _parse_content_values(config_data: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "valid_languages": config_data.get("languages", None)
        or list(DEFAULT_LANGUAGES),
        "personalities": config_data.get("personalities", None)
        or dict(DEFAULT_PERSONALITIES),
        "system_prompt_high_complexity": config_data.get(
            "system_prompts", {}
        ).get("high_complexity", ""),
        "system_prompt_low_complexity": config_data.get(
            "system_prompts", {}
        ).get("low_complexity", ""),
        "system_prompt_medium_complexity": config_data.get(
            "system_prompts", {}
        ).get("medium_complexity", ""),
        "system_prompt_thinking_addon": config_data.get(
            "system_prompts", {}
        ).get("thinking_mode_addon", ""),
    }


def _parse_validation_misc_values(
    config_data: Dict[str, Any],
) -> Dict[str, Any]:
    return {
        "min_token_length_discord": get_config_value(
            config_data, "validation", "min_token_length_discord", 50
        ),
        "min_token_length_gemini": get_config_value(
            config_data, "validation", "min_token_length_gemini", 30
        ),
        "max_text_file_size_bytes": get_config_value(
            config_data, "validation", "max_text_file_size_bytes", 5 * 1024 * 1024
        ),
        "pdf_render_scale": get_config_value(
            config_data, "validation", "pdf_render_scale", 2.0
        ),
        "max_pdf_pages": get_config_value(
            config_data, "validation", "max_pdf_pages", 20
        ),
        "leaderboard_limit": get_config_value(
            config_data, "misc", "leaderboard_limit", 10
        ),
        "channel_history_limit": get_config_value(
            config_data, "misc", "channel_history_limit", 500
        ),
        "job_timeout": get_config_value(config_data, "misc", "job_timeout", 60),
        "progress_update_threshold": get_config_value(
            config_data, "misc", "progress_update_threshold", 0.1
        ),
    }


def parse_config_values(config_data: Dict[str, Any]) -> Dict[str, Any]:
    """Translate raw YAML and environment values into BotConfig arguments."""
    values: Dict[str, Any] = {}
    for parser in (
        _parse_secret_reporting_values,
        _parse_context_response_values,
        _parse_message_ux_values,
        _parse_rag_values,
        _parse_model_values,
        _parse_generation_values,
        _parse_image_rate_values,
        _parse_content_values,
        _parse_validation_misc_values,
    ):
        values.update(parser(config_data))
    return values


def _validate_core_values(config: Any) -> List[str]:
    errors: List[str] = []
    if not config.discord_token:
        errors.append("DISCORD_BOT_TOKEN is required (set in .env)")
    if not config.gemini_api_key:
        errors.append("GEMINI_API_KEY is required (set in .env)")

    if config.max_context_messages <= 0:
        errors.append("context.max_messages must be positive")
    if (
        config.context_messages_low <= 0
        or config.context_messages_medium <= 0
        or config.context_messages_high <= 0
    ):
        errors.append("context.context_messages_low/medium/high must all be positive")
    if not (
        config.context_messages_low
        <= config.context_messages_medium
        <= config.context_messages_high
    ):
        errors.append("context.context_messages_low <= medium <= high is required")
    if not config.token_db_path or not config.token_db_path.strip():
        errors.append("bot.token_db_path must be a valid filesystem path")
    if not config.report_web_host or not str(config.report_web_host).strip():
        errors.append("reports.web_host must not be empty")
    if config.report_web_port <= 0 or config.report_web_port > 65535:
        errors.append("reports.web_port must be between 1 and 65535")
    if config.reply_context_range <= 0:
        errors.append("context.reply_range must be positive")
    if config.max_context_images < 0:
        errors.append("context.max_images must be zero or positive")
    if config.rag_enabled:
        if not config.rag_embedding_model:
            errors.append("rag.embedding_model must not be empty when rag.enabled is true")
        if not 128 <= config.rag_embedding_dimensions <= 3072:
            errors.append("rag.embedding_dimensions must be between 128 and 3072")
        if config.rag_backfill_limit < 0:
            errors.append("rag.backfill_limit must be zero or positive")
        if config.rag_lexical_candidates <= 0 or config.rag_semantic_candidates <= 0:
            errors.append("rag.lexical_candidates and rag.semantic_candidates must be positive")
        if config.rag_rerank_candidates < 0:
            errors.append("rag.rerank_candidates must be zero or positive")
        if config.rag_recency_half_life_hours <= 0:
            errors.append("rag.recency_half_life_hours must be positive")
        if (
            config.rag_max_context_messages_low <= 0
            or config.rag_max_context_messages_medium <= 0
            or config.rag_max_context_messages_high <= 0
        ):
            errors.append("rag.max_context_messages_low/medium/high must all be positive")
    if config.response_timeout <= 0:
        errors.append("response.timeout must be positive")
    if config.max_retries < 0:
        errors.append("response.max_retries must be non-negative")
    return errors


def _validate_image_message_and_generation_values(config: Any) -> List[str]:
    errors: List[str] = []
    if config.max_image_size_mb <= 0:
        errors.append("image_processing.max_size_mb must be positive")
    elif config.max_image_size_mb > 25:
        errors.append("image_processing.max_size_mb cannot exceed 25MB (Discord limit)")
    if config.image_processing_timeout <= 0:
        errors.append("image_processing.timeout must be positive")
    elif config.image_processing_timeout > 300:
        errors.append("image_processing.timeout should not exceed 300 seconds")
    if config.max_concurrent_image_edits <= 0:
        errors.append("image_processing.max_concurrent_edits must be positive")
    elif config.max_concurrent_image_edits > 10:
        errors.append("image_processing.max_concurrent_edits should not exceed 10")

    if config.message_split_length <= 0:
        errors.append("messages.split_length must be positive")
    if config.message_split_length > DISCORD_MESSAGE_LIMIT:
        errors.append(
            f"messages.split_length cannot exceed Discord's {DISCORD_MESSAGE_LIMIT} character limit"
        )
    if config.message_split_length < 100:
        errors.append("messages.split_length should be at least 100 characters")

    if not (0.0 <= config.command_suggestion_threshold <= 1.0):
        errors.append("ux.command_suggestion_threshold must be between 0.0 and 1.0")
    if not (0.0 <= config.temperature <= 2.0):
        errors.append("generation.temperature must be between 0.0 and 2.0")
    if config.max_output_tokens <= 0:
        errors.append("generation.max_output_tokens must be positive")
    if (
        config.max_output_tokens_low <= 0
        or config.max_output_tokens_medium <= 0
        or config.max_output_tokens_high <= 0
    ):
        errors.append("generation.max_output_tokens_low/medium/high must all be positive")
    if not (
        config.max_output_tokens_low
        <= config.max_output_tokens_medium
        <= config.max_output_tokens_high
    ):
        errors.append("generation.max_output_tokens_low <= medium <= high is required")
    if config.router_cache_size <= 0:
        errors.append("models.router_cache_size must be positive")
    if config.router_cache_ttl <= 0:
        errors.append("models.router_cache_ttl must be positive")
    return errors


def _validate_model_names_and_availability(config: Any) -> List[str]:
    errors: List[str] = []
    if not config.valid_models:
        errors.append("models.valid must contain at least one model")
    if not config.default_model:
        errors.append("models.default must not be empty")
    elif config.default_model not in config.valid_models:
        errors.append("models.default must be one of models.valid")
    if not config.router_model_name:
        errors.append("models.router must not be empty")
    elif config.router_model_name not in config.valid_models:
        errors.append("models.router must be one of models.valid")

    for item in config.available_models:
        if not isinstance(item, dict):
            errors.append("models.available entries must be mappings with name/value")
            continue
        model_value = str(item.get("value", "")).strip()
        if not model_value:
            errors.append("models.available entries must include a non-empty value")
            continue
        if model_value not in config.valid_models:
            errors.append("models.available values must all exist in models.valid")
    return errors


def _validate_thinking_backends(config: Any) -> List[str]:
    errors: List[str] = []
    valid_thinking_backends = {"thinking_level", "thinking_budget", "none"}
    for model_name, backend in config.model_thinking_backend.items():
        normalized_model = str(model_name).strip()
        normalized_backend = str(backend).strip().lower()
        if not normalized_model:
            errors.append("models.thinking_backend keys must not be empty")
            continue
        if normalized_model not in config.valid_models:
            errors.append("models.thinking_backend keys must be present in models.valid")
        if normalized_backend not in valid_thinking_backends:
            errors.append(
                "models.thinking_backend values must be one of: "
                "thinking_level, thinking_budget, none"
            )

    missing_backend_models = set(config.valid_models) - set(
        config.model_thinking_backend.keys()
    )
    if missing_backend_models:
        missing = ", ".join(sorted(missing_backend_models))
        errors.append(f"models.thinking_backend is missing model entries: {missing}")
    return errors


def _validate_model_complexity(config: Any) -> List[str]:
    errors: List[str] = []
    required_keys = {"low", "medium", "high"}
    if not isinstance(config.model_complexity, dict):
        return ["model_complexity must be a mapping with low/medium/high keys"]

    missing_keys = required_keys - set(config.model_complexity.keys())
    if missing_keys:
        missing = ", ".join(sorted(missing_keys))
        errors.append(f"model_complexity is missing required key(s): {missing}")

    valid_thinking_levels = {
        "default", "off", "minimal", "low", "medium", "high",
        "none", "inherit", "disabled",
    }
    for level in sorted(required_keys):
        entry = config.model_complexity.get(level)
        if not isinstance(entry, dict):
            errors.append(
                f"model_complexity.{level} must be a mapping with "
                "'model' and 'thinking_level'"
            )
            continue

        model_name = str(entry.get("model", "")).strip()
        thinking_level = str(entry.get("thinking_level", "")).strip().lower()
        if not model_name:
            errors.append(f"model_complexity.{level}.model must not be empty")
        elif model_name not in config.valid_models:
            errors.append(
                f"model_complexity.{level}.model must be one of models.valid"
            )
        if thinking_level not in valid_thinking_levels:
            errors.append(
                f"model_complexity.{level}.thinking_level must be one of: "
                "default, off, minimal, low, medium, high"
            )
    return errors


def _validate_model_values(config: Any) -> List[str]:
    errors: List[str] = []
    for validator in (
        _validate_model_names_and_availability,
        _validate_thinking_backends,
        _validate_model_complexity,
    ):
        errors.extend(validator(config))
    return errors


def _validate_feature_values(config: Any) -> List[str]:
    errors: List[str] = []
    if not config.nano_banana_model or not config.nano_banana_model.strip():
        errors.append("nano_banana.model must not be empty")
    if config.text_rate_limit_per_minute <= 0 or config.text_rate_limit_per_hour <= 0:
        errors.append("rate_limiting.text_rate_limit_per_minute/hour must be positive")
    if config.text_rate_limit_per_minute > config.text_rate_limit_per_hour:
        errors.append(
            "rate_limiting.text_rate_limit_per_minute cannot exceed "
            "text_rate_limit_per_hour"
        )
    if not config.system_prompt_high_complexity.strip():
        errors.append("system_prompts.high_complexity must not be empty")
    if not config.system_prompt_low_complexity.strip():
        errors.append("system_prompts.low_complexity must not be empty")
    if not config.system_prompt_medium_complexity.strip():
        errors.append("system_prompts.medium_complexity must not be empty")
    if config.max_pdf_pages <= 0:
        errors.append("validation.max_pdf_pages must be positive")
    return errors


def validate_config(config: Any) -> List[str]:
    """Validate a BotConfig-compatible object in the established error order."""
    errors: List[str] = []
    for validator in (
        _validate_core_values,
        _validate_image_message_and_generation_values,
        _validate_model_values,
        _validate_feature_values,
    ):
        errors.extend(validator(config))
    return errors
