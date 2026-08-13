"""Coverage for RRF fusion, embedding pacing, embedding deadlines, and the query cache."""

import asyncio
import time
import unittest
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, call, patch

from src.models.data_models import MessageContext
from src.services.context_pack_builder import ContextPackBuilder
from src.services.hybrid_context_retriever import HybridContextRetriever, _Candidate
from src.services.message_index_service import IndexedMessage


def make_config(**overrides):
    values = dict(
        rag_gating_enabled=True,
        rag_max_context_messages_low=4,
        rag_max_context_messages_medium=6,
        rag_max_context_messages_high=8,
        rag_cross_channel_enabled=False,
        rag_lexical_candidates=30,
        rag_semantic_candidates=24,
        rag_rerank_candidates=0,
        rag_rerank_min_boundary_margin=0.15,
        rag_recency_half_life_hours=72,
        rag_embedding_model="test",
        rag_embedding_batch_delay_seconds=0.0,
        rag_embedding_drain_max_batches=200,
        rag_embedding_timeout_seconds=30.0,
        rag_rrf_k=60.0,
        rag_fusion_weight_recent=0.7,
        rag_fusion_weight_lexical=2.0,
        rag_fusion_weight_semantic=2.0,
        rag_query_embedding_cache_size=128,
        rag_query_embedding_cache_ttl=900,
    )
    values.update(overrides)
    return SimpleNamespace(**values)


def make_indexed(message_id, *, age_hours=0.0):
    return IndexedMessage(
        message_id=message_id, guild_id=1, channel_id=2, author_id=3,
        author_name="Ada", is_bot=False, reply_to_message_id=None,
        created_at=datetime.now(timezone.utc) - timedelta(hours=age_hours),
        content_text=f"candidate {message_id}",
    )


def make_retriever(config=None, *, message_index=None, gemini_client=None, context_collector=None):
    return HybridContextRetriever(
        config=config if config is not None else make_config(),
        message_index=message_index if message_index is not None else SimpleNamespace(),
        context_collector=context_collector if context_collector is not None else object(),
        gemini_client=gemini_client if gemini_client is not None else SimpleNamespace(
            client=object(), embed_texts=AsyncMock(return_value=[[1.0, 0.0]]),
        ),
        pack_builder=ContextPackBuilder(),
    )


def make_message(message_id=1, *, reference=None):
    return SimpleNamespace(
        id=message_id,
        guild=SimpleNamespace(id=1),
        channel=SimpleNamespace(id=2),
        author=SimpleNamespace(id=3),
        reference=reference,
    )


