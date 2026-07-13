import asyncio
import unittest
from datetime import datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

from src.bot.discord_bot import DiscordBot
from src.models.data_models import APIResponse, EditType, ImageEditRequest
from src.services.image_processing_service import (
    ImageProcessingService,
    ProcessingJob,
    ProcessingStatus,
)
from src.services.message_splitter import MessageSplitter
from src.services.nano_banana_client import NanoBananaClient


class MessageSplitterRegressionTest(unittest.TestCase):
    def test_long_fenced_line_is_lossless_and_within_discord_limit(self):
        content = "```python\n" + ("x" * 5000) + "\n```"
        splitter = MessageSplitter(
            max_length=2000,
            preserve_formatting=True,
            add_continuation_indicators=True,
            continuation_overhead=50,
        )

        parts = splitter.split_message(content)

        self.assertTrue(parts)
        self.assertTrue(all(len(part.content) <= 2000 for part in parts))
        self.assertEqual("".join(part.content for part in parts), content)
        self.assertTrue(splitter.validate_split_integrity(content, parts))


class DiscordBotRegressionTest(unittest.IsolatedAsyncioTestCase):
    def test_request_model_precedence(self):
        gemini_client = Mock()
        gemini_client.has_runtime_model_override.return_value = True
        gemini_client.get_current_model.return_value = "global-model"
        gemini_client.get_model_for_complexity.return_value = "complexity-model"
        prefs_service = Mock()
        prefs_service.get_preferences.return_value = SimpleNamespace(
            preferred_model="user-model",
            preferred_language="spanish",
        )
        bot = SimpleNamespace(
            gemini_client=gemini_client,
            _user_prefs_service=prefs_service,
        )

        model, language = DiscordBot._resolve_request_preferences(
            bot,
            user_id=1,
            request_model_override="request-model",
            apply_user_preferences=True,
            complexity_level="high",
        )
        self.assertEqual((model, language), ("request-model", "spanish"))

        model, _ = DiscordBot._resolve_request_preferences(
            bot,
            user_id=1,
            request_model_override=None,
            apply_user_preferences=True,
            complexity_level="high",
        )
        self.assertEqual(model, "user-model")

        prefs_service.get_preferences.return_value.preferred_model = None
        model, _ = DiscordBot._resolve_request_preferences(
            bot,
            user_id=1,
            request_model_override=None,
            apply_user_preferences=True,
            complexity_level="high",
        )
        self.assertEqual(model, "global-model")

        gemini_client.has_runtime_model_override.return_value = False
        model, _ = DiscordBot._resolve_request_preferences(
            bot,
            user_id=1,
            request_model_override=None,
            apply_user_preferences=True,
            complexity_level="high",
        )
        self.assertEqual(model, "complexity-model")

    async def test_paginator_only_edit_keeps_canonical_rag_content(self):
        index_service = SimpleNamespace(
            index_discord_message_async=AsyncMock(),
            mark_deleted_async=AsyncMock(),
        )
        bot_user = SimpleNamespace(id=99)
        bot = SimpleNamespace(
            config=SimpleNamespace(rag_enabled=True, rag_index_bot_responses=True),
            user=bot_user,
            message_index_service=index_service,
        )
        author = SimpleNamespace(id=99, bot=True)
        before = SimpleNamespace(author=author, content="", id=10)
        after = SimpleNamespace(author=author, content="", id=10)

        await DiscordBot.on_message_edit(bot, before, after)

        index_service.index_discord_message_async.assert_not_awaited()
        index_service.mark_deleted_async.assert_not_awaited()

    async def test_main_response_path_does_not_wrap_client_retry_timeout(self):
        class TypingContext:
            async def __aenter__(self):
                return self

            async def __aexit__(self, exc_type, exc, traceback):
                return False

        channel = SimpleNamespace(id=5, typing=Mock(return_value=TypingContext()))
        message = SimpleNamespace(
            id=10,
            channel=channel,
            author=SimpleNamespace(id=1),
            guild=None,
            reference=None,
            reply=AsyncMock(),
        )
        gemini_client = SimpleNamespace(
            get_estimated_response_time=Mock(return_value="soon"),
            get_current_model=Mock(return_value="model"),
            get_timeout_for_model=Mock(return_value=30),
            generate_response=AsyncMock(
                return_value=APIResponse(success=True, content="response")
            ),
        )
        bot = SimpleNamespace(
            _extract_images_from_message=AsyncMock(return_value=([], [])),
            _extract_context_images=AsyncMock(return_value=([], [])),
            _extract_audio_from_message=AsyncMock(return_value=[]),
            _extract_files_from_message=AsyncMock(return_value=([], [])),
            _resolve_request_preferences=Mock(return_value=("model", None)),
            _channel_settings_service=None,
            gemini_client=gemini_client,
            performance_logger=SimpleNamespace(log_message_processing=Mock()),
            _record_token_usage=AsyncMock(),
            _send_response_safely=AsyncMock(),
            _handle_response_error=AsyncMock(),
            error_manager=Mock(),
        )

        with patch(
            "src.bot.discord_bot.asyncio.wait_for",
            side_effect=AssertionError("outer wait_for must not own Gemini retries"),
        ):
            await DiscordBot._generate_and_send_response(
                bot,
                message,
                "prompt",
                [],
                show_status_message=False,
            )

        gemini_client.generate_response.assert_awaited_once()
        bot._send_response_safely.assert_awaited_once()

    async def test_live_attachment_retains_later_messages(self):
        channel = SimpleNamespace(id=50)
        author = SimpleNamespace(id=1, display_name="Ada")

        def make_message(message_id, content, attachments):
            return SimpleNamespace(
                id=message_id,
                content=content,
                attachments=attachments,
                author=author,
                channel=channel,
                reply=AsyncMock(),
            )

        first = make_message(1, "first", [])
        attachment = make_message(2, "inspect this", [object()])
        later = make_message(3, "later", [])
        process = AsyncMock()
        bot = SimpleNamespace(
            text_rate_limiter=SimpleNamespace(
                check_and_record=AsyncMock(return_value=(True, None))
            ),
            _live_pending_messages={},
            _get_live_channel_lock=lambda _channel_id: asyncio.Lock(),
            _extract_user_prompt=lambda message: message.content,
            _build_live_user_prompt=lambda entries: DiscordBot._build_live_user_prompt(entries),
            _process_message_with_context=process,
            _live_model_name="live-model",
        )

        sent = await DiscordBot._process_live_messages(
            bot,
            [first, attachment, later],
        )

        self.assertTrue(sent)
        self.assertEqual(bot._live_pending_messages[50], [later])
        process.assert_awaited_once()
        call_args = process.await_args.args
        self.assertIs(call_args[0], attachment)
        self.assertIn("first", call_args[1])
        self.assertIn("inspect this", call_args[1])

    async def test_live_batch_rate_limits_each_participating_user(self):
        channel = SimpleNamespace(id=51)
        denied_author = SimpleNamespace(id=1, display_name="Denied")
        allowed_author = SimpleNamespace(id=2, display_name="Allowed")
        denied = SimpleNamespace(
            id=1,
            content="denied text",
            attachments=[],
            author=denied_author,
            channel=channel,
            reply=AsyncMock(),
        )
        allowed = SimpleNamespace(
            id=2,
            content="allowed attachment",
            attachments=[object()],
            author=allowed_author,
            channel=channel,
            reply=AsyncMock(),
        )

        async def check(user_id):
            return (False, "limited") if user_id == 1 else (True, None)

        process = AsyncMock()
        limiter = AsyncMock(side_effect=check)
        bot = SimpleNamespace(
            text_rate_limiter=SimpleNamespace(check_and_record=limiter),
            _live_pending_messages={},
            _get_live_channel_lock=lambda _channel_id: asyncio.Lock(),
            _extract_user_prompt=lambda message: message.content,
            _build_live_user_prompt=lambda entries: DiscordBot._build_live_user_prompt(entries),
            _process_message_with_context=process,
            _live_model_name="live-model",
        )

        await DiscordBot._process_live_messages(bot, [denied, allowed])

        self.assertEqual([call.args[0] for call in limiter.await_args_list], [1, 2])
        denied.reply.assert_awaited_once_with("limited")
        process.assert_awaited_once()
        self.assertNotIn("denied text", process.await_args.args[1])


