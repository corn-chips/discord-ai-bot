"""Coverage for DiscordBot.on_message — the hottest path in the system.

Roughly 400 lines of gate, rate-limit and routing behaviour ran here with no
direct test. That is what let BUG-0002 hide: its regression test
(``test_request_model_precedence``) calls ``_resolve_request_preferences``
directly and never reaches ``_process_message_with_context``, where the bug
actually lived, so reinstating ``model_override = routed_model`` left the whole
suite green. ``test_router_complexity_never_hardens_into_a_model_override``
below asserts that contract at the caller instead;
``scripts/mutation_check.py M-BUG0002`` is the proof it works.

Written in the house idiom: unbound-method invocation against a
``SimpleNamespace`` collaborator bag, no ``object.__new__``.
"""

import logging
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

from src.bot.discord_bot import DiscordBot
from src.bot.enhanced_command_handler import CommandIntent, RoutingDecision


def make_message(
    message_id=1,
    content="<@7> hello",
    *,
    author_bot=False,
    guild=SimpleNamespace(id=3),
    channel_id=10,
):
    return SimpleNamespace(
        id=message_id,
        content=content,
        attachments=[],
        author=SimpleNamespace(id=1, bot=author_bot, display_name="Ada"),
        channel=SimpleNamespace(id=channel_id, name="general"),
        guild=guild,
        reference=None,
        reply=AsyncMock(),
    )


def make_bot(**overrides):
    """Assemble the minimum collaborator surface ``on_message`` touches."""
    defaults = {
        "config": SimpleNamespace(rag_enabled=True),
        "user": SimpleNamespace(id=7),
        "message_index_service": SimpleNamespace(
            index_discord_message_async=AsyncMock(return_value=True)
        ),
        "hybrid_context_retriever": SimpleNamespace(
            schedule_pending_embeddings=Mock()
        ),
        "_is_live_mode_enabled": Mock(return_value=False),
        "_enqueue_live_message": AsyncMock(),
        "is_bot_mentioned": Mock(return_value=True),
        "text_rate_limiter": SimpleNamespace(
            check_and_record=AsyncMock(return_value=(True, None))
        ),
        "enhanced_command_handler": None,
        "gemini_client": SimpleNamespace(
            get_model_for_complexity=Mock(return_value="complexity-model")
        ),
        "_extract_user_prompt": Mock(return_value="hello"),
        "_process_message_with_context": AsyncMock(),
        "error_manager": SimpleNamespace(
            create_error_context=Mock(),
            handle_discord_error=Mock(),
            send_error_response=AsyncMock(),
        ),
    }
    defaults.update(overrides)
    return SimpleNamespace(**defaults)


class OnMessageGateTest(unittest.IsolatedAsyncioTestCase):
    async def test_bot_authored_message_is_dropped_before_rag_indexing(self):
        bot = make_bot()

        await DiscordBot.on_message(bot, make_message(author_bot=True))

        bot.message_index_service.index_discord_message_async.assert_not_awaited()
        bot._process_message_with_context.assert_not_awaited()

    async def test_live_mode_channel_bypasses_mention_gate_and_router(self):
        handler = SimpleNamespace(handle_message=AsyncMock())
        bot = make_bot(
            _is_live_mode_enabled=Mock(return_value=True),
            is_bot_mentioned=Mock(return_value=False),
            enhanced_command_handler=handler,
        )
        message = make_message(content="no mention here")

        await DiscordBot.on_message(bot, message)

        bot._enqueue_live_message.assert_awaited_once_with(message)
        bot.is_bot_mentioned.assert_not_called()
        handler.handle_message.assert_not_awaited()
        bot._process_message_with_context.assert_not_awaited()

    async def test_live_mode_requires_a_guild_so_dms_still_use_the_mention_gate(self):
        bot = make_bot(_is_live_mode_enabled=Mock(return_value=True))

        await DiscordBot.on_message(bot, make_message(guild=None))

        bot._enqueue_live_message.assert_not_awaited()
        bot._process_message_with_context.assert_awaited_once()

    async def test_unmentioned_guild_message_is_indexed_but_not_answered(self):
        bot = make_bot(is_bot_mentioned=Mock(return_value=False))

        await DiscordBot.on_message(bot, make_message())

        bot.message_index_service.index_discord_message_async.assert_awaited_once()
        bot._process_message_with_context.assert_not_awaited()

    async def test_rag_indexing_failure_never_blocks_the_reply(self):
        bot = make_bot(
            message_index_service=SimpleNamespace(
                index_discord_message_async=AsyncMock(
                    side_effect=RuntimeError("index down")
                )
            )
        )

        with self.assertLogs("src.bot.discord_bot", level=logging.DEBUG):
            await DiscordBot.on_message(bot, make_message())

        bot._process_message_with_context.assert_awaited_once()

    async def test_embeddings_are_scheduled_only_when_a_row_was_indexed(self):
        bot = make_bot(
            message_index_service=SimpleNamespace(
                index_discord_message_async=AsyncMock(return_value=False)
            )
        )

        await DiscordBot.on_message(bot, make_message())

        bot.hybrid_context_retriever.schedule_pending_embeddings.assert_not_called()


