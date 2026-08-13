"""Conversation grouping: segmentation rules, the reply merge, and the window query.

A retrieval hit used to hand the model one orphan message. `conversation_id`
groups a channel's messages into exchanges so a hit can be expanded into the
discussion around it.
"""

import contextlib
import importlib.util
import io
import sqlite3
import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from src.services.message_index_service import MessageIndexService

BASE = datetime(2026, 1, 1, 12, 0, tzinfo=timezone.utc)
SCRIPT_PATH = Path(__file__).resolve().parents[1] / "scripts" / "rebuild_conversations.py"


def add_message(service, message_id, *, channel_id=20, author_id=1, minutes=0.0, reply_to=None):
    service.upsert_message(
        message_id=message_id,
        guild_id=10,
        channel_id=channel_id,
        author_id=author_id,
        author_name=f"user{author_id}",
        is_bot=False,
        reply_to_message_id=reply_to,
        created_at=BASE + timedelta(minutes=minutes),
        content_text=f"message {message_id} from user {author_id} about the deploy script",
    )


def conversation_ids(service, channel_id=20):
    with sqlite3.connect(service.db_path) as conn:
        return [
            (row[0], row[1])
            for row in conn.execute(
                "SELECT message_id, conversation_id FROM message_index "
                "WHERE channel_id = ? ORDER BY created_at, message_id",
                (channel_id,),
            )
        ]