class ReciprocalRankFusionTest(unittest.TestCase):
    def _fuse(self, retriever, legs):
        candidates: dict[int, _Candidate] = {}
        for source, weight, messages in legs:
            retriever._add_candidates(candidates, messages, source=source, weight=weight)
        return candidates

    def test_two_legs_agreeing_outrank_one_legs_top_hit(self):
        # The old weight/rank curve gave rank 1 twice rank 2's score, so a single
        # leg's favourite won outright. RRF flattens that: corroboration wins.
        loner, agreed = make_indexed(1), make_indexed(2)
        filler = [make_indexed(mid) for mid in range(10, 20)]
        retriever = make_retriever()

        candidates = self._fuse(retriever, [
            ("lexical", 2.0, [loner, *filler[:2]]),
            ("semantic", 2.0, [*filler[2:4], agreed]),
            ("recent", 0.7, [*filler[4:8], agreed]),
        ])

        ordered = sorted(candidates.values(), key=lambda item: item.score, reverse=True)
        self.assertEqual(ordered[0].message.message_id, 2)
        self.assertGreater(candidates[2].score, candidates[1].score)
        self.assertEqual(candidates[2].sources, {"semantic", "recent"})

    def test_rank_scores_follow_the_rrf_curve_read_from_config(self):
        first, second = make_indexed(1, age_hours=10_000), make_indexed(2, age_hours=10_000)
        retriever = make_retriever(make_config(rag_rrf_k=10.0, rag_fusion_weight_lexical=3.0))

        candidates = self._fuse(retriever, [("lexical", 3.0, [first, second])])

        # Ancient messages make the recency term negligible, leaving the curve.
        self.assertAlmostEqual(candidates[1].score, 3.0 / 11.0, places=4)
        self.assertAlmostEqual(candidates[2].score, 3.0 / 12.0, places=4)

    def test_recency_is_scored_once_per_message_not_once_per_leg(self):
        fresh = make_indexed(1)
        retriever = make_retriever()
        boost = retriever._recency_boost(fresh.created_at, 72)
        expected_rrf = 2.0 / 61.0 + 2.0 / 61.0 + 0.7 / 61.0

        candidates = self._fuse(retriever, [
            ("lexical", 2.0, [fresh]),
            ("semantic", 2.0, [fresh]),
            ("recent", 0.7, [fresh]),
        ])

        self.assertAlmostEqual(
            candidates[1].score, expected_rrf + (0.1 / 61.0) * boost, places=6
        )
        self.assertEqual(len(candidates[1].sources), 3)
        self.assertEqual(len(candidates[1].reason_parts), 3)

    def test_recency_breaks_a_tie_but_cannot_beat_an_extra_leg(self):
        fresh, stale = make_indexed(1), make_indexed(2, age_hours=720)
        retriever = make_retriever()

        tied = self._fuse(retriever, [
            ("lexical", 2.0, [fresh]),
            ("semantic", 2.0, [stale]),
        ])
        self.assertGreater(tied[1].score, tied[2].score)

        outranked = self._fuse(retriever, [
            ("lexical", 2.0, [fresh, stale]),
            ("semantic", 2.0, [stale]),
        ])
        self.assertGreater(outranked[2].score, outranked[1].score)

    def test_retrieve_takes_its_leg_weights_from_config(self):
        # A zero lexical weight leaves a lexical-only hit on recency alone, so a
        # semantic hit at the same rank must come first.
        lexical_only, semantic_only = make_indexed(1), make_indexed(2, age_hours=720)
        retriever = make_retriever(make_config(rag_fusion_weight_lexical=0.0))

        candidates = self._fuse(retriever, [
            ("lexical", 0.0, [lexical_only]),
            ("semantic", 2.0, [semantic_only]),
        ])

        self.assertGreater(candidates[2].score, candidates[1].score)
        self.assertLess(candidates[1].score, 0.1 / 61.0)


class ConversationExpansionTest(unittest.IsolatedAsyncioTestCase):
    """Parent-document expansion: the surrounding messages are grounding, not evidence."""

    @staticmethod
    def _window(*message_ids, conversation_id=42):
        window = []
        for message_id in message_ids:
            indexed = make_indexed(message_id)
            indexed.conversation_id = conversation_id
            window.append(indexed)
        return window

    @staticmethod
    def _hit(score=0.8, conversation_id=42):
        return MessageContext(
            content="the migration landed",
            author="Ada",
            timestamp=datetime.now(timezone.utc),
            message_id=101,
            channel_id=2,
            retrieval_source="lexical",
            retrieval_score=score,
            retrieval_reason="fts match",
            conversation_id=conversation_id,
        )

    def _retriever(self, index):
        return make_retriever(
            make_config(
                rag_conversation_enabled=True,
                rag_conversation_expand_full_max_messages=12,
                rag_conversation_expand_window_messages=5,
            ),
            message_index=index,
        )

    async def test_filler_is_scored_below_the_hit_it_surrounds(self):
        # At parity a "morning" that happens to sit next to the match scores what
        # the match scores, and the packer ranks a block by its strongest member.
        index = SimpleNamespace(
            get_conversation_window_async=AsyncMock(return_value=self._window(100, 102)),
        )
        hit = self._hit(score=0.8)

        expanded = await self._retriever(index)._expand_conversations(
            [hit], max_blocks=2, exclude_ids=set()
        )

        filler = [ctx for ctx in expanded if ctx.is_conversation_filler]
        self.assertEqual([ctx.message_id for ctx in filler], [100, 102])
        self.assertEqual([ctx.retrieval_source for ctx in filler], ["conversation"] * 2)
        for ctx in filler:
            self.assertLess(ctx.retrieval_score, hit.retrieval_score)
            self.assertAlmostEqual(ctx.retrieval_score, 0.2)

    async def test_the_hit_itself_keeps_its_score_and_follows_its_window(self):
        index = SimpleNamespace(
            get_conversation_window_async=AsyncMock(return_value=self._window(100)),
        )
        hit = self._hit(score=0.8)

        expanded = await self._retriever(index)._expand_conversations(
            [hit], max_blocks=2, exclude_ids={7}
        )

        self.assertEqual([ctx.message_id for ctx in expanded], [100, 101])
        self.assertEqual(expanded[-1].retrieval_score, 0.8)
        self.assertFalse(expanded[-1].is_conversation_filler)
        self.assertEqual(
            index.get_conversation_window_async.await_args.kwargs["center_message_id"], 101
        )

    async def test_a_weaker_conversation_cannot_be_lifted_by_its_own_filler(self):
        # The consequence the factor exists for: the packer orders blocks by their
        # best member, so filler at parity lets a weak hit's conversation outrank
        # a strong hit whose own conversation was not expanded.
        strong = self._hit(score=0.9, conversation_id=None)
        strong.message_id = 200
        weak = self._hit(score=0.5)
        index = SimpleNamespace(
            get_conversation_window_async=AsyncMock(return_value=self._window(100, 102)),
        )

        expanded = await self._retriever(index)._expand_conversations(
            [strong, weak], max_blocks=2, exclude_ids=set()
        )

        self.assertLess(
            max(ctx.retrieval_score for ctx in expanded if ctx.is_conversation_filler),
            strong.retrieval_score,
        )


