import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from src.services.context_pack_builder import ContextPackBuilder
from src.services.message_index_service import MessageIndexService
from src.models.data_models import MessageContext


class MessageIndexServiceTest(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_path = str(Path(self.temp_dir.name) / "rag.db")
        self.service = MessageIndexService(self.db_path, embedding_model="test-embedding")
        self.now = datetime.now(timezone.utc)

    def tearDown(self):
        self.temp_dir.cleanup()

    def _upsert(self, message_id: int, content: str, created_offset: int = 0):
        return self.service.upsert_message(
            message_id=message_id,
            guild_id=10,
            channel_id=20,
            author_id=30 + message_id,
            author_name=f"User {message_id}",
            is_bot=False,
            reply_to_message_id=None,
            created_at=self.now + timedelta(minutes=created_offset),
            content_text=content,
        )

    def test_lexical_search_finds_exact_terms(self):
        self._upsert(101, "The build failed with TokenMismatchError in auth.py")
        self._upsert(102, "Unrelated weekend planning")

        results = self.service.search_lexical(
            "TokenMismatchError auth.py",
            guild_id=10,
            channel_id=20,
            cross_channel=False,
            limit=5,
        )

        if not self.service.fts_enabled:
            self.assertEqual(results, [])
            return

        self.assertEqual([item.message_id for item in results], [101])

    def test_semantic_search_scores_stored_embeddings(self):
        self._upsert(201, "Use the blue deployment checklist")
        self._upsert(202, "Lunch is at noon")
        pending = {message_id: content_hash for message_id, _text, content_hash in self.service.get_pending_embeddings()}

        self.service.store_embedding(201, [1.0, 0.0, 0.0], pending[201])
        self.service.store_embedding(202, [0.0, 1.0, 0.0], pending[202])

        results = self.service.search_semantic(
            [0.9, 0.1, 0.0],
            guild_id=10,
            channel_id=20,
            cross_channel=False,
            limit=2,
        )

        self.assertEqual(results[0].message_id, 201)
        self.assertGreater(results[0].semantic_score, results[1].semantic_score)

    def test_hidden_messages_are_excluded_from_retrieval(self):
        self._upsert(301, "Visible search phrase")
        self._upsert(302, "Hidden search phrase")
        self.service.mark_hidden(302, True)

        recent_ids = [
            item.message_id
            for item in self.service.search_recent(
                guild_id=10,
                channel_id=20,
                cross_channel=False,
                limit=10,
            )
        ]
        self.assertIn(301, recent_ids)
        self.assertNotIn(302, recent_ids)

        lexical = self.service.search_lexical(
            "Hidden search phrase",
            guild_id=10,
            channel_id=20,
            cross_channel=False,
            limit=10,
        )
        self.assertNotIn(302, [item.message_id for item in lexical])

    def test_ordinary_upsert_preserves_hidden_state(self):
        self._upsert(303, "Canonical hidden response")
        self.service.mark_hidden(303, True)

        self._upsert(303, "Edited presentation text")

        recent_ids = [
            item.message_id
            for item in self.service.search_recent(
                guild_id=10,
                channel_id=20,
                cross_channel=False,
                limit=10,
            )
        ]
        self.assertNotIn(303, recent_ids)

    def test_hidden_edit_is_embedded_after_unhide(self):
        self._upsert(304, "Original hidden response")
        self.service.mark_hidden(304, True)
        self._upsert(304, "Updated while hidden")

        self.service.mark_hidden(304, False)

        pending_ids = {
            message_id
            for message_id, _text, _content_hash in self.service.get_pending_embeddings()
        }
        self.assertIn(304, pending_ids)

    def test_deleted_messages_are_excluded_from_retrieval(self):
        self._upsert(351, "Keep this release note")
        self._upsert(352, "Delete this stale release note")
        self.service.mark_deleted(352)

        recent_ids = [
            item.message_id
            for item in self.service.search_recent(
                guild_id=10,
                channel_id=20,
                cross_channel=False,
                limit=10,
            )
        ]
        self.assertIn(351, recent_ids)
        self.assertNotIn(352, recent_ids)

        lexical = self.service.search_lexical(
            "stale release note",
            guild_id=10,
            channel_id=20,
            cross_channel=False,
            limit=10,
        )
        self.assertNotIn(352, [item.message_id for item in lexical])

    def test_stale_upsert_does_not_resurrect_deleted_message(self):
        self._upsert(353, "Message deleted on Discord")
        self.service.mark_deleted(353)

        self._upsert(353, "Late edit event")

        recent_ids = [
            item.message_id
            for item in self.service.search_recent(
                guild_id=10,
                channel_id=20,
                cross_channel=False,
                limit=10,
            )
        ]
        self.assertNotIn(353, recent_ids)

    def test_embedding_failures_retry_before_terminal_failure(self):
        self._upsert(361, "Semantic memory should retry after transient failure")

        self.service.mark_embedding_failed(361, "temporary outage")
        status = self.service.get_status(channel_id=20)
        self.assertEqual(status["pending_embeddings"], 1)
        self.assertEqual(status["failed_embeddings"], 0)

        self.service.mark_embedding_failed(361, "temporary outage")
        self.service.mark_embedding_failed(361, "temporary outage")
        status = self.service.get_status(channel_id=20)
        self.assertEqual(status["pending_embeddings"], 0)
        self.assertEqual(status["failed_embeddings"], 1)

    def test_status_counts_are_channel_scoped(self):
        self._upsert(371, "Channel twenty message")
        self.service.upsert_message(
            message_id=372,
            guild_id=10,
            channel_id=21,
            author_id=99,
            author_name="Other User",
            is_bot=False,
            reply_to_message_id=None,
            created_at=self.now,
            content_text="Channel twenty one message",
        )
        pending = {message_id: content_hash for message_id, _text, content_hash in self.service.get_pending_embeddings()}
        self.service.store_embedding(371, [1.0, 0.0], pending[371])

        channel_20 = self.service.get_status(channel_id=20)
        channel_21 = self.service.get_status(channel_id=21)

        self.assertEqual(channel_20["messages"], 1)
        self.assertEqual(channel_20["embedded"], 1)
        self.assertEqual(channel_20["pending_embeddings"], 0)
        self.assertEqual(channel_21["messages"], 1)
        self.assertEqual(channel_21["embedded"], 0)
        self.assertEqual(channel_21["pending_embeddings"], 1)


class ContextPackBuilderTest(unittest.TestCase):
    def test_pack_keeps_pins_first_and_respects_limit(self):
        builder = ContextPackBuilder()
        pinned = builder.build_pinned_context(
            [(1, "Always remember the launch codename is Bluebird.", "Ada", "Ray", datetime.now(timezone.utc).isoformat())],
            channel_id=20,
        )
        retrieved = [
            MessageContext(
                content="Low confidence context",
                author="User",
                timestamp=datetime.now(timezone.utc),
                message_id=401,
                channel_id=20,
                retrieval_source="semantic",
                retrieval_score=0.2,
            ),
            MessageContext(
                content="High confidence context",
                author="User",
                timestamp=datetime.now(timezone.utc),
                message_id=402,
                channel_id=20,
                retrieval_source="lexical",
                retrieval_score=0.9,
            ),
        ]

        packed = builder.build_context_pack(
            pinned_context=pinned,
            retrieved_context=retrieved,
            max_messages=2,
        )

        self.assertEqual(len(packed), 2)
        self.assertTrue(packed[0].is_pinned_memory)
        self.assertEqual(packed[1].message_id, 401)


if __name__ == "__main__":
    unittest.main()
