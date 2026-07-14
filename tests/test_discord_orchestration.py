import asyncio
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

from src.bot.discord_bot import (
    DiscordBot,
    SplitResponsePaginatorView as CompatibilityPaginatorView,
    _get_accessible_rag_channels,
    _start_automatic_rag_backlog,
)
from src.bot.live_message_coordinator import LiveMessageCoordinator
from src.bot.rag_event_coordinator import RagEventCoordinator
from src.bot.response_delivery import (
    ResponseDeliveryCoordinator,
    SplitResponsePaginatorView,
)
from src.services.message_splitter import MessageSplitter


def make_message(message_id, content, *, channel_id=10, user_id=1, attachments=None):
    return SimpleNamespace(
        id=message_id,
        content=content,
        attachments=list(attachments or []),
        author=SimpleNamespace(id=user_id, display_name=f"User {user_id}"),
        channel=SimpleNamespace(id=channel_id),
        reference=None,
        reply=AsyncMock(),
    )


class LiveMessageCoordinatorTest(unittest.IsolatedAsyncioTestCase):
    def make_coordinator(self, **overrides):
        defaults = {
            "rate_limiter": SimpleNamespace(
                check_and_record=AsyncMock(return_value=(True, None))
            ),
            "extract_user_prompt": lambda message: message.content,
            "process_message_with_context": AsyncMock(),
            "is_enabled": lambda _channel_id: True,
            "model_name": "live-model",
            "cooldown_seconds": 0,
            "reply_style_instruction": "Keep it short.",
            "turn_window": 6,
            "rag_enabled": lambda: False,
        }
        defaults.update(overrides)
        return LiveMessageCoordinator(**defaults)

    async def test_attachment_suffix_precedes_messages_queued_during_processing(self):
        process = AsyncMock()
        coordinator = self.make_coordinator(
            process_message_with_context=process,
            pending_messages={10: [make_message(4, "newer")]},
        )
        first = make_message(1, "first")
        attachment = make_message(2, "inspect", attachments=[object()])
        suffix = make_message(3, "suffix")

        sent = await coordinator.process_messages([first, attachment, suffix])

        self.assertTrue(sent)
        self.assertEqual(
            [message.id for message in coordinator.pending_messages[10]],
            [3, 4],
        )
        process.assert_awaited_once()
        self.assertIs(process.await_args.args[0], attachment)
        self.assertIn("first", process.await_args.args[1])
        self.assertIn("inspect", process.await_args.args[1])

    async def test_close_cancels_worker_and_prevents_finally_restart(self):
        started = asyncio.Event()

        async def block_processing(*_args, **_kwargs):
            started.set()
            await asyncio.Event().wait()

        coordinator = self.make_coordinator(
            process_message_with_context=block_processing,
        )
        active = make_message(1, "active", attachments=[object()])
        queued = make_message(2, "queued")

        await coordinator.enqueue(active)
        await asyncio.wait_for(started.wait(), timeout=1)
        await coordinator.enqueue(queued)
        await coordinator.close()
        await asyncio.sleep(0)

        self.assertEqual(coordinator.tasks, {})
        self.assertEqual(coordinator.pending_messages, {})
        await coordinator.enqueue(make_message(3, "after close"))
        self.assertEqual(coordinator.tasks, {})

    async def test_rate_limits_each_user_and_keeps_allowed_attachment(self):
        async def check(user_id):
            return (False, "limited") if user_id == 1 else (True, None)

        limiter = AsyncMock(side_effect=check)
        process = AsyncMock()
        coordinator = self.make_coordinator(
            rate_limiter=SimpleNamespace(check_and_record=limiter),
            process_message_with_context=process,
        )
        denied = make_message(1, "denied", user_id=1)
        allowed = make_message(2, "allowed", user_id=2, attachments=[object()])

        await coordinator.process_messages([denied, allowed])

        self.assertEqual([call.args[0] for call in limiter.await_args_list], [1, 2])
        denied.reply.assert_awaited_once_with("limited")
        process.assert_awaited_once()
        self.assertNotIn("denied", process.await_args.args[1])


