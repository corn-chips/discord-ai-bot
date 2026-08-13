"""
Hybrid context retriever for Discord message RAG.

Combines reply anchors, pinned memories, recent indexed messages, SQLite FTS5,
and locally stored Gemini embeddings. The final candidate pool can be reranked
by the existing Gemini router context selector.
"""

import asyncio
import logging
import re
import time
from collections import OrderedDict
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Optional

from ..config import BotConfig
from ..models.data_models import MessageContext
from .context_collector import ContextCollector
from .context_pack_builder import MIN_RETRIEVAL_SLOTS, ContextPackBuilder
from .message_index_service import IndexedMessage, MessageIndexService

logger = logging.getLogger(__name__)


@dataclass
class _Candidate:
    message: IndexedMessage
    score: float = 0.0
    sources: set[str] = field(default_factory=set)
    reason_parts: list[str] = field(default_factory=list)

    def add(self, source: str, score: float, reason: str) -> None:
        self.sources.add(source)
        self.score += score
        self.reason_parts.append(reason)


_PRONOUNS = frozenset({
    "he", "him", "his", "she", "her", "hers", "they", "them", "their", "theirs",
    "it", "its", "this", "that", "these", "those", "there", "then", "one",
})

# Capitalised tokens that name nothing, so they never enter a rewrite.
_NON_ENTITY_WORDS = frozenset({
    "the", "and", "but", "for", "you", "your", "yes", "not", "why", "who", "what",
    "when", "where", "how", "did", "does", "was", "were", "http", "https", "com",
    "hey", "okay", "sure", "thanks", "please", "there", "then", "they", "this",
    "that", "these", "those", "his", "her", "him", "she", "its", "it's",
})

_WORD_RE = re.compile(r"[\w']+")
_ENTITY_RE = re.compile(r"\b[A-Z][\w'-]{2,}\b")

#: Synthetic message ids for profile cards, kept clear of `PIN_MESSAGE_ID_OFFSET`
#: (9e18) so a card and a pin can never collide in the pack's dedup set.
ENTITY_CARD_MESSAGE_ID_OFFSET = 8_000_000_000_000_000_000


