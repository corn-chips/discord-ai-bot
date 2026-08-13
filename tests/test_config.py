"""Regression tests for configuration parsing and validation."""

import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import yaml

from src.config import CONFIG_FILE_PATH, BotConfig


class BotConfigTest(unittest.TestCase):
    """Cover the public parsing and validation behavior of BotConfig."""

    @staticmethod
    def _load_shipped(env):
        """Load the real config.yaml with a known environment, not this box's."""
        # Cleared rather than inherited. config.yaml is what is under test, and
        # TOKEN_DB_PATH / RAG_DATABASE_PATH / LOG_FILE override it when set, so
        # an inherited environment would let the machine decide the result.
        with patch.dict(os.environ, env, clear=True):
            return BotConfig.from_yaml(CONFIG_FILE_PATH)

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
        self.assertEqual(config.rag_database_path, "data/message_rag.db")
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
        # A document with no `safety:` block must still filter nothing. That is
        # the same product decision config.yaml states explicitly, and it is
        # taken twice -- once in the file and once here, for the config that
        # does not mention it -- so both need pinning. The four were the only
        # parse defaults in the file with no test at all.
        self.assertEqual(config.safety_harassment, "BLOCK_NONE")
        self.assertEqual(config.safety_hate_speech, "BLOCK_NONE")
        self.assertEqual(config.safety_sexually_explicit, "BLOCK_NONE")
        self.assertEqual(config.safety_dangerous_content, "BLOCK_NONE")

    def test_rag_settings_parse_the_same_defaults_from_the_parser_and_config_yaml(self):
        expected = {
            "rag_max_context_messages_low": 6,
            "rag_max_context_messages_medium": 10,
            "rag_max_context_messages_high": 14,
            "rag_embedding_batch_delay_seconds": 1.0,
            "rag_embedding_drain_max_batches": 200,
            "rag_embedding_timeout_seconds": 30.0,
            "rag_embedding_retry_reset_hours": 24.0,
            "rag_bm25_weight_content": 1.0,
            "rag_bm25_weight_author": 0.1,
            "rag_bm25_weight_attachment": 0.5,
            "rag_fts_stopwords_enabled": True,
            "rag_fts_min_and_results": 5,
            "rag_rrf_k": 60.0,
            "rag_fusion_weight_recent": 0.7,
            "rag_fusion_weight_lexical": 2.0,
            "rag_fusion_weight_semantic": 2.0,
            "rag_context_char_budget": 8000,
            "rag_query_embedding_cache_size": 128,
            "rag_query_embedding_cache_ttl": 900,
            "rag_conversation_enabled": True,
            "rag_conversation_gap_minutes": 10.0,
            "rag_conversation_max_messages": 40,
            "rag_conversation_reply_merge_max_hours": 6.0,
            "rag_conversation_turnover_window": 3,
            "rag_conversation_turnover_min_gap_minutes": 3.0,
            "rag_conversation_expand_full_max_messages": 12,
            "rag_conversation_expand_window_messages": 8,
            "rag_query_rewrite_enabled": True,
            "rag_query_rewrite_history_turns": 4,
            "rag_entity_profiles_enabled": True,
            "rag_entity_profile_min_messages": 20,
            "rag_entity_profile_refresh_hours": 24.0,
            "rag_entity_profile_max_chars": 1200,
        }

        sources = {
            "parser": self._load_yaml(None),
            "config.yaml": self._load_shipped({}),
        }
        for source, config in sources.items():
            for name, value in expected.items():
                with self.subTest(source=source, field=name):
                    self.assertEqual(getattr(config, name), value)

    def test_rag_boolean_settings_are_coerced_to_bool(self):
        data = {
            "rag": {
                "fts_stopwords_enabled": 0,
                "conversation_enabled": "",
                "query_rewrite_enabled": 1,
                "entity_profiles_enabled": "yes",
            }
        }

        config = self._load_yaml(data)

        self.assertIs(config.rag_fts_stopwords_enabled, False)
        self.assertIs(config.rag_conversation_enabled, False)
        self.assertIs(config.rag_query_rewrite_enabled, True)
        self.assertIs(config.rag_entity_profiles_enabled, True)

    def test_the_shipped_config_yaml_loads_and_validates(self):
        # The file that actually boots this bot, through the real parser and
        # the real validator. Every other test in this class builds a BotConfig
        # by hand or from a synthetic document, so nothing ran the shipped
        # config.yaml through validate_config: a value edited past a rule, or a
        # rule tightened past a value, was a green suite and a bot that exits 1
        # on the operator's machine.
        #
        # The two secrets are the only settings config.yaml cannot supply. Their
        # lengths are read out of the shipped file rather than written here, so
        # this asserts the config's own minimums are satisfiable rather than
        # re-stating today's numbers.
        minimums = self._load_shipped({})
        config = self._load_shipped(
            {
                "DISCORD_BOT_TOKEN": "d" * minimums.min_token_length_discord,
                "GEMINI_API_KEY": "g" * minimums.min_token_length_gemini,
            }
        )

        self.assertEqual(config.validate(), [])
        self.assertEqual(config.validate_tokens(), (True, []))

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
        # DAB-095: 5000 ms was already in force as Python's sqlite3 default.
        # The point of the setting is that it is now stated and tunable, so the
        # default must not drift silently.
        self.assertEqual(config.sqlite_busy_timeout_ms, 5000)
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

    def test_validate_rejects_shared_token_and_rag_database(self):
        config = self._valid_config()
        config.rag_database_path = config.token_db_path

        self.assertIn(
            "rag.database_path must be separate from bot.token_db_path",
            config.validate(),
        )

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
        # The four safety errors were the only rule in validate_config whose
        # place in this list was unpinned. Order is the product here -- it is
        # what load_and_validate_config prints, in this sequence, before
        # exiting 1 -- and these four come last because _validate_feature_values
        # runs last and they sit at the end of it.
        config.safety_harassment = "BLOCK_MEDIUM"
        config.safety_hate_speech = "BLOCK_HIGH"
        config.safety_sexually_explicit = ""
        config.safety_dangerous_content = "none"

        accepted = (
            "BLOCK_LOW_AND_ABOVE, BLOCK_MEDIUM_AND_ABOVE, BLOCK_NONE, "
            "BLOCK_ONLY_HIGH, HARM_BLOCK_THRESHOLD_UNSPECIFIED, OFF"
        )

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
                f"safety.harassment must be one of {accepted} (got 'BLOCK_MEDIUM')",
                f"safety.hate_speech must be one of {accepted} (got 'BLOCK_HIGH')",
                f"safety.sexually_explicit must be one of {accepted} (got '')",
                f"safety.dangerous_content must be one of {accepted} (got 'none')",
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
