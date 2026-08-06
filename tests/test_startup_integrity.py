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

import asyncio
import logging
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, PropertyMock, patch

import aiohttp

from src.bot.discord_bot import DiscordBot
from src.config import BotConfig
from src.services.report_service import ReportService
from src.services.report_web_server import ReportWebServer


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

        with self.assertLogs("src.bot.discord_bot", level="WARNING") as captured, patch(
            "src.bot.discord_bot.setup_commands", AsyncMock()
        ) as setup:
            await self.bot.on_ready()

        # The observable end state: the command tree was built. Before the fix
        # this was 0 -- the exception escaped into on_error and the bot ran with
        # no slash commands at all.
        setup.assert_awaited_once()
        # Degrading quietly would be its own defect, and the capture keeps the
        # traceback this test provokes out of the suite's stderr.
        self.assertIn("Could not set the bot presence", captured.output[0])

    async def test_a_backlog_failure_does_not_escape_on_ready(self):
        with self.assertLogs("src.bot.discord_bot", level="WARNING"), patch(
            "src.bot.discord_bot.setup_commands", AsyncMock()
        ) as setup, patch(
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
        #
        # The log capture is not decoration. These two tests provoke a genuine
        # registration failure, so the two CRITICAL records and their tracebacks
        # are correct -- and they were being written to the suite's stderr on
        # every run, where they read exactly like the false DAB-003 pair they
        # are not. Capturing them turns that noise into the assertion it should
        # always have been: a real failure must stay loud.
        with self.assertLogs("src.bot.discord_bot", level="CRITICAL") as captured, patch(
            "src.bot.discord_bot.setup_commands",
            side_effect=RuntimeError("registrar exploded"),
        ) as setup:
            await self.bot.on_ready()

        setup.assert_awaited_once()
        self.assertIn("registration FAILED", captured.output[0])
        self.bot._channel_settings_service.set_live_enabled(99, True)
        self.assertTrue(
            self.bot._is_live_mode_enabled(99),
            "live mode was disabled by a command registration failure",
        )

    async def test_user_preferences_survive_a_registrar_raising(self):
        with self.assertLogs("src.bot.discord_bot", level="CRITICAL"), patch(
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


class OnReadyReentryTest(OnReadyHarness):
    """DAB-003: a gateway reconnect must not be reported as a catastrophe.

    discord.py re-fires `on_ready` on every reconnect and `setup_commands` is
    not idempotent: the second call raises `CommandAlreadyRegistered` on `ping`,
    the first command it re-declares, leaving the tree intact at 22. `on_ready`
    then logged two CRITICAL records -- one with a traceback -- both saying the
    command tree was incomplete when it was complete.

    These run the *real* `setup_commands`, twice, because the defect only exists
    in the interaction between the two runs. A test with `setup_commands`
    patched cannot see it at all.
    """

    def _critical(self, records):
        return [r.getMessage() for r in records if r.levelno >= logging.CRITICAL]

    async def _run_on_ready(self):
        with self.assertLogs("src.bot.discord_bot", level="DEBUG") as captured:
            await self.bot.on_ready()
        return captured.records

    async def test_a_reconnect_does_not_report_an_intact_tree_as_a_failure(self):
        first = await self._run_on_ready()
        self.assertEqual(self._critical(first), [])
        registered = len(self.bot.tree.get_commands())
        self.assertEqual(registered, 22)

        second = await self._run_on_ready()

        self.assertEqual(
            self._critical(second), [],
            "a routine gateway reconnect still logs at the loudest level",
        )
        # The claim those records made, asserted directly: the tree is intact.
        self.assertEqual(len(self.bot.tree.get_commands()), registered)
        self.assertEqual(self.bot.tree.sync.await_count, 2)

    async def test_the_skip_is_recorded_and_only_on_the_reconnect(self):
        # The absence of the two false CRITICALs is what the tests above assert,
        # and absence is all they assert: the operator's log goes from two
        # alarming records to nothing, and "skipped, because this tree is
        # already registered" and "registered 22 commands" become
        # indistinguishable -- both end at the same "Synced 0 slash command(s)
        # globally". The positive record is what tells the two apart, so it is
        # a deliverable of DAB-003 rather than incidental output, and it needs
        # pinning from both sides: it must not appear on the first run either,
        # or it says the tree was already registered when it was just built.
        skip = (
            "Slash commands are already registered on this tree; skipping "
            "re-registration after the gateway reconnect."
        )

        first = await self._run_on_ready()
        self.assertNotIn(skip, [record.getMessage() for record in first])

        second = await self._run_on_ready()

        skipped = [record for record in second if record.getMessage() == skip]
        self.assertEqual(len(skipped), 1)
        self.assertEqual(skipped[0].levelno, logging.INFO)

    async def test_a_registration_failure_stays_critical_on_every_reconnect(self):
        # The flag must not latch on a run that did not finish, or a genuinely
        # broken tree goes quiet from the first reconnect onward.
        with patch(
            "src.bot.discord_bot.setup_commands",
            side_effect=RuntimeError("registrar exploded"),
        ):
            first = await self._run_on_ready()
            second = await self._run_on_ready()

        self.assertEqual(len(self._critical(first)), 2)
        self.assertEqual(len(self._critical(second)), 2)
        self.assertFalse(self.bot._slash_commands_registered)

    async def test_a_cancelled_registration_does_not_latch_the_flag(self):
        # `except Exception` cannot see a CancelledError -- it is a
        # BaseException, which is the trap DAB-019 hit in the live coordinator.
        # A registration cancelled mid-flight built nothing, so the flag must be
        # released or the tree is silently never registered again.
        with patch(
            "src.bot.discord_bot.setup_commands",
            side_effect=asyncio.CancelledError(),
        ):
            with self.assertRaises(asyncio.CancelledError):
                await self.bot.on_ready()

        self.assertFalse(self.bot._slash_commands_registered)
        self.assertEqual(len(self.bot.tree.get_commands()), 0)

        # And the next on_ready really does build the tree.
        await self._run_on_ready()
        self.assertEqual(len(self.bot.tree.get_commands()), 22)

    async def test_a_transient_fault_on_reconnect_cannot_shrink_a_working_tree(self):
        # Why the fix is not `tree.clear_commands()` + rebuild, which is the
        # other obvious way to make on_ready idempotent. Measured on that
        # design: a registrar raising on the second run takes the tree from 22
        # commands to 6, and `tree.sync()` is a full-replace PUT, so the other
        # 16 are deleted from Discord globally by a fault the current code
        # survives untouched.
        await self._run_on_ready()
        self.assertEqual(len(self.bot.tree.get_commands()), 22)

        with patch(
            "src.bot.commands.register_feature_commands",
            side_effect=RuntimeError("transient fault on reconnect"),
        ):
            await self._run_on_ready()

        self.assertEqual(len(self.bot.tree.get_commands()), 22)

    async def test_a_partially_registered_tree_is_not_reported_as_healthy(self):
        # The reason this is a flag rather than `bool(tree.get_commands())`:
        # register_ping_command runs first, so a registrar failing after it
        # leaves a NON-EMPTY, genuinely incomplete tree. Seeding from the tree
        # would call that healthy from the first reconnect.
        with patch(
            "src.bot.commands.register_feature_commands",
            side_effect=RuntimeError("boom"),
        ):
            first = await self._run_on_ready()
            partial = len(self.bot.tree.get_commands())
            second = await self._run_on_ready()

        self.assertGreater(partial, 0, "the tree must be non-empty for this to bite")
        self.assertLess(partial, 22)
        self.assertEqual(len(self._critical(first)), 2)
        self.assertEqual(
            len(self._critical(second)), 2,
            "an incomplete tree went quiet on reconnect",
        )


class ReportWebStatusTest(OnReadyHarness):
    """DAB-144: a refusal must not read as an operator having switched it off.

    `on_ready`'s line used to be two-state, `Active` or `Disabled`, so a
    loopback refusal -- a security decision the operator did not make and does
    need to act on -- was rendered in the same word as "you turned this off".
    The three-state wording is the whole of the answer to the audit's objection
    that a check inside `start()` "degrades to a log line": it does, so the log
    line has to say which of the three things happened.
    `get_service_health_status` carries the identical branch and reaches an
    operator through /config.

    It went untested on the grounds that it is only a log string. It is also
    the only signal the operator gets, and each of the three states below is
    produced by its real cause: a server that starts and serves, a server that
    refuses 0.0.0.0, and the feature switched off.
    """

    def _web_server(self, host):
        service = ReportService(str(Path(self._dir.name) / "reports.db"))
        # Port 0: the harness nulls the bot's own server precisely because the
        # configured 8080 would be held for the rest of the suite.
        server = ReportWebServer(service, host=host, port=0)
        self.bot.report_web_server = server
        self.addAsyncCleanup(server.stop)
        return server

    async def _on_ready_status(self):
        """`on_ready`'s rendered log records, and the /config health mapping.

        Captured at `src` rather than at `src.bot.discord_bot`, because the
        pair the operator reads is split across two of them: the status line is
        the bot's, and the refusal that explains it is the web server's.
        """

        with self.assertLogs("src", "INFO") as captured, patch(
            "src.bot.discord_bot.setup_commands", AsyncMock()
        ):
            await self.bot.on_ready()
        health = await self.bot.get_service_health_status()
        return [record.getMessage() for record in captured.records], health

    async def test_a_server_that_starts_is_reported_as_active(self):
        server = self._web_server("127.0.0.1")

        lines, health = await self._on_ready_status()

        # "Active" has to mean the page is up, not that an attribute is set --
        # and since on_ready is what starts it, this is also the proof that it
        # did.
        async with aiohttp.ClientSession() as session:
            async with session.get(server.url) as response:
                self.assertEqual(response.status, 200)
        self.assertIn("  - Report Web UI: Active", lines)
        self.assertEqual(health["report_web_ui"], "Available")

    async def test_a_refused_bind_is_reported_as_unavailable_not_disabled(self):
        # The state that had no word of its own before DAB-144:
        # `reports.web_enabled` is true and the page is not there, which is
        # neither "Active" nor a choice anybody made.
        self._web_server("0.0.0.0")

        lines, health = await self._on_ready_status()

        self.assertIn("  - Report Web UI: Unavailable", lines)
        self.assertEqual(health["report_web_ui"], "Unavailable")
        # And the reason is in the same startup log, where an operator reading
        # "Unavailable" will look for it.
        self.assertTrue(
            [line for line in lines if "NOT started" in line and "loopback" in line],
            "the status line said Unavailable without saying why",
        )

    async def test_the_feature_switched_off_is_reported_as_disabled(self):
        # The harness has already nulled the server; this is the operator
        # having set reports.web_enabled to false, and it keeps its own word.
        self.bot.report_web_server = None
        self.bot.config.report_web_enabled = False

        lines, health = await self._on_ready_status()

        self.assertIn("  - Report Web UI: Disabled", lines)
        self.assertEqual(health["report_web_ui"], "Disabled")


if __name__ == "__main__":
    unittest.main()
