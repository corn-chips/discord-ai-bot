"""Guards for DAB-002: a registrar failure must not silently disable features.

`on_ready` wrapped `setup_commands` and `tree.sync()` in a single broad
`except Exception: log`. Three services were attached to the bot as a side
effect of `register_personalization_commands`, so any registrar raising left
those attributes absent -- and every reader reaches them through
`getattr`/`hasattr`, so nothing failed loudly. `_is_live_mode_enabled` returned
False for every channel and user preferences were skipped, for the lifetime of
the process, after one log line that blamed Discord.

These assert the OBSERVABLE END STATE -- that the services exist and work after
a registrar blows up -- rather than that a particular message was logged. A
logging assertion would pass just as happily over a bot that had lost live mode,
which is the same class of mistake as asserting a permission attribute that
never reaches the wire.
"""

import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, PropertyMock, patch

from src.bot.discord_bot import DiscordBot
from src.config import BotConfig


def make_config(tmp):
    return BotConfig(
        discord_token="t",
        gemini_api_key="k",
        token_db_path=str(Path(tmp) / "token.db"),
        rag_database_path=str(Path(tmp) / "rag.db"),
        available_models=[{"name": "A", "value": "model-a"}],
        valid_models=["model-a"],
        valid_languages=["english"],
        model_display_names={"model-a": "A"},
        model_descriptions={"model-a": "First"},
    )


class StartupServiceAvailabilityTest(unittest.TestCase):
    """The three services must exist from construction, not from registration."""

    def setUp(self):
        self._dir = tempfile.TemporaryDirectory()
        self.addCleanup(self._dir.cleanup)
        self.config = make_config(self._dir.name)
        self.bot = DiscordBot(self.config)
        self.addCleanup(lambda: self.bot._pdf_executor.shutdown(wait=False))

    def test_the_services_exist_before_any_command_is_registered(self):
        # No setup_commands has run against this bot.
        for attribute in (
            "_channel_settings_service",
            "_message_visibility_service",
            "_user_prefs_service",
        ):
            with self.subTest(attribute=attribute):
                self.assertIsNotNone(getattr(self.bot, attribute, None))

    def test_live_mode_can_be_read_before_registration(self):
        # The observable symptom of the defect: this returned False for every
        # channel forever, because the service it consults did not exist.
        self.assertIsNotNone(
            getattr(self.bot, "_channel_settings_service", None),
            "live mode cannot be evaluated without the channel settings service",
        )
        # And the query itself works rather than raising.
        self.assertIn(self.bot._is_live_mode_enabled(12345), (True, False))

    def test_live_mode_reflects_a_real_setting_round_trip(self):
        service = self.bot._channel_settings_service
        service.set_live_enabled(4242, True)

        self.assertTrue(self.bot._is_live_mode_enabled(4242))

    def test_user_preferences_are_usable_before_registration(self):
        service = self.bot._user_prefs_service

        service.set_model(7, "model-a")

        self.assertEqual(service.get_preferences(7).preferred_model, "model-a")


class OnReadyHarness(unittest.IsolatedAsyncioTestCase):
    """Run the real `on_ready` against a stubbed gateway.

    `Client.user` and `Client.guilds` are read-only properties, and `on_ready`
    dereferences `self.user.id` on its third line. Without these PropertyMocks
    the coroutine dies at `discord_bot.py:509` with
    `AttributeError: 'NoneType' object has no attribute 'id'` -- which is how
    the two registrar tests below used to end, several hundred lines before
    reaching the code they were named for. They passed anyway, because their
    `try/except Exception: pass` swallowed it and their assertions only needed
    the constructor to have run.
    """

    async def asyncSetUp(self):
        self._dir = tempfile.TemporaryDirectory()
        self.addCleanup(self._dir.cleanup)
        self.config = make_config(self._dir.name)
        self.bot = DiscordBot(self.config)
        self.addCleanup(lambda: self.bot._pdf_executor.shutdown(wait=False))

        user = patch.object(type(self.bot), "user", new_callable=PropertyMock)
        guilds = patch.object(type(self.bot), "guilds", new_callable=PropertyMock)
        self.user_property = user.start()
        self.guilds_property = guilds.start()
        self.addCleanup(user.stop)
        self.addCleanup(guilds.stop)
        self.user_property.return_value = SimpleNamespace(id=99)
        self.guilds_property.return_value = []

        self.change_presence = AsyncMock()
        self.bot.change_presence = self.change_presence
        sync = patch.object(self.bot.tree, "sync", AsyncMock(return_value=[]))
        sync.start()
        self.addCleanup(sync.stop)

        # on_ready really does start these. The report web server binds
        # 127.0.0.1:8080 and is never stopped, so leaving it in place makes the
        # suite hold a listening socket for the rest of the process and the
        # second test in this class fails to bind.
        self.bot.report_web_server = None
        self.bot.image_processing_service = None


class OnReadyResilienceTest(OnReadyHarness):
    """DAB-009: one bare await between two guarded regions cost everything."""

    async def test_a_presence_failure_does_not_skip_command_registration(self):
        self.change_presence.side_effect = RuntimeError("gateway closed")

        with patch("src.bot.discord_bot.setup_commands", AsyncMock()) as setup:
            await self.bot.on_ready()

        # The observable end state: the command tree was built. Before the fix
        # this was 0 -- the exception escaped into on_error and the bot ran with
        # no slash commands at all.
        setup.assert_awaited_once()

    async def test_a_backlog_failure_does_not_escape_on_ready(self):
        with patch("src.bot.discord_bot.setup_commands", AsyncMock()) as setup, patch(
            "src.bot.discord_bot._start_automatic_rag_backlog",
            side_effect=RuntimeError("permissions cache empty"),
        ):
            await self.bot.on_ready()

        setup.assert_awaited_once()


class RegistrarFailureTest(OnReadyHarness):
    """A raising registrar must not take the features down with it."""

    async def test_live_mode_survives_a_registrar_raising(self):
        # Simulate the exact shape of the defect: setup_commands explodes. With
        # the harness above, on_ready now actually reaches this call.
        with patch(
            "src.bot.discord_bot.setup_commands",
            side_effect=RuntimeError("registrar exploded"),
        ) as setup:
            await self.bot.on_ready()

        setup.assert_awaited_once()
        self.bot._channel_settings_service.set_live_enabled(99, True)
        self.assertTrue(
            self.bot._is_live_mode_enabled(99),
            "live mode was disabled by a command registration failure",
        )

    async def test_user_preferences_survive_a_registrar_raising(self):
        with patch(
            "src.bot.discord_bot.setup_commands",
            side_effect=RuntimeError("registrar exploded"),
        ) as setup:
            await self.bot.on_ready()

        setup.assert_awaited_once()
        service = getattr(self.bot, "_user_prefs_service", None)
        self.assertIsNotNone(
            service, "user preferences were lost to a registration failure"
        )
        service.set_language(3, "english")
        self.assertEqual(service.get_preferences(3).preferred_language, "english")


if __name__ == "__main__":
    unittest.main()
