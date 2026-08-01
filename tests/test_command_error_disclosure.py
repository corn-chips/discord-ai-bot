"""What a slash command discloses when it fails, and where it discloses it.

Three sites, two defects, one class: a reply that hands the user something the
bot knows about the machine it runs on.

`/deepresearch` and `/summarize` (PPR-09) interpolated `str(exc)` into a
**public** channel message under a bare `except Exception`, so every exception
type reached it. Measured before the fix, by raising real exceptions through
the real callbacks: absolute filesystem paths including the OS username, SQL
and table names, and a 343-character provider error posted in full -- the last
being longer than the 200-character cap `error_manager` applies to the same
class of text, so these two sites were strictly more permissive than the path
DAB-153 hardened.

`/rag status` (PPR-05) rendered the resolved absolute database path into an
embed field. The finding was filed against the `str(exc)` in the field above
it, and that turned out to be the wrong target: none of the three realistic
failure modes carries a path, the embed is ephemeral, and `/rag` is gated on
Manage Server -- so the exception text goes to exactly the administrator it is
for. The path in the next field is the actual disclosure, and it was in the
healthy embed too, where there is no exception at all.

These assert the arguments that reach Discord -- the message text, the embed
fields, and `ephemeral` -- because that is what the user sees.
"""

import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import discord
from discord import app_commands
from discord.ext import commands

from src.bot.commands import setup_commands
from src.config import BotConfig

# Stand-ins for the two things a reply must never carry. The path is built at
# runtime from a real temporary directory, so this file contains no absolute
# path of its own.
SECRET_TABLE = "message_index"


class CommandErrorDisclosureTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self._dir = tempfile.TemporaryDirectory()
        self.addCleanup(self._dir.cleanup)
        self.secret_path = str(Path(self._dir.name).resolve() / "token_usage.db")
        self.config = BotConfig(
            token_db_path=str(Path(self._dir.name) / "commands.db"),
            rag_database_path=str(Path(self._dir.name) / "message_rag.db"),
            available_models=[{"name": "Model A", "value": "model-a"}],
            valid_models=["model-a"],
            valid_languages=["english", "auto"],
        )
        self._bots = []

    async def asyncTearDown(self):
        for bot in self._bots:
            await bot.close()

    async def _register(self, gemini_client=None, **attrs):
        bot = commands.Bot(command_prefix="!", intents=discord.Intents.none())
        bot.config = self.config
        bot.image_processing_service = None
        for name, value in attrs.items():
            setattr(bot, name, value)
        self._bots.append(bot)
        # The registrars close over the gemini_client *parameter*, not
        # bot.gemini_client, so a fake set only as an attribute is never
        # consulted -- and /deepresearch then dies on get_current_model long
        # before the failure a test injected, which makes the test green for
        # the wrong reason. Passed in properly here.
        await setup_commands(
            bot, self.config, gemini_client or object(), object(), None
        )
        return bot

    @staticmethod
    def _command(bot, path):
        parts = path.split(" ")
        command = discord.utils.get(bot.tree.get_commands(), name=parts[0])
        for part in parts[1:]:
            command = discord.utils.get(command.commands, name=part)
        return command

    def _interaction(self):
        return SimpleNamespace(
            channel_id=20,
            channel=SimpleNamespace(history=AsyncMock()),
            guild=SimpleNamespace(id=3),
            guild_id=3,
            user=SimpleNamespace(
                id=1,
                display_name="Someone",
                guild_permissions=SimpleNamespace(
                    manage_guild=True, administrator=True, manage_channels=True
                ),
            ),
            response=SimpleNamespace(send_message=AsyncMock(), defer=AsyncMock()),
            followup=SimpleNamespace(send=AsyncMock()),
        )

    def _assert_no_disclosure(self, call):
        """The reply carries neither a filesystem path nor an internal name."""

        text = " ".join(str(arg) for arg in call.args)
        text += " ".join(f"{k}={v}" for k, v in call.kwargs.items() if k != "ephemeral")
        self.assertNotIn(self.secret_path, text)
        self.assertNotIn(self._dir.name, text)
        self.assertNotIn(SECRET_TABLE, text)
        self.assertNotIn("Errno", text)

    # ── PPR-09 ────────────────────────────────────────────────────────

    async def _drive_deepresearch(self, raiser):
        """Run /deepresearch to the point where `raiser` fires, and return the
        final reply.

        The intermediate progress messages are legitimate and go to the same
        followup, so the reply under test is the last one.
        """

        bot = await self._register(
            gemini_client=SimpleNamespace(
                get_current_model=lambda: "model-a",
                generate_response=raiser,
            ),
            token_tracker=None,
            hybrid_context_retriever=None,
            message_index_service=None,
        )
        interaction = self._interaction()

        with self.assertLogs("src.bot.commands", "ERROR") as logs:
            await self._command(bot, "deepresearch").callback(interaction, "a topic")

        # The failure has to be the one the test injected. Without this the
        # command can die earlier -- on an incomplete double, say -- and the
        # sanitised reply is then evidence of nothing.
        self.assertIn(self.marker, "".join(logs.output))
        return interaction.followup.send.await_args

    async def test_deepresearch_does_not_post_the_exception_into_the_channel(self):
        self.marker = self.secret_path

        async def raiser(*args, **kwargs):
            raise FileNotFoundError(2, "No such file or directory", self.secret_path)

        call = await self._drive_deepresearch(raiser)

        self._assert_no_disclosure(call)
        # Ephemeral as well as sanitised: the person who ran the command is the
        # only one with any use for "it failed".
        self.assertIs(call.kwargs.get("ephemeral"), True)

    async def test_summarize_does_not_post_the_exception_into_the_channel(self):
        async def raiser(*args, **kwargs):
            raise RuntimeError(f"no such table: {SECRET_TABLE}")

        bot = await self._register(
            gemini_client=SimpleNamespace(generate_response=raiser),
            token_tracker=None,
            hybrid_context_retriever=None,
            message_index_service=None,
        )
        interaction = self._interaction()

        def history(*args, **kwargs):
            raise RuntimeError(f"no such table: {SECRET_TABLE}")

        interaction.channel = SimpleNamespace(history=history)

        with self.assertLogs("src.bot.commands", "ERROR") as logs:
            await self._command(bot, "summarize").callback(interaction)
        self.assertIn(SECRET_TABLE, "".join(logs.output))

        call = interaction.followup.send.await_args
        self._assert_no_disclosure(call)
        self.assertIs(call.kwargs.get("ephemeral"), True)

    async def test_a_long_provider_error_is_not_relayed_at_all(self):
        # error_manager caps echoed text at 200 characters; these two sites had
        # no cap, so a 343-character provider error went out in full.
        long_detail = "google.genai.errors.ClientError: 400 INVALID_ARGUMENT. " + ("x" * 320)
        self.marker = "INVALID_ARGUMENT"

        async def raiser(*args, **kwargs):
            raise RuntimeError(long_detail)

        call = await self._drive_deepresearch(raiser)

        text = " ".join(str(arg) for arg in call.args)
        self.assertNotIn("INVALID_ARGUMENT", text)
        self.assertLess(len(text), 200)

    # ── PPR-05 ────────────────────────────────────────────────────────

    def _status_payload(self, **overrides):
        payload = {
            "messages": 1,
            "embedded": 1,
            "pending_embeddings": 0,
            "failed_embeddings": 0,
            "skipped_embeddings": 0,
            "cached_vectors": 0,
            "vector_cache_bytes": 0,
            "fts_enabled": True,
            "embedding_model": "m",
            "embedding_dimensions": 8,
            "database_path": self.secret_path,
        }
        payload.update(overrides)
        return payload

    async def _rag_status_fields(self, **overrides):
        bot = await self._register(
            message_index_service=SimpleNamespace(
                get_status_async=AsyncMock(return_value=self._status_payload(**overrides))
            )
        )
        interaction = self._interaction()

        await self._command(bot, "rag status").callback(interaction)

        embed = interaction.followup.send.await_args.kwargs["embed"]
        return embed

    async def test_the_healthy_status_embed_does_not_carry_the_database_path(self):
        embed = await self._rag_status_fields()

        rendered = " ".join(f"{f.name} {f.value}" for f in embed.fields)
        self.assertNotIn(self.secret_path, rendered)
        self.assertNotIn(self._dir.name, rendered)

    async def test_the_degraded_status_embed_does_not_carry_the_database_path(self):
        embed = await self._rag_status_fields(
            error="DatabaseError: file is not a database"
        )

        rendered = " ".join(f"{f.name} {f.value}" for f in embed.fields)
        self.assertNotIn(self.secret_path, rendered)
        self.assertNotIn(self._dir.name, rendered)

    async def test_the_degraded_embed_still_tells_the_admin_what_broke(self):
        # The other direction. This embed is ephemeral and /rag is gated on
        # Manage Server, so the exception text reaches only the administrator
        # it is for -- removing it would cost real diagnostic value and close
        # nothing, which is why PPR-05's original target was the wrong one.
        embed = await self._rag_status_fields(
            error="DatabaseError: file is not a database"
        )

        rendered = " ".join(f"{f.name} {f.value}" for f in embed.fields)
        self.assertIn("file is not a database", rendered)


if __name__ == "__main__":
    unittest.main()
