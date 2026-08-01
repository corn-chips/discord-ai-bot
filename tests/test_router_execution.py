"""How the LLM router executes: off the shared executor, and under a deadline.

DAB-024. The router runs on every mentioning message, before the text path, and
it used to call the *synchronous* Gemini SDK through ``asyncio.to_thread``. That
puts it on the event loop's default ``ThreadPoolExecutor``, which is shared with
every other ``to_thread`` in the process -- roughly twenty SQLite calls across
``message_index_service``, ``token_tracker`` and the pin loads. The pool is
``min(32, cpu_count + 4)``, so six to eight threads on a small host. The call
also had no deadline of any kind, so a hung provider held a worker until the TCP
connection died and a handful of them starved every database operation the bot
performs.

These tests assert the two observable properties that fix rests on, rather than
asserting that some particular function was not called: banning a call is the
mistake DAB-204 was filed about.
"""

import asyncio
import concurrent.futures
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

from src.bot.enhanced_command_handler import EnhancedCommandHandler


class _RecordingExecutor(concurrent.futures.ThreadPoolExecutor):
    """A default executor that remembers whether anything was scheduled on it."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.submitted = []

    def submit(self, fn, /, *args, **kwargs):
        self.submitted.append(fn)
        return super().submit(fn, *args, **kwargs)


class RouterExecutionTest(unittest.IsolatedAsyncioTestCase):
    @staticmethod
    def _make_handler(generate_content, *, response_timeout=30):
        config = SimpleNamespace(
            router_model_name="router-model",
            router_temperature=0.1,
            router_max_output_tokens=100,
            router_cache_size=8,
            router_cache_ttl=60,
            response_timeout=response_timeout,
        )
        bot = SimpleNamespace(config=config)
        gemini_client = SimpleNamespace(
            client=SimpleNamespace(
                # The synchronous surface stays present and must stay unused;
                # if the router regresses to it, the recording executor sees it.
                models=SimpleNamespace(generate_content=Mock()),
                aio=SimpleNamespace(
                    models=SimpleNamespace(generate_content=generate_content),
                ),
            )
        )
        return EnhancedCommandHandler(
            bot=bot,
            image_processing_service=Mock(),
            error_manager=Mock(),
            gemini_client=gemini_client,
        )

    async def test_the_router_schedules_no_work_on_the_shared_default_executor(self):
        """The property that matters: the router must not consume a pool thread.

        Asserted by installing an instrumented default executor and checking
        nothing was submitted to it, which is the same thing every SQLite
        ``to_thread`` in the bot is competing for. A router that goes back to
        ``asyncio.to_thread`` submits here and fails.
        """
        generate_content = AsyncMock(
            return_value=SimpleNamespace(
                text='{"intent": "text", "complexity": "low", '
                     '"edit_type": null, "needs_context": true}'
            )
        )
        handler = self._make_handler(generate_content)

        loop = asyncio.get_running_loop()
        executor = _RecordingExecutor(max_workers=2)
        loop.set_default_executor(executor)
        try:
            decision = await handler._check_intent_and_complexity("hello there", False)
        finally:
            loop.set_default_executor(concurrent.futures.ThreadPoolExecutor())
            executor.shutdown(wait=False)

        generate_content.assert_awaited_once()
        self.assertEqual(decision.complexity, "low")
        self.assertEqual(
            executor.submitted,
            [],
            "the router scheduled work on the shared default executor; that pool "
            "is what every SQLite to_thread in the bot competes for, and a hung "
            "provider call holding one of its 6-8 threads starves all of them",
        )

    async def test_a_hung_router_call_is_abandoned_and_degrades_to_the_default(self):
        """A deadline the executor could never have given us.

        Under ``to_thread`` a hung call could not be abandoned at all: cancelling
        the await frees the coroutine and leaves the worker thread running.
        Awaiting the coroutine directly means the cancellation reaches the
        request. ``asyncio.TimeoutError`` is an ``Exception``, so it degrades
        through the router's existing handler to a default decision rather than
        propagating.
        """
        started = asyncio.Event()

        async def never_finishes(*_args, **_kwargs):
            started.set()
            await asyncio.Future()

        handler = self._make_handler(never_finishes, response_timeout=0.05)

        decision = await asyncio.wait_for(
            handler._check_intent_and_complexity("hello there", False),
            timeout=5,
        )

        self.assertTrue(started.is_set(), "the router never issued its request")
        # The documented safe fallback: unknown intent, low complexity, and
        # context retained.
        self.assertEqual(decision.complexity, "low")
        self.assertTrue(decision.needs_context)


if __name__ == "__main__":
    unittest.main()