class ConversationSegmentationTest(unittest.TestCase):
    def setUp(self):
        self._dir = tempfile.TemporaryDirectory()
        self.addCleanup(self._dir.cleanup)
        self.db_path = str(Path(self._dir.name) / "rag.db")

    def build(self, **kwargs):
        return MessageIndexService(self.db_path, embedding_model="test-embedding", **kwargs)

    def test_a_silence_longer_than_the_gap_starts_a_new_conversation(self):
        service = self.build(conversation_gap_minutes=10.0)
        for index in range(3):
            add_message(service, 100 + index, minutes=index)
        for index in range(3):
            add_message(service, 200 + index, minutes=40 + index)

        service.recompute_channel_conversations(20)

        ids = dict(conversation_ids(service))
        self.assertEqual({ids[100], ids[101], ids[102]}, {100})
        self.assertEqual({ids[200], ids[201], ids[202]}, {200})

    def test_a_gap_inside_the_threshold_keeps_one_conversation(self):
        service = self.build(conversation_gap_minutes=10.0)
        add_message(service, 100, minutes=0)
        add_message(service, 101, minutes=9)

        service.recompute_channel_conversations(20)

        self.assertEqual(conversation_ids(service), [(100, 100), (101, 100)])

    def test_the_size_cap_breaks_a_busy_channel_into_several_conversations(self):
        service = self.build(conversation_max_messages=4)
        for index in range(10):
            add_message(service, 100 + index, minutes=index)

        service.recompute_channel_conversations(20)

        ids = [conversation for _message_id, conversation in conversation_ids(service)]
        self.assertEqual(ids, [100] * 4 + [104] * 4 + [108] * 2)

    def test_participant_turnover_with_a_sub_gap_splits(self):
        service = self.build(conversation_turnover_window=3, conversation_turnover_min_gap_minutes=3.0)
        for index in range(3):
            add_message(service, 100 + index, author_id=1, minutes=index)
        for index in range(3):
            add_message(service, 200 + index, author_id=2, minutes=5 + index * 0.5)

        service.recompute_channel_conversations(20)

        ids = dict(conversation_ids(service))
        self.assertEqual({ids[100], ids[101], ids[102]}, {100})
        self.assertEqual({ids[200], ids[201], ids[202]}, {200})

    def test_turnover_without_the_sub_gap_does_not_split(self):
        # A newcomer joining an active discussion is not a new conversation.
        service = self.build(conversation_turnover_window=3, conversation_turnover_min_gap_minutes=3.0)
        for index in range(3):
            add_message(service, 100 + index, author_id=1, minutes=index * 0.5)
        for index in range(3):
            add_message(service, 200 + index, author_id=2, minutes=2 + index * 0.5)

        service.recompute_channel_conversations(20)

        ids = {conversation for _message_id, conversation in conversation_ids(service)}
        self.assertEqual(ids, {100})

    def test_a_reply_inside_the_hour_bound_merges_the_two_conversations(self):
        service = self.build(conversation_gap_minutes=10.0, conversation_reply_merge_max_hours=6.0)
        for index in range(3):
            add_message(service, 100 + index, minutes=index)
        add_message(service, 200, minutes=45, reply_to=101)
        add_message(service, 201, minutes=46)

        service.recompute_channel_conversations(20)

        ids = {conversation for _message_id, conversation in conversation_ids(service)}
        self.assertEqual(ids, {100})

    def test_a_reply_outside_the_hour_bound_is_left_alone(self):
        # Otherwise a reply to a three-day-old message drags the whole thing in.
        service = self.build(conversation_gap_minutes=10.0, conversation_reply_merge_max_hours=6.0)
        for index in range(3):
            add_message(service, 100 + index, minutes=index)
        add_message(service, 200, minutes=60 * 10, reply_to=101)

        service.recompute_channel_conversations(20)

        ids = dict(conversation_ids(service))
        self.assertEqual(ids[100], 100)
        self.assertEqual(ids[200], 200)

    def test_a_merge_that_would_exceed_the_cap_is_refused(self):
        service = self.build(conversation_gap_minutes=10.0, conversation_max_messages=4)
        for index in range(4):
            add_message(service, 100 + index, minutes=index)
        add_message(service, 200, minutes=45, reply_to=100)

        service.recompute_channel_conversations(20)

        ids = dict(conversation_ids(service))
        self.assertEqual(ids[200], 200)
        self.assertEqual({ids[100], ids[101], ids[102], ids[103]}, {100})

    def test_recomputing_twice_yields_identical_ids(self):
        service = self.build(conversation_gap_minutes=10.0, conversation_max_messages=5)
        for index in range(12):
            add_message(service, 100 + index, author_id=1 + (index % 3), minutes=index * 4)
        add_message(service, 300, minutes=50, reply_to=104)

        first = service.recompute_channel_conversations(20)
        after_first = conversation_ids(service)
        second = service.recompute_channel_conversations(20)

        self.assertEqual(after_first, conversation_ids(service))
        self.assertEqual(first["conversations"], second["conversations"])
        self.assertEqual(second["changed"], 0)

    def test_a_dry_run_reports_without_writing(self):
        service = self.build(conversation_turnover_window=3, conversation_turnover_min_gap_minutes=3.0)
        for index in range(3):
            add_message(service, 100 + index, author_id=1, minutes=index)
        for index in range(3):
            add_message(service, 200 + index, author_id=2, minutes=5 + index * 0.5)
        before = conversation_ids(service)

        result = service.recompute_channel_conversations(20, dry_run=True)

        self.assertEqual(result["messages"], 6)
        self.assertGreater(result["changed"], 0)
        self.assertEqual(conversation_ids(service), before)

    def test_channels_are_segmented_independently(self):
        service = self.build()
        add_message(service, 100, channel_id=20, minutes=0)
        add_message(service, 200, channel_id=21, minutes=0.5)

        service.recompute_channel_conversations(20)
        service.recompute_channel_conversations(21)

        self.assertEqual(conversation_ids(service, 20), [(100, 100)])
        self.assertEqual(conversation_ids(service, 21), [(200, 200)])
        self.assertEqual(service.list_indexed_channel_ids(), [20, 21])


