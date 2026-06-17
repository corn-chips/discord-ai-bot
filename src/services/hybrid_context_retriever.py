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
        self._backfilled_channels: set[int] = set()

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

    async def _maybe_backfill_channel(self, message, bot_user_id: Optional[int]) -> None:
        channel_id = int(message.channel.id)
        if channel_id in self._backfilled_channels:
            return
        if self.config.rag_backfill_limit <= 0:
            self._backfilled_channels.add(channel_id)
            return
        indexed = await self.message_index.backfill_channel(
            message.channel,
            limit=self.config.rag_backfill_limit,
            include_bot_user_id=bot_user_id if self.config.rag_index_bot_responses else None,
        )
        self._backfilled_channels.add(channel_id)
        logger.info("RAG backfilled %s message(s) for channel %s", indexed, channel_id)

    async def _embed_pending_documents(self) -> None:
        if not getattr(self.gemini_client, "client", None):
            return
        pending = await self.message_index.get_pending_embeddings_async(
            limit=min(16, max(1, self.config.rag_semantic_candidates)),
        )
        if not pending:
            return

        texts = [text for _message_id, text, _content_hash in pending]
        vectors = await self.gemini_client.embed_texts(
            texts,
            model_name=self.config.rag_embedding_model,
            task_type="RETRIEVAL_DOCUMENT",
        )
        for (message_id, _text, content_hash), vector in zip(pending, vectors):
            if vector:
                await self.message_index.store_embedding_async(message_id, vector, content_hash)
            else:
                await self.message_index.mark_embedding_failed_async(message_id, "empty embedding response")

    async def _embed_query(self, query: str) -> list[float]:
        if not getattr(self.gemini_client, "client", None):
            return []
        query_text = f"task: question answering | query: {query}"
        vectors = await self.gemini_client.embed_texts(
            [query_text],
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
    ) -> list[MessageContext]:
        started = time.perf_counter()
        guild_id = message.guild.id if message.guild else None
        channel_id = message.channel.id
        max_messages = self._max_messages_for_complexity(complexity_level)
        exclude_ids = {message.id}
        fallback_reason = None

        try:
            await self._maybe_backfill_channel(message, bot_user_id)
            await self._embed_pending_documents()

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

            if (
                self.config.rag_rerank_candidates > 0
                and len(retrieved_context) > max_messages
                and getattr(self.gemini_client, "client", None)
            ):
                try:
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
                    logger.warning("RAG reranker failed; using fused ranking: %s", exc)

            if reply_context:
                existing = {ctx.message_id for ctx in retrieved_context}
                retrieved_context = reply_context + [
                    ctx for ctx in retrieved_context if ctx.message_id not in existing or ctx.message_id not in reply_ids
                ]

            pins = await self._load_pins(channel_id)
            pinned_context = self.pack_builder.build_pinned_context(pins, channel_id=channel_id)
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
            )
            raise
