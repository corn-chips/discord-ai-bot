"""Regression coverage for the Gemini response-generation pipeline."""

import asyncio
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

from google.genai import types
from PIL import Image

from src.models.data_models import TokenUsage
from src.services.gemini_client import GeminiClient


class GeminiPipelineTest(unittest.IsolatedAsyncioTestCase):
    """Exercise the public response path without network access or real tokens."""

    @staticmethod
    def _response(finish_reason="STOP", text=" answer ", *, candidates=True):
        candidate = SimpleNamespace(
            finish_reason=SimpleNamespace(name=finish_reason),
            safety_ratings=[],
        )
        return SimpleNamespace(
            candidates=[candidate] if candidates else [],
            text=text,
        )

    @staticmethod
    def _error_context(*, should_retry, retry_after=None):
        return SimpleNamespace(
            error_type=SimpleNamespace(value="rate_limit"),
            should_retry=should_retry,
            retry_after=retry_after,
            user_message="provider unavailable",
        )

    def _make_client(self, *, response=None, max_retries=0):
        client = object.__new__(GeminiClient)
        client.config = SimpleNamespace(
            max_retries=max_retries,
            temperature=0.7,
            top_p=0.8,
            top_k=40,
            max_output_tokens_low=100,
            max_output_tokens_medium=200,
            max_output_tokens_high=300,
            safety_harassment="BLOCK_NONE",
            safety_hate_speech="BLOCK_NONE",
            safety_sexually_explicit="BLOCK_NONE",
            safety_dangerous_content="BLOCK_NONE",
            model_thinking_backend={"runtime-model": "none"},
        )
        client.client = object()
        client._current_model_name = "runtime-model"
        client._current_complexity_level = "medium"
        client._thinking_level_override = None
        client._force_search = False
        client.error_manager = SimpleNamespace(handle_api_error=Mock())
        client.performance_logger = SimpleNamespace(log_api_call=Mock())
        client.format_prompt = Mock(return_value="FORMATTED PROMPT")
        client._should_use_search = Mock(return_value=False)
        client._resolve_thinking_level_for_request = Mock(return_value="low")
        client.get_timeout_for_model = Mock(return_value=1)
        client._get_model_display_name = Mock(return_value="Display Model")
        client._extract_grounding_sources = Mock(return_value=[])
        client._extract_token_usage = Mock(return_value=None)
        client._calculate_backoff_delay = Mock(return_value=0.25)
        client._generate_response_async = AsyncMock(
            return_value=response or self._response()
        )
        return client

    async def test_embedding_2_uses_developer_api_compatible_retrieval_inputs(self):
        embed_content = AsyncMock(
            return_value=SimpleNamespace(
                embeddings=[
                    SimpleNamespace(values=[1, 2]),
                    SimpleNamespace(values=[3, 4]),
                ]
            )
        )
        client = object.__new__(GeminiClient)
        client.config = SimpleNamespace(rag_embedding_model="gemini-embedding-2")
        client.client = SimpleNamespace(
            aio=SimpleNamespace(
                models=SimpleNamespace(embed_content=embed_content),
            )
        )

        vectors = await client.embed_texts(
            [
                "title: Discord message by User at 2026-07-13 | text: first message",
                "second message",
            ],
            task_type="RETRIEVAL_DOCUMENT",
        )

        self.assertEqual(vectors, [[1.0, 2.0], [3.0, 4.0]])
        request = embed_content.await_args.kwargs
        self.assertEqual(request["model"], "gemini-embedding-2")
        self.assertEqual(request["config"].output_dimensionality, 768)
        self.assertIsNone(request["config"].task_type)
        self.assertIsNone(request["config"].auto_truncate)
        self.assertEqual(len(request["contents"]), 2)
        self.assertTrue(all(isinstance(item, types.Content) for item in request["contents"]))
        self.assertEqual(
            [item.parts[0].text for item in request["contents"]],
            [
                "title: Discord message by User at 2026-07-13 | text: first message",
                "title: none | text: second message",
            ],
        )

    async def test_embedding_2_formats_retrieval_query_without_task_type_config(self):
        embed_content = AsyncMock(
            return_value=SimpleNamespace(
                embeddings=[SimpleNamespace(values=[0.5, 0.25])]
            )
        )
        client = object.__new__(GeminiClient)
        client.config = SimpleNamespace(rag_embedding_model="gemini-embedding-2")
        client.client = SimpleNamespace(
            aio=SimpleNamespace(
                models=SimpleNamespace(embed_content=embed_content),
            )
        )

        vectors = await client.embed_texts(
            ["where is the answer?"],
            task_type="RETRIEVAL_QUERY",
        )

        self.assertEqual(vectors, [[0.5, 0.25]])
        request = embed_content.await_args.kwargs
        self.assertEqual(request["config"].output_dimensionality, 768)
        self.assertIsNone(request["config"].task_type)
        self.assertEqual(
            request["contents"][0].parts[0].text,
            "task: search result | query: where is the answer?",
        )

    async def test_embedding_1_keeps_supported_task_type_without_auto_truncate(self):
        embed_content = AsyncMock(
            return_value=SimpleNamespace(
                embeddings=[SimpleNamespace(values=[1])]
            )
        )
        client = object.__new__(GeminiClient)
        client.config = SimpleNamespace(rag_embedding_model="gemini-embedding-001")
        client.client = SimpleNamespace(
            aio=SimpleNamespace(
                models=SimpleNamespace(embed_content=embed_content),
            )
        )

        vectors = await client.embed_texts(
            ["message"],
            task_type="RETRIEVAL_DOCUMENT",
        )

        self.assertEqual(vectors, [[1.0]])
        request = embed_content.await_args.kwargs
        self.assertEqual(request["contents"], ["message"])
        self.assertEqual(request["config"].task_type, "RETRIEVAL_DOCUMENT")
        self.assertEqual(request["config"].output_dimensionality, 768)
        self.assertIsNone(request["config"].auto_truncate)

    async def test_request_precedence_and_multimodal_part_order_are_preserved(self):
        client = self._make_client()
        client._should_use_search.return_value = False
        grounding = [{"uri": "https://example.test", "title": "Example"}]
        usage = TokenUsage(input_tokens=3, output_tokens=4, total_tokens=7)
        client._extract_grounding_sources.return_value = grounding
        client._extract_token_usage.return_value = usage
        image = Image.new("RGB", (2, 2), "red")
        self.addCleanup(image.close)

        result = await client.generate_response(
            "hello",
            images=[image],
            image_context=[{"source_type": "current_message"}],
            audio_files=[{"data": b"audio", "mime_type": "audio/wav"}],
            model_override="request-model",
            prompt_mode_override="thinking",
            thinking_level_override="disabled",
            complexity_override="high",
            search_override=True,
            personality_prompt="friendly",
            language="spanish",
        )

        self.assertTrue(result.success)
        self.assertEqual(
            result.content,
            "🤖 *[Model: Display Model]*\n"
            "🌐 *[Grounding: Online Search Enabled]*\n\nanswer",
        )
        self.assertEqual(result.grounding_sources, grounding)
        self.assertIs(result.token_usage, usage)
        client._should_use_search.assert_not_called()
        client._resolve_thinking_level_for_request.assert_not_called()
        client.get_timeout_for_model.assert_called_once_with(
            "request-model",
            thinking_level="off",
        )
        client.format_prompt.assert_called_once()
        format_kwargs = client.format_prompt.call_args.kwargs
        self.assertEqual(format_kwargs["complexity_override"], "high")
        self.assertEqual(format_kwargs["personality_prompt"], "friendly")
        self.assertEqual(format_kwargs["language"], "spanish")
        normalized_context = format_kwargs["image_context"]
        self.assertEqual(normalized_context[0]["image_index"], 1)
        self.assertEqual(normalized_context[0]["source_type"], "current_message")

        call = client._generate_response_async.await_args
        content_parts = call.args[0]
        self.assertEqual(content_parts[0], "FORMATTED PROMPT")
        self.assertIsInstance(content_parts[1], types.Part)
        self.assertEqual(content_parts[1].inline_data.mime_type, "image/png")
        self.assertIsInstance(content_parts[2], types.Part)
        self.assertEqual(content_parts[2].inline_data.mime_type, "audio/wav")
        self.assertEqual(call.args[1:3], (True, None))
        self.assertEqual(call.kwargs["model_override"], "request-model")
        self.assertEqual(call.kwargs["thinking_level"], "off")
        self.assertEqual(call.kwargs["complexity_level"], "high")
        client._extract_grounding_sources.assert_called_once_with(
            client._generate_response_async.return_value,
            True,
        )

    async def test_implicit_routing_uses_runtime_model_and_resolvers(self):
        client = self._make_client()
        client._should_use_search.return_value = True
        client._resolve_thinking_level_for_request.return_value = "medium"

        result = await client.generate_response("latest updates")

        self.assertTrue(result.success)
        client._should_use_search.assert_called_once_with("latest updates")
        client._resolve_thinking_level_for_request.assert_called_once_with(
            "runtime-model",
            prompt_mode_override=None,
            complexity_level="medium",
        )
        client.get_timeout_for_model.assert_called_once_with(
            "runtime-model",
            thinking_level="medium",
        )

    async def test_retryable_provider_error_uses_backoff_then_succeeds(self):
        client = self._make_client(max_retries=1)
        response = self._response()
        client._generate_response_async.side_effect = [RuntimeError("busy"), response]
        client.error_manager.handle_api_error.return_value = self._error_context(
            should_retry=True
        )

        with patch(
            "src.services.gemini_client.asyncio.sleep",
            new_callable=AsyncMock,
        ) as sleep_mock:
            result = await client.generate_response("hello")

        self.assertTrue(result.success)
        self.assertEqual(client._generate_response_async.await_count, 2)
        client.error_manager.handle_api_error.assert_called_once()
        client._calculate_backoff_delay.assert_called_once_with(0)
        sleep_mock.assert_awaited_once_with(0.25)
        calls = client.performance_logger.log_api_call.call_args_list
        self.assertEqual(calls[0].kwargs["error_type"], "rate_limit")
        self.assertTrue(calls[1].kwargs["success"])

    async def test_retry_after_takes_precedence_over_computed_backoff(self):
        client = self._make_client(max_retries=1)
        client._generate_response_async.side_effect = [RuntimeError("busy"), self._response()]
        client.error_manager.handle_api_error.return_value = self._error_context(
            should_retry=True,
            retry_after=3,
        )

        with patch(
            "src.services.gemini_client.asyncio.sleep",
            new_callable=AsyncMock,
        ) as sleep_mock:
            result = await client.generate_response("hello")

        self.assertTrue(result.success)
        client._calculate_backoff_delay.assert_not_called()
        sleep_mock.assert_awaited_once_with(3)

    async def test_an_empty_stop_response_backs_off_before_each_retry(self):
        """B3-03 / DAB-042.

        A STOP finish reason carrying no usable text makes
        ``_interpret_provider_response`` return ``None``, so the attempt loop goes
        round again. Nothing on that path slept, so every one of
        ``max_retries + 1`` attempts fired back to back: four fully billed
        provider calls -- images and the whole assembled context included --
        inside 0.0001 s, ending in a generic ``unknown_error``.

        That is a live 4x cost multiplier with a real trigger, which is why it is
        separated from the rest of DAB-042 (a latency ceiling with a theoretical
        one) and landed first.
        """
        client = self._make_client(max_retries=3)
        client._generate_response_async = AsyncMock(
            return_value=self._response(finish_reason="STOP", text="")
        )

        with patch(
            "src.services.gemini_client.asyncio.sleep",
            new_callable=AsyncMock,
        ) as sleep_mock:
            result = await client.generate_response("hello")

        self.assertFalse(result.success)
        self.assertEqual(client._generate_response_async.await_count, 4)
        # Three gaps between four attempts. Without a backoff this is 0.
        self.assertEqual(sleep_mock.await_count, 3)
        self.assertEqual(
            [call.args[0] for call in sleep_mock.await_args_list],
            [0.25, 0.25, 0.25],
        )

    async def test_timeout_is_applied_to_each_attempt(self):
        client = self._make_client(max_retries=1)
        client.get_timeout_for_model.return_value = 0.001
        calls = 0

        async def never_finishes(*_args, **_kwargs):
            nonlocal calls
            calls += 1
            await asyncio.Future()

        client._generate_response_async = never_finishes

        with patch(
            "src.services.gemini_client.asyncio.sleep",
            new_callable=AsyncMock,
        ) as sleep_mock:
            result = await client.generate_response("hello")

        self.assertFalse(result.success)
        self.assertEqual(result.error_type, "timeout")
        self.assertEqual(result.content, "Request timed out after multiple attempts")
        self.assertEqual(calls, 2)
        timeout_logs = client.performance_logger.log_api_call.call_args_list
        self.assertEqual(len(timeout_logs), 2)
        self.assertTrue(all(call.kwargs["error_type"] == "timeout" for call in timeout_logs))
        # Added with the B3-03 backoff. The timeout branch used to loop with no
        # delay too; harmless while an attempt costs a real 120 s, but a hammer
        # the moment response.timeout is set small -- as it is here, at 0.001 s,
        # which is precisely the shape nothing in validate_config rejects.
        self.assertEqual(sleep_mock.await_count, 1)

    async def test_cancelled_error_propagates_without_retry_or_error_conversion(self):
        client = self._make_client(max_retries=2)
        calls = 0

        async def cancelled(*_args, **_kwargs):
            nonlocal calls
            calls += 1
            raise asyncio.CancelledError

        client._generate_response_async = cancelled

        with self.assertRaises(asyncio.CancelledError):
            await client.generate_response("hello")

        self.assertEqual(calls, 1)
        client.error_manager.handle_api_error.assert_not_called()
        client.performance_logger.log_api_call.assert_not_called()

    async def test_finish_reason_error_messages_are_preserved(self):
        cases = [
            (
                "MAX_TOKENS",
                "",
                "max_tokens",
                "The response was too complex to generate. Please try breaking your question into smaller parts.",
            ),
            (
                "SAFETY",
                "blocked",
                "safety_filter",
                "I can't respond to that due to content safety guidelines. Please rephrase your message.",
            ),
            (
                "RECITATION",
                "blocked",
                "recitation",
                "I can't provide that response as it may be copyrighted content.",
            ),
            (
                "OTHER",
                "blocked",
                "unknown_finish_reason",
                "Received an unexpected response from the AI. Please try again.",
            ),
        ]

        for finish_reason, text, error_type, content in cases:
            with self.subTest(finish_reason=finish_reason):
                client = self._make_client(
                    response=self._response(finish_reason, text)
                )
                result = await client.generate_response("hello")
                self.assertFalse(result.success)
                self.assertEqual(result.error_type, error_type)
                self.assertEqual(result.content, content)

    async def test_empty_provider_responses_remain_distinct(self):
        client = self._make_client()
        client._generate_response_async.return_value = None
        none_result = await client.generate_response("hello")
        self.assertEqual(none_result.error_type, "empty_response")
        self.assertEqual(none_result.content, "The AI returned no response")

        client = self._make_client(response=self._response(candidates=False))
        no_candidates_result = await client.generate_response("hello")
        self.assertEqual(no_candidates_result.error_type, "empty_response")
        self.assertEqual(no_candidates_result.content, "The AI generated an empty response")

    async def test_stop_without_text_preserves_retry_then_unknown_error_behavior(self):
        client = self._make_client(
            response=self._response("STOP", ""),
            max_retries=1,
        )

        result = await client.generate_response("hello")

        self.assertFalse(result.success)
        self.assertEqual(result.error_type, "unknown_error")
        self.assertEqual(client._generate_response_async.await_count, 2)

    async def test_sdk_boundary_disables_search_for_multimodal_content(self):
        provider_response = object()
        generate_content = AsyncMock(return_value=provider_response)
        client = self._make_client()
        client.client = SimpleNamespace(
            aio=SimpleNamespace(
                models=SimpleNamespace(generate_content=generate_content)
            )
        )
        content = ["prompt", object()]

        result = await GeminiClient._generate_response_async(
            client,
            content,
            use_search=True,
            model_override="runtime-model",
            thinking_level="default",
            complexity_level="medium",
        )

        self.assertIs(result, provider_response)
        call = generate_content.await_args
        self.assertEqual(call.kwargs["model"], "runtime-model")
        self.assertIs(call.kwargs["contents"], content)
        generation_config = call.kwargs["config"]
        self.assertEqual(generation_config.tools, [])
        self.assertEqual(generation_config.max_output_tokens, 200)

    async def test_sdk_boundary_streams_chunks_in_order_and_returns_full_text(self):
        chunks = [
            SimpleNamespace(text="first", candidates=["candidate-1"]),
            SimpleNamespace(
                text=" second",
                candidates=["candidate-2"],
                usage_metadata=SimpleNamespace(total_token_count=4),
            ),
        ]

        class FakeStream:
            def __aiter__(self):
                return self

            async def __anext__(self):
                if not chunks:
                    raise StopAsyncIteration
                return chunks.pop(0)

        generate_stream = AsyncMock(return_value=FakeStream())
        callback = AsyncMock()
        client = self._make_client()
        client.client = SimpleNamespace(
            aio=SimpleNamespace(
                models=SimpleNamespace(generate_content_stream=generate_stream)
            )
        )

        result = await GeminiClient._generate_response_async(
            client,
            ["prompt"],
            on_chunk=callback,
            model_override="runtime-model",
            thinking_level="default",
            complexity_level="low",
        )

        self.assertEqual(result.text, "first second")
        self.assertEqual(result.candidates, ["candidate-2"])
        self.assertEqual(result.usage_metadata.total_token_count, 4)
        self.assertEqual(
            [call.args[0] for call in callback.await_args_list],
            ["first", " second"],
        )

    async def test_unconfigured_client_returns_configuration_error(self):
        client = self._make_client()
        client.client = None

        result = await client.generate_response("hello")

        self.assertFalse(result.success)
        self.assertEqual(result.error_type, "configuration_error")
        client._generate_response_async.assert_not_awaited()


