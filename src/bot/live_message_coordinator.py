"""Live-mode batching, queueing, and rolling-context coordination."""

from __future__ import annotations

import asyncio
import logging
from collections import deque
from datetime import datetime, timezone
from typing import Any, Awaitable, Callable, Deque, Dict, List, Optional, Tuple

import discord

from ..models.data_models import MessageContext


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
        try:
            while not self._closing:
                if not self.is_enabled(channel_id):
                    async with lock:
                        self.pending_messages.pop(channel_id, None)
                    break

                async with lock:
                    pending_messages = self.pending_messages.pop(channel_id, [])

                if not pending_messages:
                    break

                response_sent = False
                try:
                    response_sent = await self.process_messages(pending_messages)
                except Exception as exc:
                    first_message = pending_messages[0]
                    last_message = pending_messages[-1]
                    logger.error(
                        "Live worker error in channel %s for messages %s-%s: %s",
                        channel_id,
                        first_message.id,
                        last_message.id,
                        exc,
                        exc_info=True,
                    )

                if self._closing or not self.is_enabled(channel_id):
                    async with lock:
                        self.pending_messages.pop(channel_id, None)
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
        if not messages:
            return False

        channel_id = messages[-1].channel.id
        attachment_index = next(
            (index for index, message in enumerate(messages) if message.attachments),
            None,
        )
        if attachment_index is not None:
            remaining_messages = messages[attachment_index + 1:]
            messages = messages[:attachment_index + 1]
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
            is_allowed, rate_limit_message = await self.rate_limiter.check_and_record(user_id)
            if is_allowed:
                allowed_user_ids.add(user_id)
            else:
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
                )
                seen_context_ids = set()
                live_context = []
                for context_entry in rag_context + rolling_context:
                    if context_entry.message_id in seen_context_ids:
                        continue
                    seen_context_ids.add(context_entry.message_id)
                    live_context.append(context_entry)
            except Exception as exc:
                logger.debug("Live-mode RAG retrieval failed; using rolling context: %s", exc)

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
