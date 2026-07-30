"""Guards for DAB-213: per-user ceiling on the expensive commands."""

import asyncio
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock

from src.services.rate_limiter import TextRateLimiter
from src.bot.command_modules.research import _expensive_command_allowed


def make_interaction(user_id=1):
    return SimpleNamespace(
        user=SimpleNamespace(id=user_id),
        response=SimpleNamespace(send_message=AsyncMock()),
    )


class ExpensiveCommandCooldownTest(unittest.IsolatedAsyncioTestCase):
    """/deepresearch and /summarize were governed only by the general text
    limit, which permits 60 invocations an hour. /deepresearch makes two model
    calls, one on the high-complexity model; /summarize feeds up to
    channel_history_limit messages into a single prompt with no per-message
    truncation. Neither is meaningfully bounded by a 60/hour ceiling.
    """

    def _bot(self, per_minute=1, per_hour=4):
        return SimpleNamespace(
            expensive_command_limiter=TextRateLimiter(
                per_minute=per_minute, per_hour=per_hour
            )
        )

    async def test_the_first_invocation_is_allowed(self):
        bot = self._bot()
        self.assertTrue(await _expensive_command_allowed(bot, make_interaction()))

    async def test_the_hourly_ceiling_is_enforced_per_user(self):
        bot = self._bot(per_minute=10, per_hour=4)

        allowed = [
            await _expensive_command_allowed(bot, make_interaction(user_id=7))
            for _ in range(6)
        ]

        self.assertEqual(allowed, [True, True, True, True, False, False])

    async def test_a_refused_caller_is_told_and_the_command_does_not_run(self):
        bot = self._bot(per_minute=1, per_hour=4)
        interaction = make_interaction()
        await _expensive_command_allowed(bot, interaction)

        second = make_interaction()
        allowed = await _expensive_command_allowed(bot, second)

        self.assertFalse(allowed)
        second.response.send_message.assert_awaited_once()
        self.assertTrue(second.response.send_message.await_args.kwargs["ephemeral"])

    async def test_one_users_spending_does_not_block_another(self):
        bot = self._bot(per_minute=1, per_hour=4)
        await _expensive_command_allowed(bot, make_interaction(user_id=1))

        self.assertTrue(
            await _expensive_command_allowed(bot, make_interaction(user_id=2))
        )

    async def test_a_bot_without_the_limiter_is_not_broken_by_the_guard(self):
        # Test doubles construct partial bots; the guard must not hard-require
        # the attribute.
        self.assertTrue(
            await _expensive_command_allowed(SimpleNamespace(), make_interaction())
        )


if __name__ == "__main__":
    unittest.main()
