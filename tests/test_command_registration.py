"""Regression coverage for the slash-command registration contract."""

import tempfile
import sys
import unittest
from datetime import timedelta
from pathlib import Path
from types import ModuleType, SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

import discord
from discord import app_commands
from discord.ext import commands

from src.bot.commands import (
    _format_report_status,
    _format_timedelta,
    _get_model_description,
    _truncate_text,
    setup_commands,
)
from src.config import BotConfig
from src.models.data_models import EditType, ImageEditRequest


EXPECTED_SIGNATURE = [
    ("ping", "Check if the bot is responsive", "ping"),
    ("report", "Submit a bot issue or feature request", "report"),
    ("report-status", "Check the status of a submitted report", "report_status"),
    ("stats", "View bot statistics and usage data", "stats"),
    ("token-leaderboard", "See the top token users in this server", "token_leaderboard"),
    ("features", "Discover all available bot features and capabilities", "features_command"),
    ("clear-cache", "Clear bot's message cache (Admin only)", "clear_cache"),
    ("dev", "Toggle developer mode for detailed error output", "dev_mode"),
    ("api-usage", "Display API usage statistics and rate limit information", "api_usage"),
    ("usage-report", "Generate a downloadable usage report (CSV + Markdown)", "usage_report"),
    ("deepresearch", "Perform a deep research task and generate a report", "deepresearch"),
    ("config", "Configure bot settings", "GROUP"),
    ("config model", "Switch between Gemini Flash AI models", "model"),
    ("config thinking", "Set API thinking level for responses", "thinking"),
    ("config deepsearch", "Toggle DeepSearch (Force Google Search)", "deepsearch"),
    ("config image-generation", "Enable or disable AI image generation", "image_generation"),
    ("config debug", "Toggle Debug Logging", "debug"),
    ("config info", "View current bot configuration and feature status", "config_info"),
    ("rag", "Manage local message retrieval memory", "GROUP"),
    ("rag status", "Show local message RAG index status", "rag_status"),
    ("rag backfill", "Pre-generate local RAG data from channel history", "rag_backfill"),
    ("rag delete", "Delete stored message RAG data", "rag_delete"),
    ("summarize", "Summarize the current conversation", "summarize"),
    ("personality", "Set the bot's personality/tone for this channel", "personality"),
    ("personality-info", "Show the current personality setting for this channel", "personality_info"),
    ("live", "Toggle mention-free live mode for this channel", "live"),
    ("pin", "Pin a memory for the bot to always remember in this channel", "pin"),
    ("pins", "List all pinned bot memories for this channel", "pins"),
    ("hide", "Replace recent Grok messages in this channel with '.'", "hide"),
    ("unhide", "Restore recent Grok messages hidden in this channel", "unhide"),
    ("preferences", "Manage your personal bot preferences", "GROUP"),
    ("preferences model", "Set your preferred AI model", "prefs_model"),
    ("preferences language", "Set your preferred response language", "prefs_language"),
    ("preferences show", "Show your current preferences", "prefs_show"),
    ("preferences clear", "Reset all your preferences to defaults", "prefs_clear"),
]


class CommandRegistrationTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self._temp_dir = tempfile.TemporaryDirectory()
        self._bots = []
        self.config = BotConfig(
            token_db_path=str(Path(self._temp_dir.name) / "commands.db"),
            available_models=[
                {"name": "Model A", "value": "model-a"},
                {"name": "Model B", "value": "model-b"},
            ],
            valid_models=["model-a", "model-b"],
            valid_languages=["english", "auto"],
            model_display_names={"model-a": "Model A"},
            model_descriptions={"model-a": "First model"},
        )

    async def asyncTearDown(self):
        for bot in self._bots:
            await bot.close()
        self._temp_dir.cleanup()

    async def _register(
        self,
        image_processing_service=None,
        gemini_client=None,
        performance_logger=None,
    ):
        bot = commands.Bot(command_prefix="!", intents=discord.Intents.none())
        bot.config = self.config
        bot.image_processing_service = image_processing_service
        self._bots.append(bot)
        await setup_commands(
            bot,
            self.config,
            gemini_client or object(),
            performance_logger or object(),
            None,
        )
        return bot

    @staticmethod
    def _flatten_signature(bot):
        signature = []
        for command in bot.tree.get_commands():
            if isinstance(command, app_commands.Group):
                signature.append((command.name, command.description, "GROUP"))
                signature.extend(
                    (
                        f"{command.name} {child.name}",
                        child.description,
                        child.callback.__name__,
                    )
                    for child in command.commands
                )
            else:
                signature.append(
                    (command.name, command.description, command.callback.__name__)
                )
        return signature

    @staticmethod
    def _command(bot, path):
        names = path.split()
        command = bot.tree.get_command(names[0])
        if len(names) == 2:
            command = command.get_command(names[1])
        return command

    @staticmethod
    def _choices(command, parameter_name):
        parameter = next(
            parameter
            for parameter in command.parameters
            if parameter.name == parameter_name
        )
        return [(choice.name, choice.value) for choice in parameter.choices]

    async def test_tree_signature_and_metadata_match_registration_contract(self):
        bot = await self._register()

        self.assertEqual(self._flatten_signature(bot), EXPECTED_SIGNATURE)
        self.assertEqual(len(bot.tree.get_commands()), 22)
        self.assertEqual(len(EXPECTED_SIGNATURE), 35)

        report = self._command(bot, "report")
        self.assertEqual(
            self._choices(report, "category"),
            [("Issue", "issue"), ("Feature", "feature")],
        )
        self.assertEqual(
            self._choices(self._command(bot, "config model"), "model_name"),
            [("Model A", "model-a"), ("Model B", "model-b")],
        )
        self.assertEqual(
            self._choices(self._command(bot, "preferences model"), "model"),
            [("model-a", "model-a"), ("model-b", "model-b")],
        )
        self.assertEqual(
            self._choices(self._command(bot, "preferences language"), "language"),
            [("English", "english"), ("Auto", "auto")],
        )
        self.assertEqual(
            self._choices(self._command(bot, "rag delete"), "scope"),
            [("Current channel", "channel"), ("All channels", "all")],
        )

        clear_cache = self._command(bot, "clear-cache")
        self.assertTrue(clear_cache.default_permissions.administrator)

        for path in ("rag status", "rag backfill", "rag delete"):
            self.assertEqual(self._command(bot, path).checks, [])

        personality = self._command(bot, "personality")
        self.assertTrue(next(iter(personality.parameters)).autocomplete)
        self.assertFalse(next(iter(self._command(bot, "live").parameters)).required)
        self.assertFalse(
            next(iter(self._command(bot, "rag backfill").parameters)).required
        )
        self.assertTrue(hasattr(bot, "_pin_service"))
        self.assertTrue(hasattr(bot, "_message_visibility_service"))

    async def test_rag_delete_scopes_deletion_and_stops_background_work(self):
        bot = await self._register()
        bot.message_index_service = SimpleNamespace(
            delete_rag_data_async=AsyncMock(
                return_value={"messages": 4, "pins": 2}
            )
        )
        bot.hybrid_context_retriever = SimpleNamespace(
            cancel_background_work=AsyncMock()
        )
        interaction = SimpleNamespace(
            channel_id=20,
            response=SimpleNamespace(send_message=AsyncMock(), defer=AsyncMock()),
            followup=SimpleNamespace(send=AsyncMock()),
        )

        command = self._command(bot, "rag delete")
        await command.callback(
            interaction,
            app_commands.Choice(name="Current channel", value="channel"),
        )

        interaction.response.defer.assert_awaited_once_with(ephemeral=True)
        bot.hybrid_context_retriever.cancel_background_work.assert_awaited_once_with(
            channel_id=20
        )
        bot.message_index_service.delete_rag_data_async.assert_awaited_once_with(
            channel_id=20
        )
        self.assertIn(
            "Deleted 4 indexed RAG message(s) and 2 pinned memory item(s) for this channel",
            interaction.followup.send.await_args.args[0],
        )

        bot.hybrid_context_retriever.cancel_background_work.reset_mock()
        bot.message_index_service.delete_rag_data_async.reset_mock()
        await command.callback(
            interaction,
            app_commands.Choice(name="All channels", value="all"),
        )

        bot.hybrid_context_retriever.cancel_background_work.assert_awaited_once_with(
            channel_id=None
        )
        bot.message_index_service.delete_rag_data_async.assert_awaited_once_with(
            channel_id=None
        )

    async def test_image_commands_keep_conditional_registration_and_metadata(self):
        bot = await self._register(image_processing_service=object())

        self.assertEqual(
            [command.name for command in bot.tree.get_commands()][5:10],
            ["features", "edit-image", "image-queue", "clear-cache", "dev"],
        )
        edit_image = self._command(bot, "edit-image")
        self.assertEqual(edit_image.description, "Edit an uploaded image using AI")
        self.assertEqual(
            [
                (parameter.name, parameter.required, parameter.type)
                for parameter in edit_image.parameters
            ],
            [
                ("image", True, discord.AppCommandOptionType.attachment),
                ("instruction", True, discord.AppCommandOptionType.string),
                ("edit_type", False, discord.AppCommandOptionType.string),
            ],
        )
        self.assertEqual(
            self._command(bot, "image-queue").description,
            "Check the image processing queue status",
        )

    async def test_rag_backfill_defaults_to_complete_history_background_job(self):
        bot = await self._register()
        bot.message_index_service = SimpleNamespace(
            db_path=Path("data/token_usage.db")
        )
        bot.hybrid_context_retriever = SimpleNamespace(
            start_channel_pregeneration=Mock(return_value=True)
        )
        channel = SimpleNamespace(id=20)
        interaction = SimpleNamespace(
            channel=channel,
            channel_id=20,
            response=SimpleNamespace(
                send_message=AsyncMock(),
                defer=AsyncMock(),
            ),
            followup=SimpleNamespace(send=AsyncMock()),
        )

        await self._command(bot, "rag backfill").callback(interaction, None)

        interaction.response.defer.assert_awaited_once_with(ephemeral=True)
        bot.hybrid_context_retriever.start_channel_pregeneration.assert_called_once_with(
            channel,
            limit=None,
            include_bot_user_id=None,
        )
        followup_text = interaction.followup.send.await_args.args[0]
        self.assertIn("entire accessible channel history", followup_text)
        self.assertIn("/rag status", followup_text)

    async def test_edit_image_callback_builds_and_submits_request(self):
        service = SimpleNamespace(
            get_user_rate_limit_info=AsyncMock(
                return_value={"requests_remaining": 1}
            ),
            process_image_edit=AsyncMock(return_value="job-1"),
            get_job_status=AsyncMock(
                return_value=SimpleNamespace(
                    status=SimpleNamespace(value="failed"),
                    error_message="expected test failure",
                )
            ),
        )
        bot = await self._register(image_processing_service=service)
        interaction = SimpleNamespace(
            user=SimpleNamespace(id=123),
            channel=SimpleNamespace(id=456),
            response=SimpleNamespace(
                defer=AsyncMock(),
                send_message=AsyncMock(),
            ),
            followup=SimpleNamespace(send=AsyncMock()),
        )
        attachment = SimpleNamespace(
            content_type="image/png",
            size=128,
            read=AsyncMock(return_value=b"image-bytes"),
        )

        await self._command(bot, "edit-image").callback(
            interaction,
            attachment,
            "remove the background",
            None,
        )

        interaction.response.defer.assert_awaited_once_with()
        attachment.read.assert_awaited_once_with()
        service.process_image_edit.assert_awaited_once()
        request = service.process_image_edit.await_args.args[0]
        self.assertIsInstance(request, ImageEditRequest)
        self.assertEqual(request.user_id, "123")
        self.assertEqual(request.channel_id, "456")
        self.assertEqual(request.image_data, b"image-bytes")
        self.assertEqual(request.instruction, "remove the background")
        self.assertEqual(request.edit_type, EditType.GENERAL_EDIT)
        service.get_job_status.assert_awaited_once_with("job-1")
        self.assertIn(
            "expected test failure",
            interaction.followup.send.await_args_list[-1].args[0],
        )

    async def test_api_usage_callback_resolves_image_service_from_src_package(self):
        image_health = SimpleNamespace(
            get_service_health_info=Mock(
                return_value={
                    "status": "healthy",
                    "requests_in_last_minute": 1,
                    "rate_limit_remaining": 2,
                    "consecutive_failures": 0,
                }
            )
        )
        fake_module = ModuleType("src.services.nano_banana_client")
        fake_module.nano_banana_client = image_health
        gemini_client = SimpleNamespace(
            get_api_usage_info=Mock(return_value={}),
            get_current_model=Mock(return_value="model-a"),
            get_thinking_level=Mock(return_value="low"),
        )
        performance_logger = SimpleNamespace(
            get_summary_stats=Mock(return_value={})
        )
        bot = await self._register(
            gemini_client=gemini_client,
            performance_logger=performance_logger,
        )
        interaction = SimpleNamespace(
            user=SimpleNamespace(display_name="Tester"),
            response=SimpleNamespace(send_message=AsyncMock()),
        )

        with patch.dict(
            sys.modules,
            {"src.services.nano_banana_client": fake_module},
        ):
            await self._command(bot, "api-usage").callback(interaction)

        image_health.get_service_health_info.assert_called_once_with()
        sent_embed = interaction.response.send_message.await_args.kwargs["embed"]
        self.assertIn(
            "🍌 Image Generation Service",
            [field.name for field in sent_embed.fields],
        )

    def test_public_command_helpers_remain_importable(self):
        self.assertEqual(_format_report_status("in_progress"), "In Progress")
        self.assertEqual(_truncate_text("abcdef", 5), "ab...")
        self.assertEqual(_format_timedelta(timedelta(hours=1, minutes=2)), "1h 2m")
        self.assertEqual(_get_model_description("model-a", self.config), "First model")


if __name__ == "__main__":
    unittest.main()
