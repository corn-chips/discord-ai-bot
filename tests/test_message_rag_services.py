import asyncio
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

from src.services.context_pack_builder import ContextPackBuilder
from src.services.hybrid_context_retriever import HybridContextRetriever
from src.services.message_index_service import MessageIndexService
from src.services.pin_service import PinService
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

    def test_a_healthy_status_carries_no_error_marker(self):
        self._upsert(373, "Channel twenty message")

        self.assertNotIn("error", self.service.get_status(channel_id=20))

    def test_an_unreadable_index_is_distinguishable_from_an_empty_one(self):
        # DAB-170: get_status caught every exception and returned all zeros,
        # which is exactly what a healthy, freshly-created index returns. An
        # operator running /rag status against a corrupt database saw an
        # ordinary, apparently healthy report.
        self._upsert(374, "Channel twenty message")
        with open(self.db_path, "wb") as handle:
            handle.write(b"this is not a sqlite database" * 64)

        status = self.service.get_status(channel_id=20)

        self.assertEqual(status["messages"], 0, "precondition: the counts do read as empty")
        self.assertTrue(status.get("error"), "a read failure must be visible in the payload")
        self.assertIn("database", status["error"].lower())

    def test_index_and_embeddings_persist_across_service_instances(self):
        self._upsert(381, "Persistent local RAG memory")
        pending = {
            message_id: content_hash
            for message_id, _text, content_hash in self.service.get_pending_embeddings()
        }
        self.service.store_embedding(381, [0.1, 0.2], pending[381])
        with self.service._connection() as conn:
            storage_type = conn.execute(
                "SELECT typeof(embedding_vector) FROM message_embeddings WHERE message_id = ?",
                (381,),
            ).fetchone()[0]

        reopened = MessageIndexService(
            self.db_path,
            embedding_model="test-embedding",
        )
        status = reopened.get_status(channel_id=20)
        recent = reopened.search_recent(
            guild_id=10,
            channel_id=20,
            cross_channel=False,
            limit=10,
        )

        self.assertEqual(status["messages"], 1)
        self.assertEqual(status["embedded"], 1)
        self.assertEqual(storage_type, "blob")
        self.assertTrue(Path(status["database_path"]).is_absolute())
        self.assertEqual([message.message_id for message in recent], [381])

    def test_embedding_model_change_requeues_existing_message(self):
        self._upsert(382, "Re-embed this message after a model change")
        pending = {
            message_id: content_hash
            for message_id, _text, content_hash in self.service.get_pending_embeddings()
        }
        self.service.store_embedding(382, [1.0, 0.0], pending[382])

        migrated = MessageIndexService(self.db_path, embedding_model="new-embedding")
        migrated.upsert_message(
            message_id=382,
            guild_id=10,
            channel_id=20,
            author_id=412,
            author_name="User 382",
            is_bot=False,
            reply_to_message_id=None,
            created_at=self.now,
            content_text="Re-embed this message after a model change",
        )

        status = migrated.get_status(channel_id=20)
        self.assertEqual(status["embedded"], 0)
        self.assertEqual(status["pending_embeddings"], 1)

    def test_pending_embedding_batches_can_be_scoped_to_one_channel(self):
        self._upsert(383, "Channel twenty pending")
        self.service.upsert_message(
            message_id=384,
            guild_id=10,
            channel_id=21,
            author_id=414,
            author_name="Other channel user",
            is_bot=False,
            reply_to_message_id=None,
            created_at=self.now,
            content_text="Channel twenty one pending",
        )

        pending = self.service.get_pending_embeddings(channel_id=21)

        self.assertEqual([message_id for message_id, _text, _hash in pending], [384])

    def test_delete_rag_data_can_be_scoped_to_channel_or_all_channels(self):
        pins = PinService(self.db_path)
        self._upsert(391, "Delete channel twenty memory")
        self.service.upsert_message(
            message_id=392,
            guild_id=10,
            channel_id=21,
            author_id=422,
            author_name="Other channel user",
            is_bot=False,
            reply_to_message_id=None,
            created_at=self.now,
            content_text="Keep channel twenty one memory",
        )
        self.service.record_retrieval_event(
            guild_id=10,
            channel_id=20,
            user_id=30,
            query_length=5,
            selected_message_ids=[391],
            fallback_reason=None,
            latency_ms=1,
        )
        self.service.record_retrieval_event(
            guild_id=10,
            channel_id=21,
            user_id=31,
            query_length=5,
            selected_message_ids=[392],
            fallback_reason=None,
            latency_ms=1,
        )
        self.service.advance_backfill_progress(
            channel_id=20,
            guild_id=10,
            last_message_id=391,
        )
        self.service.advance_backfill_progress(
            channel_id=21,
            guild_id=10,
            last_message_id=392,
        )
        pins.add_pin(20, "Delete channel twenty pin", "Ada", "Grace")
        pins.add_pin(21, "Keep channel twenty one pin", "Lin", "Margaret")
        with self.service._connection(transaction=True) as conn:
            conn.execute("CREATE TABLE unrelated_data (value TEXT)")
            conn.execute("INSERT INTO unrelated_data (value) VALUES ('preserved')")

        deleted = self.service.delete_rag_data(channel_id=20)

        self.assertEqual(deleted, {"messages": 1, "pins": 1})
        self.assertEqual(self.service.get_status(channel_id=20)["messages"], 0)
        self.assertEqual(self.service.get_status(channel_id=21)["messages"], 1)
        self.assertIsNone(self.service.get_backfill_progress(20))
        self.assertIsNotNone(self.service.get_backfill_progress(21))
        self.assertEqual(pins.get_pins(20), [])
        self.assertEqual(len(pins.get_pins(21)), 1)
        with self.service._connection() as conn:
            event_channels = [
                row[0]
                for row in conn.execute(
                    "SELECT channel_id FROM message_retrieval_events ORDER BY channel_id"
                ).fetchall()
            ]
            unrelated = conn.execute("SELECT value FROM unrelated_data").fetchone()[0]
        self.assertEqual(event_channels, [21])
        self.assertEqual(unrelated, "preserved")

        deleted = self.service.delete_rag_data()

        self.assertEqual(deleted, {"messages": 1, "pins": 1})
        self.assertEqual(self.service.get_status()["messages"], 0)
        self.assertIsNone(self.service.get_backfill_progress(21))
        self.assertEqual(pins.get_pins(21), [])
        with self.service._connection() as conn:
            remaining_events = conn.execute(
                "SELECT COUNT(*) FROM message_retrieval_events"
            ).fetchone()[0]
            unrelated = conn.execute("SELECT value FROM unrelated_data").fetchone()[0]
        self.assertEqual(remaining_events, 0)
        self.assertEqual(unrelated, "preserved")


