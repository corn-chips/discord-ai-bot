"""Regression tests for configuration parsing and validation."""

import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import yaml

from src.config import BotConfig


class BotConfigTest(unittest.TestCase):
    """Cover the public parsing and validation behavior of BotConfig."""

    def _load_yaml(self, data, env=None):
        with tempfile.TemporaryDirectory() as temp_dir:
            config_path = Path(temp_dir) / "config.yaml"
            config_path.write_text(
                yaml.safe_dump(data, sort_keys=False),
                encoding="utf-8",
            )
            with patch.dict(os.environ, env or {}, clear=True):
                return BotConfig.from_yaml(config_path)

    @staticmethod
    def _valid_config():
        model_name = "test-model"
        return BotConfig(
            discord_token="d" * 50,
            gemini_api_key="g" * 30,
            default_model=model_name,
            router_model_name=model_name,
            available_models=[{"name": "Test Model", "value": model_name}],
            valid_models=[model_name],
            model_thinking_backend={model_name: "thinking_level"},
            model_complexity={
                "low": {"model": model_name, "thinking_level": "minimal"},
                "medium": {"model": model_name, "thinking_level": "low"},
                "high": {"model": model_name, "thinking_level": "high"},
            },
            nano_banana_model="test-image-model",
            system_prompt_high_complexity="high prompt",
            system_prompt_low_complexity="low prompt",
            system_prompt_medium_complexity="medium prompt",
        )

    def test_from_yaml_uses_expected_defaults_for_empty_document(self):
        config = self._load_yaml(None)

        self.assertEqual(config.discord_token, "")
        self.assertEqual(config.gemini_api_key, "")
        self.assertEqual(config.nano_banana_api_key, "")
        self.assertFalse(config.dev_mode_enabled)
        self.assertTrue(config.report_web_enabled)
        self.assertEqual(config.report_web_port, 8080)
        self.assertTrue(config.rag_enabled)
        self.assertEqual(config.rag_embedding_model, "gemini-embedding-2")
        self.assertEqual(config.rag_embedding_dimensions, 768)
        self.assertEqual(config.default_model, "")
        self.assertEqual(config.router_model_name, "")
        self.assertEqual(config.available_models, [])
        self.assertEqual(config.valid_models, [])
        self.assertEqual(
            config.personalities,
            {"default": "You are a helpful AI assistant. Respond naturally and informatively."},
        )
        self.assertIn("auto", config.valid_languages)
        self.assertEqual(config.max_text_file_size_bytes, 5 * 1024 * 1024)

    def test_from_yaml_preserves_environment_and_explicit_false_zero_values(self):
        data = {
            "bot": {"dev_mode": False, "token_db_path": "yaml.db"},
            "reports": {"web_enabled": False, "web_port": "0"},
            "logging": {"file": "yaml.log"},
            "rag": {"enabled": False, "backfill_limit": "0"},
            "languages": [],
            "personalities": {},
        }
        config = self._load_yaml(
            data,
            {
                "DISCORD_BOT_TOKEN": "discord-secret",
                "GEMINI_API_KEY": "gemini-secret",
                "NANO_BANANA_API_KEY": "",
                "TOKEN_DB_PATH": "",
                "LOG_FILE": "",
            },
        )

        self.assertEqual(config.discord_token, "discord-secret")
        self.assertEqual(config.gemini_api_key, "gemini-secret")
        self.assertEqual(config.nano_banana_api_key, "")
        self.assertEqual(config.token_db_path, "yaml.db")
        self.assertEqual(config.log_file, "yaml.log")
        self.assertFalse(config.dev_mode_enabled)
        self.assertFalse(config.report_web_enabled)
        self.assertEqual(config.report_web_port, 0)
        self.assertFalse(config.rag_enabled)
        self.assertEqual(config.rag_backfill_limit, 0)
        self.assertIn("auto", config.valid_languages)
        self.assertEqual(list(config.personalities), ["default"])

    def test_from_yaml_normalizes_model_settings_and_fallbacks(self):
        data = {
            "models": {
                "default": " alpha ",
                "router": "",
                "available": [
                    {"name": " Alpha Display ", "value": " alpha "},
                    {"value": " beta "},
                    "not-a-mapping",
                    {"name": "empty", "value": " "},
                ],
                "valid": [],
                "display_names": {},
                "descriptions": {" alpha ": " First model ", "": "ignored"},
                "thinking_backend": {" alpha ": " THINKING_LEVEL ", "": "none"},
            },
            "model_complexity": {
                "low": None,
                "medium": {"model": 0, "thinking_level": False},
                "high": "not-a-mapping",
            },
        }

        config = self._load_yaml(data)

        self.assertEqual(
            config.available_models,
            [
                {"name": "Alpha Display", "value": "alpha"},
                {"name": "beta", "value": "beta"},
            ],
        )
        self.assertEqual(config.valid_models, ["alpha", "beta"])
        self.assertEqual(config.default_model, "alpha")
        self.assertEqual(config.router_model_name, "alpha")
        self.assertEqual(
            config.model_display_names,
            {"alpha": "Alpha Display", "beta": "beta"},
        )
        self.assertEqual(config.model_descriptions, {"alpha": "First model"})
        self.assertEqual(config.model_thinking_backend, {"alpha": "thinking_level"})
        self.assertEqual(
            config.model_complexity,
            {
                "low": {"model": "alpha", "thinking_level": "minimal"},
                "medium": {"model": "0", "thinking_level": "False"},
                "high": {"model": "alpha", "thinking_level": "high"},
            },
        )

    def test_from_yaml_exits_with_guidance_when_file_is_missing(self):
        missing_path = Path("definitely-missing-config.yaml")

        with patch("builtins.print") as print_mock:
            with self.assertRaisesRegex(SystemExit, "1"):
                BotConfig.from_yaml(missing_path)

        self.assertEqual(
            [call.args[0] for call in print_mock.call_args_list],
            [
                f"Config file not found at {missing_path}",
                "Copy config.yaml.example to config.yaml and edit it.",
            ],
        )

    def test_validate_accepts_complete_valid_config(self):
        self.assertEqual(self._valid_config().validate(), [])

    def test_validate_preserves_error_messages_and_order(self):
        config = self._valid_config()
        config.discord_token = ""
        config.gemini_api_key = ""
        config.max_context_messages = 0
        config.context_messages_low = 0
        config.context_messages_medium = -1
        config.context_messages_high = -2
        config.token_db_path = " "
        config.report_web_host = " "
        config.report_web_port = 0
        config.reply_context_range = 0
        config.max_context_images = -1
        config.rag_embedding_model = ""
        config.rag_embedding_dimensions = 64
        config.rag_backfill_limit = -1
        config.rag_lexical_candidates = 0
        config.rag_rerank_candidates = -1
        config.rag_recency_half_life_hours = 0
        config.rag_max_context_messages_low = 0
        config.response_timeout = 0
        config.max_retries = -1
        config.max_image_size_mb = 0
        config.image_processing_timeout = 301
        config.max_concurrent_image_edits = 11
        config.message_split_length = 0
        config.command_suggestion_threshold = -0.1
        config.temperature = 2.1
        config.max_output_tokens = 0
        config.max_output_tokens_low = 0
        config.max_output_tokens_medium = -1
        config.max_output_tokens_high = -2
        config.router_cache_size = 0
        config.router_cache_ttl = 0
        config.valid_models = []
        config.default_model = ""
        config.router_model_name = ""
        config.nano_banana_model = ""
        config.text_rate_limit_per_minute = 0
        config.text_rate_limit_per_hour = -1
        config.system_prompt_high_complexity = " "
        config.system_prompt_low_complexity = " "
        config.system_prompt_medium_complexity = " "
        config.max_pdf_pages = 0

        self.assertEqual(
            config.validate(),
            [
                "DISCORD_BOT_TOKEN is required (set in .env)",
                "GEMINI_API_KEY is required (set in .env)",
                "context.max_messages must be positive",
                "context.context_messages_low/medium/high must all be positive",
                "context.context_messages_low <= medium <= high is required",
                "bot.token_db_path must be a valid filesystem path",
                "reports.web_host must not be empty",
                "reports.web_port must be between 1 and 65535",
                "context.reply_range must be positive",
                "context.max_images must be zero or positive",
                "rag.embedding_model must not be empty when rag.enabled is true",
                "rag.embedding_dimensions must be between 128 and 3072",
                "rag.backfill_limit must be zero or positive",
                "rag.lexical_candidates and rag.semantic_candidates must be positive",
                "rag.rerank_candidates must be zero or positive",
                "rag.recency_half_life_hours must be positive",
                "rag.max_context_messages_low/medium/high must all be positive",
                "response.timeout must be positive",
                "response.max_retries must be non-negative",
                "image_processing.max_size_mb must be positive",
                "image_processing.timeout should not exceed 300 seconds",
                "image_processing.max_concurrent_edits should not exceed 10",
                "messages.split_length must be positive",
                "messages.split_length should be at least 100 characters",
                "ux.command_suggestion_threshold must be between 0.0 and 1.0",
                "generation.temperature must be between 0.0 and 2.0",
                "generation.max_output_tokens must be positive",
                "generation.max_output_tokens_low/medium/high must all be positive",
                "generation.max_output_tokens_low <= medium <= high is required",
                "models.router_cache_size must be positive",
                "models.router_cache_ttl must be positive",
                "models.valid must contain at least one model",
                "models.default must not be empty",
                "models.router must not be empty",
                "models.available values must all exist in models.valid",
                "models.thinking_backend keys must be present in models.valid",
                "model_complexity.high.model must be one of models.valid",
                "model_complexity.low.model must be one of models.valid",
                "model_complexity.medium.model must be one of models.valid",
                "nano_banana.model must not be empty",
                "rate_limiting.text_rate_limit_per_minute/hour must be positive",
                "rate_limiting.text_rate_limit_per_minute cannot exceed text_rate_limit_per_hour",
                "system_prompts.high_complexity must not be empty",
                "system_prompts.low_complexity must not be empty",
                "system_prompts.medium_complexity must not be empty",
                "validation.max_pdf_pages must be positive",
            ],
        )

    def test_validate_skips_rag_details_when_rag_is_disabled(self):
        config = self._valid_config()
        config.rag_enabled = False
        config.rag_embedding_model = ""
        config.rag_embedding_dimensions = 64
        config.rag_backfill_limit = -1
        config.rag_lexical_candidates = 0
        config.rag_semantic_candidates = 0
        config.rag_rerank_candidates = -1
        config.rag_recency_half_life_hours = 0
        config.rag_max_context_messages_low = 0
        config.rag_max_context_messages_medium = 0
        config.rag_max_context_messages_high = 0

        self.assertEqual(config.validate(), [])

    def test_validate_tokens_uses_configured_thresholds_and_error_order(self):
        config = self._valid_config()
        config.discord_token = "d" * (config.min_token_length_discord - 1)
        config.gemini_api_key = "g" * (config.min_token_length_gemini - 1)
        config.nano_banana_api_key = "n" * 19

        self.assertEqual(
            config.validate_tokens(),
            (
                False,
                [
                    "DISCORD_BOT_TOKEN appears to be invalid (too short)",
                    "GEMINI_API_KEY appears to be invalid (too short)",
                    "NANO_BANANA_API_KEY appears to be invalid (too short)",
                ],
            ),
        )


if __name__ == "__main__":
    unittest.main()