class ConversationInlineAssignmentTest(unittest.TestCase):
    """The live path: rules (a) and (b) plus a single-hop reply merge, at upsert time."""

    def setUp(self):
        self._dir = tempfile.TemporaryDirectory()
        self.addCleanup(self._dir.cleanup)
        self.db_path = str(Path(self._dir.name) / "rag.db")

    def build(self, **kwargs):
        return MessageIndexService(self.db_path, embedding_model="test-embedding", **kwargs)

    def test_a_live_message_joins_the_conversation_in_progress(self):
        service = self.build(conversation_gap_minutes=10.0)
        add_message(service, 100, minutes=0)
        add_message(service, 101, minutes=1)

        self.assertEqual(conversation_ids(service), [(100, 100), (101, 100)])

    def test_a_live_message_after_the_gap_starts_a_new_conversation(self):
        service = self.build(conversation_gap_minutes=10.0)
        add_message(service, 100, minutes=0)
        add_message(service, 101, minutes=30)

        self.assertEqual(conversation_ids(service), [(100, 100), (101, 101)])

    def test_the_live_size_cap_holds(self):
        service = self.build(conversation_max_messages=3)
        for index in range(4):
            add_message(service, 100 + index, minutes=index)

        ids = [conversation for _message_id, conversation in conversation_ids(service)]
        self.assertEqual(ids, [100, 100, 100, 103])

    def test_a_live_reply_adopts_the_conversation_it_answers(self):
        service = self.build(conversation_gap_minutes=10.0)
        add_message(service, 100, minutes=0)
        add_message(service, 200, minutes=45, reply_to=100)

        self.assertEqual(dict(conversation_ids(service))[200], 100)

    def test_a_live_reply_into_a_full_conversation_stays_separate(self):
        service = self.build(conversation_gap_minutes=10.0, conversation_max_messages=3)
        for index in range(3):
            add_message(service, 100 + index, minutes=index)
        add_message(service, 200, minutes=45, reply_to=100)

        self.assertEqual(dict(conversation_ids(service))[200], 200)

    def test_a_live_reply_past_the_merge_bound_stays_separate(self):
        service = self.build(conversation_gap_minutes=10.0, conversation_reply_merge_max_hours=6.0)
        add_message(service, 100, minutes=0)
        add_message(service, 200, minutes=60 * 10, reply_to=100)

        self.assertEqual(dict(conversation_ids(service))[200], 200)

    def test_reindexing_an_edited_message_keeps_its_conversation(self):
        service = self.build(conversation_gap_minutes=10.0)
        add_message(service, 100, minutes=0)
        add_message(service, 101, minutes=1)
        add_message(service, 102, minutes=90)
        service.upsert_message(
            message_id=100,
            guild_id=10,
            channel_id=20,
            author_id=1,
            author_name="user1",
            is_bot=False,
            reply_to_message_id=None,
            created_at=BASE,
            content_text="an edited first message that still belongs where it was",
        )

        self.assertEqual(dict(conversation_ids(service))[100], 100)

    def test_assignment_is_skipped_when_conversations_are_disabled(self):
        service = self.build(conversation_enabled=False)
        add_message(service, 100, minutes=0)

        self.assertEqual(conversation_ids(service), [(100, None)])


class ConversationWindowTest(unittest.TestCase):
    def setUp(self):
        self._dir = tempfile.TemporaryDirectory()
        self.addCleanup(self._dir.cleanup)
        self.db_path = str(Path(self._dir.name) / "rag.db")
        self.service = MessageIndexService(self.db_path, embedding_model="test-embedding")
        for index in range(21):
            add_message(self.service, 100 + index, minutes=index)
        self.service.recompute_channel_conversations(20)
        self.conversation_id = dict(conversation_ids(self.service))[100]

    def window(self, centre, **kwargs):
        kwargs.setdefault("full_max_messages", 12)
        kwargs.setdefault("window_messages", 5)
        return self.service.get_conversation_window(
            self.conversation_id, center_message_id=centre, **kwargs
        )

    def test_a_short_conversation_is_returned_in_full_and_in_order(self):
        service = MessageIndexService(
            str(Path(self._dir.name) / "short.db"), embedding_model="test-embedding"
        )
        for index in range(5):
            add_message(service, 500 + index, minutes=index)
        service.recompute_channel_conversations(20)

        window = service.get_conversation_window(
            500, center_message_id=502, full_max_messages=12, window_messages=3
        )

        self.assertEqual([message.message_id for message in window], [500, 501, 502, 503, 504])

    def test_a_long_conversation_is_centred_on_the_hit(self):
        window = self.window(110)

        self.assertEqual([message.message_id for message in window], [108, 109, 110, 111, 112])

    def test_the_window_clips_at_the_start_and_still_returns_a_full_window(self):
        window = self.window(100)

        self.assertEqual([message.message_id for message in window], [100, 101, 102, 103, 104])

    def test_the_window_clips_at_the_end_and_still_returns_a_full_window(self):
        window = self.window(120)

        self.assertEqual([message.message_id for message in window], [116, 117, 118, 119, 120])

    def test_excluded_messages_are_dropped_without_moving_the_centre(self):
        window = self.window(110, exclude_message_ids=[109, 111])

        self.assertEqual([message.message_id for message in window], [108, 110, 112])

    def test_hidden_and_deleted_messages_stay_out_of_the_window(self):
        self.service.mark_hidden(109)
        self.service.mark_deleted(111)

        window = self.window(110)

        self.assertEqual([message.message_id for message in window], [107, 108, 110, 112, 113])

    def test_the_window_carries_the_conversation_id_on_every_message(self):
        window = self.window(110)

        self.assertEqual(
            {message.conversation_id for message in window}, {self.conversation_id}
        )

    def test_an_unknown_conversation_returns_nothing(self):
        self.assertEqual(
            self.service.get_conversation_window(
                987654, center_message_id=110, full_max_messages=12, window_messages=5
            ),
            [],
        )


