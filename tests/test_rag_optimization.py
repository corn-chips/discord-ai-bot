"""Focused coverage for cost-efficient hybrid RAG behavior."""

import sqlite3
import tempfile
import threading
import unittest
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import numpy as np

from src.services.context_pack_builder import ContextPackBuilder, MIN_RETRIEVAL_SLOTS
from src.services.hybrid_context_retriever import HybridContextRetriever
from src.models.data_models import MessageContext
from src.services.message_index_service import IndexedMessage, MessageIndexService
from src.services.pin_service import PinService


class EmbeddingEfficiencyTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.path = str(Path(self.temp.name) / "rag.db")
        self.service = MessageIndexService(self.path, embedding_model="test", embedding_dimensions=3)

    def tearDown(self):
        self.temp.cleanup()

    def _upsert(self, message_id, text, attachment=""):
        self.assertTrue(self.service.upsert_message(
            message_id=message_id, guild_id=1, channel_id=2, author_id=3,
            author_name="Ada", is_bot=False, reply_to_message_id=None,
            created_at=datetime.now(timezone.utc), content_text=text,
            attachment_summary=attachment,
        ))

    def _scalar(self, query, params=()):
        conn = sqlite3.connect(self.path)
        try:
            return conn.execute(query, params).fetchone()[0]
        finally:
            conn.close()

    def test_trivial_messages_stay_fts_searchable_and_edits_transition_both_ways(self):
        self._upsert(1, "hi")
        self.assertEqual(self._scalar("SELECT embedding_status FROM message_embeddings WHERE message_id=1"), "skipped")
        self.assertEqual([item.message_id for item in self.service.search_lexical("hi", guild_id=1, channel_id=2, cross_channel=False, limit=5)], [1])
        self._upsert(1, "this message is eligible")
        self.assertEqual(self._scalar("SELECT embedding_status FROM message_embeddings WHERE message_id=1"), "pending")
        self._upsert(1, "ok")
        self.assertEqual(self._scalar("SELECT embedding_status FROM message_embeddings WHERE message_id=1"), "skipped")

    def test_real_discord_metadata_does_not_make_trivial_text_eligible(self):
        message = SimpleNamespace(
            id=11,
            author=SimpleNamespace(id=3, bot=False, display_name="Ada", name="Ada"),
            content="hi",
            system_content="",
            attachments=[],
            embeds=[],
            stickers=[],
            message_snapshots=[],
            type=SimpleNamespace(name="default"),
            channel=SimpleNamespace(id=2),
            guild=SimpleNamespace(id=1),
            reference=None,
            created_at=datetime.now(timezone.utc),
        )

        self.assertTrue(self.service.index_discord_message(message))
        self.assertIn("Message type: default", self._scalar("SELECT content_text FROM message_index WHERE message_id=11"))
        self.assertEqual(self._scalar("SELECT embedding_status FROM message_embeddings WHERE message_id=11"), "skipped")

    def test_legacy_json_embedding_loads_into_vector_cache(self):
        self._upsert(12, "legacy vector remains searchable")
        conn = sqlite3.connect(self.path)
        try:
            conn.execute(
                "UPDATE message_embeddings SET embedding_vector=?, embedding_status='done' WHERE message_id=12",
                ("[1.0,0.0,0.0]",),
            )
            conn.commit()
        finally:
            conn.close()

        results = self.service.search_semantic(
            [1, 0, 0], guild_id=1, channel_id=2,
            cross_channel=False, limit=5,
        )

        self.assertEqual([item.message_id for item in results], [12])

    def test_cache_mutation_waits_for_initial_load_lock(self):
        completed = threading.Event()

        def mutate_cache():
            self.service._cache_upsert(13, 1, 2, np.asarray([1, 0, 0], dtype=np.float32))
            completed.set()

        with self.service._vector_lock:
            worker = threading.Thread(target=mutate_cache)
            worker.start()
            self.assertFalse(completed.wait(0.05))
            self.service._vector_loaded = True

        worker.join(timeout=1)
        self.assertTrue(completed.is_set())
        self.assertEqual(self.service._vector_count, 1)

    def test_warm_vector_cache_updates_without_full_reload(self):
        self._upsert(1, "first eligible message")
        content_hash = self._scalar("SELECT content_hash FROM message_embeddings WHERE message_id=1")
        self.assertTrue(self.service.store_embedding(1, [1, 0, 0], content_hash))
        self.assertEqual(self.service.search_semantic([1, 0, 0], guild_id=1, channel_id=2, cross_channel=False, limit=5)[0].message_id, 1)
        self._upsert(2, "second eligible message")
        content_hash = self._scalar("SELECT content_hash FROM message_embeddings WHERE message_id=2")
        self.assertTrue(self.service.store_embedding(2, [0.9, 0.1, 0], content_hash))
        self.assertEqual(self.service.get_status()["cached_vectors"], 2)
        self.service.mark_hidden(1)
        self.assertEqual([item.message_id for item in self.service.search_semantic([1, 0, 0], guild_id=1, channel_id=2, cross_channel=False, limit=5)], [2])