class EmbeddingDrainPacingTest(unittest.IsolatedAsyncioTestCase):
    @staticmethod
    def _index(batches):
        return SimpleNamespace(
            get_pending_embeddings_async=AsyncMock(side_effect=batches),
            store_embedding_async=AsyncMock(return_value=True),
            mark_embedding_failed_async=AsyncMock(),
        )

    @staticmethod
    def _pending(count):
        return [(mid, f"text {mid}", f"hash-{mid}") for mid in range(count)]

    @staticmethod
    def _gemini(count):
        return SimpleNamespace(
            client=object(),
            embed_texts=AsyncMock(return_value=[[1.0, 0.0]] * count),
        )

    async def test_delay_sits_between_batches_only(self):
        retriever = make_retriever(
            make_config(rag_embedding_batch_delay_seconds=0.5),
            message_index=self._index([self._pending(2), self._pending(2), []]),
            gemini_client=self._gemini(2),
        )

        with patch(
            "src.services.hybrid_context_retriever.asyncio.sleep",
            new_callable=AsyncMock,
        ) as sleep_mock:
            totals = await retriever._drain_pending_documents(channel_id=2)

        self.assertEqual(totals, (4, 4, 0))
        self.assertEqual(sleep_mock.await_args_list, [call(0.5), call(0.5)])

    async def test_drain_stops_at_the_batch_cap_and_logs_it(self):
        # Without a cap a first boot over a large channel fires one embedding
        # call per 16 messages, back to back, until the channel is exhausted.
        gemini = self._gemini(2)
        retriever = make_retriever(
            make_config(rag_embedding_drain_max_batches=3),
            message_index=SimpleNamespace(
                get_pending_embeddings_async=AsyncMock(return_value=self._pending(2)),
                store_embedding_async=AsyncMock(return_value=True),
                mark_embedding_failed_async=AsyncMock(),
            ),
            gemini_client=gemini,
        )

        with self.assertLogs("src.services.hybrid_context_retriever", "INFO") as captured:
            totals = await retriever._drain_pending_documents(channel_id=2)

        self.assertEqual(totals, (6, 6, 0))
        self.assertEqual(gemini.embed_texts.await_count, 3)
        self.assertIn("3-batch cap", "\n".join(captured.output))

    async def test_shutdown_during_the_delay_ends_the_drain(self):
        retriever = make_retriever(
            make_config(rag_embedding_batch_delay_seconds=30.0),
            message_index=SimpleNamespace(
                get_pending_embeddings_async=AsyncMock(return_value=self._pending(2)),
                store_embedding_async=AsyncMock(return_value=True),
                mark_embedding_failed_async=AsyncMock(),
            ),
            gemini_client=self._gemini(2),
        )
        first_batch = asyncio.Event()
        original_sleep = asyncio.sleep

        async def note_and_sleep(delay, *args, **kwargs):
            first_batch.set()
            await original_sleep(delay, *args, **kwargs)

        with patch("src.services.hybrid_context_retriever.asyncio.sleep", note_and_sleep):
            task = asyncio.create_task(retriever._drain_pending_documents(channel_id=2))
            retriever._embedding_tasks[2] = task
            await first_batch.wait()
            started = time.monotonic()
            await retriever.close()

        self.assertTrue(task.cancelled())
        self.assertLess(time.monotonic() - started, 5.0)