class OnMessageRateLimitTest(unittest.IsolatedAsyncioTestCase):
    async def test_rate_limited_user_gets_the_limiter_text_and_no_model_call(self):
        handler = SimpleNamespace(handle_message=AsyncMock())
        bot = make_bot(
            text_rate_limiter=SimpleNamespace(
                check_and_record=AsyncMock(return_value=(False, "slow down"))
            ),
            enhanced_command_handler=handler,
        )
        message = make_message()

        await DiscordBot.on_message(bot, message)

        message.reply.assert_awaited_once_with("slow down")
        handler.handle_message.assert_not_awaited()
        bot._process_message_with_context.assert_not_awaited()

    async def test_rate_limit_is_checked_before_the_router_spends_a_token(self):
        order = []
        bot = make_bot(
            text_rate_limiter=SimpleNamespace(
                check_and_record=AsyncMock(
                    side_effect=lambda _uid: order.append("limit") or (True, None)
                )
            ),
            enhanced_command_handler=SimpleNamespace(
                handle_message=AsyncMock(
                    side_effect=lambda _m: order.append("router")
                    or (False, RoutingDecision())
                )
            ),
        )

        await DiscordBot.on_message(bot, make_message())

        self.assertEqual(order, ["limit", "router"])


class OnMessageRoutingTest(unittest.IsolatedAsyncioTestCase):
    @staticmethod
    def _handler(decision, handled=False):
        return SimpleNamespace(
            handle_message=AsyncMock(return_value=(handled, decision))
        )

    async def test_router_claiming_the_message_stops_the_pipeline(self):
        handler = self._handler(
            RoutingDecision(intent=CommandIntent.IMAGE_GENERATE), handled=True
        )
        bot = make_bot(enhanced_command_handler=handler)

        await DiscordBot.on_message(bot, make_message())

        bot._process_message_with_context.assert_not_awaited()

    async def test_router_decision_is_forwarded_to_the_context_pipeline(self):
        handler = self._handler(
            RoutingDecision(
                intent=CommandIntent.UNKNOWN,
                complexity="high",
                needs_context=False,
            )
        )
        bot = make_bot(enhanced_command_handler=handler)

        await DiscordBot.on_message(bot, make_message())

        kwargs = bot._process_message_with_context.await_args.kwargs
        self.assertEqual(kwargs["complexity_level"], "high")
        self.assertEqual(kwargs["routed_intent"], "unknown")
        self.assertFalse(kwargs["needs_context"])

    async def test_router_complexity_never_hardens_into_a_model_override(self):
        """BUG-0002 contract, asserted at the caller rather than the helper.

        ``_resolve_request_preferences`` implements the precedence policy, but
        it is only reached with ``request_model_override=None`` if the router
        leaves ``model_override`` unset. Pinning it here is what makes the
        precedence policy observable end to end.
        """
        handler = self._handler(RoutingDecision(complexity="high"))
        bot = make_bot(enhanced_command_handler=handler)

        await DiscordBot.on_message(bot, make_message())

        kwargs = bot._process_message_with_context.await_args.kwargs
        self.assertIsNone(
            kwargs["model_override"],
            "router complexity must stay advisory; a hard override bypasses "
            "/config model and /preferences model (BUG-0002)",
        )

    async def test_mention_only_message_is_answered_with_guidance_not_a_model_call(self):
        bot = make_bot(_extract_user_prompt=Mock(return_value="   "))
        message = make_message(content="<@7>")

        await DiscordBot.on_message(bot, message)

        message.reply.assert_awaited_once()
        self.assertIn("didn't see a message", message.reply.await_args.args[0])
        bot._process_message_with_context.assert_not_awaited()

    async def test_unexpected_failure_is_routed_through_the_error_manager(self):
        bot = make_bot(
            _process_message_with_context=AsyncMock(side_effect=RuntimeError("boom"))
        )
        message = make_message()

        await DiscordBot.on_message(bot, message)

        bot.error_manager.handle_discord_error.assert_called_once()
        bot.error_manager.send_error_response.assert_awaited_once()
        self.assertIs(
            bot.error_manager.send_error_response.await_args.args[0], message
        )


if __name__ == "__main__":
    unittest.main()