class HybridContextRetriever:
    """Retrieves and packs relevant Discord context for the response model."""

    EMBEDDING_BATCH_SIZE = 16
    BACKGROUND_EMBED_DELAY_SECONDS = 2.0
    # Profiles refresh on a 24-hour cadence, so one pass per guild per minute is
    # already far more often than they can change; the delay is what keeps a
    # busy channel from re-scanning the index on every message.
    BACKGROUND_PROFILE_DELAY_SECONDS = 60.0
    # One profile summary reads at most this many of the person's conversations
    # per pass; the rest wait for the next one.
    PROFILE_MAX_CONVERSATIONS = 8
    PROFILE_MAX_MESSAGES_PER_CONVERSATION = 30
    PROFILE_TRANSCRIPT_CHARS = 6000
    # The profile drain is bounded from the embedding drain's two knobs, scaled
    # for a subsystem whose unit of work is a whole generate_response call rather
    # than one 16-message embedding batch. Sharing the dials outright let one
    # pass buy 200 completions.
    PROFILE_DRAIN_BATCH_DIVISOR = 10
    PROFILE_DRAIN_MAX_PROFILES = 20
    PROFILE_DRAIN_DELAY_FACTOR = 3.0
    MAX_ENTITY_CARDS = 2
    # A follow-up this short carries no retrievable terms of its own.
    REWRITE_SHORT_QUERY_WORDS = 4
    REWRITE_MAX_TERMS = 6
    # Rewrite legs fuse below the raw query so a bad rewrite cannot outrank it.
    REWRITE_FUSION_FACTOR = 0.6
    # Conversation filler ranks below the hit it surrounds.
    FILLER_SCORE_FACTOR = 0.25

    def __init__(
        self,
        *,
        config: BotConfig,
        message_index: MessageIndexService,
        context_collector: ContextCollector,
        gemini_client,
        pack_builder: ContextPackBuilder,
        pin_service=None,
        entity_profile_service=None,
    ):
        self.config = config
        self.message_index = message_index
        self.context_collector = context_collector
        self.gemini_client = gemini_client
        self.pack_builder = pack_builder
        self.pin_service = pin_service
        self.entity_profile_service = entity_profile_service
        self._embedding_lock = asyncio.Lock()
        self._query_embedding_cache: "OrderedDict[str, tuple[float, list[float]]]" = OrderedDict()
        self._query_embedding_cache_size = max(1, getattr(config, "rag_query_embedding_cache_size", 128))
        self._query_embedding_cache_ttl = max(1, getattr(config, "rag_query_embedding_cache_ttl", 900))
        self._embedding_tasks: dict[int, asyncio.Task] = {}
        self._profile_tasks: dict[int, asyncio.Task] = {}
        self._pregeneration_tasks: dict[int, asyncio.Task] = {}
        self._pregeneration_status: dict[int, dict] = {}
        self._all_channels_task: Optional[asyncio.Task] = None
        self._all_channels_status: Optional[dict] = None
        self._closed = False

    def set_pin_service(self, pin_service) -> None:
        self.pin_service = pin_service

    def _max_messages_for_complexity(self, complexity_level: str) -> int:
        if complexity_level == "high":
            return self.config.rag_max_context_messages_high
        if complexity_level == "medium":
            return self.config.rag_max_context_messages_medium
        return self.config.rag_max_context_messages_low

    def _embedding_timeout(self) -> float:
        return float(getattr(self.config, "rag_embedding_timeout_seconds", 30.0))

    @staticmethod
    def _recency_boost(created_at: datetime, half_life_hours: int) -> float:
        now = datetime.now(timezone.utc)
        timestamp = created_at if created_at.tzinfo else created_at.replace(tzinfo=timezone.utc)
        age_hours = max(0.0, (now - timestamp).total_seconds() / 3600.0)
        return 0.5 ** (age_hours / max(1, half_life_hours))

    async def embed_pending_documents(
        self,
        *,
        channel_id: Optional[int] = None,
    ) -> tuple[int, int, int]:
        """Embed one local batch and return processed, stored, and failed counts."""
        if not getattr(self.gemini_client, "client", None):
            return 0, 0, 0

        async with self._embedding_lock:
            pending = await self.message_index.get_pending_embeddings_async(
                limit=min(
                    self.EMBEDDING_BATCH_SIZE,
                    max(1, self.config.rag_semantic_candidates),
                ),
                channel_id=channel_id,
            )
            if not pending:
                return 0, 0, 0

            texts = [text for _message_id, text, _content_hash in pending]
            timeout = self._embedding_timeout()
            try:
                vectors = await asyncio.wait_for(
                    self.gemini_client.embed_texts(
                        texts,
                        model_name=self.config.rag_embedding_model,
                        task_type="RETRIEVAL_DOCUMENT",
                    ),
                    timeout=timeout,
                )
            except asyncio.TimeoutError:
                # Unhandled, this left the rows pending and unattempted, so the
                # next drain re-sent and re-paid for the identical batch.
                logger.warning(
                    "RAG document embedding timed out after %ss; marking %s row(s) failed",
                    timeout,
                    len(pending),
                )
                for message_id, _text, _content_hash in pending:
                    await self.message_index.mark_embedding_failed_async(
                        message_id,
                        "embedding request timed out",
                    )
                return len(pending), 0, len(pending)
            stored = 0
            failed = 0
            for (message_id, _text, content_hash), vector in zip(pending, vectors):
                if vector:
                    if await self.message_index.store_embedding_async(
                        message_id,
                        vector,
                        content_hash,
                    ):
                        stored += 1
                else:
                    failed += 1
                    await self.message_index.mark_embedding_failed_async(
                        message_id,
                        "empty embedding response",
                    )
            return len(pending), stored, failed

    async def _drain_pending_documents(
        self,
        *,
        channel_id: int,
        initial_delay: float = 0.0,
    ) -> tuple[int, int, int]:
        if initial_delay > 0:
            await asyncio.sleep(initial_delay)

        totals = [0, 0, 0]
        batches = 0
        max_batches = max(1, int(getattr(self.config, "rag_embedding_drain_max_batches", 200)))
        batch_delay = max(0.0, float(getattr(self.config, "rag_embedding_batch_delay_seconds", 1.0)))
        while not self._closed:
            processed, stored, failed = await self.embed_pending_documents(
                channel_id=channel_id,
            )
            totals[0] += processed
            totals[1] += stored
            totals[2] += failed
            batches += 1
            if processed == 0 or (stored == 0 and failed > 0):
                break
            if batches >= max_batches:
                logger.info(
                    "RAG embedding drain for channel %s stopped at its %s-batch cap "
                    "(processed=%s stored=%s failed=%s); the remainder waits for a later pass",
                    channel_id, max_batches, totals[0], totals[1], totals[2],
                )
                break
            if batch_delay:
                await asyncio.sleep(batch_delay)
        return tuple(totals)

    def schedule_pending_embeddings(self, channel_id: int) -> None:
        """Debounce local document embedding so message handling never awaits it."""
        if self._closed or not getattr(self.gemini_client, "client", None):
            return
        channel_id = int(channel_id)
        active = self._embedding_tasks.get(channel_id)
        if active is not None and not active.done():
            return

        task = asyncio.create_task(
            self._drain_pending_documents(
                channel_id=channel_id,
                initial_delay=self.BACKGROUND_EMBED_DELAY_SECONDS,
            )
        )
        self._embedding_tasks[channel_id] = task

        def cleanup(completed: asyncio.Task) -> None:
            if self._embedding_tasks.get(channel_id) is completed:
                self._embedding_tasks.pop(channel_id, None)
            if completed.cancelled():
                return
            error = completed.exception()
            if error is not None:
                logger.warning(
                    "Background RAG embedding failed for channel %s: %s",
                    channel_id,
                    error,
                )

        task.add_done_callback(cleanup)

    def _entity_profiles_available(self) -> bool:
        return bool(
            self.entity_profile_service
            and getattr(self.config, "rag_entity_profiles_enabled", True)
        )

    async def _summarize_profile(self, profile) -> Optional[bool]:
        """Update one person's card. None means there was nothing new to read."""
        service = self.entity_profile_service
        try:
            transcript, watermark = await service.collect_new_activity_async(
                profile,
                max_conversations=self.PROFILE_MAX_CONVERSATIONS,
                max_messages_per_conversation=self.PROFILE_MAX_MESSAGES_PER_CONVERSATION,
                max_chars=self.PROFILE_TRANSCRIPT_CHARS,
            )
            if not transcript or watermark <= profile.summarized_message_id:
                return None

            max_chars = int(getattr(self.config, "rag_entity_profile_max_chars", 1200))
            existing = (profile.summary or "").strip() or "(none yet)"
            prompt = (
                f"Maintain a factual profile of the Discord user {profile.display_name} "
                f"(id {profile.author_id}).\n\n"
                f"Existing profile:\n{existing}\n\n"
                f"New conversations since the last update:\n{transcript}\n\n"
                f"Rewrite the profile so it covers both, in under {max_chars} characters. "
                "State who they are, what they work on, what they have done or decided here, "
                "and how they interact. Plain prose, no preamble, no speculation."
            )
            response = await self.gemini_client.generate_response(
                prompt,
                complexity_override="low",
            )
            summary = (getattr(response, "content", "") or "").strip()
            if not getattr(response, "success", False) or not summary:
                raise RuntimeError(
                    getattr(response, "error_type", None) or "empty profile summary"
                )
            await service.store_summary_async(
                profile.guild_id,
                profile.author_id,
                summary=summary[:max_chars],
                covered_message_id=watermark,
            )
            return True
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            # The watermark is untouched, so nothing is skipped for good; the
            # failure's backoff is what keeps the retry from being immediate.
            logger.warning(
                "Entity profile summary failed for %s: %s", profile.author_id, exc
            )
            await service.record_failure_async(profile.guild_id, profile.author_id, str(exc))
            return False

    async def _drain_entity_profiles(
        self,
        *,
        guild_id: int,
        initial_delay: float = 0.0,
    ) -> tuple[int, int]:
        if initial_delay > 0:
            await asyncio.sleep(initial_delay)
        # Profiles are guild memory; a DM has no scope to keep one in.
        if not guild_id or not self._entity_profiles_available():
            return 0, 0

        service = self.entity_profile_service
        await service.observe_authors_async(guild_id)
        max_profiles = max(
            1,
            min(
                self.PROFILE_DRAIN_MAX_PROFILES,
                int(getattr(self.config, "rag_embedding_drain_max_batches", 200))
                // self.PROFILE_DRAIN_BATCH_DIVISOR,
            ),
        )
        delay = (
            max(0.0, float(getattr(self.config, "rag_embedding_batch_delay_seconds", 1.0)))
            * self.PROFILE_DRAIN_DELAY_FACTOR
        )
        due = await service.profiles_due_async(
            guild_id,
            min_messages=int(getattr(self.config, "rag_entity_profile_min_messages", 20)),
            refresh_hours=float(getattr(self.config, "rag_entity_profile_refresh_hours", 24.0)),
            limit=max_profiles,
        )

        summarized = 0
        failed = 0
        for index, profile in enumerate(due):
            if self._closed:
                break
            outcome = await self._summarize_profile(profile)
            if outcome is True:
                summarized += 1
            elif outcome is False:
                failed += 1
            if delay and index + 1 < len(due):
                await asyncio.sleep(delay)
        if summarized or failed:
            logger.info(
                "Entity profile pass for guild %s: summarized=%s failed=%s due=%s",
                guild_id, summarized, failed, len(due),
            )
        return summarized, failed

    def schedule_entity_profiles(self, guild_id: Optional[int]) -> None:
        """Debounce profile summarization so no user-visible reply waits on it."""
        if self._closed or not guild_id or not self._entity_profiles_available():
            return
        if not getattr(self.gemini_client, "client", None):
            return
        guild_key = int(guild_id)
        active = self._profile_tasks.get(guild_key)
        if active is not None and not active.done():
            return

        task = asyncio.create_task(
            self._drain_entity_profiles(
                guild_id=guild_key,
                initial_delay=self.BACKGROUND_PROFILE_DELAY_SECONDS,
            )
        )
        self._profile_tasks[guild_key] = task

        def cleanup(completed: asyncio.Task) -> None:
            if self._profile_tasks.get(guild_key) is completed:
                self._profile_tasks.pop(guild_key, None)
            if completed.cancelled():
                return
            error = completed.exception()
            if error is not None:
                logger.warning(
                    "Background entity profile pass failed for guild %s: %s",
                    guild_key,
                    error,
                )

        task.add_done_callback(cleanup)

    def start_channel_pregeneration(
        self,
        channel,
        *,
        limit: Optional[int],
        include_bot_user_id: Optional[int] = None,
    ) -> bool:
        """Start one complete-history indexing and embedding job for a channel."""
        if self._closed:
            return False
        channel_id = int(channel.id)
        active = self._pregeneration_tasks.get(channel_id)
        if active is not None and not active.done():
            return False

        self._pregeneration_status[channel_id] = {
            "phase": "scanning",
            "limit": limit,
            "indexed": 0,
            "embedded": 0,
            "failed": 0,
            "error": None,
        }
        task = asyncio.create_task(
            self._run_channel_pregeneration(
                channel,
                limit=limit,
                include_bot_user_id=include_bot_user_id,
            )
        )
        self._pregeneration_tasks[channel_id] = task
        return True

    def start_all_channel_pregeneration(
        self,
        channels,
        *,
        limit: Optional[int],
        include_bot_user_id: Optional[int] = None,
    ) -> bool:
        """Start a sequential resumable backlog pass over accessible channels."""
        if self._closed:
            return False
        active = self._all_channels_task
        if active is not None and not active.done():
            return False

        unique_channels = []
        seen_channel_ids = set()
        for channel in channels:
            channel_id = int(channel.id)
            if channel_id in seen_channel_ids:
                continue
            seen_channel_ids.add(channel_id)
            unique_channels.append(channel)
        if not unique_channels:
            return False

        self._all_channels_status = {
            "phase": "running",
            "total": len(unique_channels),
            "processed": 0,
            "failed": 0,
        }
        self._all_channels_task = asyncio.create_task(
            self._run_all_channel_pregeneration(
                unique_channels,
                limit=limit,
                include_bot_user_id=include_bot_user_id,
            )
        )
        return True

    async def _run_all_channel_pregeneration(
        self,
        channels,
        *,
        limit: Optional[int],
        include_bot_user_id: Optional[int],
    ) -> None:
        progress = self._all_channels_status
        try:
            for channel in channels:
                if self._closed:
                    break
                channel_id = int(channel.id)
                started = self.start_channel_pregeneration(
                    channel,
                    limit=limit,
                    include_bot_user_id=include_bot_user_id,
                )
                task = self._pregeneration_tasks.get(channel_id)
                if task is not None and not task.done():
                    await task
                if not started and task is None:
                    logger.warning(
                        "Skipped RAG backlog channel %s because it could not be started",
                        channel_id,
                    )
                channel_status = self._pregeneration_status.get(channel_id, {})
                progress["processed"] += 1
                if channel_status.get("phase") == "failed":
                    progress["failed"] += 1
            progress["phase"] = "complete"
            logger.info(
                "RAG backlog pass complete: processed=%s total=%s failed=%s",
                progress["processed"],
                progress["total"],
                progress["failed"],
            )
        except asyncio.CancelledError:
            progress["phase"] = "cancelled"
            raise
        finally:
            if self._all_channels_task is asyncio.current_task():
                self._all_channels_task = None

    async def _run_channel_pregeneration(
        self,
        channel,
        *,
        limit: Optional[int],
        include_bot_user_id: Optional[int],
    ) -> None:
        channel_id = int(channel.id)
        progress = self._pregeneration_status[channel_id]
        try:
            indexed = await self.message_index.backfill_channel(
                channel,
                limit=limit,
                include_bot_user_id=include_bot_user_id,
            )
            progress.update(phase="embedding", indexed=indexed)
            _processed, embedded, failed = await self._drain_pending_documents(
                channel_id=channel_id,
            )
            progress.update(embedded=embedded, failed=failed)

            status = await self.message_index.get_status_async(channel_id=channel_id)
            if status.get("error"):
                # A degraded status is all zeros, so the test below would read
                # it as "nothing pending, nothing failed" and declare the run
                # complete over a database it could not open (DAB-170).
                progress.update(phase="failed", error=status["error"])
            elif status["pending_embeddings"] or status["failed_embeddings"]:
                progress["phase"] = "partial"
            else:
                progress["phase"] = "complete"
            logger.info(
                "RAG pre-generation %s for channel %s: indexed=%s embedded=%s pending=%s failed=%s",
                progress["phase"],
                channel_id,
                indexed,
                status["embedded"],
                status["pending_embeddings"],
                status["failed_embeddings"],
            )
        except asyncio.CancelledError:
            progress["phase"] = "cancelled"
            raise
        except Exception as exc:
            progress.update(phase="failed", error=str(exc))
            logger.warning(
                "RAG pre-generation failed for channel %s: %s",
                channel_id,
                exc,
                exc_info=True,
            )
        finally:
            self._pregeneration_tasks.pop(channel_id, None)

    def get_pregeneration_status(self, channel_id: int) -> Optional[dict]:
        status = self._pregeneration_status.get(int(channel_id))
        return dict(status) if status is not None else None

    def get_all_channels_pregeneration_status(self) -> Optional[dict]:
        return (
            dict(self._all_channels_status)
            if self._all_channels_status is not None
            else None
        )

    async def cancel_background_work(
        self,
        *,
        channel_id: Optional[int] = None,
    ) -> None:
        """Stop jobs that could repopulate RAG data while it is being deleted."""
        tasks = set()
        if self._all_channels_task is not None and not self._all_channels_task.done():
            tasks.add(self._all_channels_task)

        if channel_id is None:
            tasks.update(
                task
                for task in (
                    *self._embedding_tasks.values(),
                    *self._profile_tasks.values(),
                    *self._pregeneration_tasks.values(),
                )
                if not task.done()
            )
        else:
            channel_id = int(channel_id)
            for task in (
                self._embedding_tasks.get(channel_id),
                self._pregeneration_tasks.get(channel_id),
            ):
                if task is not None and not task.done():
                    tasks.add(task)

        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)

        if channel_id is None:
            self._embedding_tasks.clear()
            # Profile work is guild-scoped, so only a global cancel stops it.
            self._profile_tasks.clear()
            self._pregeneration_tasks.clear()
            for status in self._pregeneration_status.values():
                if status.get("phase") in {"scanning", "embedding"}:
                    status["phase"] = "cancelled"
        else:
            self._embedding_tasks.pop(channel_id, None)
            self._pregeneration_tasks.pop(channel_id, None)
            status = self._pregeneration_status.get(channel_id)
            if status and status.get("phase") in {"scanning", "embedding"}:
                status["phase"] = "cancelled"

        self._all_channels_task = None
        if (
            self._all_channels_status
            and self._all_channels_status.get("phase") == "running"
        ):
            self._all_channels_status["phase"] = "cancelled"

    async def close(self) -> None:
        """Cancel background indexing and embedding jobs during bot shutdown."""
        self._closed = True
        tasks = {
            task
            for task in (
                *self._embedding_tasks.values(),
                *self._profile_tasks.values(),
                *self._pregeneration_tasks.values(),
                self._all_channels_task,
            )
            if task is not None
            if not task.done()
        }
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        self._embedding_tasks.clear()
        self._profile_tasks.clear()
        self._pregeneration_tasks.clear()
        self._all_channels_task = None

    @staticmethod
    def _normalize_query(query: str) -> str:
        """Normalize a query for embedding cache lookups and rewrite de-duplication."""
        return re.sub(r"\s+", " ", (query or "").strip().lower())

    @classmethod
    def _needs_rewrite(cls, user_prompt: str) -> bool:
        words = _WORD_RE.findall((user_prompt or "").lower())
        if not words:
            return False
        return (
            len(words) <= cls.REWRITE_SHORT_QUERY_WORDS
            or any(word in _PRONOUNS for word in words)
        )

    def _local_rewrite(
        self,
        user_prompt: str,
        recent: list[IndexedMessage],
    ) -> Optional[str]:
        """Append names from the recent window so a pronoun has something to match."""
        if not recent or not self._needs_rewrite(user_prompt):
            return None

        turns = max(1, int(getattr(self.config, "rag_query_rewrite_history_turns", 4)))
        window = recent[:turns]
        seen = {word.lower() for word in _WORD_RE.findall(user_prompt or "")}
        terms: list[str] = []

        def push(term: str) -> None:
            key = term.strip().lower()
            if (
                len(terms) >= self.REWRITE_MAX_TERMS
                or len(key) < 3
                or key in seen
                or key in _NON_ENTITY_WORDS
            ):
                return
            seen.add(key)
            terms.append(term.strip())

        for indexed in window:
            if not indexed.is_bot:
                push(indexed.author_name or "")
        for indexed in window:
            for token in _ENTITY_RE.findall(indexed.content_text or ""):
                push(token)

        return f"{(user_prompt or '').strip()} {' '.join(terms)}".strip() if terms else None

    def _query_variants(
        self,
        user_prompt: str,
        recent: list[IndexedMessage],
        router_query: Optional[str],
    ) -> list[tuple[str, str, float]]:
        """Raw query first, then any distinct rewrite at a reduced fusion factor."""
        variants = [("raw", user_prompt, 1.0)]
        if not getattr(self.config, "rag_query_rewrite_enabled", True):
            return variants
        seen = {self._normalize_query(user_prompt)}
        for label, rewrite in (
            ("local", self._local_rewrite(user_prompt, recent)),
            ("router", router_query),
        ):
            normalized = self._normalize_query(rewrite or "")
            if not normalized or normalized in seen:
                continue
            seen.add(normalized)
            variants.append((label, rewrite.strip(), self.REWRITE_FUSION_FACTOR))
        return variants

    def _get_cached_query_embedding(self, cache_key: str) -> Optional[list[float]]:
        cached = self._query_embedding_cache.get(cache_key)
        if not cached:
            return None
        cached_at, embedding = cached
        if time.monotonic() - cached_at > self._query_embedding_cache_ttl:
            self._query_embedding_cache.pop(cache_key, None)
            return None
        self._query_embedding_cache.move_to_end(cache_key)
        return embedding

    def _set_cached_query_embedding(self, cache_key: str, embedding: list[float]) -> None:
        self._query_embedding_cache[cache_key] = (time.monotonic(), embedding)
        self._query_embedding_cache.move_to_end(cache_key)
        while len(self._query_embedding_cache) > self._query_embedding_cache_size:
            self._query_embedding_cache.popitem(last=False)

    async def _embed_queries(self, queries: list[str]) -> list[list[float]]:
        """Embed every query variant in one call, cache hits taken first."""
        if not getattr(self.gemini_client, "client", None):
            return [[] for _query in queries]
        keys = [self._normalize_query(query) for query in queries]
        # Variants that normalize alike differ only in case and spacing, so
        # either spelling can stand for the pair.
        query_by_key = dict(zip(keys, queries))
        resolved = {key: self._get_cached_query_embedding(key) for key in keys}
        misses = [key for key in dict.fromkeys(keys) if resolved[key] is None]
        if misses:
            timeout = self._embedding_timeout()
            try:
                vectors = await asyncio.wait_for(
                    self.gemini_client.embed_texts(
                        [query_by_key[key] for key in misses],
                        model_name=self.config.rag_embedding_model,
                        task_type="RETRIEVAL_QUERY",
                    ),
                    timeout=timeout,
                )
            except asyncio.TimeoutError:
                # Degrade like an unavailable embedder: the semantic leg is skipped,
                # the user still gets recent and lexical context.
                logger.warning(
                    "RAG query embedding timed out after %ss; skipping the semantic leg",
                    timeout,
                )
                vectors = []
            for position, key in enumerate(misses):
                embedding = vectors[position] if position < len(vectors) else []
                if embedding:
                    resolved[key] = embedding
                    self._set_cached_query_embedding(key, embedding)
        return [resolved[key] or [] for key in keys]

    async def _embed_query(self, query: str) -> list[float]:
        return (await self._embed_queries([query]))[0]

    async def _load_pins(self, channel_id: int) -> list[tuple]:
        if not self.pin_service:
            return []
        try:
            return await asyncio.to_thread(self.pin_service.get_pins, channel_id)
        except Exception as exc:
            logger.warning("Failed to load RAG pinned memories for channel %s: %s", channel_id, exc)
            return []

    async def _load_entity_cards(
        self,
        *,
        guild_id: Optional[int],
        channel_id: int,
        user_prompt: str,
        capacity: int,
    ) -> list[MessageContext]:
        """Profile cards for people the query names, by mention, name or alias."""
        if capacity <= 0 or not self._entity_profiles_available():
            return []
        service = self.entity_profile_service
        try:
            profiles = await service.find_profiles_for_query_async(
                guild_id,
                user_prompt,
                limit=capacity,
            )
        except Exception as exc:
            logger.warning("Entity profile lookup failed: %s", exc)
            return []

        max_chars = int(getattr(self.config, "rag_entity_profile_max_chars", 1200))
        cards = []
        for profile in profiles:
            text = service.format_card(profile, max_chars=max_chars)
            if not text:
                continue
            cards.append(
                MessageContext(
                    content=text,
                    author=f"Profile of {profile.display_name}",
                    timestamp=datetime.now(timezone.utc),
                    message_id=ENTITY_CARD_MESSAGE_ID_OFFSET + (profile.author_id % 10**17),
                    channel_id=channel_id,
                    retrieval_source="entity_profile",
                    retrieval_score=1.0,
                    retrieval_reason=f"Profile memory for {profile.display_name}",
                    is_pinned_memory=True,
                )
            )
        return cards

    async def _load_reply_anchor_context(self, message) -> list[MessageContext]:
        if not getattr(message, "reference", None):
            return []
        try:
            reply_context = await self.context_collector.get_reply_context(message)
        except Exception as exc:
            logger.warning("RAG reply-anchor context failed: %s", exc)
            return []
        results = []
        for ctx in reply_context:
            if ctx.message_id == message.id:
                continue
            ctx.retrieval_source = "reply_anchor"
            ctx.retrieval_score = 1.0
            ctx.retrieval_reason = "Direct reply/thread anchor"
            results.append(ctx)
        return results

    def _add_candidates(
        self,
        candidates: dict[int, _Candidate],
        messages: list[IndexedMessage],
        *,
        source: str,
        weight: float,
    ) -> None:
        rrf_k = float(getattr(self.config, "rag_rrf_k", 60.0))
        # A tenth of a rank-1 hit at unit weight: enough to order near-equal
        # candidates, too small to outweigh another leg finding the same message.
        recency_scale = 0.1 / (rrf_k + 1.0)
        for rank, indexed in enumerate(messages, start=1):
            candidate = candidates.get(indexed.message_id)
            if candidate is None:
                # Scored on first sight, so recency counts once per message
                # rather than once per leg that found it.
                candidate = _Candidate(
                    message=indexed,
                    score=recency_scale * self._recency_boost(
                        indexed.created_at,
                        self.config.rag_recency_half_life_hours,
                    ),
                )
                candidates[indexed.message_id] = candidate
            candidate.add(source, weight / (rrf_k + rank), f"{source} rank {rank}")

    async def _expand_conversations(
        self,
        retrieved: list[MessageContext],
        *,
        max_blocks: int,
        exclude_ids: set[int],
    ) -> list[MessageContext]:
        """Expand each hit into its surrounding conversation (parent-document retrieval).

        Only the strongest `max_blocks` conversations are fetched, because the packer
        admits at most that many blocks and every extra one is a wasted query.
        """
        if not getattr(self.config, "rag_conversation_enabled", True) or not retrieved:
            return retrieved

        expanded: list[MessageContext] = []
        seen_conversations: set[int] = set()
        hit_ids = {ctx.message_id for ctx in retrieved}
        for ctx in retrieved:
            conversation_id = getattr(ctx, "conversation_id", None)
            # Anchors are already precise, and pins carry synthetic ids.
            if (
                conversation_id is None
                or ctx.retrieval_source == "reply_anchor"
                or ctx.is_pinned_memory
                or conversation_id in seen_conversations
                or len(seen_conversations) >= max_blocks
            ):
                expanded.append(ctx)
                continue
            seen_conversations.add(conversation_id)
            try:
                window = await self.message_index.get_conversation_window_async(
                    conversation_id,
                    center_message_id=ctx.message_id,
                    full_max_messages=self.config.rag_conversation_expand_full_max_messages,
                    window_messages=self.config.rag_conversation_expand_window_messages,
                    exclude_message_ids=exclude_ids,
                )
            except Exception as exc:
                logger.warning("Conversation expansion failed for %s: %s", conversation_id, exc)
                expanded.append(ctx)
                continue
            for indexed in window:
                if indexed.message_id in hit_ids:
                    continue
                expanded.append(
                    self.pack_builder.build_message_context(
                        indexed,
                        source="conversation",
                        # Below its own hit: filler is grounding, not evidence,
                        # and must not outrank a real match when the pack is ranked.
                        score=(ctx.retrieval_score or 0.0) * self.FILLER_SCORE_FACTOR,
                        reason=f"surrounding conversation {conversation_id}",
                        is_conversation_filler=True,
                    )
                )
            expanded.append(ctx)
        return expanded

    async def retrieve(
        self,
        *,
        message,
        user_prompt: str,
        complexity_level: str,
        bot_user_id: Optional[int] = None,
        needs_context: bool = True,
        force_full_context: bool = False,
        search_query: Optional[str] = None,
    ) -> list[MessageContext]:
        started = time.perf_counter()
        guild_id = message.guild.id if message.guild else None
        channel_id = message.channel.id
        max_messages = self._max_messages_for_complexity(complexity_level)
        exclude_ids = {message.id}
        fallback_reason = None
        reranker_used = False
        reranker_reason = "not_evaluated"
        recent = lexical = semantic = []
        query_embedding = []

        try:
            pins = await self._load_pins(channel_id)
            pinned_context = self.pack_builder.build_pinned_context(pins, channel_id=channel_id)
            if getattr(self.config, "rag_gating_enabled", True) and not needs_context and not force_full_context:
                packed = self.pack_builder.build_context_pack(
                    pinned_context=pinned_context,
                    retrieved_context=[],
                    max_messages=max_messages,
                )
                latency_ms = int((time.perf_counter() - started) * 1000)
                await self.message_index.record_retrieval_event_async(
                    guild_id=guild_id, channel_id=channel_id,
                    user_id=message.author.id if message.author else None,
                    query_length=len(user_prompt or ""),
                    selected_message_ids=[ctx.message_id for ctx in packed],
                    fallback_reason=None, latency_ms=latency_ms,
                    retrieval_mode="pins_only", reranker_reason="gated_no_context",
                )
                return packed

            reply_context = await self._load_reply_anchor_context(message)
            reply_ids = {ctx.message_id for ctx in reply_context}
            exclude_ids.update(reply_ids)

            # The recent leg comes first because the rewrite is built from it;
            # it is index-driven and sub-millisecond, so serialising it is free.
            recent = await self.message_index.search_recent_async(
                guild_id=guild_id,
                channel_id=channel_id,
                cross_channel=self.config.rag_cross_channel_enabled,
                limit=max(max_messages, self.config.rag_lexical_candidates // 2),
                exclude_message_ids=exclude_ids,
            )
            variants = self._query_variants(user_prompt, recent, search_query)

            async def semantic_leg(embedding: list[float]):
                if not embedding:
                    return embedding, []
                return embedding, await self.message_index.search_semantic_async(
                    embedding,
                    guild_id=guild_id,
                    channel_id=channel_id,
                    cross_channel=self.config.rag_cross_channel_enabled,
                    limit=self.config.rag_semantic_candidates,
                    exclude_message_ids=exclude_ids,
                )

            async def semantic_legs_for(queries: list[str]):
                # One embedding call for every variant, then fan out: three
                # variants used to mean three round trips and three charges.
                embeddings = await self._embed_queries(queries)
                return await asyncio.gather(
                    *(semantic_leg(embedding) for embedding in embeddings)
                )

            legs = await asyncio.gather(
                *(
                    self.message_index.search_lexical_async(
                        query,
                        guild_id=guild_id,
                        channel_id=channel_id,
                        cross_channel=self.config.rag_cross_channel_enabled,
                        limit=self.config.rag_lexical_candidates,
                        exclude_message_ids=exclude_ids,
                    )
                    for _label, query, _factor in variants
                ),
                semantic_legs_for([query for _label, query, _factor in variants]),
            )
            lexical_legs = list(legs[:-1])
            semantic_legs = list(legs[-1])
            lexical = lexical_legs[0]
            query_embedding, semantic = semantic_legs[0]

            candidates: dict[int, _Candidate] = {}
            self._add_candidates(
                candidates, recent, source="recent",
                weight=float(getattr(self.config, "rag_fusion_weight_recent", 0.7)),
            )
            lexical_weight = float(getattr(self.config, "rag_fusion_weight_lexical", 2.0))
            semantic_weight = float(getattr(self.config, "rag_fusion_weight_semantic", 2.0))
            for (label, _query, factor), hits in zip(variants, lexical_legs):
                self._add_candidates(
                    candidates, hits,
                    source="lexical" if label == "raw" else f"lexical_{label}",
                    weight=lexical_weight * factor,
                )
            for (label, _query, factor), (_embedding, hits) in zip(variants, semantic_legs):
                self._add_candidates(
                    candidates, hits,
                    source="semantic" if label == "raw" else f"semantic_{label}",
                    weight=semantic_weight * factor,
                )

            fused = sorted(candidates.values(), key=lambda item: item.score, reverse=True)
            available_slots = self.pack_builder.available_retrieval_slots(
                pinned_context=pinned_context,
                reply_context=reply_context,
                max_messages=max_messages,
            )

            # Profile cards rank below pins and above ordinary retrieval, and
            # may only take slots that sit ABOVE the DAB-073 floor. Spending
            # into the floor is the bug that floor exists for: the index would
            # still be searched, scored and paid for, and then dropped from the
            # pack. So a channel whose pins already reach the floor gets no
            # cards at all, and elsewhere the recomputed budget below is a real
            # count rather than the clamp.
            entity_cards = await self._load_entity_cards(
                guild_id=guild_id,
                channel_id=channel_id,
                user_prompt=user_prompt,
                capacity=min(
                    self.MAX_ENTITY_CARDS,
                    max(0, available_slots - MIN_RETRIEVAL_SLOTS),
                ),
            )
            if entity_cards:
                pinned_context = pinned_context + entity_cards
                available_slots = self.pack_builder.available_retrieval_slots(
                    pinned_context=pinned_context,
                    reply_context=reply_context,
                    max_messages=max_messages,
                )

            rerank_pool = fused[: max(available_slots, self.config.rag_rerank_candidates)]
            retrieved_context = [
                self.pack_builder.build_message_context(
                    candidate.message,
                    source="+".join(sorted(candidate.sources)),
                    score=candidate.score,
                    reason="; ".join(candidate.reason_parts[:4]),
                )
                for candidate in rerank_pool
            ]

            if available_slots <= 0:
                reranker_reason = "no_available_slots"
            elif len(fused) <= available_slots:
                reranker_reason = "within_context_limit"
            elif self.config.rag_rerank_candidates <= 0:
                reranker_reason = "disabled"
            elif not getattr(self.gemini_client, "client", None):
                reranker_reason = "client_unavailable"
            else:
                boundary = fused[available_slots - 1].score
                excluded = fused[available_slots].score
                boundary_gap = max(0.0, boundary - excluded) / max(abs(boundary), 1e-9)
                margin = getattr(self.config, "rag_rerank_min_boundary_margin", 0.15)
                reranker_reason = "ambiguous_boundary" if boundary_gap < margin else "stable_boundary"
            if reranker_reason == "ambiguous_boundary":
                try:
                    reranker_used = True
                    reranked = await self.gemini_client.select_relevant_context(
                        user_prompt,
                        retrieved_context[: self.config.rag_rerank_candidates],
                        max_messages=available_slots,
                        anchor_message_ids=reply_ids,
                    )
                    if reranked:
                        retrieved_context = reranked
                except Exception as exc:
                    fallback_reason = "rerank_failed"
                    reranker_reason = "rerank_failed"
                    logger.warning("RAG reranker failed; using fused ranking: %s", exc)

            if reply_context:
                # Anchors lead, and a reranked copy of one is dropped rather than
                # listed twice. The old `not in existing` disjunct was always
                # False -- `existing` came from the list being filtered.
                retrieved_context = reply_context + [
                    ctx for ctx in retrieved_context if ctx.message_id not in reply_ids
                ]

            # A block is worth many messages, so expand far fewer blocks than the
            # message budget: one per slot buries the hits in their own filler.
            retrieved_context = await self._expand_conversations(
                retrieved_context,
                max_blocks=max(2, max_messages // 3),
                exclude_ids=exclude_ids,
            )

            packed = self.pack_builder.build_context_pack(
                pinned_context=pinned_context,
                retrieved_context=retrieved_context,
                max_messages=max_messages,
                char_budget=int(getattr(self.config, "rag_context_char_budget", 0) or 0),
            )

            latency_ms = int((time.perf_counter() - started) * 1000)
            await self.message_index.record_retrieval_event_async(
                guild_id=guild_id,
                channel_id=channel_id,
                user_id=message.author.id if message.author else None,
                query_length=len(user_prompt or ""),
                selected_message_ids=[ctx.message_id for ctx in packed],
                fallback_reason=fallback_reason,
                latency_ms=latency_ms,
                retrieval_mode="full",
                recent_candidates=len(recent), lexical_candidates=len(lexical),
                semantic_candidates=len(semantic), query_embedding_used=bool(query_embedding),
                reranker_used=reranker_used, reranker_reason=reranker_reason,
            )
            logger.info(
                "Hybrid RAG selected %s context item(s): recent=%s lexical=%s semantic=%s "
                "pins=%s profiles=%s queries=%s latency=%sms",
                len(packed),
                len(recent),
                len(lexical),
                len(semantic),
                len(pinned_context) - len(entity_cards),
                len(entity_cards),
                "+".join(label for label, _query, _factor in variants),
                latency_ms,
            )
            return packed

        except Exception as exc:
            latency_ms = int((time.perf_counter() - started) * 1000)
            logger.warning("Hybrid RAG failed; caller should use legacy fallback: %s", exc, exc_info=True)
            await self.message_index.record_retrieval_event_async(
                guild_id=guild_id,
                channel_id=channel_id,
                user_id=message.author.id if message.author else None,
                query_length=len(user_prompt or ""),
                selected_message_ids=[],
                fallback_reason=type(exc).__name__,
                latency_ms=latency_ms,
                retrieval_mode="failed", recent_candidates=len(recent),
                lexical_candidates=len(lexical), semantic_candidates=len(semantic),
                query_embedding_used=bool(query_embedding), reranker_used=reranker_used,
                reranker_reason=reranker_reason,
            )
            raise
