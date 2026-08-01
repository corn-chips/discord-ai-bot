"""Guards for DAB-077: the eligibility reconcile scanned the whole index every boot.

`_reconcile_embedding_eligibility` joins `message_index` to `message_embeddings`
and walks every row, and `_ensure_schema` called it unconditionally. Measured on
this machine at ~132-character message bodies, median of 7: 6.9 ms at 1,000
rows, 64.7 ms at 10,000, 741.8 ms at 100,000 -- 50-65% of
`MessageIndexService.__init__`. It has real work to do only when something that
decides its answer has changed.

**These tests never assert that the reconcile "was not called".** They assert
what a caller can see: a row is left in a state only a skipped reconcile leaves
it in. The setup is the same every time -- put a row into a state the reconcile
would correct, construct the service, and look at the row. Corrected means it
ran; untouched means it did not.

The asymmetry that shapes the fingerprint: a reconcile wrongly skipped is
permanent and silent. The row stays `skipped`, `get_pending_embeddings` filters
on `= 'pending'` so it never returns it, it never becomes `done`, and it is
invisible to `search_semantic` and to the vector cache forever -- an identical
re-upsert does not repair it, only a real edit does. A reconcile wrongly run
costs one boot's scan. So the rule inputs are hashed from their own source
rather than tracked by a version constant somebody can forget to bump, and
`test_editing_the_eligibility_rule_re_runs_the_reconcile` is the test that keeps
that true.
"""

import sqlite3
import tempfile
import unittest
from pathlib import Path

from src.services.message_index_service import MessageIndexService

LONG_ENOUGH = "the quick brown fox jumps over the lazy dog repeatedly and at length"