class ConversationWindowChannelScopeTest(unittest.TestCase):
    """The window query has to bind channel_id on every path or it full-scans.

    Dropping the predicate when the centre row was missing lost
    `idx_message_index_conversation` outright: SQLite scanned `message_index`
    and built a temp B-tree for the ORDER BY.
    """

    def setUp(self):
        self._dir = tempfile.TemporaryDirectory()
        self.addCleanup(self._dir.cleanup)
        self.db_path = str(Path(self._dir.name) / "rag.db")
        self.service = MessageIndexService(self.db_path, embedding_model="test-embedding")
        for index in range(30):
            add_message(self.service, 100 + index, minutes=index * 0.5)
        self.service.recompute_channel_conversations(20)

    def _forget(self, *message_ids):
        """Remove rows outright, as `delete_rag_data` does -- not a tombstone."""
        with sqlite3.connect(self.db_path) as conn:
            conn.executemany(
                "DELETE FROM message_index WHERE message_id = ?",
                [(message_id,) for message_id in message_ids],
            )
            conn.commit()

    def _traced(self, **kwargs):
        statements = []
        original = self.service._connection

        @contextlib.contextmanager
        def traced(*args, **inner):
            with original(*args, **inner) as conn:
                conn.set_trace_callback(statements.append)
                try:
                    yield conn
                finally:
                    conn.set_trace_callback(None)

        self.service._connection = traced
        try:
            window = self.service.get_conversation_window(
                100, full_max_messages=12, window_messages=5, **kwargs
            )
        finally:
            self.service._connection = original
        return window, statements

    def _plan(self, sql):
        with sqlite3.connect(self.db_path) as conn:
            return " | ".join(row[3] for row in conn.execute("EXPLAIN QUERY PLAN " + sql))

    def assert_index_driven(self, statements):
        matching = [sql for sql in statements if "FROM message_index m" in sql]
        self.assertTrue(matching, statements)
        plan = self._plan(matching[0])
        # SEARCH, not SCAN: unbound, channel_id left the plan
        # "SCAN m USING INDEX ... | USE TEMP B-TREE FOR ORDER BY", which walks
        # every conversation in the database and then sorts the result.
        self.assertIn("SEARCH m USING INDEX idx_message_index_conversation", plan)
        self.assertNotIn("SCAN", plan)
        self.assertNotIn("USE TEMP B-TREE FOR ORDER BY", plan)

    def test_a_missing_centre_row_still_drives_from_the_index(self):
        self._forget(110)

        window, statements = self._traced(center_message_id=110)

        self.assert_index_driven(statements)
        self.assertEqual([message.message_id for message in window][-1], 129)

    def test_an_explicit_channel_drives_the_index_and_skips_the_lookup(self):
        window, statements = self._traced(center_message_id=110, channel_id=20)

        self.assert_index_driven(statements)
        self.assertEqual([message.message_id for message in window], [108, 109, 110, 111, 112])
        self.assertFalse([sql for sql in statements if "SELECT channel_id" in sql])

    def test_a_window_whose_channel_cannot_be_resolved_returns_nothing(self):
        # Neither the centre nor the conversation's own root message is left to
        # name the channel, and a scan is not worth what it would find.
        self._forget(100, 110)

        window, statements = self._traced(center_message_id=110)

        self.assertEqual(window, [])
        self.assertFalse([sql for sql in statements if "FROM message_index m" in sql])


