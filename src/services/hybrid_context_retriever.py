"""
Hybrid context retriever for Discord message RAG.

Combines reply anchors, pinned memories, recent indexed messages, SQLite FTS5,
and locally stored Gemini embeddings. The final candidate pool can be reranked
by the existing Gemini router context selector.
"""

import asyncio
import logging
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Optional

from ..config import BotConfig
from ..models.data_models import MessageContext
from .context_collector import ContextCollector
from .context_pack_builder import ContextPackBuilder
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


class HybridContextRetriever:
    """Retrieves and packs relevant Discord context for the response model."""

    EMBEDDING_BATCH_SIZE = 16
    BACKGROUND_EMBED_DELAY_SECONDS = 2.0

    def __init__(
        self,
        *,
        config: BotConfig,
        message_index: MessageIndexService,
        context_collector: ContextCollector,
        gemini_client,
        pack_builder: ContextPackBuilder,
        pin_service=None,
    ):
        self.config = config
        self.message_index = message_index
        self.context_collector = context_collector
        self.gemini_client = gemini_client
        self.pack_builder = pack_builder
        self.pin_service = pin_service
        self._embedding_lock = asyncio.Lock()
        self._embedding_tasks: dict[int, asyncio.Task] = {}
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
            vectors = await self.gemini_client.embed_texts(
                texts,
                model_name=self.config.rag_embedding_model,
                task_type="RETRIEVAL_DOCUMENT",
            )
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
        while not self._closed:
            processed, stored, failed = await self.embed_pending_documents(
                channel_id=channel_id,
            )
            totals[0] += processed
            totals[1] += stored
            totals[2] += failed
            if processed == 0 or (stored == 0 and failed > 0):
                break
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
            if status["pending_embeddings"] or status["failed_embeddings"]:
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
        self._pregeneration_tasks.clear()
        self._all_channels_task = None

    async def _embed_query(self, query: str) -> list[float]:
        if not getattr(self.gemini_client, "client", None):
            return []
        vectors = await self.gemini_client.embed_texts(
            [query],
            model_name=self.config.rag_embedding_model,
            task_type="RETRIEVAL_QUERY",
        )
        return vectors[0] if vectors and vectors[0] else []

    async def _load_pins(self, channel_id: int) -> list[tuple]:
        if not self.pin_service:
            return []
        try:
            return await asyncio.to_thread(self.pin_service.get_pins, channel_id)
        except Exception as exc:
            logger.warning("Failed to load RAG pinned memories for channel %s: %s", channel_id, exc)
            return []

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
        for rank, indexed in enumerate(messages, start=1):
            candidate = candidates.get(indexed.message_id)
            if candidate is None:
                candidate = _Candidate(message=indexed)
                candidates[indexed.message_id] = candidate
            rank_score = weight / max(1, rank)
            recency = 0.2 * self._recency_boost(
                indexed.created_at,
                self.config.rag_recency_half_life_hours,
            )
            candidate.add(source, rank_score + recency, f"{source} rank {rank}")

    async def retrieve(
        self,
        *,
        message,
        user_prompt: str,
        complexity_level: str,
        bot_user_id: Optional[int] = None,
        needs_context: bool = True,
        force_full_context: bool = False,
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

            recent, lexical = await asyncio.gather(
                self.message_index.search_recent_async(
                    guild_id=guild_id,
                    channel_id=channel_id,
                    cross_channel=self.config.rag_cross_channel_enabled,
                    limit=max(max_messages, self.config.rag_lexical_candidates // 2),
                    exclude_message_ids=exclude_ids,
                ),
                self.message_index.search_lexical_async(
                    user_prompt,
                    guild_id=guild_id,
                    channel_id=channel_id,
                    cross_channel=self.config.rag_cross_channel_enabled,
                    limit=self.config.rag_lexical_candidates,
                    exclude_message_ids=exclude_ids,
                ),
            )

            query_embedding = await self._embed_query(user_prompt)
            semantic = []
            if query_embedding:
                semantic = await self.message_index.search_semantic_async(
                    query_embedding,
                    guild_id=guild_id,
                    channel_id=channel_id,
                    cross_channel=self.config.rag_cross_channel_enabled,
                    limit=self.config.rag_semantic_candidates,
                    exclude_message_ids=exclude_ids,
                )

            candidates: dict[int, _Candidate] = {}
            self._add_candidates(candidates, recent, source="recent", weight=0.7)
            self._add_candidates(candidates, lexical, source="lexical", weight=2.0)
            self._add_candidates(candidates, semantic, source="semantic", weight=2.0)

            fused = sorted(candidates.values(), key=lambda item: item.score, reverse=True)
            rerank_pool = fused[: max(max_messages, self.config.rag_rerank_candidates)]
            retrieved_context = [
                self.pack_builder.build_message_context(
                    candidate.message,
                    source="+".join(sorted(candidate.sources)),
                    score=candidate.score,
                    reason="; ".join(candidate.reason_parts[:4]),
                )
                for candidate in rerank_pool
            ]

            if len(fused) <= max_messages:
                reranker_reason = "within_context_limit"
            elif self.config.rag_rerank_candidates <= 0:
                reranker_reason = "disabled"
            elif not getattr(self.gemini_client, "client", None):
                reranker_reason = "client_unavailable"
            else:
                boundary = fused[max_messages - 1].score
                excluded = fused[max_messages].score
                boundary_gap = max(0.0, boundary - excluded) / max(abs(boundary), 1e-9)
                margin = getattr(self.config, "rag_rerank_min_boundary_margin", 0.15)
                reranker_reason = "ambiguous_boundary" if boundary_gap < margin else "stable_boundary"
            if reranker_reason == "ambiguous_boundary":
                try:
                    reranker_used = True
                    reranked = await self.gemini_client.select_relevant_context(
                        user_prompt,
                        retrieved_context[: self.config.rag_rerank_candidates],
                        max_messages=max_messages,
                        anchor_message_ids=reply_ids,
                    )
                    if reranked:
                        retrieved_context = reranked
                except Exception as exc:
                    fallback_reason = "rerank_failed"
                    reranker_reason = "rerank_failed"
                    logger.warning("RAG reranker failed; using fused ranking: %s", exc)

            if reply_context:
                existing = {ctx.message_id for ctx in retrieved_context}
                retrieved_context = reply_context + [
                    ctx for ctx in retrieved_context if ctx.message_id not in existing or ctx.message_id not in reply_ids
                ]

            packed = self.pack_builder.build_context_pack(
                pinned_context=pinned_context,
                retrieved_context=retrieved_context,
                max_messages=max_messages,
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
                "Hybrid RAG selected %s context item(s): recent=%s lexical=%s semantic=%s pins=%s latency=%sms",
                len(packed),
                len(recent),
                len(lexical),
                len(semantic),
                len(pinned_context),
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