class RagEventCoordinatorTest(unittest.IsolatedAsyncioTestCase):
    def make_coordinator(self, index_service, **overrides):
        defaults = {
            "message_index_service": index_service,
            "rag_enabled": lambda: True,
            "index_bot_responses": lambda: True,
            "get_bot_user": lambda: SimpleNamespace(id=99),
        }
        defaults.update(overrides)
        return RagEventCoordinator(**defaults)

    async def test_paginator_only_edit_preserves_canonical_bot_text(self):
        index_service = SimpleNamespace(
            index_discord_message_async=AsyncMock(),
            mark_deleted_async=AsyncMock(),
        )
        coordinator = self.make_coordinator(index_service)
        author = SimpleNamespace(id=99, bot=True)
        before = SimpleNamespace(id=10, author=author, content="")
        after = SimpleNamespace(id=10, author=author, content="")

        await coordinator.handle_message_edit(before, after)

        index_service.index_discord_message_async.assert_not_awaited()
        index_service.mark_deleted_async.assert_not_awaited()

    async def test_ineligible_edit_marks_existing_index_row_deleted(self):
        index_service = SimpleNamespace(
            index_discord_message_async=AsyncMock(return_value=False),
            mark_deleted_async=AsyncMock(),
        )
        coordinator = self.make_coordinator(index_service)
        author = SimpleNamespace(id=1, bot=False)
        before = SimpleNamespace(id=10, author=author, content="before")
        after = SimpleNamespace(id=10, author=author, content="after")

        await coordinator.handle_message_edit(before, after)

        index_service.index_discord_message_async.assert_awaited_once_with(
            after,
            include_bot_user_id=99,
        )
        index_service.mark_deleted_async.assert_awaited_once_with(10)

    async def test_bulk_delete_continues_after_individual_failure(self):
        index_service = SimpleNamespace(
            mark_deleted_async=AsyncMock(
                side_effect=[RuntimeError("first failed"), None, None]
            ),
        )
        coordinator = self.make_coordinator(index_service)

        await coordinator.handle_raw_bulk_delete([1, 2, 3])

        self.assertEqual(
            [call.args[0] for call in index_service.mark_deleted_async.await_args_list],
            [1, 2, 3],
        )


class AutomaticRagBacklogTest(unittest.TestCase):
    @staticmethod
    def _channel(channel_id, *, view=True, history=True, messageable=True):
        channel = SimpleNamespace(id=channel_id)
        channel.history = Mock() if messageable else None
        channel.permissions_for = Mock(
            return_value=SimpleNamespace(
                view_channel=view,
                read_message_history=history,
            )
        )
        return channel

    def test_starts_for_every_unique_readable_message_channel(self):
        readable = self._channel(10)
        hidden = self._channel(11, view=False)
        no_history = self._channel(12, history=False)
        category = self._channel(13, messageable=False)
        thread = self._channel(20)
        duplicate_thread = self._channel(10)
        member = SimpleNamespace(id=99)
        guild = SimpleNamespace(
            id=1,
            me=member,
            channels=[readable, hidden, no_history, category],
            threads=[thread, duplicate_thread],
        )
        retriever = SimpleNamespace(
            start_all_channel_pregeneration=Mock(return_value=True)
        )
        owner = SimpleNamespace(
            user=SimpleNamespace(id=99),
            guilds=[guild],
            config=SimpleNamespace(
                rag_enabled=True,
                rag_backfill_limit=0,
                rag_index_bot_responses=True,
            ),
            hybrid_context_retriever=retriever,
        )

        self.assertEqual(
            [channel.id for channel in _get_accessible_rag_channels(owner)],
            [10, 20],
        )
        started_count = _start_automatic_rag_backlog(owner)

        self.assertEqual(started_count, 2)
        retriever.start_all_channel_pregeneration.assert_called_once()
        call = retriever.start_all_channel_pregeneration.call_args
        self.assertEqual([channel.id for channel in call.args[0]], [10, 20])
        self.assertIsNone(call.kwargs["limit"])
        self.assertEqual(call.kwargs["include_bot_user_id"], 99)