class InlineAndBatchMergeAgreeTest(unittest.TestCase):
    """The live reply merge and the rebuild must not segment the same data differently.

    Inline reassigned the single replying MESSAGE; `_segment_channel_rows`
    union-merges the whole CONVERSATIONS. Same history, two answers, decided by
    whether it arrived live or through a rebuild.
    """

    def setUp(self):
        self._dir = tempfile.TemporaryDirectory()
        self.addCleanup(self._dir.cleanup)
        self.db_path = str(Path(self._dir.name) / "rag.db")

    def build(self, **kwargs):
        return MessageIndexService(self.db_path, embedding_model="test-embedding", **kwargs)

    def _reply_across_two_conversations(self, service):
        add_message(service, 100, minutes=0)
        add_message(service, 101, minutes=1)
        add_message(service, 200, minutes=45)
        add_message(service, 201, minutes=46)
        add_message(service, 202, minutes=47, reply_to=100)

    def test_a_live_reply_merges_the_conversation_the_rebuild_would_merge(self):
        service = self.build(conversation_gap_minutes=10.0, conversation_reply_merge_max_hours=6.0)
        self._reply_across_two_conversations(service)

        live = dict(conversation_ids(service))
        service.recompute_channel_conversations(20)

        self.assertEqual(set(live.values()), {100})
        self.assertEqual(live, dict(conversation_ids(service)))

    def test_the_live_size_cap_counts_both_conversations_as_the_rebuild_does(self):
        # Checking the target alone let a merge take the pair past the cap, and
        # the rebuild then refused the same merge.
        service = self.build(
            conversation_gap_minutes=10.0,
            conversation_reply_merge_max_hours=6.0,
            conversation_max_messages=4,
        )
        self._reply_across_two_conversations(service)

        live = dict(conversation_ids(service))
        service.recompute_channel_conversations(20)

        self.assertEqual(live[202], 200)
        self.assertEqual(live, dict(conversation_ids(service)))


class ConversationWindowAsyncTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self._dir = tempfile.TemporaryDirectory()
        self.addCleanup(self._dir.cleanup)
        self.service = MessageIndexService(
            str(Path(self._dir.name) / "rag.db"), embedding_model="test-embedding"
        )
        for index in range(4):
            add_message(self.service, 100 + index, minutes=index)
        self.service.recompute_channel_conversations(20)

    async def test_the_async_wrapper_returns_the_same_window(self):
        window = await self.service.get_conversation_window_async(
            100, center_message_id=101, full_max_messages=12, window_messages=2
        )

        self.assertEqual([message.message_id for message in window], [100, 101, 102, 103])


class RebuildConversationsScriptTest(unittest.TestCase):
    """scripts/rebuild_conversations.py, run in-process against a real database."""

    def setUp(self):
        self._dir = tempfile.TemporaryDirectory()
        self.addCleanup(self._dir.cleanup)
        self.db_path = str(Path(self._dir.name) / "rag.db")
        self.db = SimpleNamespace(db_path=self.db_path)
        self.config_path = Path(self._dir.name) / "config.yaml"
        self.config_path.write_text(
            "rag:\n"
            "  embedding_model: test-embedding\n"
            "  conversation_gap_minutes: 10.0\n"
            "  conversation_turnover_window: 3\n"
            "  conversation_turnover_min_gap_minutes: 3.0\n",
            encoding="utf-8",
        )
        service = MessageIndexService(self.db_path, embedding_model="test-embedding")
        for index in range(3):
            add_message(service, 100 + index, author_id=1, minutes=index)
        for index in range(3):
            add_message(service, 200 + index, author_id=2, minutes=5 + index * 0.5)
        add_message(service, 300, channel_id=21, minutes=0)

        spec = importlib.util.spec_from_file_location("rebuild_conversations", SCRIPT_PATH)
        self.script = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(self.script)

    def run_script(self, *args):
        argv = ["rebuild_conversations.py", "--db", self.db_path, "--config", str(self.config_path), *args]
        output = io.StringIO()
        with patch.object(sys, "argv", argv), contextlib.redirect_stdout(output):
            code = self.script.main()
        return code, output.getvalue()

    def test_a_dry_run_reports_the_split_without_writing_it(self):
        code, output = self.run_script("--dry-run")

        self.assertEqual(code, 0)
        self.assertIn("nothing written", output)
        self.assertEqual({conversation for _mid, conversation in conversation_ids(self.db)}, {100})

    def test_a_real_run_applies_the_turnover_split_the_live_path_missed(self):
        code, output = self.run_script()

        self.assertEqual(code, 0)
        self.assertIn("channel 20", output)
        ids = dict(conversation_ids(self.db))
        self.assertEqual(ids[102], 100)
        self.assertEqual(ids[200], 200)

    def test_one_channel_can_be_rebuilt_alone(self):
        self.run_script("--channel", "21")

        self.assertEqual({conversation for _mid, conversation in conversation_ids(self.db)}, {100})
        self.assertEqual(conversation_ids(self.db, 21), [(300, 300)])


