"""DAB-078: the retrieval queries could not use an index, and full-scanned.

The only scope index on `message_index` was
`(guild_id, channel_id, created_at DESC)`. The shipped default path is
channel-scoped (`config.yaml`, `cross_channel_enabled: false`), so the leading
column was never bound: SQLite skip-scanned every distinct `guild_id` and then
built a temp B-tree to satisfy `ORDER BY created_at DESC`, for a query that
wants twelve rows. Latency was exactly linear in corpus size -- 24.9 ms at
100,000 rows on the channel path, 61.4 ms on the guild path.

These assert the **query plan**, not a duration. A timing assertion here would
be the same mistake as `tests/test_rag_optimization.py:94`, which `AGENTS.md`
already flags as flaky: it would pass on a loaded machine for the wrong reason
and fail on a slow one for no reason. The plan is the thing that is either right
or wrong, and `USE TEMP B-TREE FOR ORDER BY` is the exact string that says the
index is not supplying the order.
"""

import sqlite3
import tempfile
import unittest
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path

from src.services.message_index_service import MessageIndexService


class QueryPlanTest(unittest.TestCase):
    def setUp(self):
        self._dir = tempfile.TemporaryDirectory()
        self.addCleanup(self._dir.cleanup)
        self.db_path = str(Path(self._dir.name) / "rag.db")
        self.service = MessageIndexService(self.db_path, embedding_model="test-embedding")
        now = datetime.now(timezone.utc)
        for offset in range(25):
            self.service.upsert_message(
                message_id=900_000 + offset,
                guild_id=10,
                channel_id=20 + (offset % 3),
                author_id=5,
                author_name="Ada",
                is_bot=False,
                reply_to_message_id=None,
                created_at=now - timedelta(seconds=offset),
                content_text=f"a message about topic {offset} worth indexing",
            )

    def capture_sql(self, call, *, containing):
        """Run a real service call and return the SQL it actually executed.

        Copying the query text into this file and explaining *that* would pin a
        string the test owns rather than the one the service runs, so a mutation
        of the service's `ORDER BY` would leave every assertion here green. It
        did: `M-DAB212` survived until this replaced the copies. The trace
        callback is the difference between asserting the plan and asserting a
        paraphrase of it.
        """
        statements = []
        original = self.service._connection

        @contextmanager
        def traced(*args, **kwargs):
            with original(*args, **kwargs) as conn:
                conn.set_trace_callback(statements.append)
                try:
                    yield conn
                finally:
                    conn.set_trace_callback(None)

        self.service._connection = traced
        try:
            call()
        finally:
            self.service._connection = original

        matching = [sql for sql in statements if containing in sql]
        self.assertTrue(
            matching,
            f"no executed statement contained {containing!r}: {statements}",
        )
        return matching[0]

    def assert_index_supplies_the_order(self, sql, expected_index):
        # The trace callback hands back the expanded statement, parameters
        # already substituted, so this explains the literal SQL that ran.
        with sqlite3.connect(self.db_path) as conn:
            plan = [row[3] for row in conn.execute("EXPLAIN QUERY PLAN " + sql)]
        joined = " | ".join(plan)
        self.assertNotIn(
            "USE TEMP B-TREE FOR ORDER BY",
            joined,
            f"the index is not supplying the order: {joined}",
        )
        self.assertIn(expected_index, joined, joined)

    def test_channel_scoped_retrieval_uses_the_channel_index(self):
        sql = self.capture_sql(
            lambda: self.service.search_recent(
                guild_id=10, channel_id=20, cross_channel=False, limit=12
            ),
            containing="FROM message_index m",
        )
        self.assert_index_supplies_the_order(
            sql, "idx_message_index_channel_time"
        )

    def test_guild_scoped_retrieval_uses_the_guild_index(self):
        sql = self.capture_sql(
            lambda: self.service.search_recent(
                guild_id=10, channel_id=20, cross_channel=True, limit=12
            ),
            containing="FROM message_index m",
        )
        self.assert_index_supplies_the_order(
            sql, "idx_message_index_guild_time"
        )

    def test_the_pending_embedding_poll_uses_the_channel_index_too(self):
        # channel_id is always bound in production: _drain_pending_documents
        # takes it as a required keyword-only argument, so the unscoped form of
        # this query is dead code.
        #
        # This is the bonus DAB-078 pays and DAB-212 would have taken away. With
        # this index the poll drives from message_index in created_at order and
        # needs no temp B-tree; rewriting the ORDER BY to e.message_id --
        # DAB-212's prescription -- puts the B-tree back and costs 111x on this
        # exact query. See docs/ANALYSIS_CORRECTIONS.md item 13.
        sql = self.capture_sql(
            lambda: self.service.get_pending_embeddings(limit=16, channel_id=20),
            containing="FROM message_embeddings e",
        )
        self.assert_index_supplies_the_order(
            sql, "idx_message_index_channel_time"
        )

    def test_the_superseded_composite_index_is_gone(self):
        # Kept alongside the two partials it cannot beat, it cost +35% on every
        # write and +8 MB per 100k rows for no query.
        with sqlite3.connect(self.db_path) as conn:
            names = {
                row[0]
                for row in conn.execute(
                    "SELECT name FROM sqlite_master WHERE type='index'"
                )
            }
        self.assertNotIn("idx_message_index_scope_time", names)
        self.assertIn("idx_message_index_channel_time", names)
        self.assertIn("idx_message_index_guild_time", names)

    def test_the_scope_indexes_exclude_hidden_and_deleted_rows(self):
        # The partial predicate looks like decoration and is not. On a corpus
        # with no tombstones it changes nothing measurable -- same plan, same
        # size, and it costs a few percent on writes. On one that has them it is
        # the whole win: at 100k rows with 90% hidden or deleted, measured
        # 0.024 ms against a 416 KB index with the clause, and 14.965 ms against
        # a 4,052 KB index without. 623x.
        #
        # Pinned on the schema rather than on a plan because the plan is
        # identical either way -- the difference is how many dead entries the
        # index carries -- which makes the clause exactly the kind of thing a
        # later tidy-up deletes as redundant.
        #
        # It can never hide a row a caller wants: every query using these
        # indexes filters on exactly `hidden = 0 AND deleted_at IS NULL`.
        with sqlite3.connect(self.db_path) as conn:
            definitions = {
                row[0]: row[1]
                for row in conn.execute(
                    "SELECT name, sql FROM sqlite_master WHERE type='index' "
                    "AND name LIKE 'idx_message_index_%_time'"
                )
            }
        self.assertEqual(len(definitions), 2, definitions)
        for name, sql in definitions.items():
            with self.subTest(index=name):
                self.assertIn("WHERE hidden = 0 AND deleted_at IS NULL", sql)

    def test_an_existing_database_is_migrated_to_the_new_indexes(self):
        # _ensure_schema runs on every construction, so an installed database
        # created before this change must lose the composite and gain the
        # partials when the service is next opened.
        with sqlite3.connect(self.db_path) as conn:
            conn.execute("DROP INDEX IF EXISTS idx_message_index_channel_time")
            conn.execute("DROP INDEX IF EXISTS idx_message_index_guild_time")
            conn.execute(
                "CREATE INDEX idx_message_index_scope_time "
                "ON message_index (guild_id, channel_id, created_at DESC)"
            )
            conn.commit()

        self.service = MessageIndexService(self.db_path, embedding_model="test-embedding")

        sql = self.capture_sql(
            lambda: self.service.search_recent(
                guild_id=10, channel_id=20, cross_channel=False, limit=12
            ),
            containing="FROM message_index m",
        )
        self.assert_index_supplies_the_order(
            sql, "idx_message_index_channel_time"
        )

    def test_retrieval_still_returns_the_newest_messages_first(self):
        # The plan assertions above say the index supplies the order; this says
        # the order is still the right one.
        #
        # The fixture deliberately runs `created_at` DOWN as `message_id` runs
        # UP, so recency order and id order disagree. That is what makes this
        # test able to fail, and it is also the concrete reason DAB-212's
        # "ORDER BY e.message_id DESC is semantically identical" is not true in
        # general -- here it would return the batch exactly backwards.
        recent = self.service.search_recent(
            guild_id=10, channel_id=20, cross_channel=False, limit=5
        )
        self.assertTrue(recent)
        timestamps = [message.created_at for message in recent]
        self.assertEqual(timestamps, sorted(timestamps, reverse=True))
        ids = [message.message_id for message in recent]
        self.assertNotEqual(
            ids,
            sorted(ids, reverse=True),
            "fixture no longer separates recency order from id order",
        )