class EmbeddingDeadlineTest(unittest.IsolatedAsyncioTestCase):
    @staticmethod
    def _hanging_client():
        async def never_returns(*args, **kwargs):
            await asyncio.sleep(30)
            return [[1.0, 0.0]]

        return SimpleNamespace(client=object(), embed_texts=never_returns)

    async def test_query_embedding_timeout_degrades_to_no_semantic_leg(self):
        retriever = make_retriever(
            make_config(rag_embedding_timeout_seconds=0.01),
            gemini_client=self._hanging_client(),
        )

        with self.assertLogs("src.services.hybrid_context_retriever", "WARNING") as captured:
            self.assertEqual(await retriever._embed_query("who said that"), [])

        self.assertIn("timed out", "\n".join(captured.output))
        self.assertEqual(len(retriever._query_embedding_cache), 0)

    async def test_retrieve_still_answers_when_the_query_embedding_times_out(self):
        hit = make_indexed(7)
        index = SimpleNamespace(
            search_recent_async=AsyncMock(return_value=[hit]),
            search_lexical_async=AsyncMock(return_value=[hit]),
            search_semantic_async=AsyncMock(return_value=[]),
            record_retrieval_event_async=AsyncMock(),
        )
        retriever = make_retriever(
            make_config(rag_embedding_timeout_seconds=0.01),
            message_index=index,
            gemini_client=self._hanging_client(),
        )

        packed = await retriever.retrieve(
            message=make_message(), user_prompt="who said that", complexity_level="low",
        )

        self.assertEqual([ctx.message_id for ctx in packed], [7])
        index.search_semantic_async.assert_not_awaited()
        event = index.record_retrieval_event_async.await_args.kwargs
        self.assertFalse(event["query_embedding_used"])
        self.assertEqual(event["retrieval_mode"], "full")

    async def test_a_document_embedding_timeout_marks_the_whole_batch_failed(self):
        # The timeout used to escape the drain: no row was marked, no attempt was
        # counted, and the identical batch was re-sent and re-billed on every
        # later pass, forever.
        index = SimpleNamespace(
            get_pending_embeddings_async=AsyncMock(
                return_value=[(1, "text", "hash-1"), (2, "more text", "hash-2")],
            ),
            store_embedding_async=AsyncMock(return_value=True),
            mark_embedding_failed_async=AsyncMock(),
        )
        retriever = make_retriever(
            make_config(rag_embedding_timeout_seconds=0.01),
            message_index=index,
            gemini_client=self._hanging_client(),
        )

        totals = await retriever._drain_pending_documents(channel_id=2)

        self.assertEqual(totals, (2, 0, 2))
        self.assertEqual(
            [call.args[0] for call in index.mark_embedding_failed_async.await_args_list],
            [1, 2],
        )
        # And the drain gives up instead of re-sending the same batch.
        self.assertEqual(index.get_pending_embeddings_async.await_count, 1)


