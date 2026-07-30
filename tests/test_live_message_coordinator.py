"""DAB-019: a live-mode worker exception must not destroy the batch.

`run_channel_worker` popped the whole pending batch out of the dict *before*
processing it and wrapped processing in a bare `except Exception` that only
logged. The batch was unreachable after the pop: no retry, no requeue, no
user-facing error. The probe destroyed 25 messages with one failed call and
zero replies, in the bot's flagship mention-free mode, where neither the user
nor the operator can see it happen.

These tests drive the real worker loop -- `run_channel_worker`, not
`process_messages` -- because the pop, the requeue and the attempt budget all
live there, and a test that called `process_messages` directly could not
observe any of them.

Three of them exist because the fix the ticket prescribes is wrong. B2 proposed
requeuing the popped list verbatim:

    self.pending_messages[cid] = pending_messages + self.pending_messages.get(cid, [])

which duplicates the attachment suffix `process_messages` already requeued
itself, re-debits the rate limiter for every surviving user, and re-runs a
Gemini call that has already been made and paid for. See
`docs/ANALYSIS_CORRECTIONS.md` item 8.
"""

import asyncio
import time
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock

from src.bot.live_message_coordinator import LiveMessageCoordinator


CHANNEL_ID = 77


def make_message(message_id, content, *, user_id=1, attachments=None):
    return SimpleNamespace(
        id=message_id,
        content=content,
        attachments=list(attachments or []),
        author=SimpleNamespace(id=user_id, display_name=f"User {user_id}"),
        channel=SimpleNamespace(id=CHANNEL_ID),
        reference=None,
        created_at=None,
        reply=AsyncMock(),
    )


def ok_response(content="answered"):
    return SimpleNamespace(
        success=True,
        content=content,
        grounding_sources=None,
        token_usage=None,
    )