if __name__ == "__main__":
    unittest.main()


class MigrationLedgerTest(unittest.TestCase):
    """DAB-083: `rag_migrations` must be owned by the schema, not by a migration.

    The ledger's `CREATE TABLE IF NOT EXISTS` lived inside
    `_migrate_legacy_database`, so whether it existed on a fresh install was an
    accident of construction order -- absent for `MessageIndexService` alone,
    present when `TokenTracker` ran first as it does in `DiscordBot.__init__`.
    Any ledger read added anywhere earlier raises `no such table` from a
    synchronous constructor, which aborts `DiscordBot.__init__` (DAB-067).
    """

    def setUp(self):
        self._dir = tempfile.TemporaryDirectory()
        self.addCleanup(self._dir.cleanup)
        self.db_path = str(Path(self._dir.name) / "rag.db")

    def test_the_ledger_exists_on_a_fresh_database_with_no_migration(self):
        MessageIndexService(self.db_path, embedding_model="test-embedding")

        with sqlite3.connect(self.db_path) as conn:
            # The observable end state is that a ledger read WORKS, not that a
            # row appears in sqlite_master: the failure this prevents is an
            # OperationalError out of a constructor.
            rows = conn.execute("SELECT name FROM rag_migrations").fetchall()
        self.assertEqual(rows, [])

    def test_a_fresh_install_does_not_record_a_migration_it_never_ran(self):
        # token_usage.db always exists -- TokenTracker creates it -- and has
        # never held RAG tables. Recording the migration against it burned the
        # one shot and logged a successful copy of nothing.
        legacy_path = Path(self._dir.name) / "token_usage.db"
        with sqlite3.connect(legacy_path) as legacy:
            legacy.execute("CREATE TABLE token_usage (id INTEGER PRIMARY KEY)")
            legacy.commit()

        MessageIndexService(
            self.db_path,
            embedding_model="test-embedding",
            legacy_db_path=str(legacy_path),
        )

        with sqlite3.connect(self.db_path) as conn:
            recorded = conn.execute("SELECT name FROM rag_migrations").fetchall()
        self.assertEqual(
            recorded, [], "a migration that copied nothing was recorded as done"
        )

    def test_a_real_legacy_database_still_migrates_and_is_recorded(self):
        legacy_path = Path(self._dir.name) / "legacy.db"
        legacy_service = MessageIndexService(
            str(legacy_path), embedding_model="test-embedding"
        )
        legacy_service.upsert_message(
            message_id=555,
            guild_id=10,
            channel_id=20,
            author_id=5,
            author_name="Ada",
            is_bot=False,
            reply_to_message_id=None,
            created_at=datetime.now(timezone.utc),
            content_text="a legacy message worth carrying across",
        )

        migrated = MessageIndexService(
            self.db_path,
            embedding_model="test-embedding",
            legacy_db_path=str(legacy_path),
        )

        self.assertEqual(migrated.get_status()["messages"], 1)
        with sqlite3.connect(self.db_path) as conn:
            recorded = [
                row[0] for row in conn.execute("SELECT name FROM rag_migrations")
            ]
        self.assertEqual(recorded, ["legacy_shared_rag_v1"])