class ThoughtPartExtractionTest(unittest.TestCase):
    """DAB-039: the model's private reasoning must never become the answer.

    Latent at present -- nothing in the repository sets ``include_thoughts``, so
    the API returns no thought parts. These tests build real SDK ``types.Part``
    objects with ``thought=True`` so the guard is exercised anyway, because the
    day someone enables thinking output is not the day to discover this.

    The exposed path is narrow and specific. The SDK's own ``.text`` property
    already skips thought parts, so the leak lives in the fallback loop in
    ``_get_response_text`` -- which runs precisely when ``.text`` came back
    empty, and a thoughts-only response is exactly what produces that.
    """

    @staticmethod
    def _response_with_parts(parts):
        # A real types.Content, so part.thought behaves as the SDK defines it
        # rather than as a permissive mock would allow.
        return SimpleNamespace(
            text=None,
            candidates=[SimpleNamespace(content=types.Content(parts=parts))],
        )

    def test_a_thoughts_only_response_yields_no_text_rather_than_the_reasoning(self):
        client = GeminiClient.__new__(GeminiClient)
        response = self._response_with_parts(
            [types.Part(text="The user seems annoyed; I should hedge.", thought=True)]
        )

        self.assertIsNone(client._get_response_text(response))

    def test_reasoning_preceding_the_answer_is_skipped_not_returned(self):
        client = GeminiClient.__new__(GeminiClient)
        response = self._response_with_parts(
            [
                types.Part(text="Step 1: recall the formula. Step 2: apply it.", thought=True),
                types.Part(text="The answer is 42."),
            ]
        )

        extracted = client._get_response_text(response)

        self.assertEqual(extracted, "The answer is 42.")
        self.assertNotIn("Step 1", extracted)

    def test_an_ordinary_answer_is_unaffected_by_the_guard(self):
        client = GeminiClient.__new__(GeminiClient)
        response = self._response_with_parts([types.Part(text="Plain answer.")])

        self.assertEqual(client._get_response_text(response), "Plain answer.")


if __name__ == "__main__":
    unittest.main()