class RetrievalGatingTest(unittest.IsolatedAsyncioTestCase):
    @staticmethod
    def _candidate(message_id):
        return IndexedMessage(
            message_id=message_id, guild_id=1, channel_id=2, author_id=3,
            author_name="Ada", is_bot=False, reply_to_message_id=None,
            created_at=datetime.now(timezone.utc), content_text=f"candidate {message_id}",
        )

    @staticmethod
    def _config():
        return SimpleNamespace(
            rag_gating_enabled=True, rag_max_context_messages_low=4,
            rag_max_context_messages_medium=6, rag_max_context_messages_high=8,
            rag_cross_channel_enabled=False, rag_lexical_candidates=30,
            rag_semantic_candidates=24, rag_rerank_candidates=12,
            rag_rerank_min_boundary_margin=0.15,
            rag_recency_half_life_hours=72, rag_embedding_model="test",
        )

    async def test_self_contained_request_uses_pins_only(self):
        index = SimpleNamespace(
            search_recent_async=AsyncMock(), search_lexical_async=AsyncMock(),
            search_semantic_async=AsyncMock(), record_retrieval_event_async=AsyncMock(),
        )
        gemini = SimpleNamespace(client=object(), embed_texts=AsyncMock(), select_relevant_context=AsyncMock())
        pins = SimpleNamespace(get_pins=lambda channel_id: [(9, "remember blue", "Ada", "Ray", datetime.now(timezone.utc).isoformat())])
        config = SimpleNamespace(
            rag_gating_enabled=True, rag_max_context_messages_low=4,
            rag_max_context_messages_medium=6, rag_max_context_messages_high=8,
        )
        retriever = HybridContextRetriever(config=config, message_index=index,
            context_collector=object(), gemini_client=gemini,
            pack_builder=ContextPackBuilder(), pin_service=pins)
        message = SimpleNamespace(id=1, guild=SimpleNamespace(id=1), channel=SimpleNamespace(id=2), author=SimpleNamespace(id=3), reference=None)
        result = await retriever.retrieve(message=message, user_prompt="2+2", complexity_level="low", needs_context=False)
        self.assertEqual(len(result), 1)
        self.assertTrue(result[0].is_pinned_memory)
        gemini.embed_texts.assert_not_awaited(); gemini.select_relevant_context.assert_not_awaited()
        index.search_recent_async.assert_not_awaited(); index.search_lexical_async.assert_not_awaited()
        await retriever.close()

    async def test_rerank_boundary_uses_slots_remaining_after_pins(self):
        first, second = self._candidate(21), self._candidate(22)
        index = SimpleNamespace(
            search_recent_async=AsyncMock(return_value=[first, second]),
            search_lexical_async=AsyncMock(return_value=[second, first]),
            search_semantic_async=AsyncMock(return_value=[first, second]),
            record_retrieval_event_async=AsyncMock(),
        )
        gemini = SimpleNamespace(
            client=object(), embed_texts=AsyncMock(return_value=[[1, 0, 0]]),
            select_relevant_context=AsyncMock(return_value=[]),
        )
        pins = SimpleNamespace(get_pins=lambda channel_id: [
            (pin_id, f"pin {pin_id}", "Ada", "Ray", datetime.now(timezone.utc).isoformat())
            for pin_id in range(1, 4)
        ])
        retriever = HybridContextRetriever(
            config=self._config(), message_index=index, context_collector=object(),
            gemini_client=gemini, pack_builder=ContextPackBuilder(), pin_service=pins,
        )
        message = SimpleNamespace(id=1, guild=SimpleNamespace(id=1), channel=SimpleNamespace(id=2), author=SimpleNamespace(id=3), reference=None)

        result = await retriever.retrieve(message=message, user_prompt="find it", complexity_level="low")

        # DAB-073 changed this contract deliberately. The test previously
        # asserted max_messages=1 and an "ambiguous_boundary" rerank, which
        # encoded the starvation as intended behaviour: 3 pins against a budget
        # of 4 left exactly one retrieval slot, so two candidates competing for
        # it forced a paid rerank to break the tie.
        #
        # available_retrieval_slots now carries a floor, so both candidates fit
        # and no rerank is needed. That is the point of the fix -- the earlier
        # behaviour spent a Gemini call to choose between two messages only
        # because pins had eaten the budget.
        gemini.select_relevant_context.assert_not_awaited()
        self.assertEqual(
            index.record_retrieval_event_async.await_args.kwargs["reranker_reason"],
            "within_context_limit",
        )
        # Pin priority in the pack itself is unchanged: pins still come first.
        self.assertEqual(sum(item.is_pinned_memory for item in result), 3)
        await retriever.close()

    async def test_the_rerank_boundary_still_binds_when_candidates_exceed_the_floor(self):
        # The floor raises the budget; it does not remove reranking. With more
        # candidates than slots the tie-break still runs, which is what keeps
        # the previous test from being a claim that reranking never happens.
        candidates = [self._candidate(30 + offset) for offset in range(6)]
        index = SimpleNamespace(
            search_recent_async=AsyncMock(return_value=candidates),
            search_lexical_async=AsyncMock(return_value=list(reversed(candidates))),
            search_semantic_async=AsyncMock(return_value=candidates),
            record_retrieval_event_async=AsyncMock(),
        )
        gemini = SimpleNamespace(
            client=object(), embed_texts=AsyncMock(return_value=[[1, 0, 0]]),
            select_relevant_context=AsyncMock(return_value=[]),
        )
        pins = SimpleNamespace(get_pins=lambda channel_id: [
            (pin_id, f"pin {pin_id}", "Ada", "Ray", datetime.now(timezone.utc).isoformat())
            for pin_id in range(1, 4)
        ])
        retriever = HybridContextRetriever(
            config=self._config(), message_index=index, context_collector=object(),
            gemini_client=gemini, pack_builder=ContextPackBuilder(), pin_service=pins,
        )
        message = SimpleNamespace(id=1, guild=SimpleNamespace(id=1), channel=SimpleNamespace(id=2), author=SimpleNamespace(id=3), reference=None)

        await retriever.retrieve(message=message, user_prompt="find it", complexity_level="low")

        # The boundary is evaluated rather than short-circuited. Whether it then
        # reranks depends on how close the scores are (ambiguous vs stable), and
        # asserting a particular verdict would pin the fusion arithmetic rather
        # than the gating behaviour this test is about.
        reason = index.record_retrieval_event_async.await_args.kwargs["reranker_reason"]
        self.assertIn(reason, {"ambiguous_boundary", "stable_boundary"})
        await retriever.close()


