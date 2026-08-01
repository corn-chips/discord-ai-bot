"""Focused tests for response-generation orchestration."""

import asyncio
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

from src.bot.response_generation import ResponseGenerationCoordinator
from src.models.data_models import APIResponse, TokenUsage
from src.utils.error_manager import ErrorType


class _TypingContext:
    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, traceback):
        return False


class ResponseGenerationTest(unittest.IsolatedAsyncioTestCase):
    def _make_message(self):
        channel = SimpleNamespace(
            id=50,
            typing=Mock(return_value=_TypingContext()),
            send=AsyncMock(),
        )
        author = SimpleNamespace(
            id=7,
            display_name="Ada",
            global_name="Ada Global",
            name="ada",
        )
        return SimpleNamespace(
            id=99,
            channel=channel,
            author=author,
            guild=SimpleNamespace(id=8, name="Guild"),
            reference=None,
            reply=AsyncMock(),
        )

    def _make_coordinator(self, api_response=None, generate_side_effect=None):
        if api_response is None:
            api_response = APIResponse(success=True, content="answer")
        generate_response = AsyncMock(
            return_value=api_response,
            side_effect=generate_side_effect,
        )
        gemini_client = SimpleNamespace(
            generate_response=generate_response,
            get_current_model=Mock(return_value="current-model"),
            get_estimated_response_time=Mock(return_value="soon"),
            get_timeout_for_model=Mock(return_value=30),
        )
        error_manager = SimpleNamespace(
            get_user_message=Mock(return_value="Base error"),
            create_error_context=Mock(return_value=object()),
            log_error=Mock(),
            send_error_response=AsyncMock(),
        )
        status_message = SimpleNamespace(delete=AsyncMock())
        tracker = SimpleNamespace(record_usage=AsyncMock())
        dependencies = {
            "gemini_client": gemini_client,
            "error_manager": error_manager,
            "performance_logger": SimpleNamespace(log_message_processing=Mock()),
            "extract_images": AsyncMock(return_value=([], [])),
            "extract_context_images": AsyncMock(return_value=([], [])),
            "extract_audio": AsyncMock(return_value=[]),
            "extract_files": AsyncMock(return_value=([], [])),
            "attach_image_order_metadata": Mock(side_effect=lambda entries, *_: entries),
            "remove_duplicate_context": Mock(side_effect=lambda recent, replied: recent + replied),
            "resolve_request_preferences": Mock(return_value=("preferred-model", "fr")),
            "get_personality_prompt": Mock(return_value="channel persona"),
            "get_token_tracker": Mock(return_value=tracker),
            "send_status_message": AsyncMock(return_value=status_message),
            "deliver_response": AsyncMock(),
        }
        return ResponseGenerationCoordinator(**dependencies), dependencies, status_message

    async def test_success_preserves_preferences_arguments_status_tokens_and_delivery(self):
        token_usage = TokenUsage(input_tokens=2, output_tokens=3, total_tokens=5)
        api_response = APIResponse(
            success=True,
            content="answer",
            grounding_sources=[{"url": "https://example.com"}],
            token_usage=token_usage,
        )
        coordinator, dependencies, status_message = self._make_coordinator(api_response)
        message = self._make_message()
        current_image = object()
        context_image = object()
        current_metadata = {"source_message_id": 99}
        context_metadata = {"source_message_id": 80}
        audio_file = {"data": b"audio", "mime_type": "audio/ogg"}
        dependencies["extract_images"].return_value = (
            [current_image],
            [current_metadata],
        )
        dependencies["extract_context_images"].return_value = (
            [context_image],
            [context_metadata],
        )
        dependencies["extract_audio"].return_value = [audio_file]

        # This call used to run under
        # patch("src.bot.response_generation.asyncio.wait_for", side_effect=AssertionError).
        # That attribute is the singleton asyncio module, so the patch was global
        # and failed any whole-sequence deadline, correct ones included -- a
        # second copy of the DAB-204 obstruction, in a file DAB-204 never names.
        # The retry-budget contract now lives in one place,
        # test_bug_regressions.test_main_response_path_preserves_the_full_client_retry_budget,
        # which asserts the budget instead of banning the call. Every assertion
        # below is unchanged; only the wrapper is gone.
        await coordinator.generate_and_send_response(
            message,
            "question",
            ["context"],
            model_override="request-model",
            prompt_mode_override="fast",
            search_override=True,
            apply_user_preferences=True,
            complexity_level="high",
        )

        dependencies["resolve_request_preferences"].assert_called_once_with(
            user_id=7,
            request_model_override="request-model",
            apply_user_preferences=True,
            complexity_level="high",
        )
        generate_call = dependencies["gemini_client"].generate_response.await_args
        self.assertEqual(generate_call.args, ("question", ["context"]))
        self.assertEqual(generate_call.kwargs["model_override"], "preferred-model")
        self.assertEqual(generate_call.kwargs["prompt_mode_override"], "fast")
        self.assertEqual(generate_call.kwargs["complexity_override"], "high")
        self.assertIs(generate_call.kwargs["search_override"], True)
        self.assertEqual(generate_call.kwargs["personality_prompt"], "channel persona")
        self.assertEqual(generate_call.kwargs["language"], "fr")
        self.assertEqual(
            generate_call.kwargs["images"],
            [current_image, context_image],
        )
        self.assertEqual(
            generate_call.kwargs["image_context"],
            [current_metadata, context_metadata],
        )
        self.assertEqual(generate_call.kwargs["audio_files"], [audio_file])
        dependencies["extract_context_images"].assert_awaited_once_with(
            message,
            ["context"],
            {99},
        )
        dependencies["send_status_message"].assert_awaited_once()
        status_message.delete.assert_awaited_once()
        dependencies["get_token_tracker"].return_value.record_usage.assert_awaited_once_with(
            user_id=7,
            username="Ada",
            guild_id=8,
            guild_name="Guild",
            input_tokens=2,
            output_tokens=3,
            total_tokens=5,
        )
        dependencies["deliver_response"].assert_awaited_once_with(
            message,
            "answer",
            api_response.grounding_sources,
        )

    async def test_api_error_maps_error_type_and_cleans_status(self):
        api_response = APIResponse(
            success=False,
            error_type="rate_limit",
            content="slow down",
            retry_after=4,
        )
        coordinator, dependencies, status_message = self._make_coordinator(api_response)
        message = self._make_message()

        await coordinator.generate_and_send_response(message, "question", [])

        status_message.delete.assert_awaited_once()
        error_context = dependencies["error_manager"].log_error.call_args.args[0]
        self.assertEqual(error_context.error_type, ErrorType.RATE_LIMIT)
        self.assertEqual(error_context.retry_after, 4)
        self.assertIn("slow down", error_context.user_message)
        dependencies["error_manager"].send_error_response.assert_awaited_once_with(
            message,
            error_context,
        )
        dependencies["deliver_response"].assert_not_awaited()

    async def test_cancellation_propagates_without_error_or_delivery(self):
        coordinator, dependencies, status_message = self._make_coordinator(
            generate_side_effect=asyncio.CancelledError()
        )
        message = self._make_message()

        with self.assertRaises(asyncio.CancelledError):
            await coordinator.generate_and_send_response(message, "question", [])

        status_message.delete.assert_not_awaited()
        dependencies["error_manager"].send_error_response.assert_not_awaited()
        dependencies["deliver_response"].assert_not_awaited()

    async def test_preference_flags_are_forwarded_when_status_is_disabled(self):
        coordinator, dependencies, _ = self._make_coordinator()
        message = self._make_message()

        await coordinator.generate_and_send_response(
            message,
            "question",
            [],
            model_override=None,
            apply_user_preferences=False,
            complexity_level="medium",
            show_status_message=False,
        )

        dependencies["resolve_request_preferences"].assert_called_once_with(
            user_id=7,
            request_model_override=None,
            apply_user_preferences=False,
            complexity_level="medium",
        )
        dependencies["send_status_message"].assert_not_awaited()
        self.assertEqual(
            dependencies["gemini_client"].generate_response.await_args.kwargs[
                "model_override"
            ],
            "preferred-model",
        )


if __name__ == "__main__":
    unittest.main()
