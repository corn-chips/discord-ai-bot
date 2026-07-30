"""Coverage for the silent hybrid-RAG -> legacy context fallback.

Prototype for I6-03. ``_process_message_with_context`` swallows *any* hybrid
retrieval exception and quietly reverts to ``ContextCollector``. The bot keeps
answering, so a fully broken RAG stack presents as "nothing happened" and can
survive indefinitely. These tests pin the fallback as an intentional, *audible*
contract rather than an accident.
"""

import logging
import unittest
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import discord

from src.bot.discord_bot import DiscordBot
from src.models.data_models import MessageContext


def make_context(message_id, content="ctx"):
    return MessageContext(
        content=content,
        author="Ada",
        timestamp=datetime(2024, 1, 1, tzinfo=timezone.utc),
        message_id=message_id,
        channel_id=10,
    )


def make_message(message_id=99, *, reference=None):
    return SimpleNamespace(
        id=message_id,
        content="question",
        attachments=[],
        author=SimpleNamespace(id=1, bot=False, display_name="Ada"),
        channel=SimpleNamespace(id=10, name="general"),
        guild=SimpleNamespace(id=3),
        reference=reference,
        reply=AsyncMock(),
    )


def make_bot(**overrides):
    collector = SimpleNamespace(
        get_channel_context=AsyncMock(return_value=[make_context(1)]),
        get_reply_context=AsyncMock(return_value=[make_context(2)]),
        _remove_duplicate_messages=Mock(
            side_effect=lambda a, b: list(a) + list(b)
        ),
    )
    defaults = {
        "config": SimpleNamespace(
            rag_enabled=True,
            max_context_messages=25,
            context_messages_low=5,
            context_messages_medium=10,
            context_messages_high=20,
        ),
        "user": SimpleNamespace(id=7),
        "hybrid_context_retriever": SimpleNamespace(retrieve=AsyncMock(return_value=[])),
        "context_collector": collector,
        "gemini_client": SimpleNamespace(
            select_relevant_context=AsyncMock(
                side_effect=lambda _p, ctx, **_k: list(ctx)
            )
        ),
        "_get_context_limit_for_complexity": Mock(return_value=5),
        "_generate_and_send_response": AsyncMock(),
        "error_manager": SimpleNamespace(
            create_error_context=Mock(),
            log_error=Mock(),
            send_error_response=AsyncMock(),
        ),
    }
    defaults.update(overrides)
    return SimpleNamespace(**defaults)


class HybridRagHappyPathTest(unittest.IsolatedAsyncioTestCase):
    async def test_successful_retrieval_skips_the_legacy_collector_entirely(self):
        rag_context = [make_context(5, "from rag")]
        bot = make_bot(
            hybrid_context_retriever=SimpleNamespace(
                retrieve=AsyncMock(return_value=rag_context)
            )
        )

        await DiscordBot._process_message_with_context(bot, make_message(), "question")

        bot.context_collector.get_channel_context.assert_not_awaited()
        self.assertEqual(
            bot._generate_and_send_response.await_args.args[2], rag_context
        )

    async def test_a_reply_forces_full_context_through_the_retriever(self):
        bot = make_bot()
        message = make_message(reference=SimpleNamespace(message_id=4))

        await DiscordBot._process_message_with_context(bot, message, "question")

        kwargs = bot.hybrid_context_retriever.retrieve.await_args.kwargs
        self.assertTrue(kwargs["force_full_context"])
        self.assertEqual(kwargs["bot_user_id"], 7)

    async def test_image_generation_intent_never_pays_for_rag_retrieval(self):
        bot = make_bot()

        await DiscordBot._process_message_with_context(
            bot, make_message(), "draw a cat", routed_intent="image_generate"
        )

        bot.hybrid_context_retriever.retrieve.assert_not_awaited()
        self.assertEqual(bot._generate_and_send_response.await_args.args[2], [])

    async def test_an_empty_but_successful_retrieval_is_not_a_failure(self):
        """`[]` means "RAG found nothing", not "RAG is broken".

        Gating on truthiness instead of ``is not None`` sends every
        empty-but-successful retrieval down the legacy path, paying for a
        second ``select_relevant_context`` call on a large share of traffic.
        """
        bot = make_bot(
            hybrid_context_retriever=SimpleNamespace(retrieve=AsyncMock(return_value=[]))
        )

        await DiscordBot._process_message_with_context(bot, make_message(), "question")

        bot.context_collector.get_channel_context.assert_not_awaited()
        bot.gemini_client.select_relevant_context.assert_not_awaited()
        bot._generate_and_send_response.assert_awaited_once()
        self.assertEqual(bot._generate_and_send_response.await_args.args[2], [])


