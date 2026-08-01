"""Who can run the gated top-level commands, asserted at the enforcement boundary.

Three things are checked for every gated command, and all three are needed:

1. **The serialised payload**, read from ``to_dict()`` rather than from the
   Python attribute. DAB-141 is the reason: a fix set ``default_permissions`` on
   a *subcommand*, discord.py silently dropped it from the payload Discord
   actually receives, and the ticket's own acceptance test passed on a gate that
   protected nothing. A local attribute is not evidence.
2. **An executed refusal** -- the callback is really invoked with an
   unprivileged caller, and the side effect is asserted *not* to have happened.
   "It replied with an error" is not the same as "it did not do the thing".
3. **An executed success** -- the callback is invoked with a caller who legitimately
   holds the permission, and the side effect is asserted to *have* happened.
   A gate can fail in two directions, and locking out the people who are
   supposed to use a feature is an outage on a single-operator bot. Every gate
   here names who should still get through.

``dm_permission`` carries as much weight as the permission bits. Discord does not
evaluate ``default_member_permissions`` in a DM at all, so a command with
permission 8 and ``dm_permission: true`` is simply open -- which is how
``/clear-cache`` was reachable by a caller with no permissions anywhere.
"""

import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import discord
from discord import app_commands
from discord.ext import commands

from src.bot.commands import setup_commands
from src.config import BotConfig
from src.services.channel_settings_service import ChannelSettingsService


# Permission bit values discord.py serialises for the gates used here.
ADMINISTRATOR = 8
MANAGE_GUILD = 32
MANAGE_CHANNELS = 16


def interaction(*, permissions=None, in_guild=True, channel_id=4242):
    """A fake interaction whose guild_permissions carry only what is passed."""
    return SimpleNamespace(
        guild=(SimpleNamespace(id=3, get_member=Mock(return_value=None)) if in_guild else None),
        guild_id=3 if in_guild else None,
        channel_id=channel_id,
        user=SimpleNamespace(
            id=99,
            guild_permissions=SimpleNamespace(
                **{
                    "administrator": False,
                    "manage_guild": False,
                    "manage_channels": False,
                    **(permissions or {}),
                }
            ),
        ),
        response=SimpleNamespace(send_message=AsyncMock(), defer=AsyncMock()),
        followup=SimpleNamespace(send=AsyncMock()),
    )


class CommandAuthorizationTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self._dir = tempfile.TemporaryDirectory()
        self.addCleanup(self._dir.cleanup)
        self.db_path = str(Path(self._dir.name) / "settings.db")
        self.config = BotConfig(
            token_db_path=self.db_path,
            rag_database_path=str(Path(self._dir.name) / "rag.db"),
            available_models=[{"name": "A", "value": "model-a"}],
            valid_models=["model-a"],
            valid_languages=["english"],
            model_display_names={"model-a": "A"},
            model_descriptions={"model-a": "First"},
        )
        self.bot = commands.Bot(command_prefix="!", intents=discord.Intents.none())
        self.bot.config = self.config
        self.bot.image_processing_service = None
        self.performance_logger = SimpleNamespace(clear_cache=Mock())
        await setup_commands(
            self.bot,
            self.config,
            SimpleNamespace(get_current_model=Mock(return_value="model-a")),
            self.performance_logger,
            None,
        )
        self.addAsyncCleanup(self.bot.close)

    def _command(self, name):
        return next(c for c in self.bot.tree.get_commands() if c.name == name)

    def _payload(self, name):
        return self._command(name).to_dict(self.bot.tree)

    # ---------------------------------------------------------------- payloads

    def test_the_serialised_payloads_carry_the_gates_discord_will_enforce(self):
        expected = {
            # name            default_member_permissions   dm_permission
            "dev": (str(ADMINISTRATOR), False),
            "clear-cache": (str(ADMINISTRATOR), False),
            "live": (str(MANAGE_CHANNELS), False),
            "rag": (str(MANAGE_GUILD), False),
        }
        for name, (permission, dm_allowed) in expected.items():
            with self.subTest(command=name):
                payload = self._payload(name)
                self.assertEqual(
                    str(payload.get("default_member_permissions")),
                    permission,
                    f"/{name} does not serialise the permission it claims",
                )
                self.assertIs(
                    payload.get("dm_permission"),
                    dm_allowed,
                    f"/{name} is reachable in DMs, where Discord does not "
                    f"evaluate default_member_permissions at all",
                )

    # -------------------------------------------------------------- /live gate

    async def test_live_refuses_an_ordinary_member_and_writes_nothing(self):
        """/live is the largest remaining spend lever.

        Turning it on makes every message in the channel a billed Gemini call
        with no mention required. The assertion that matters is the absence of
        the database row, not the presence of an error message.
        """
        call = interaction()

        await self._command("live").callback(call, True)

        call.response.send_message.assert_awaited_once()
        self.assertIn("Manage Channels", call.response.send_message.await_args.args[0])
        settings = ChannelSettingsService(
            db_path=self.db_path, personalities=self.config.personalities
        )
        self.assertFalse(
            settings.get_live_enabled(4242),
            "an unprivileged caller enabled live mode for the channel",
        )

    async def test_live_still_works_for_a_channel_moderator(self):
        """Who should still be able to run this: anyone with Manage Channels.

        The bar is deliberately Manage Channels rather than Administrator.
        /live is a normal-use feature that changes how the bot behaves in one
        channel, which is what Manage Channels means; requiring Administrator
        would leave a routine feature usable only by the server owner.
        """
        call = interaction(permissions={"manage_channels": True})

        await self._command("live").callback(call, True)

        settings = ChannelSettingsService(
            db_path=self.db_path, personalities=self.config.personalities
        )
        self.assertTrue(
            settings.get_live_enabled(4242),
            "a channel moderator was locked out of live mode",
        )

    async def test_live_refuses_outside_a_guild(self):
        call = interaction(permissions={"manage_channels": True}, in_guild=False)

        await self._command("live").callback(call, True)

        settings = ChannelSettingsService(
            db_path=self.db_path, personalities=self.config.personalities
        )
        self.assertFalse(settings.get_live_enabled(4242))

    # ------------------------------------------------------- /clear-cache gate

    async def test_clear_cache_refuses_a_dm_caller_with_no_permissions(self):
        """The DM hole. Payload permission 8, and previously wide open anyway.

        Discord does not apply default_member_permissions outside a guild, so
        before guild_only this exact call cleared the cache.
        """
        call = interaction(in_guild=False)

        await self._command("clear-cache").callback(call)

        self.performance_logger.clear_cache.assert_not_called()
        call.response.send_message.assert_awaited_once()
        self.assertIn("Administrator", call.response.send_message.await_args.args[0])

    async def test_clear_cache_refuses_an_ordinary_guild_member(self):
        call = interaction()

        await self._command("clear-cache").callback(call)

        self.performance_logger.clear_cache.assert_not_called()

    async def test_clear_cache_still_works_for_an_administrator(self):
        """Who should still be able to run this: guild administrators."""
        call = interaction(permissions={"administrator": True})

        await self._command("clear-cache").callback(call)

        self.performance_logger.clear_cache.assert_called_once()


if __name__ == "__main__":
    unittest.main()
