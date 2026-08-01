"""Guards for PPR-02: the shortfall retry could never succeed, and cost every boot.

`_migrate_legacy_database` recorded its success as `copied = after - before`
compared against the legacy row count. On the *second* boot after a shortfall
the rows are already in `main`, so `INSERT OR IGNORE` copies nothing: `copied`
is 0, `expected` is N, and a table holding 100% of its legacy rows reports
itself short. Forever. The ledger was never written, so every boot re-ran the
ATTACH, the copy, the eligibility reconcile and a full FTS teardown and rebuild.

The post-condition is now "no legacy row's key is absent from the target",
which is idempotent by construction.

The second half is the early return. `if not legacy_tables_found: return` sat
*after* three expensive statements, so on every boot of every normal install --
`TokenTracker` always creates `token_usage.db` and it has never held RAG tables
-- the bot paid for an embedding-model reset, a second full-table reconcile and
a complete FTS rebuild in order to discover there was nothing to migrate.

Only two of those three were redundant. The FTS rebuild was quietly repairing
an empty FTS table, so `_repair_empty_fts` now does that job in `_ensure_schema`
for the empty case only -- 0.33 ms flat at 100,000 rows against 2,547 ms for the
unconditional rebuild. `test_an_empty_fts_table_is_rebuilt_from_the_index` is
the guard that stops the early return silently taking that repair away with it.
"""

import sqlite3
import tempfile
import unittest
from pathlib import Path

from src.services.message_index_service import MessageIndexService

LEGACY_TABLES = {
    "message_index": """
        CREATE TABLE message_index (
            message_id INTEGER PRIMARY KEY, channel_id INTEGER NOT NULL,
            guild_id INTEGER, author_id INTEGER, author_name TEXT NOT NULL,
            created_at TEXT NOT NULL, indexed_at TEXT NOT NULL,
            content_text TEXT NOT NULL, content_hash TEXT NOT NULL,
            hidden INTEGER NOT NULL DEFAULT 0, deleted_at TEXT
        )
    """,
    "message_backfill_progress": """
        CREATE TABLE message_backfill_progress (
            channel_id INTEGER PRIMARY KEY, last_message_id INTEGER,
            updated_at TEXT NOT NULL, completed_at TEXT
        )
    """,
}