class EligibilityReconcileGateTest(unittest.TestCase):
    def setUp(self):
        self._dir = tempfile.TemporaryDirectory()
        self.addCleanup(self._dir.cleanup)
        self.rag = str(Path(self._dir.name) / "rag.db")
        self.service = MessageIndexService(db_path=self.rag)
        self.model = self.service.embedding_model

    # ── helpers ──────────────────────────────────────────────────────

    def _seed(self, message_id=1, text=LONG_ENOUGH):
        with sqlite3.connect(self.rag) as conn:
            conn.execute(
                "INSERT INTO message_index (message_id, channel_id, guild_id,"
                " author_id, author_name, created_at, indexed_at, content_text,"
                " content_hash) VALUES (?, 1, 2, 3, 'ada', '2026-01-01T00:00:00',"
                " '2026-01-01T00:00:00', ?, 'h')",
                (message_id, text),
            )
            conn.execute(
                "INSERT INTO message_embeddings (message_id, content_hash,"
                " embedding_model, embedding_status) VALUES (?, 'h', ?, 'pending')",
                (message_id, self.model),
            )

    def _force_wrong(self, message_id=1):
        """Put the row into the state a correct reconcile would fix."""
        with sqlite3.connect(self.rag) as conn:
            conn.execute(
                "UPDATE message_embeddings SET embedding_status = 'skipped'"
                " WHERE message_id = ?",
                (message_id,),
            )

    def _status(self, message_id=1):
        with sqlite3.connect(self.rag) as conn:
            return conn.execute(
                "SELECT embedding_status FROM message_embeddings WHERE message_id = ?",
                (message_id,),
            ).fetchone()[0]

    def _rebuild(self, **kwargs):
        return MessageIndexService(db_path=self.rag, **kwargs)

    # ── the gate ─────────────────────────────────────────────────────

    def test_a_database_from_before_the_gate_is_reconciled_exactly_once(self):
        # The upgrade path. An existing database has no fingerprint row, so the
        # first construction after this change must still do the full scan --
        # and the second must not.
        self._seed()
        self._force_wrong()
        with sqlite3.connect(self.rag) as conn:
            conn.execute("DELETE FROM rag_state")

        self._rebuild()
        self.assertEqual(self._status(), "pending")

        self._force_wrong()
        self._rebuild()
        self.assertEqual(self._status(), "skipped")

    def test_an_unchanged_second_construction_does_not_rescan(self):
        self._seed()
        self._rebuild()
        self._force_wrong()

        self._rebuild()

        self.assertEqual(
            self._status(), "skipped",
            "the reconcile rescanned the whole index with nothing to do",
        )

    def test_changing_a_threshold_re_runs_the_reconcile(self):
        self._seed()
        self._rebuild()
        self._force_wrong()

        # A long message is trivial under a high enough word floor, so this both
        # bumps the fingerprint and changes the answer.
        self._rebuild(embedding_min_words=500, embedding_min_alphanumeric_chars=5000)

        self.assertEqual(self._status(), "skipped")
        self._force_wrong()
        self._rebuild()
        self.assertEqual(self._status(), "pending")

    def test_changing_the_embedding_model_re_runs_the_reconcile(self):
        # A TRIVIAL row, deliberately. A model change resets every row to
        # `pending` elsewhere in `_ensure_schema`, so a substantial message
        # reaches `pending` whether or not the reconcile ran -- the first draft
        # of this test used one and passed with the model left out of the
        # fingerprint entirely. Only the reconcile puts a trivial row back to
        # `skipped`, so only a trivial row can tell the two apart. Leave the
        # model out and one config edit bills the whole history, every trivial
        # message included.
        self._seed(text="ok")
        with sqlite3.connect(self.rag) as conn:
            conn.execute("DELETE FROM rag_state")
        self._rebuild()
        self.assertEqual(self._status(), "skipped")

        self._rebuild(embedding_model="some-other-model", embedding_dimensions=768)

        self.assertEqual(
            self._status(), "skipped",
            "a model change left a trivial message queued for a paid embedding",
        )

    def test_editing_the_eligibility_rule_re_runs_the_reconcile(self):
        # The input a hand-maintained version constant is forgotten for. The
        # fingerprint hashes the rule's own source, so a subclass that redefines
        # it is a different rule and gets a rescan.
        self._seed()
        self._rebuild()
        self._force_wrong()

        class WidenedRule(MessageIndexService):
            def _embedding_is_trivial(self, content_text, attachment_summary=""):
                # A different rule, spelled differently on purpose.
                return False

        WidenedRule(db_path=self.rag)

        self.assertEqual(self._status(), "pending")

    def test_the_fingerprint_table_holds_exactly_one_row(self):
        self._rebuild()
        self._rebuild(embedding_min_words=99)

        with sqlite3.connect(self.rag) as conn:
            rows = conn.execute("SELECT id FROM rag_state").fetchall()
            with self.assertRaises(sqlite3.IntegrityError):
                conn.execute("INSERT INTO rag_state(id) VALUES (2)")
        self.assertEqual([r[0] for r in rows], [1])

    def test_a_legacy_migration_still_reconciles_what_it_imported(self):
        # The gate is on `_ensure_schema` only. `_migrate_legacy_database` runs
        # the reconcile again, ungated, because rows it has just imported have
        # never been through it -- and the fingerprint already matches by then,
        # so gating that call site too would leave them permanently unembeddable.
        legacy = str(Path(self._dir.name) / "token_usage.db")
        conn = sqlite3.connect(legacy)
        conn.execute(
            "CREATE TABLE message_index (message_id INTEGER PRIMARY KEY,"
            " channel_id INTEGER NOT NULL, guild_id INTEGER, author_id INTEGER,"
            " author_name TEXT NOT NULL, created_at TEXT NOT NULL,"
            " indexed_at TEXT NOT NULL, content_text TEXT NOT NULL,"
            " content_hash TEXT NOT NULL)"
        )
        conn.execute(
            "CREATE TABLE message_embeddings (message_id INTEGER PRIMARY KEY,"
            " content_hash TEXT NOT NULL, embedding_model TEXT NOT NULL,"
            " embedding_status TEXT NOT NULL)"
        )
        conn.execute(
            "INSERT INTO message_index VALUES (55, 1, 2, 3, 'ada',"
            " '2026-01-01T00:00:00', '2026-01-01T00:00:00', ?, 'h')",
            (LONG_ENOUGH,),
        )
        # Imported already wrong: a substantial message marked skipped.
        conn.execute(
            "INSERT INTO message_embeddings VALUES (55, 'h', ?, 'skipped')",
            (self.model,),
        )
        conn.commit()
        conn.close()

        self._rebuild()  # writes the fingerprint with an empty index
        MessageIndexService(db_path=self.rag, legacy_db_path=legacy)

        self.assertEqual(
            self._status(55), "pending",
            "rows imported by the legacy migration were never reconciled",
        )


if __name__ == "__main__":
    unittest.main()