class MessageBackfillTest(unittest.IsolatedAsyncioTestCase):
    async def test_none_limit_requests_complete_history_in_oldest_first_order(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            service = MessageIndexService(
                str(Path(temp_dir) / "rag.db"),
                embedding_model="test-embedding",
            )
            service.index_discord_message_async = AsyncMock(
                side_effect=[True, False, True]
            )

            class FakeChannel:
                id = 20

                def __init__(self):
                    self.history_kwargs = None

                async def history(self, **kwargs):
                    self.history_kwargs = kwargs
                    for message_id in (1, 2, 3):
                        yield SimpleNamespace(id=message_id)

            channel = FakeChannel()

            indexed = await service.backfill_channel(
                channel,
                limit=None,
                include_bot_user_id=99,
            )

            self.assertEqual(indexed, 2)
            self.assertEqual(
                channel.history_kwargs,
                {"limit": None, "oldest_first": True},
            )
            self.assertEqual(
                [call.args[0].id for call in service.index_discord_message_async.await_args_list],
                [1, 2, 3],
            )

    async def test_interrupted_backfill_resumes_from_persisted_channel_cursor(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            db_path = str(Path(temp_dir) / "rag.db")
            service = MessageIndexService(db_path, embedding_model="test-embedding")
            service.index_discord_message_async = AsyncMock(return_value=True)

            class InterruptedChannel:
                id = 20
                guild = SimpleNamespace(id=10)

                async def history(self, **_kwargs):
                    yield SimpleNamespace(id=1)
                    yield SimpleNamespace(id=2)
                    raise RuntimeError("connection lost")

            with self.assertRaisesRegex(RuntimeError, "connection lost"):
                await service.backfill_channel(
                    InterruptedChannel(),
                    limit=None,
                    include_bot_user_id=99,
                )

            interrupted = service.get_backfill_progress(20)
            self.assertEqual(interrupted["last_message_id"], 2)
            self.assertEqual(interrupted["scanned_messages"], 2)
            self.assertIsNone(interrupted["completed_at"])

            reopened = MessageIndexService(db_path, embedding_model="test-embedding")
            reopened.index_discord_message_async = AsyncMock(return_value=True)

            class ResumedChannel:
                id = 20
                guild = SimpleNamespace(id=10)

                def __init__(self):
                    self.history_kwargs = None

                async def history(self, **kwargs):
                    self.history_kwargs = kwargs
                    yield SimpleNamespace(id=3)

            channel = ResumedChannel()
            indexed = await reopened.backfill_channel(
                channel,
                limit=None,
                include_bot_user_id=99,
            )

            self.assertEqual(indexed, 1)
            self.assertEqual(channel.history_kwargs["after"].id, 2)
            self.assertEqual(channel.history_kwargs["limit"], None)
            self.assertTrue(channel.history_kwargs["oldest_first"])
            resumed = reopened.get_backfill_progress(20)
            self.assertEqual(resumed["last_message_id"], 3)
            self.assertEqual(resumed["scanned_messages"], 3)
            self.assertIsNotNone(resumed["completed_at"])


class RagPregenerationTest(unittest.IsolatedAsyncioTestCase):
    @staticmethod
    def _make_retriever(message_index):
        gemini_client = SimpleNamespace(
            client=object(),
            embed_texts=AsyncMock(return_value=[[1.0, 0.0], [0.0, 1.0]]),
        )
        retriever = HybridContextRetriever(
            config=SimpleNamespace(
                rag_semantic_candidates=40,
                rag_embedding_model="gemini-embedding-2",
            ),
            message_index=message_index,
            context_collector=object(),
            gemini_client=gemini_client,
            pack_builder=object(),
        )
        return retriever, gemini_client

    async def test_cancel_background_work_stops_jobs_that_can_repopulate_channel(self):
        retriever, _gemini_client = self._make_retriever(SimpleNamespace())
        channel_embedding = asyncio.create_task(asyncio.sleep(60))
        channel_backfill = asyncio.create_task(asyncio.sleep(60))
        global_backlog = asyncio.create_task(asyncio.sleep(60))
        other_embedding = asyncio.create_task(asyncio.sleep(60))
        retriever._embedding_tasks = {20: channel_embedding, 21: other_embedding}
        retriever._pregeneration_tasks = {20: channel_backfill}
        retriever._pregeneration_status = {20: {"phase": "scanning"}}
        retriever._all_channels_task = global_backlog
        retriever._all_channels_status = {"phase": "running"}

        await retriever.cancel_background_work(channel_id=20)

        self.assertTrue(channel_embedding.cancelled())
        self.assertTrue(channel_backfill.cancelled())
        self.assertTrue(global_backlog.cancelled())
        self.assertFalse(other_embedding.cancelled())
        self.assertEqual(retriever._pregeneration_status[20]["phase"], "cancelled")
        self.assertEqual(retriever._all_channels_status["phase"], "cancelled")

        other_embedding.cancel()
        await asyncio.gather(other_embedding, return_exceptions=True)

    async def test_pregeneration_does_not_report_complete_over_an_unreadable_index(self):
        # A degraded status is all zeros, so `pending or failed` reads as
        # "nothing outstanding" and the job declared itself complete over a
        # database it could not open (DAB-170).
        message_index = SimpleNamespace(
            backfill_channel=AsyncMock(return_value=2),
            get_pending_embeddings_async=AsyncMock(return_value=[]),
            store_embedding_async=AsyncMock(return_value=True),
            mark_embedding_failed_async=AsyncMock(),
            get_status_async=AsyncMock(
                return_value={
                    "embedded": 0,
                    "pending_embeddings": 0,
                    "failed_embeddings": 0,
                    "error": "DatabaseError: file is not a database",
                }
            ),
        )
        retriever, _gemini_client = self._make_retriever(message_index)

        retriever.start_channel_pregeneration(
            SimpleNamespace(id=20),
            limit=None,
            include_bot_user_id=99,
        )
        await retriever._pregeneration_tasks[20]

        status = retriever.get_pregeneration_status(20)
        self.assertEqual(status["phase"], "failed")
        self.assertIn("file is not a database", status["error"])

    async def test_complete_history_pregeneration_indexes_and_embeds_in_background(self):
        pending = [
            (1, "title: one | text: first", "hash-1"),
            (2, "title: two | text: second", "hash-2"),
        ]
        message_index = SimpleNamespace(
            backfill_channel=AsyncMock(return_value=2),
            get_pending_embeddings_async=AsyncMock(side_effect=[pending, []]),
            store_embedding_async=AsyncMock(return_value=True),
            mark_embedding_failed_async=AsyncMock(),
            get_status_async=AsyncMock(
                return_value={
                    "embedded": 2,
                    "pending_embeddings": 0,
                    "failed_embeddings": 0,
                }
            ),
        )
        retriever, gemini_client = self._make_retriever(message_index)
        channel = SimpleNamespace(id=20)

        started = retriever.start_channel_pregeneration(
            channel,
            limit=None,
            include_bot_user_id=99,
        )
        task = retriever._pregeneration_tasks[20]
        await task

        self.assertTrue(started)
        message_index.backfill_channel.assert_awaited_once_with(
            channel,
            limit=None,
            include_bot_user_id=99,
        )
        gemini_client.embed_texts.assert_awaited_once()
        self.assertEqual(message_index.store_embedding_async.await_count, 2)
        self.assertEqual(
            retriever.get_pregeneration_status(20),
            {
                "phase": "complete",
                "limit": None,
                "indexed": 2,
                "embedded": 2,
                "failed": 0,
                "error": None,
            },
        )
        await retriever.close()

    async def test_background_embedding_schedule_is_debounced_per_channel(self):
        pending = [(1, "title: one | text: first", "hash-1")]
        message_index = SimpleNamespace(
            get_pending_embeddings_async=AsyncMock(side_effect=[pending, []]),
            store_embedding_async=AsyncMock(return_value=True),
            mark_embedding_failed_async=AsyncMock(),
        )
        retriever, gemini_client = self._make_retriever(message_index)
        gemini_client.embed_texts.return_value = [[1.0, 0.0]]
        retriever.BACKGROUND_EMBED_DELAY_SECONDS = 0

        retriever.schedule_pending_embeddings(20)
        first_task = retriever._embedding_tasks[20]
        retriever.schedule_pending_embeddings(20)
        second_task = retriever._embedding_tasks[20]
        await first_task

        self.assertIs(first_task, second_task)
        gemini_client.embed_texts.assert_awaited_once()
        await retriever.close()

    async def test_all_channel_pregeneration_runs_each_channel_sequentially(self):
        message_index = SimpleNamespace(
            backfill_channel=AsyncMock(side_effect=[1, 2]),
            get_pending_embeddings_async=AsyncMock(return_value=[]),
            store_embedding_async=AsyncMock(return_value=True),
            mark_embedding_failed_async=AsyncMock(),
            get_status_async=AsyncMock(
                return_value={
                    "embedded": 0,
                    "pending_embeddings": 0,
                    "failed_embeddings": 0,
                }
            ),
        )
        retriever, _gemini_client = self._make_retriever(message_index)
        channels = [SimpleNamespace(id=20), SimpleNamespace(id=21)]

        started = retriever.start_all_channel_pregeneration(
            channels,
            limit=None,
            include_bot_user_id=99,
        )
        task = retriever._all_channels_task
        await task

        self.assertTrue(started)
        self.assertEqual(
            [call.args[0].id for call in message_index.backfill_channel.await_args_list],
            [20, 21],
        )
        self.assertEqual(
            retriever.get_all_channels_pregeneration_status(),
            {
                "phase": "complete",
                "total": 2,
                "processed": 2,
                "failed": 0,
            },
        )
        await retriever.close()

    async def test_retrieve_does_not_wait_for_history_or_document_embeddings(self):
        message_index = SimpleNamespace(
            search_recent_async=AsyncMock(return_value=[]),
            search_lexical_async=AsyncMock(return_value=[]),
            search_semantic_async=AsyncMock(return_value=[]),
            record_retrieval_event_async=AsyncMock(),
        )
        gemini_client = SimpleNamespace(
            client=object(),
            embed_texts=AsyncMock(return_value=[[1.0, 0.0]]),
        )
        retriever = HybridContextRetriever(
            config=SimpleNamespace(
                rag_max_context_messages_low=4,
                rag_max_context_messages_medium=8,
                rag_max_context_messages_high=12,
                rag_lexical_candidates=40,
                rag_semantic_candidates=40,
                rag_rerank_candidates=0,
                rag_recency_half_life_hours=72,
                rag_cross_channel_enabled=False,
                rag_embedding_model="gemini-embedding-2",
            ),
            message_index=message_index,
            context_collector=object(),
            gemini_client=gemini_client,
            pack_builder=ContextPackBuilder(),
        )
        message = SimpleNamespace(
            id=50,
            guild=SimpleNamespace(id=10),
            channel=SimpleNamespace(id=20),
            author=SimpleNamespace(id=30),
            reference=None,
        )

        result = await retriever.retrieve(
            message=message,
            user_prompt="find this",
            complexity_level="low",
            bot_user_id=99,
        )

        self.assertEqual(result, [])
        gemini_client.embed_texts.assert_awaited_once_with(
            ["find this"],
            model_name="gemini-embedding-2",
            task_type="RETRIEVAL_QUERY",
        )
        message_index.record_retrieval_event_async.assert_awaited_once()
        await retriever.close()


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