class LegacyRagMigrationTest(unittest.TestCase):
    def setUp(self):
        self._dir = tempfile.TemporaryDirectory()
        self.addCleanup(self._dir.cleanup)
        self.legacy = str(Path(self._dir.name) / "token_usage.db")
        self.rag = str(Path(self._dir.name) / "message_rag.db")
        sqlite3.connect(self.legacy).close()

    def _seed_legacy(self, rows=3):
        conn = sqlite3.connect(self.legacy)
        for ddl in LEGACY_TABLES.values():
            conn.execute(ddl)
        conn.executemany(
            "INSERT INTO message_index (message_id, channel_id, guild_id, author_id,"
            " author_name, created_at, indexed_at, content_text, content_hash)"
            " VALUES (?, 1, 2, 3, 'ada', ?, ?, ?, ?)",
            [(1000 + i, f"2026-01-0{i + 1}T00:00:00", f"2026-01-0{i + 1}T00:00:00",
              f"legacy message number {i} with plenty of words in it", f"hash{i}")
             for i in range(rows)],
        )
        conn.execute(
            "INSERT INTO message_backfill_progress (channel_id, last_message_id,"
            " updated_at) VALUES (1, 999, '2026-01-01T00:00:00')"
        )
        conn.commit()
        conn.close()

    def _service(self):
        return MessageIndexService(db_path=self.rag, legacy_db_path=self.legacy)

    def _ledger(self):
        with sqlite3.connect(self.rag) as conn:
            return [r[0] for r in conn.execute("SELECT name FROM rag_migrations")]

    def _indexed(self):
        with sqlite3.connect(self.rag) as conn:
            return conn.execute("SELECT COUNT(*) FROM message_index").fetchone()[0]

    # ── the post-condition ───────────────────────────────────────────

    def test_a_retry_over_rows_already_copied_records_success(self):
        # The exact state a shortfall leaves behind: every legacy row is already
        # in the target and the ledger is absent. `after - before` is 0 against
        # an expectation of 3, so this boot -- and every later one -- called a
        # complete table short.
        self._seed_legacy()
        self._service()
        with sqlite3.connect(self.rag) as conn:
            conn.execute("DELETE FROM rag_migrations")
        self.assertEqual(self._ledger(), [])

        self._service()

        self.assertEqual(self._ledger(), ["legacy_shared_rag_v1"])
        self.assertEqual(self._indexed(), 3)

    def test_a_genuinely_short_copy_is_still_refused(self):
        # The post-condition must not become vacuous. A legacy row the target
        # cannot accept has to keep the ledger unwritten, which is what makes
        # the migration retryable at all (DAB-066).
        # The DAB-066 mechanism exactly: the legacy schema lacks a column the
        # target declares NOT NULL with no default, so it is not in the copied
        # intersection, and `INSERT OR IGNORE` -- whose conflict resolution
        # extends to NOT NULL violations -- drops every row without an error.
        conn = sqlite3.connect(self.legacy)
        conn.execute(
            """
            CREATE TABLE message_index (
                message_id INTEGER PRIMARY KEY, channel_id INTEGER NOT NULL,
                guild_id INTEGER, author_id INTEGER, author_name TEXT NOT NULL,
                created_at TEXT NOT NULL, indexed_at TEXT NOT NULL,
                content_text TEXT NOT NULL
            )
            """
        )
        conn.execute(
            "INSERT INTO message_index VALUES (2000, 1, 2, 3, 'ada',"
            " '2026-02-01T00:00:00', '2026-02-01T00:00:00', 'no hash column here')"
        )
        conn.commit()
        conn.close()

        with self.assertLogs("src.services.message_index_service", "ERROR") as captured:
            service = self._service()

        self.assertIn("still absent from the target", captured.output[0])
        self.assertEqual(self._ledger(), [])
        self.assertEqual(self._indexed(), 0)
        # And the bot still boots: a migration fault must never be fatal.
        self.assertIsNotNone(service.get_status())

    def test_a_first_run_still_copies_and_records(self):
        self._seed_legacy()

        self._service()

        self.assertEqual(self._ledger(), ["legacy_shared_rag_v1"])
        self.assertEqual(self._indexed(), 3)

    def test_no_legacy_tables_leaves_the_migration_available(self):
        self._service()
        self.assertEqual(self._ledger(), [])

        self._seed_legacy()
        self._service()

        self.assertEqual(self._ledger(), ["legacy_shared_rag_v1"])
        self.assertEqual(self._indexed(), 3)

    # ── the early return, and what it must not take with it ──────────

    def test_an_empty_fts_table_is_rebuilt_from_the_index(self):
        # Moving the early return above the unconditional FTS rebuild removes a
        # repair that was happening by accident on every boot. Missing FTS rows
        # are permanent -- nothing else in the repository rebuilds them -- so
        # the repair has to move with it, not disappear.
        service = self._service()
        if not service.fts_enabled:
            self.skipTest("SQLite build has no FTS5")
        with sqlite3.connect(self.rag) as conn:
            conn.execute(
                "INSERT INTO message_index (message_id, channel_id, guild_id,"
                " author_id, author_name, content_text, content_hash, created_at,"
                " indexed_at) VALUES (77, 1, 2, 3, 'ada', 'searchable haystack text',"
                " 'h', '2026-01-01T00:00:00', '2026-01-01T00:00:00')"
            )
            conn.execute("DELETE FROM message_search_fts")

        rebuilt = MessageIndexService(db_path=self.rag)

        self.assertTrue(
            rebuilt.search_lexical(
                "haystack", channel_id=1, guild_id=2, cross_channel=False, limit=5
            ),
            "an empty FTS table was left empty, and nothing else rebuilds it",
        )

    def test_a_populated_fts_table_is_not_rebuilt(self):
        # The repair is affordable only because it does nothing in the case that
        # is always true. An unconditional rebuild is 2,547 ms at 100k rows.
        service = self._service()
        if not service.fts_enabled:
            self.skipTest("SQLite build has no FTS5")
        with sqlite3.connect(self.rag) as conn:
            conn.execute(
                "INSERT INTO message_index (message_id, channel_id, guild_id,"
                " author_id, author_name, content_text, content_hash, created_at,"
                " indexed_at) VALUES (88, 1, 2, 3, 'ada', 'indexed text', 'h',"
                " '2026-01-01T00:00:00', '2026-01-01T00:00:00')"
            )
            # A row the index does not have: proof the table was left alone
            # rather than rebuilt from message_index.
            conn.execute(
                "INSERT INTO message_search_fts(rowid, content_text, author_name,"
                " attachment_summary) VALUES (99, 'sentinel row', 'ada', '')"
            )

        MessageIndexService(db_path=self.rag)

        with sqlite3.connect(self.rag) as conn:
            rows = conn.execute(
                "SELECT rowid FROM message_search_fts ORDER BY rowid"
            ).fetchall()
        self.assertEqual([r[0] for r in rows], [99])


if __name__ == "__main__":
    unittest.main()
