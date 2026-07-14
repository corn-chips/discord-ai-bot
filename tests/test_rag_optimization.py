"""Focused coverage for cost-efficient hybrid RAG behavior."""

import sqlite3
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

from src.services.context_pack_builder import ContextPackBuilder
from src.services.hybrid_context_retriever import HybridContextRetriever
from src.services.message_index_service import MessageIndexService


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


if __name__ == "__main__":
    unittest.main()