class ResponseDeliveryCoordinatorTest(unittest.IsolatedAsyncioTestCase):
    def make_delivery(self, *, split_length=20, rendered_text=None, attachments=None):
        splitter = MessageSplitter(
            max_length=split_length,
            preserve_formatting=False,
            add_continuation_indicators=False,
        )
        renderer = SimpleNamespace(
            process_response=Mock(
                return_value=SimpleNamespace(
                    text=rendered_text,
                    attachments=list(attachments or []),
                )
            )
        )
        index_callback = AsyncMock()
        delivery = ResponseDeliveryCoordinator(
            message_splitter=splitter,
            content_renderer=renderer,
            error_manager=Mock(),
            get_split_length=lambda: split_length,
            index_sent_bot_response=index_callback,
        )
        return delivery, renderer, index_callback

    async def test_paginated_response_indexes_canonical_text_and_replies_sources(self):
        canonical_text = "alpha beta gamma delta epsilon zeta"
        attachment = object()
        delivery, _renderer, index_callback = self.make_delivery(
            split_length=15,
            rendered_text=canonical_text,
            attachments=[attachment],
        )
        sent_message = SimpleNamespace(
            id=50,
            channel=SimpleNamespace(id=10),
            reply=AsyncMock(),
        )
        message = make_message(1, "request")
        message.guild = SimpleNamespace(id=20)
        message.reply.return_value = sent_message
        message.channel.send = AsyncMock()

        result = await delivery.send_response_safely(
            message,
            "raw model response",
            [{"uri": "https://example.test", "title": "Example"}],
        )

        self.assertIs(result, sent_message)
        reply_kwargs = message.reply.await_args.kwargs
        self.assertEqual(reply_kwargs["files"], [attachment])
        self.assertEqual(reply_kwargs["embed"].title, "Response")
        self.assertGreater(len(reply_kwargs["view"].pages), 1)
        message.channel.send.assert_not_awaited()
        sent_message.reply.assert_awaited_once()
        self.assertIn("<https://example.test>", sent_message.reply.await_args.args[0])
        index_callback.assert_awaited_once_with(
            source_message=message,
            sent_message=sent_message,
            response_content=canonical_text,
        )

    async def test_simple_fallback_replies_first_then_sends_remaining_chunks(self):
        delivery, _renderer, _index_callback = self.make_delivery(
            split_length=3,
            rendered_text="unused",
        )
        first_sent = SimpleNamespace(id=50)
        message = make_message(1, "request")
        message.reply.return_value = first_sent
        message.channel.send = AsyncMock()
        attachment = object()

        result = await delivery.send_simple_split_response(
            message,
            "abcdefgh",
            attachments=[attachment],
        )

        self.assertIs(result, first_sent)
        message.reply.assert_awaited_once_with("abc", files=[attachment])
        self.assertEqual(
            [call.args[0] for call in message.channel.send.await_args_list],
            ["def", "gh"],
        )

    async def test_empty_response_uses_fallback_without_rendering_or_indexing(self):
        delivery, renderer, index_callback = self.make_delivery(
            rendered_text="unused",
        )
        message = make_message(1, "request")

        result = await delivery.send_response_safely(message, "   ")

        self.assertIsNone(result)
        message.reply.assert_awaited_once()
        renderer.process_response.assert_not_called()
        index_callback.assert_not_awaited()

    async def test_discord_bot_private_wrapper_delegates_to_delivery_collaborator(self):
        sent_message = object()
        delivery = SimpleNamespace(
            send_response_safely=AsyncMock(return_value=sent_message),
        )
        bot = SimpleNamespace(_response_delivery=delivery)
        message = make_message(1, "request")

        result = await DiscordBot._send_response_safely(
            bot,
            message,
            "response",
            [{"uri": "https://example.test"}],
        )

        self.assertIs(result, sent_message)
        delivery.send_response_safely.assert_awaited_once_with(
            message,
            "response",
            [{"uri": "https://example.test"}],
        )
        self.assertIs(CompatibilityPaginatorView, SplitResponsePaginatorView)


if __name__ == "__main__":
    unittest.main()