class QueryEmbeddingCacheTest(unittest.IsolatedAsyncioTestCase):
    @staticmethod
    def _gemini():
        return SimpleNamespace(
            client=object(),
            embed_texts=AsyncMock(return_value=[[1.0, 0.0, 0.0]]),
        )

    async def test_repeat_question_is_served_from_cache_after_normalization(self):
        gemini = self._gemini()
        retriever = make_retriever(gemini_client=gemini)

        first = await retriever._embed_query("Who said that?")
        second = await retriever._embed_query("  who   SAID   that? ")

        self.assertEqual(first, [1.0, 0.0, 0.0])
        self.assertEqual(second, first)
        gemini.embed_texts.assert_awaited_once()

    async def test_expired_entry_is_re_embedded(self):
        gemini = self._gemini()
        retriever = make_retriever(
            make_config(rag_query_embedding_cache_ttl=60), gemini_client=gemini,
        )

        await retriever._embed_query("who said that")
        cached_at, vector = retriever._query_embedding_cache["who said that"]
        retriever._query_embedding_cache["who said that"] = (cached_at - 61.0, vector)
        await retriever._embed_query("who said that")

        self.assertEqual(gemini.embed_texts.await_count, 2)
        self.assertEqual(len(retriever._query_embedding_cache), 1)

    async def test_cache_evicts_least_recently_used_entries(self):
        gemini = self._gemini()
        retriever = make_retriever(
            make_config(rag_query_embedding_cache_size=2), gemini_client=gemini,
        )

        await retriever._embed_query("first")
        await retriever._embed_query("second")
        await retriever._embed_query("first")
        await retriever._embed_query("third")

        self.assertEqual(list(retriever._query_embedding_cache), ["first", "third"])
        self.assertEqual(gemini.embed_texts.await_count, 3)

    async def test_empty_embedding_is_never_cached(self):
        gemini = SimpleNamespace(client=object(), embed_texts=AsyncMock(return_value=[[]]))
        retriever = make_retriever(gemini_client=gemini)

        self.assertEqual(await retriever._embed_query("who said that"), [])
        self.assertEqual(await retriever._embed_query("who said that"), [])

        self.assertEqual(len(retriever._query_embedding_cache), 0)
        self.assertEqual(gemini.embed_texts.await_count, 2)


class ReplyAnchorOrderingTest(unittest.IsolatedAsyncioTestCase):
    async def test_anchor_leads_and_a_reranked_copy_of_it_is_not_duplicated(self):
        # The filter carried a dead disjunct (`not in existing`, always False)
        # over the live one; the reranker may return the anchor itself.
        anchor = MessageContext(
            content="the anchored message", author="Ada",
            timestamp=datetime.now(timezone.utc), message_id=99, channel_id=2,
        )
        candidates = [make_indexed(mid) for mid in range(10, 15)]
        index = SimpleNamespace(
            search_recent_async=AsyncMock(return_value=[]),
            search_lexical_async=AsyncMock(return_value=candidates),
            search_semantic_async=AsyncMock(return_value=[]),
            record_retrieval_event_async=AsyncMock(),
        )
        reranked = [
            MessageContext(
                content="anchor copy", author="Ada",
                timestamp=datetime.now(timezone.utc), message_id=99, channel_id=2,
                retrieval_source="lexical",
            ),
            candidates[3].to_context(
                retrieval_source="lexical", retrieval_score=0.1, retrieval_reason="lexical",
            ),
        ]
        gemini = SimpleNamespace(
            client=object(),
            embed_texts=AsyncMock(return_value=[[]]),
            select_relevant_context=AsyncMock(return_value=reranked),
        )
        retriever = make_retriever(
            make_config(rag_rerank_candidates=5),
            message_index=index,
            gemini_client=gemini,
            context_collector=SimpleNamespace(get_reply_context=AsyncMock(return_value=[anchor])),
        )

        packed = await retriever.retrieve(
            message=make_message(reference=SimpleNamespace(message_id=99)),
            user_prompt="what about it",
            complexity_level="low",
        )

        gemini.select_relevant_context.assert_awaited_once()
        self.assertEqual([ctx.message_id for ctx in packed], [99, 13])
        self.assertEqual(packed[0].retrieval_source, "reply_anchor")


if __name__ == "__main__":
    unittest.main()