class ConversationSchemaTest(unittest.TestCase):
    def setUp(self):
        self._dir = tempfile.TemporaryDirectory()
        self.addCleanup(self._dir.cleanup)
        self.db_path = str(Path(self._dir.name) / "rag.db")

    def test_an_existing_database_gains_the_column_and_the_index(self):
        # Editing the CREATE TABLE body alone leaves installed databases without
        # the column, and the index on it then fails to create.
        with sqlite3.connect(self.db_path) as conn:
            conn.execute(
                """
                CREATE TABLE message_index (
                    message_id INTEGER PRIMARY KEY,
                    guild_id INTEGER,
                    channel_id INTEGER NOT NULL,
                    author_id INTEGER,
                    author_name TEXT NOT NULL,
                    is_bot INTEGER NOT NULL DEFAULT 0,
                    reply_to_message_id INTEGER,
                    created_at TEXT NOT NULL,
                    indexed_at TEXT NOT NULL,
                    content_text TEXT NOT NULL,
                    attachment_summary TEXT NOT NULL DEFAULT '',
                    embedding_eligibility_text TEXT,
                    content_hash TEXT NOT NULL,
                    hidden INTEGER NOT NULL DEFAULT 0,
                    deleted_at TEXT
                )
                """
            )
            conn.commit()

        service = MessageIndexService(self.db_path, embedding_model="test-embedding")

        with sqlite3.connect(self.db_path) as conn:
            columns = {row[1] for row in conn.execute("PRAGMA table_info(message_index)")}
            indexes = {
                row[0]: row[1]
                for row in conn.execute(
                    "SELECT name, sql FROM sqlite_master WHERE type='index'"
                )
            }
        self.assertIn("conversation_id", columns)
        self.assertIn("idx_message_index_conversation", indexes)
        self.assertIn(
            "WHERE hidden = 0 AND deleted_at IS NULL",
            indexes["idx_message_index_conversation"],
        )
        add_message(service, 100, minutes=0)
        self.assertEqual(conversation_ids(service), [(100, 100)])

    def test_the_window_query_is_index_driven(self):
        service = MessageIndexService(self.db_path, embedding_model="test-embedding")
        for index in range(30):
            add_message(service, 100 + index, minutes=index * 0.5)
        service.recompute_channel_conversations(20)

        # Explain the SQL the service really ran, not a copy of it kept here.
        statements = []
        original = service._connection

        @contextlib.contextmanager
        def traced(*args, **kwargs):
            with original(*args, **kwargs) as conn:
                conn.set_trace_callback(statements.append)
                try:
                    yield conn
                finally:
                    conn.set_trace_callback(None)

        service._connection = traced
        try:
            service.get_conversation_window(
                100, center_message_id=110, full_max_messages=12, window_messages=5
            )
        finally:
            service._connection = original

        matching = [sql for sql in statements if "FROM message_index m" in sql]
        self.assertTrue(matching, statements)
        with sqlite3.connect(self.db_path) as conn:
            plan = " | ".join(
                row[3] for row in conn.execute("EXPLAIN QUERY PLAN " + matching[0])
            )
        self.assertIn("idx_message_index_conversation", plan)
        self.assertNotIn("USE TEMP B-TREE FOR ORDER BY", plan)


if __name__ == "__main__":
    unittest.main()