class HybridRagFallbackTest(unittest.IsolatedAsyncioTestCase):
    @staticmethod
    def _broken_bot(exc=RuntimeError("embedding backend down"), **overrides):
        return make_bot(
            hybrid_context_retriever=SimpleNamespace(
                retrieve=AsyncMock(side_effect=exc)
            ),
            **overrides,
        )

    async def test_retrieval_failure_still_answers_via_the_legacy_collector(self):
        bot = self._broken_bot()

        await DiscordBot._process_message_with_context(bot, make_message(), "question")

        bot.context_collector.get_channel_context.assert_awaited_once()
        bot._generate_and_send_response.assert_awaited_once()
        self.assertEqual(
            [ctx.message_id for ctx in bot._generate_and_send_response.await_args.args[2]],
            [1],
        )

    async def test_the_fallback_is_audible_at_warning_level_with_the_cause(self):
        """A silent fallback is indistinguishable from a healthy RAG stack.

        This is the *only* signal an operator gets that retrieval is dead, so
        the level and the embedded cause are part of the contract.
        """
        bot = self._broken_bot()

        with self.assertLogs("src.bot.discord_bot", level=logging.WARNING) as captured:
            await DiscordBot._process_message_with_context(
                bot, make_message(), "question"
            )

        self.assertTrue(
            any(
                "using legacy context fallback" in line
                and "embedding backend down" in line
                for line in captured.output
            ),
            captured.output,
        )

    async def test_the_fallback_never_double_sends_a_response(self):
        bot = self._broken_bot()

        await DiscordBot._process_message_with_context(bot, make_message(), "question")

        self.assertEqual(bot._generate_and_send_response.await_count, 1)

    async def test_a_failure_after_delivery_is_not_retried_through_the_fallback(self):
        """Guards the ordering hazard inside the ``try`` block.

        ``_generate_and_send_response`` is called *inside* the same ``try`` that
        catches retrieval errors, so a delivery-time exception would re-enter
        the legacy path and answer twice. Only the retriever may trigger it.
        """
        bot = make_bot(
            hybrid_context_retriever=SimpleNamespace(
                retrieve=AsyncMock(return_value=[make_context(5)])
            ),
            _generate_and_send_response=AsyncMock(
                side_effect=RuntimeError("delivery failed")
            ),
        )

        await DiscordBot._process_message_with_context(bot, make_message(), "question")

        self.assertEqual(
            bot._generate_and_send_response.await_count,
            1,
            "a delivery failure must not be retried through the legacy path",
        )

    async def test_the_fallback_survives_a_forbidden_channel_history(self):
        collector = SimpleNamespace(
            get_channel_context=AsyncMock(
                side_effect=discord.Forbidden(
                    SimpleNamespace(status=403, reason="Forbidden"), "no access"
                )
            ),
            get_reply_context=AsyncMock(return_value=[]),
            _remove_duplicate_messages=Mock(side_effect=lambda a, b: list(a) + list(b)),
        )
        bot = self._broken_bot(context_collector=collector)

        await DiscordBot._process_message_with_context(bot, make_message(), "question")

        bot._generate_and_send_response.assert_awaited_once()
        self.assertEqual(bot._generate_and_send_response.await_args.args[2], [])

    async def test_rag_disabled_globally_takes_the_legacy_path_without_warning(self):
        bot = make_bot(
            config=SimpleNamespace(
                rag_enabled=False,
                max_context_messages=25,
                context_messages_low=5,
                context_messages_medium=10,
                context_messages_high=20,
            )
        )

        with self.assertLogs("src.bot.discord_bot", level=logging.DEBUG) as captured:
            await DiscordBot._process_message_with_context(
                bot, make_message(), "question"
            )

        bot.hybrid_context_retriever.retrieve.assert_not_awaited()
        bot.context_collector.get_channel_context.assert_awaited_once()
        self.assertFalse(
            [line for line in captured.output if "legacy context fallback" in line]
        )


if __name__ == "__main__":
    unittest.main()
