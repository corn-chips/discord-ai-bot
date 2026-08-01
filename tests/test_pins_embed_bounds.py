"""Guards for PPR-06 / DAB-163: `/pins` must fit inside every embed ceiling.

`MAX_PINS_PER_CHANNEL` is 25 and a Discord embed holds 25 fields, so the pin cap
reads as though it closes the field limit by construction. It does not. The
binding ceiling is the embed's **6,000-character total**, and `/pins` reaches it
first: measured with discord.py 2.7.1's own `len(embed)`, 25 pins with
200-character previews come to 5,996 characters at 8-character display names and
**6,046 at nine** -- so most channels at the permitted cap raise
`HTTPException: Embed size exceeds maximum size of 6000`. The delete buttons
that would take the channel back under the cap live on the message that will not
send, and `/pins` is the only delete UI.

Four further ceilings have to hold at the same time, and none of them is
enforced by discord.py -- `add_field` accepts 30 fields with 300-character names
and 2,000-character values and `to_dict()` passes them all through:

- **25 fields.** More than 25 rows in a channel is reachable: every pin migrated
  before round 2 Phase 3 bypassed `add_pin`'s caps, and 60 *short* pins never
  reach 6,000 characters at all, so a total-length check alone lets 60 fields out.
- **256 per field name, 1,024 per field value.** `author_name` and `pinned_by`
  are unconstrained TEXT; only `content` goes through `_sanitise_pin_content`.
- **25 view children.** `discord.ui.View` raises `ValueError` on the 26th, while
  building the reply, so the interaction is never acknowledged -- strictly worse
  than a 400.

Every assertion below is made against the `discord.Embed` and `discord.ui.View`
objects the real callback hands to `interaction.response.send_message`, measured
with `len(embed)`. Before this test existed, a `/pins` that showed one pin and
handed out zero delete buttons passed the whole suite.
"""

import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import discord
from discord.ext import commands

from src.bot.commands import setup_commands
from src.config import BotConfig
from src.constants import (
    DISCORD_EMBED_FIELD_COUNT_LIMIT,
    DISCORD_EMBED_FIELD_NAME_LIMIT,
    DISCORD_EMBED_FIELD_VALUE_LIMIT,
    DISCORD_EMBED_TOTAL_LIMIT,
)
from src.services.pin_service import MAX_PINS_PER_CHANNEL, PinService

CHANNEL = 4242


def interaction():
    return SimpleNamespace(
        guild=SimpleNamespace(id=3, get_member=Mock(return_value=None)),
        guild_id=3,
        channel_id=CHANNEL,
        user=SimpleNamespace(id=99, display_name="ada"),
        response=SimpleNamespace(send_message=AsyncMock(), defer=AsyncMock()),
        followup=SimpleNamespace(send=AsyncMock()),
    )


class PinsEmbedBoundsTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self._dir = tempfile.TemporaryDirectory()
        self.addCleanup(self._dir.cleanup)
        self.rag_path = str(Path(self._dir.name) / "rag.db")
        self.config = BotConfig(
            token_db_path=str(Path(self._dir.name) / "tokens.db"),
            rag_database_path=self.rag_path,
            available_models=[{"name": "A", "value": "model-a"}],
            valid_models=["model-a"],
            valid_languages=["english"],
            model_display_names={"model-a": "A"},
            model_descriptions={"model-a": "First"},
        )
        self.pin_service = PinService(db_path=self.rag_path)

        bot = commands.Bot(command_prefix="!", intents=discord.Intents.default())
        bot.config = self.config
        bot.image_processing_service = None
        bot._pin_service = self.pin_service
        await setup_commands(bot, self.config, object(), None, None)
        self.pins_callback = next(
            command for command in bot.tree.get_commands() if command.name == "pins"
        ).callback

    # ── helpers ──────────────────────────────────────────────────────

    def _seed(self, count, *, name_length=9, content_length=1_500):
        """Write rows straight into the table.

        `add_pin` would refuse past the caps, and the states that break `/pins`
        are exactly the ones the caps do not stop: a channel at the permitted
        25, and a channel migrated over the cap before round 2 Phase 3.
        """
        import sqlite3

        who = "n" * name_length
        with sqlite3.connect(self.rag_path) as conn:
            conn.executemany(
                "INSERT INTO pinned_messages"
                " (channel_id, content, author_name, pinned_by, pinned_at)"
                " VALUES (?, ?, ?, ?, ?)",
                [
                    (CHANNEL, f"pin {i} " + "x" * content_length, who, who,
                     f"2026-01-01T00:{i % 60:02d}:{i // 60:02d}")
                    for i in range(count)
                ],
            )

    async def _render(self):
        call = interaction()
        await self.pins_callback(call)
        call.response.send_message.assert_awaited_once()
        kwargs = call.response.send_message.await_args.kwargs
        return kwargs["embed"], kwargs["view"]

    def _assert_within_every_ceiling(self, embed, view, *, at_least=1):
        # The lower bound comes first, and it is not decoration. Every ceiling
        # below is satisfied by a `/pins` that lists one pin, or none -- halving
        # DISCORD_EMBED_TOTAL_LIMIT in a scratch copy left the whole suite green
        # while the command showed 12 of 25. Hiding a pin the user could have
        # seen is a smaller failure than a 400, but it is still a failure, and
        # the only way back under the cap is to see and delete pins.
        self.assertGreaterEqual(
            len(embed.fields), at_least,
            f"only {len(embed.fields)} of the pins were listed",
        )
        self.assertLessEqual(
            len(embed), DISCORD_EMBED_TOTAL_LIMIT,
            f"embed is {len(embed)} characters; Discord rejects the message",
        )
        self.assertLessEqual(len(embed.fields), DISCORD_EMBED_FIELD_COUNT_LIMIT)
        for field in embed.fields:
            self.assertLessEqual(len(field.name or ""), DISCORD_EMBED_FIELD_NAME_LIMIT)
            self.assertLessEqual(len(field.value or ""), DISCORD_EMBED_FIELD_VALUE_LIMIT)
        # Every listed pin must be deletable, or the channel cannot get back
        # under the cap -- which is the whole reason the overflow is not
        # self-healing.
        self.assertEqual(len(view.children), len(embed.fields))

    # ── the ceilings ─────────────────────────────────────────────────

    async def test_a_full_channel_of_long_named_pins_still_fits(self):
        # The canonical failing case: 25 pins, 9-character names -> 6,046.
        self._seed(MAX_PINS_PER_CHANNEL, name_length=9)

        embed, view = await self._render()

        self._assert_within_every_ceiling(embed, view)

    async def test_a_full_channel_that_only_just_fits_still_lists_all_of_it(self):
        # 25 pins at eight-character names measure 5,996 and used to render.
        # Reserving the longer "Showing N of M ..." footer unconditionally would
        # hide one of them to buy room for a footer that is never set.
        self._seed(MAX_PINS_PER_CHANNEL, name_length=8)

        embed, view = await self._render()

        self._assert_within_every_ceiling(
            embed, view, at_least=MAX_PINS_PER_CHANNEL
        )
        # No footer at all when nothing is hidden: `Embed.__len__` counts one,
        # the description already carries the count, and at this exact shape a
        # footer of any length is the difference between 25 pins and 24.
        self.assertIsNone(embed.footer.text)

    async def test_the_longest_display_name_discord_allows_still_fits(self):
        self._seed(MAX_PINS_PER_CHANNEL, name_length=32)

        embed, view = await self._render()

        self._assert_within_every_ceiling(embed, view)

    async def test_a_channel_migrated_over_the_pin_cap_still_fits(self):
        # 60 short pins never reach 6,000 characters, so a length check alone
        # emits 60 fields and then raises ValueError building a 60-button view.
        self._seed(60, name_length=4, content_length=20)

        embed, view = await self._render()

        self._assert_within_every_ceiling(embed, view)
        self.assertEqual(len(embed.fields), DISCORD_EMBED_FIELD_COUNT_LIMIT)

    async def test_no_pin_length_near_the_ceiling_produces_an_over_limit_embed(self):
        # A single hand-picked shape cannot see the footer reserve. Each field
        # costs ~240 characters, so a budget that forgets to reserve the footer
        # still happens to fit for most inputs -- the loop stops one whole field
        # short of the ceiling and the ~47-character footer disappears into the
        # slack. It only shows up where the last admitted field lands within a
        # footer's width of the limit.
        #
        # Swept against a build that drops the reserve: 53 of 5,985 shapes go
        # over, up to 6,046, while the shipped build stays inside on every one.
        # This band holds nine of them.
        import sqlite3

        for count in (MAX_PINS_PER_CHANNEL, MAX_PINS_PER_CHANNEL + 1):
            for content_length in range(150, 211):
                with self.subTest(count=count, content_length=content_length):
                    with sqlite3.connect(self.rag_path) as conn:
                        conn.execute("DELETE FROM pinned_messages")
                    self._seed(count, name_length=32, content_length=content_length)
                    embed, view = await self._render()
                    self._assert_within_every_ceiling(embed, view)

    async def test_unbounded_author_names_are_clamped_per_field(self):
        # author_name and pinned_by are unconstrained TEXT and never pass
        # through _sanitise_pin_content.
        self._seed(60, name_length=900, content_length=20)

        embed, view = await self._render()

        self._assert_within_every_ceiling(embed, view)

    async def test_astral_characters_do_not_slip_past_the_budget(self):
        self._seed(MAX_PINS_PER_CHANNEL, name_length=8)
        import sqlite3

        with sqlite3.connect(self.rag_path) as conn:
            conn.execute(
                "UPDATE pinned_messages SET content = ?", ("\U0001F600" * 400,)
            )

        embed, view = await self._render()

        self._assert_within_every_ceiling(embed, view)
        utf16 = sum(
            len(text) + sum(1 for c in text if ord(c) > 0xFFFF)
            for text in [embed.title or "", embed.description or "",
                         (embed.footer.text or "") if embed.footer else ""]
            + [f.name or "" for f in embed.fields]
            + [f.value or "" for f in embed.fields]
        )
        self.assertLessEqual(utf16, DISCORD_EMBED_TOTAL_LIMIT)

    # ── what the user is told, and what still works ──────────────────

    async def test_a_truncated_listing_says_so_and_says_what_to_do(self):
        self._seed(60, name_length=4, content_length=20)

        embed, _view = await self._render()

        self.assertIsNotNone(embed.footer.text)
        self.assertIn("of 60", embed.footer.text)
        self.assertIn("delete", embed.footer.text.lower())
        # The description still reports the true total, so "25 shown" is never
        # mistaken for "25 exist".
        self.assertIn("60 pinned message(s)", embed.description)

    async def test_a_small_channel_lists_every_pin_with_a_button_each(self):
        self._seed(3, name_length=9)

        embed, view = await self._render()

        self._assert_within_every_ceiling(embed, view)
        self.assertEqual(len(embed.fields), 3)
        self.assertEqual(len(view.children), 3)
        self.assertIsNone(embed.footer.text)

    async def test_an_empty_channel_is_unchanged(self):
        call = interaction()

        await self.pins_callback(call)

        message = call.response.send_message.await_args.args[0]
        self.assertIn("No pinned memories", message)


if __name__ == "__main__":
    unittest.main()
