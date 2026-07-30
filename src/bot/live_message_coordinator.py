"""Live-mode batching, queueing, and rolling-context coordination."""

from __future__ import annotations

import asyncio
import logging
from collections import deque
from datetime import datetime, timezone
from typing import Any, Awaitable, Callable, Deque, Dict, List, Optional, Set, Tuple

import discord

from ..models.data_models import APIResponse, MessageContext


logger = logging.getLogger(__name__)

AsyncCallback = Callable[..., Awaitable[Any]]


class LiveMessageCoordinator:
    """Own per-channel live-mode state and serialize batched responses."""

    def __init__(
        self,
        *,
        rate_limiter: Any,
        extract_user_prompt: Callable[[discord.Message], str],
        process_message_with_context: AsyncCallback,
        is_enabled: Callable[[int], bool],
        model_name: str,
        cooldown_seconds: float,
        reply_style_instruction: str,
        turn_window: int,
        rag_enabled: Callable[[], bool],
        retrieve_context: Optional[AsyncCallback] = None,
        generate_response: Optional[AsyncCallback] = None,
        handle_response_error: Optional[AsyncCallback] = None,
        record_token_usage: Optional[AsyncCallback] = None,
        send_response: Optional[AsyncCallback] = None,
        get_bot_user: Callable[[], Any] = lambda: None,
        get_personality_prompt: Callable[[int], Optional[str]] = lambda _channel_id: None,
        tasks: Optional[Dict[int, asyncio.Task]] = None,
        pending_messages: Optional[Dict[int, List[discord.Message]]] = None,
        locks: Optional[Dict[int, asyncio.Lock]] = None,
        context: Optional[Dict[int, Deque[MessageContext]]] = None,
        max_retry_attempts: int = 3,
        retry_backoff_seconds: float = 1.0,
    ) -> None:
        self.rate_limiter = rate_limiter
        self.extract_user_prompt = extract_user_prompt
        self.process_message_with_context = process_message_with_context
        self.is_enabled = is_enabled
        self.model_name = model_name
        self.cooldown_seconds = cooldown_seconds
        self.reply_style_instruction = reply_style_instruction
        self.turn_window = turn_window
        self.rag_enabled = rag_enabled
        self.retrieve_context = retrieve_context
        self.generate_response = generate_response
        self.handle_response_error = handle_response_error
        self.record_token_usage = record_token_usage
        self.send_response = send_response
        self.get_bot_user = get_bot_user
        self.get_personality_prompt = get_personality_prompt

        self.tasks = tasks if tasks is not None else {}
        self.pending_messages = pending_messages if pending_messages is not None else {}
        self.locks = locks if locks is not None else {}
        self.context = context if context is not None else {}
        self._closing = False

        # Retry policy for a batch whose processing raised. Constructor
        # arguments rather than config.yaml keys: this is a fixed durability
        # policy, not an operator dial, and the four-place config rule in
        # AGENTS.md would cost four files for a value nobody tunes. Tests set
        # the backoff to 0.
        self.max_retry_attempts = max_retry_attempts
        self.retry_backoff_seconds = retry_backoff_seconds

    def get_channel_lock(self, channel_id: int) -> asyncio.Lock:
        """Get or create the lock guarding one channel's live state."""
        lock = self.locks.get(channel_id)
        if lock is None:
            lock = asyncio.Lock()
            self.locks[channel_id] = lock
        return lock

    def get_context_buffer(self, channel_id: int) -> Deque[MessageContext]:
        """Get or create the rolling in-memory context for one channel."""
        buffer = self.context.get(channel_id)
        if buffer is None:
            buffer = deque(maxlen=self.turn_window * 2)
            self.context[channel_id] = buffer
        return buffer

    async def enqueue(self, message: discord.Message) -> None:
        """Enqueue a message and ensure exactly one worker owns its channel."""
        if self._closing:
            return

        channel_id = message.channel.id
        lock = self.get_channel_lock(channel_id)
        async with lock:
            if self._closing:
                return
            self.pending_messages.setdefault(channel_id, []).append(message)
            task = self.tasks.get(channel_id)
            if task and not task.done():
                return

            self.tasks[channel_id] = asyncio.create_task(
                self.run_channel_worker(channel_id),
                name=f"live-worker-{channel_id}",
            )

    async def run_channel_worker(self, channel_id: int) -> None:
        """Process one channel serially, preserving the response cooldown."""
        lock = self.get_channel_lock(channel_id)
        current_task = asyncio.current_task()

        # Retry bookkeeping is deliberately local to the worker rather than a
        # per-channel dict on the coordinator. A retry re-enters this same loop
        # via `continue`, so no state has to outlive the worker -- and the
        # coordinator gains no seventh never-evicted container.
        #
        # `owed` is the receipt: the messages this batch still has to answer.
        # `_answer_batch` narrows it as the batch narrows and empties it the
        # moment a paid model call has been made, so the retry can never
        # re-answer or re-bill a turn.
        # `charged` remembers which users the rate limiter has already debited
        # for this turn, so a retry does not spend a second token per user.
        owed: List[discord.Message] = []
        charged: Set[int] = set()
        attempts = 0
        try:
            while not self._closing:
                if not self.is_enabled(channel_id):
                    async with lock:
                        self._drain_disabled_channel(channel_id)
                    break

                async with lock:
                    pending_messages = self.pending_messages.pop(channel_id, [])

                if not pending_messages:
                    break

                response_sent = False
                try:
                    response_sent = await self._answer_batch(
                        pending_messages,
                        owed=owed,
                        charged=charged,
                    )
                except Exception as exc:
                    attempts += 1
                    logger.error(
                        "Live worker error in channel %s for messages %s-%s "
                        "(attempt %s of %s, %s message(s) still unanswered): %s",
                        channel_id,
                        pending_messages[0].id,
                        pending_messages[-1].id,
                        attempts,
                        self.max_retry_attempts,
                        len(owed),
                        exc,
                        exc_info=True,
                    )
                    if owed and attempts < self.max_retry_attempts:
                        async with lock:
                            queued = self.pending_messages.get(channel_id, [])
                            self.pending_messages[channel_id] = list(owed) + queued
                        await asyncio.sleep(self.retry_backoff_seconds * attempts)
                        continue
                    if owed:
                        await self._abandon_batch(channel_id, list(owed))
                    attempts = 0
                    charged.clear()
                else:
                    attempts = 0
                    charged.clear()

                if self._closing or not self.is_enabled(channel_id):
                    async with lock:
                        self._drain_disabled_channel(channel_id)
                    break

                if response_sent:
                    await asyncio.sleep(self.cooldown_seconds)
        finally:
            async with lock:
                if self.tasks.get(channel_id) is current_task:
                    self.tasks.pop(channel_id, None)
                has_pending = bool(self.pending_messages.get(channel_id))
                if not self._closing and has_pending and self.is_enabled(channel_id):
                    self.tasks[channel_id] = asyncio.create_task(
                        self.run_channel_worker(channel_id),
                        name=f"live-worker-{channel_id}",
                    )

    def _drain_disabled_channel(self, channel_id: int) -> None:
        """Discard a channel's queue when live mode stops owning it.

        Dropping the queue here is deliberate and pre-existing: the operator
        turned live mode off, so the bot should stop answering. What was not
        deliberate is doing it in silence, which is indistinguishable from
        DAB-019's own symptom.
        """
        dropped = self.pending_messages.pop(channel_id, None)
        if dropped:
            logger.warning(
                "Discarded %s queued live message(s) in channel %s: live mode is "
                "no longer enabled for it",
                len(dropped),
                channel_id,
            )

    async def _abandon_batch(
        self,
        channel_id: int,
        owed: List[discord.Message],
    ) -> None:
        """Tell the user their turn was dropped after the retry budget ran out."""
        logger.error(
            "Abandoning %s live message(s) in channel %s after %s failed attempts",
            len(owed),
            channel_id,
            self.max_retry_attempts,
        )
        if self.handle_response_error is None:
            logger.error(
                "No live error callback is wired, so the %s dropped message(s) in "
                "channel %s cannot be acknowledged to the user",
                len(owed),
                channel_id,
            )
            return

        # content stays None on purpose: handle_response_error appends it to the
        # Discord reply verbatim, and str(exc) leaks paths and SQL (DAB-153).
        try:
            await self.handle_response_error(
                owed[-1],
                APIResponse(success=False, error_type="unknown_error"),
            )
        except Exception as exc:
            # This runs inside a bare create_task; letting it escape produces
            # only an unretrieved-task warning and loses the original fault.
            logger.error(
                "Could not notify channel %s that its live batch was dropped: %s",
                channel_id,
                exc,
                exc_info=True,
            )

    async def close(self) -> None:
        """Cancel active workers without allowing late worker restarts."""
        if self._closing:
            active_tasks = [task for task in self.tasks.values() if not task.done()]
            if active_tasks:
                await asyncio.gather(*active_tasks, return_exceptions=True)
            return

        self._closing = True
        self.pending_messages.clear()
        active_tasks = [task for task in self.tasks.values() if not task.done()]
        for task in active_tasks:
            task.cancel()
        if active_tasks:
            await asyncio.gather(*active_tasks, return_exceptions=True)
        self.tasks.clear()

    def append_context_entry(
        self,
        channel_id: int,
        content: str,
        author: str,
        message_id: int,
        timestamp: datetime,
        is_reply: bool = False,
        replied_to_id: Optional[int] = None,
    ) -> None:
        """Append one bounded entry to the channel's rolling context."""
        cleaned = (content or "").strip()
        if not cleaned:
            return
        if len(cleaned) > 1200:
            cleaned = cleaned[:1200] + "..."

        self.get_context_buffer(channel_id).append(
            MessageContext(
                content=cleaned,
                author=author,
                timestamp=timestamp,
                message_id=message_id,
                channel_id=channel_id,
                is_reply=is_reply,
                replied_to_id=replied_to_id,
            )
        )

    async def process_messages(self, messages: List[discord.Message]) -> bool:
        """Process one ordered batch of live-mode messages."""
        return await self._answer_batch(messages, owed=[], charged=set())

    async def _answer_batch(
        self,
        messages: List[discord.Message],
        *,
        owed: List[discord.Message],
        charged: Set[int],
    ) -> bool:
        """Answer one ordered batch, keeping `owed` and `charged` accurate.

        `owed` is a caller-supplied list that this method keeps equal to "the
        messages this call still has to answer". The worker requeues exactly
        that list when this method raises, which is what makes a retry safe:

        - it is narrowed the moment the batch is narrowed (attachment split,
          rate-limit refusal), so nothing already requeued or already answered
          with a refusal is answered twice;
        - it is emptied the moment a model call has been *made*, because from
          that point the turn has been paid for and re-running it would buy a
          duplicate reply and a duplicate Gemini charge -- the DAB-001 shape.
          A model call that *raises* has produced nothing and is retryable;
          one that *returns*, successfully or not, is not.

        On every normal return `owed` is empty.

        `charged` accumulates the user ids the rate limiter has already debited
        for this turn. A retry skips them, so one turn costs each participant
        exactly one token however many attempts it takes.
        """
        if not messages:
            owed.clear()
            return False

        owed[:] = messages
        channel_id = messages[-1].channel.id
        attachment_index = next(
            (index for index, message in enumerate(messages) if message.attachments),
            None,
        )
        if attachment_index is not None:
            remaining_messages = messages[attachment_index + 1:]
            messages = messages[:attachment_index + 1]
            owed[:] = messages
            if remaining_messages:
                lock = self.get_channel_lock(channel_id)
                async with lock:
                    newly_queued = self.pending_messages.get(channel_id, [])
                    self.pending_messages[channel_id] = remaining_messages + newly_queued

        rate_limit_replied = False
        allowed_user_ids = set()
        messages_by_user: Dict[int, List[discord.Message]] = {}
        for queued_message in messages:
            messages_by_user.setdefault(queued_message.author.id, []).append(queued_message)

        for user_id, user_messages in messages_by_user.items():
            # A user the limiter already cleared for this turn is not debited
            # again by a retry.
            if user_id in charged:
                allowed_user_ids.add(user_id)
                continue

            is_allowed, rate_limit_message = await self.rate_limiter.check_and_record(user_id)
            if is_allowed:
                charged.add(user_id)
                allowed_user_ids.add(user_id)
            else:
                # Drop the refused user from the receipt *before* replying. A
                # refusal is a deliberate, notified drop -- retrying it would
                # either debit them twice or tell them twice -- and narrowing
                # here rather than after the loop keeps that true even if the
                # limiter raises on a later user.
                owed[:] = [
                    message for message in owed if message.author.id != user_id
                ]
                try:
                    await user_messages[-1].reply(rate_limit_message)
                    rate_limit_replied = True
                except (discord.Forbidden, discord.HTTPException) as exc:
                    logger.warning(
                        "Could not send live-mode rate-limit reply to user %s: %s",
                        user_id,
                        exc,
                    )

        messages = [message for message in messages if message.author.id in allowed_user_ids]
        if not messages:
            return rate_limit_replied

        target_message = messages[-1]
        prompt_entries: List[Tuple[discord.Message, str]] = []
        for message in messages:
            prompt = self.extract_user_prompt(message)
            if prompt:
                prompt_entries.append((message, prompt))

        if messages[-1].attachments:
            attachment_message = messages[-1]
            attachment_entries = [
                (message, self.extract_user_prompt(message))
                for message in messages
            ]
            attachment_entries = [entry for entry in attachment_entries if entry[1]]
            attachment_prompt = self.build_user_prompt(attachment_entries)
            # Cleared *before* the await, not after. This call owns its own
            # generation, billing and user-facing error handling, so a raise out
            # of it may already have cost a Gemini call; retrying it could buy a
            # second one. Everything up to here is retryable, this is not.
            owed.clear()
            await self.process_message_with_context(
                attachment_message,
                attachment_prompt,
                complexity_level="low",
                routed_intent="live_mode",
                model_override=self.model_name,
                prompt_mode_override="short",
                search_override=False,
                show_status_message=False,
                skip_context_media=False,
                apply_user_preferences=False,
            )
            return True

        if not prompt_entries:
            owed.clear()
            return False

        user_prompt = self.build_user_prompt(prompt_entries)
        rolling_context = list(self.get_context_buffer(channel_id))
        live_context = rolling_context
        bot_user = self.get_bot_user()
        if self.rag_enabled() and self.retrieve_context is not None:
            try:
                rag_context = await self.retrieve_context(
                    message=target_message,
                    user_prompt=user_prompt,
                    complexity_level="low",
                    bot_user_id=bot_user.id if bot_user else None,
                    force_full_context=True,
                )
                seen_context_ids = set()
                live_context = []
                for context_entry in rag_context + rolling_context:
                    if context_entry.message_id in seen_context_ids:
                        continue
                    seen_context_ids.add(context_entry.message_id)
                    live_context.append(context_entry)
            except Exception as exc:
                # WARNING, matching the mention path's identical fallback at
                # discord_bot.py:894-899. At the shipped log_level: INFO this
                # was the only one of the two that was invisible, so live mode
                # could run indefinitely on rolling context alone, with the
                # index never consulted and nothing to see (DAB-168).
                logger.warning(
                    "Live-mode RAG retrieval failed; using rolling context: %s",
                    exc,
                    exc_info=True,
                )

        personality_prompt = self.reply_style_instruction
        channel_personality = self.get_personality_prompt(channel_id)
        if channel_personality:
            personality_prompt = f"{channel_personality}\n{self.reply_style_instruction}"

        if self.generate_response is None:
            raise RuntimeError("Live response generation callback is unavailable")
        api_response = await self.generate_response(
            user_prompt,
            live_context,
            model_override=self.model_name,
            prompt_mode_override="short",
            complexity_override="low",
            search_override=False,
            personality_prompt=personality_prompt,
            language=None,
        )
        # The model has answered and the call has been billed. Nothing after
        # this line may be retried, because a retry would generate again.
        owed.clear()

        if not api_response.success:
            if self.handle_response_error is None:
                raise RuntimeError("Live response error callback is unavailable")
            await self.handle_response_error(target_message, api_response)
            return True

        if self.record_token_usage is not None:
            await self.record_token_usage(target_message, api_response.token_usage)
        if self.send_response is None:
            raise RuntimeError("Live response delivery callback is unavailable")
        sent_message = await self.send_response(
            target_message,
            api_response.content,
            api_response.grounding_sources,
        )

        for source_message, source_prompt in prompt_entries:
            self.append_context_entry(
                channel_id=channel_id,
                content=source_prompt,
                author=source_message.author.display_name,
                message_id=source_message.id,
                timestamp=source_message.created_at,
                is_reply=source_message.reference is not None,
                replied_to_id=(
                    source_message.reference.message_id
                    if source_message.reference
                    else None
                ),
            )

        if sent_message:
            bot_user = self.get_bot_user()
            self.append_context_entry(
                channel_id=channel_id,
                content=api_response.content,
                author=bot_user.display_name if bot_user else "Grok",
                message_id=sent_message.id,
                timestamp=datetime.now(timezone.utc),
                is_reply=True,
                replied_to_id=target_message.id,
            )
            return True

        return rate_limit_replied

    @staticmethod
    def build_user_prompt(prompt_entries: List[Tuple[discord.Message, str]]) -> str:
        """Build one ordered prompt from a live-mode message batch."""
        if not prompt_entries:
            return ""
        if len(prompt_entries) == 1:
            return prompt_entries[0][1]
        user_prompt_lines = [
            f"- {message.author.display_name}: {prompt}"
            for message, prompt in prompt_entries
        ]
        return (
            "New live chat messages (oldest to newest):\n"
            + "\n".join(user_prompt_lines)
            + "\nReply once to all of these in one short chat response."
        )