class ImageProcessingLifecycleTest(unittest.IsolatedAsyncioTestCase):
    async def test_stop_marks_queued_jobs_cancelled(self):
        request = ImageEditRequest(
            user_id="1",
            image_data=b"image",
            instruction="edit",
            edit_type=EditType.GENERAL_EDIT,
            timestamp=datetime.now(),
            channel_id="2",
        )
        job = ProcessingJob(
            job_id="queued-job",
            request=request,
            status=ProcessingStatus.QUEUED,
            created_at=datetime.now(),
        )
        service = SimpleNamespace(
            _is_running=True,
            _worker_tasks=[],
            _cleanup_task=None,
            _lock=asyncio.Lock(),
            _jobs={job.job_id: job},
            _active_jobs={},
            _completed_jobs=[],
            _progress_callbacks={},
            _job_queue=asyncio.Queue(),
        )
        await service._job_queue.put(job)

        await ImageProcessingService.stop(service)

        self.assertEqual(job.status, ProcessingStatus.CANCELLED)
        self.assertTrue(job.completion_event.is_set())
        self.assertEqual(service._jobs, {})
        self.assertEqual(service._completed_jobs, [job])
        self.assertEqual(service._job_queue.qsize(), 0)

    async def test_cancelling_active_worker_leaves_job_terminal(self):
        request = ImageEditRequest(
            user_id="1",
            image_data=b"image",
            instruction="edit",
            edit_type=EditType.GENERAL_EDIT,
            timestamp=datetime.now(),
            channel_id="2",
        )
        job = ProcessingJob(
            job_id="active-job",
            request=request,
            status=ProcessingStatus.QUEUED,
            created_at=datetime.now(),
        )
        request_started = asyncio.Event()

        async def wait_forever(*_args):
            request_started.set()
            await asyncio.Event().wait()

        service = SimpleNamespace(
            _processing_semaphore=asyncio.Semaphore(1),
            _lock=asyncio.Lock(),
            _active_jobs={},
            _completed_jobs=[],
            _progress_callbacks={},
            _jobs={job.job_id: job},
            _stats={
                "successful_requests": 0,
                "failed_requests": 0,
                "average_processing_time": 0.0,
            },
            _update_progress=AsyncMock(),
            client=SimpleNamespace(edit_image=AsyncMock(side_effect=wait_forever)),
            config=SimpleNamespace(max_image_size_mb=10),
        )

        with patch(
            "src.services.image_processing_service.validate_image",
            return_value=SimpleNamespace(is_valid=True),
        ):
            task = asyncio.create_task(
                ImageProcessingService._process_job(service, job, "worker")
            )
            await request_started.wait()
            task.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await task

        self.assertEqual(job.status, ProcessingStatus.CANCELLED)
        self.assertTrue(job.completion_event.is_set())
        self.assertNotIn(job.job_id, service._jobs)
        self.assertEqual(service._completed_jobs, [job])


class NanoBananaClientRegressionTest(unittest.TestCase):
    def test_configures_sdk_transport_timeout_in_milliseconds(self):
        with patch("src.services.nano_banana_client.genai.Client") as client_factory:
            NanoBananaClient(
                api_key="test-key",
                model_name="image-model",
                timeout=7,
            )

        http_options = client_factory.call_args.kwargs["http_options"]
        self.assertEqual(http_options.timeout, 7000)


if __name__ == "__main__":
    unittest.main()