class ContextPriorityTest(unittest.TestCase):
    def test_reply_anchor_is_not_starved_by_pins(self):
        builder = ContextPackBuilder()
        pins = builder.build_pinned_context(
            [(pin_id, f"pin {pin_id}", "Ada", "Ray", datetime.now(timezone.utc).isoformat()) for pin_id in range(1, 5)],
            channel_id=2,
        )
        anchor = MessageContext(
            content="direct reply anchor", author="Ada",
            timestamp=datetime.now(timezone.utc), message_id=99, channel_id=2,
            retrieval_source="reply_anchor",
        )

        packed = builder.build_context_pack(
            pinned_context=pins, retrieved_context=[anchor], max_messages=4,
        )

        self.assertEqual(len(packed), 4)
        self.assertEqual(sum(item.is_pinned_memory for item in packed), 3)
        self.assertEqual(packed[-1].message_id, 99)


class RagDatabaseIsolationTest(unittest.TestCase):
    def test_legacy_rag_data_is_copied_to_dedicated_database_only(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            token_db = str(Path(temp_dir) / "token_usage.db")
            rag_db = str(Path(temp_dir) / "message_rag.db")
            legacy_index = MessageIndexService(
                token_db, embedding_model="test", embedding_dimensions=3,
            )
            legacy_index.upsert_message(
                message_id=31, guild_id=1, channel_id=2, author_id=3,
                author_name="Ada", is_bot=False, reply_to_message_id=None,
                created_at=datetime.now(timezone.utc),
                content_text="legacy searchable message",
            )
            legacy_pins = PinService(token_db)
            legacy_pins.add_pin(2, "legacy pin", "Ada", "Ray", guild_id=1)
            conn = sqlite3.connect(token_db)
            try:
                conn.execute("CREATE TABLE token_usage_sentinel(value TEXT)")
                conn.execute("INSERT INTO token_usage_sentinel VALUES ('preserved')")
                conn.commit()
            finally:
                conn.close()

            migrated_pins = PinService(rag_db, legacy_db_path=token_db)
            migrated_index = MessageIndexService(
                rag_db, embedding_model="test", embedding_dimensions=3,
                legacy_db_path=token_db,
            )

            self.assertEqual(migrated_index.get_status()["messages"], 1)
            self.assertEqual(len(migrated_pins.get_pins(2)), 1)
            self.assertEqual(
                [item.message_id for item in migrated_index.search_lexical(
                    "searchable", guild_id=1, channel_id=2,
                    cross_channel=False, limit=5,
                )],
                [31],
            )
            target = sqlite3.connect(rag_db)
            source = sqlite3.connect(token_db)
            try:
                self.assertIsNone(target.execute(
                    "SELECT 1 FROM sqlite_master WHERE type='table' AND name='token_usage_sentinel'"
                ).fetchone())
                self.assertEqual(source.execute(
                    "SELECT value FROM token_usage_sentinel"
                ).fetchone()[0], "preserved")
                self.assertEqual(source.execute(
                    "SELECT COUNT(*) FROM message_index"
                ).fetchone()[0], 1)
            finally:
                target.close()
                source.close()


if __name__ == "__main__":
    unittest.main()


class RetrievalFloorTest(unittest.TestCase):
    """DAB-073: pins must not drive the pre-retrieval budget to zero.

    Pins were allowed every packing slot but one, and the same arithmetic fed
    `available_retrieval_slots`. A channel with enough pinned memories therefore
    reported zero slots, the index was never searched, and the model answered
    from pins alone with the conversation invisible to it -- while the operator
    still paid for the embedding call that produced nothing usable.

    The floor lives in `available_retrieval_slots`, not in the packing. Pins keep
    absolute priority when the pack is assembled; what changes is that retrieval
    is always given a budget to compete for.
    """

    @staticmethod
    def _pins(count, channel_id=2):
        return ContextPackBuilder().build_pinned_context(
            [
                (pin_id, f"pin {pin_id}", "Ada", "Ray", datetime.now(timezone.utc).isoformat())
                for pin_id in range(1, count + 1)
            ],
            channel_id=channel_id,
        )

    def test_many_pins_no_longer_starve_retrieval_to_zero(self):
        builder = ContextPackBuilder()

        for max_messages in (4, 6, 8):
            with self.subTest(max_messages=max_messages):
                slots = builder.available_retrieval_slots(
                    pinned_context=self._pins(max_messages * 3),
                    reply_context=[],
                    max_messages=max_messages,
                )
                self.assertGreaterEqual(slots, MIN_RETRIEVAL_SLOTS)

    def test_the_floor_yields_at_budgets_too_small_to_divide(self):
        builder = ContextPackBuilder()

        self.assertEqual(
            builder.available_retrieval_slots(
                pinned_context=self._pins(5), reply_context=[], max_messages=1
            ),
            0,
        )

    def test_pins_still_take_priority_when_the_pack_is_assembled(self):
        # The floor is a retrieval budget, not a packing quota. With no
        # retrieved candidates to spend it on, pins fill the pack as before.
        builder = ContextPackBuilder()
        pins = self._pins(8)

        packed = builder.build_context_pack(
            pinned_context=pins, retrieved_context=[], max_messages=4
        )

        self.assertEqual(len(packed), 4)
        self.assertTrue(all(item.is_pinned_memory for item in packed))
