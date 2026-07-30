"""Guards for DAB-148: /config mutations are process-global.

`set_force_search`, `set_model` and `set_thinking_level` take no guild argument,
there is one GeminiClient for the process, and `bot.image_generation_enabled`
and the root logger's level are equally shared. So one member of one guild
changes behaviour for every guild the bot serves. `/config deepsearch` forces a
web search on every query for everyone until restart, which is an unowned spend
lever; `/config debug` puts the root logger at DEBUG process-wide.

Guarded at runtime rather than in the payload, and that is a deliberate
departure from the /rag pattern. discord.py drops a subcommand's
default_permissions from to_dict() (DAB-141), so the only payload-level option
is to gate the whole group -- which would take `/config info` away from ordinary
members for no security benefit. `test_config_info_stays_available_to_members`
pins that exemption so a later "tidy-up" cannot quietly remove it.
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

MUTATING = ("model", "thinking", "deepsearch", "image-generation", "debug")


def member(*, manage_guild=False, administrator=False, in_guild=True):
    return SimpleNamespace(
        guild=(
            SimpleNamespace(id=3, get_member=Mock(return_value=None))
            if in_guild
            else None
        ),
        user=SimpleNamespace(
            id=99,
            guild_permissions=SimpleNamespace(
                manage_guild=manage_guild, administrator=administrator
            ),
        ),
        response=SimpleNamespace(send_message=AsyncMock(), defer=AsyncMock()),
        followup=SimpleNamespace(send=AsyncMock()),
    )


class ConfigCommandGateTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self._dir = tempfile.TemporaryDirectory()
        self.addCleanup(self._dir.cleanup)
        self.config = BotConfig(
            token_db_path=str(Path(self._dir.name) / "a.db"),
            rag_database_path=str(Path(self._dir.name) / "b.db"),
            available_models=[{"name": "A", "value": "model-a"}],
            valid_models=["model-a"],
            valid_languages=["english"],
            model_display_names={"model-a": "A"},
            model_descriptions={"model-a": "First"},
        )
        self.bot = commands.Bot(command_prefix="!", intents=discord.Intents.none())
        self.bot.config = self.config
        self.bot.image_processing_service = None
        self.gemini = SimpleNamespace(
            get_current_model=Mock(return_value="model-a"),
            set_model=Mock(return_value=True),
            get_thinking_level=Mock(return_value="medium"),
            set_thinking_level=Mock(return_value=True),
            set_force_search=Mock(),
        )
        await setup_commands(self.bot, self.config, self.gemini, object(), None)
        self.addAsyncCleanup(self.bot.close)
        self.group = next(
            c for c in self.bot.tree.get_commands() if c.name == "config"
        )

    def _sub(self, name):
        return next(c for c in self.group.commands if c.name == name)

    async def _call(self, name, interaction):
        sub = self._sub(name)
        if name == "model":
            await sub.callback(interaction, app_commands.Choice(name="A", value="model-a"))
        elif name == "thinking":
            await sub.callback(interaction, app_commands.Choice(name="Med", value="medium"))
        else:
            await sub.callback(interaction, True)

    async def test_an_ordinary_member_cannot_change_global_configuration(self):
        for name in MUTATING:
            with self.subTest(subcommand=name):
                interaction = member()
                await self._call(name, interaction)

                interaction.response.send_message.assert_awaited_once()
                text = interaction.response.send_message.await_args.args[0]
                self.assertIn("Manage Server", text)

        # None of the global levers moved.
        self.gemini.set_force_search.assert_not_called()
        self.gemini.set_model.assert_not_called()
        self.gemini.set_thinking_level.assert_not_called()

    async def test_a_privileged_member_can_still_change_configuration(self):
        interaction = member(manage_guild=True)

        await self._call("deepsearch", interaction)

        self.gemini.set_force_search.assert_called_once_with(True)

    async def test_the_guard_refuses_outside_a_guild(self):
        interaction = member(manage_guild=True, in_guild=False)

        await self._call("deepsearch", interaction)

        self.gemini.set_force_search.assert_not_called()

    async def test_config_info_stays_available_to_members(self):
        # Deliberate exemption. /config info only reports the current model,
        # thinking level and a few limits, and gating the group to protect the
        # mutators would take it away for no benefit.
        #
        # The assertion is that the permission guard does not fire. Rendering
        # the embed needs a connected client (bot.user), which this harness has
        # no business faking, so an AttributeError from further down the body is
        # tolerated -- it can only be reached once the guard has let us past.
        interaction = member()

        try:
            await self._sub("info").callback(interaction)
        except AttributeError:
            pass

        refusals = [
            call
            for call in interaction.response.send_message.await_args_list
            if call.args and "Manage Server" in str(call.args[0])
        ]
        self.assertEqual(refusals, [], "/config info was gated; it should stay open")


if __name__ == "__main__":
    from discord import app_commands  # noqa: F401

    unittest.main()