class LiveBatchDurabilityTest(unittest.IsolatedAsyncioTestCase):
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
            "generate_response": AsyncMock(return_value=ok_response()),
            "send_response": AsyncMock(return_value=SimpleNamespace(id=9001)),
            "handle_response_error": AsyncMock(),
            "record_token_usage": AsyncMock(),
            "max_retry_attempts": 3,
            "retry_backoff_seconds": 0,
        }
        defaults.update(overrides)
        return LiveMessageCoordinator(**defaults)

    async def drive(self, coordinator, messages, *, timeout=5):
        """Queue a batch and run the worker to quiescence."""
        coordinator.pending_messages[CHANNEL_ID] = list(messages)
        await asyncio.wait_for(
            coordinator.run_channel_worker(CHANNEL_ID),
            timeout=timeout,
        )
        # The `finally` clause respawns a worker if anything is still queued.
        for _ in range(10):
            task = coordinator.tasks.get(CHANNEL_ID)
            if task is None:
                break
            await asyncio.wait_for(asyncio.shield(task), timeout=timeout)

    async def test_a_transient_failure_is_retried_and_the_batch_is_answered(self):
        generate = AsyncMock(
            side_effect=[RuntimeError("transient API 503"), ok_response()]
        )
        send = AsyncMock(return_value=SimpleNamespace(id=9001))
        coordinator = self.make_coordinator(generate_response=generate, send_response=send)
        batch = [
            make_message(1, "first"),
            make_message(2, "second"),
            make_message(3, "third"),
        ]

        await self.drive(coordinator, batch)

        self.assertEqual(generate.await_count, 2, "the failed batch was not retried")
        retried_prompt = generate.await_args_list[1].args[0]
        for text in ("first", "second", "third"):
            self.assertIn(text, retried_prompt)
        send.assert_awaited_once()
        self.assertEqual(coordinator.pending_messages, {})

    async def test_the_attachment_suffix_is_not_duplicated_by_a_retry(self):
        # `_answer_batch` requeues the post-attachment suffix itself before it
        # can fail. Requeuing the popped list verbatim therefore queues that
        # suffix a second time -- and the `finally` respawn re-splits it every
        # pass, so the duplication compounds.
        limiter = AsyncMock(
            side_effect=[
                RuntimeError("limiter unavailable"),
                RuntimeError("limiter unavailable"),
                (True, None),
                (True, None),
            ]
        )
        generate = AsyncMock(return_value=ok_response())
        process_with_context = AsyncMock()
        coordinator = self.make_coordinator(
            rate_limiter=SimpleNamespace(check_and_record=limiter),
            generate_response=generate,
            process_message_with_context=process_with_context,
        )
        batch = [
            make_message(1, "before"),
            make_message(2, "look at this", attachments=[object()]),
            make_message(3, "after"),
        ]

        await self.drive(coordinator, batch)

        process_with_context.assert_awaited_once()
        attachment_prompt = process_with_context.await_args.args[1]
        self.assertIn("before", attachment_prompt)
        self.assertIn("look at this", attachment_prompt)

        # The suffix is answered exactly once. Under the naive requeue this is
        # the four-way "New live chat messages" prompt listing message 3 over
        # and over.
        generate.assert_awaited_once()
        self.assertEqual(generate.await_args.args[0], "after")
        self.assertEqual(coordinator.pending_messages, {})

    async def test_one_turn_debits_each_user_once_however_many_attempts(self):
        async def check(user_id):
            return (False, "limited") if user_id == 1 else (True, None)

        limiter = AsyncMock(side_effect=check)
        generate = AsyncMock(
            side_effect=[RuntimeError("transient API 503"), ok_response()]
        )
        coordinator = self.make_coordinator(
            rate_limiter=SimpleNamespace(check_and_record=limiter),
            generate_response=generate,
        )
        denied = make_message(1, "denied text", user_id=1)
        allowed = make_message(2, "allowed text", user_id=2)

        await self.drive(coordinator, [denied, allowed])

        # Two users, one turn, two limiter calls -- not four. A retry that
        # re-debits can refuse a turn the limiter already allowed, which turns a
        # transient Gemini fault into a bogus "rate limit reached" reply.
        self.assertEqual([call.args[0] for call in limiter.await_args_list], [1, 2])
        # The refused user is told once and is never retried into the answer.
        denied.reply.assert_awaited_once_with("limited")
        self.assertEqual(generate.await_count, 2)
        self.assertEqual(generate.await_args_list[1].args[0], "allowed text")

    async def test_a_failure_after_the_model_answered_never_regenerates(self):
        # DAB-001 in a second file: once generate_response has returned, the
        # call has been billed. Anything that fails afterwards must not buy a
        # duplicate answer and a duplicate charge -- and must still tell the
        # user, or the fix has bought the no-duplicate half and left the
        # silent-loss half open, which is the defect it set out to close.
        failures = {
            "record_token_usage": AsyncMock(side_effect=RuntimeError("database is locked")),
            "send_response": AsyncMock(side_effect=RuntimeError("gateway hung up")),
        }
        for name, broken in failures.items():
            with self.subTest(failing=name):
                generate = AsyncMock(return_value=ok_response())
                notify = AsyncMock()
                coordinator = self.make_coordinator(
                    generate_response=generate,
                    handle_response_error=notify,
                    **{name: broken},
                )

                await self.drive(coordinator, [make_message(1, "only")])

                self.assertEqual(
                    generate.await_count,
                    1,
                    "a post-generation failure bought a second Gemini call",
                )
                notify.assert_awaited_once()
                self.assertEqual(coordinator.pending_messages, {})

    async def test_an_attachment_turn_is_not_regenerated_either(self):
        # process_message_with_context owns its own generation, billing and
        # error reporting, so a raise out of it may already have cost a call --
        # and it has already told the user itself, which is why this path does
        # not add a second notification.
        process_with_context = AsyncMock(side_effect=RuntimeError("boom"))
        coordinator = self.make_coordinator(
            process_message_with_context=process_with_context,
        )

        await self.drive(
            coordinator,
            [make_message(1, "look", attachments=[object()])],
        )

        process_with_context.assert_awaited_once()
        self.assertEqual(coordinator.pending_messages, {})

    async def test_a_cancelled_worker_does_not_lose_its_batch_in_silence(self):
        # asyncio.CancelledError is a BaseException in 3.12, so the worker's
        # `except Exception` never sees it. Without a receipt check in the
        # `finally`, a cancellation mid-generation drops the whole batch with
        # not even a log line -- DAB-019's exact symptom on the shutdown path.
        started = asyncio.Event()

        async def hang(*_args, **_kwargs):
            started.set()
            await asyncio.Event().wait()

        coordinator = self.make_coordinator(generate_response=hang)
        batch = [make_message(1, "first"), make_message(2, "second")]
        coordinator.pending_messages[CHANNEL_ID] = list(batch)
        worker = asyncio.create_task(coordinator.run_channel_worker(CHANNEL_ID))
        coordinator.tasks[CHANNEL_ID] = worker
        await asyncio.wait_for(started.wait(), timeout=2)

        worker.cancel()
        await asyncio.gather(worker, return_exceptions=True)

        # Live mode is still on, so the batch is recoverable and must be back in
        # the queue for the next worker rather than gone.
        self.assertEqual(
            [message.id for message in coordinator.pending_messages[CHANNEL_ID]],
            [1, 2],
        )

    async def test_close_discards_a_cancelled_batch_loudly_rather_than_silently(self):
        started = asyncio.Event()

        async def hang(*_args, **_kwargs):
            started.set()
            await asyncio.Event().wait()

        coordinator = self.make_coordinator(generate_response=hang)
        await coordinator.enqueue(make_message(1, "first"))
        await asyncio.wait_for(started.wait(), timeout=2)

        with self.assertLogs("src.bot.live_message_coordinator", level="WARNING") as logs:
            await coordinator.close()

        self.assertEqual(coordinator.pending_messages, {})
        self.assertTrue(
            any("unanswered" in line for line in logs.output),
            logs.output,
        )

    async def test_the_user_is_told_when_the_retry_budget_runs_out(self):
        generate = AsyncMock(side_effect=RuntimeError("permanently broken"))
        notify = AsyncMock()
        coordinator = self.make_coordinator(
            generate_response=generate,
            handle_response_error=notify,
            max_retry_attempts=3,
            retry_backoff_seconds=0.05,
        )
        batch = [make_message(1, "first"), make_message(2, "last")]

        started = time.monotonic()
        await self.drive(coordinator, batch)
        elapsed = time.monotonic() - started

        self.assertEqual(generate.await_count, 3, "the attempt budget was not honoured")
        notify.assert_awaited_once()
        # Notified against the newest message in the batch, which is the one the
        # error reply threads onto.
        self.assertIs(notify.await_args.args[0], batch[-1])
        self.assertFalse(notify.await_args.args[1].success)
        self.assertEqual(coordinator.pending_messages, {})
        # Two backoffs of 0.05 s and 0.10 s. A spinning retry returns instantly.
        self.assertGreaterEqual(elapsed, 0.1)

    async def test_a_rag_retrieval_failure_is_visible_at_the_default_log_level(self):
        # DAB-168. This is the one place in the suite where asserting on a log
        # record is asserting the contract rather than a proxy for it: the
        # defect *is* the level. At the shipped `log_level: INFO` a live-mode
        # retrieval failure was DEBUG and therefore invisible, while the
        # identical mention-path failure was a WARNING, so live mode could run
        # on rolling context alone with nothing to see. The fallback behaviour
        # itself is asserted too -- the turn is still answered.
        retrieve = AsyncMock(side_effect=RuntimeError("index unavailable"))
        generate = AsyncMock(return_value=ok_response())
        coordinator = self.make_coordinator(
            rag_enabled=lambda: True,
            retrieve_context=retrieve,
            generate_response=generate,
        )

        with self.assertLogs(
            "src.bot.live_message_coordinator", level="WARNING"
        ) as captured:
            await self.drive(coordinator, [make_message(1, "hello")])

        self.assertTrue(
            any("RAG retrieval failed" in line for line in captured.output),
            captured.output,
        )
        generate.assert_awaited_once()

    async def test_a_dropped_batch_is_still_dropped_when_no_error_callback_exists(self):
        # handle_response_error is Optional on the constructor. A bot missing it
        # must not leave the worker spinning or raise out of a bare create_task.
        generate = AsyncMock(side_effect=RuntimeError("permanently broken"))
        coordinator = self.make_coordinator(
            generate_response=generate,
            handle_response_error=None,
            max_retry_attempts=2,
        )

        await self.drive(coordinator, [make_message(1, "only")])

        self.assertEqual(generate.await_count, 2)
        self.assertEqual(coordinator.pending_messages, {})


if __name__ == "__main__":
    unittest.main()
