"""DAB-095: the one connection seam stated no contract about lock waiting.

`sqlite_connection` is, per `AGENTS.md`, the only `sqlite3.connect` site under
`src/`. It took no pragma configuration and exposed no hook for one. There *was*
a busy timeout -- 5000 ms, Python's `sqlite3.connect` default -- but it was
implicit and undocumented, so no caller could reason about it and nothing could
tune it. That, rather than the absence of WAL, is the defect these pin.

**WAL is deliberately not set**, against the ticket's own recommendation, and
the reasoning is recorded in `sqlite_connection`'s docstring and in
`docs/ANALYSIS_CORRECTIONS.md` item 14. In one line: on a per-call-connection
architecture WAL costs +0.34 ms on every call and made no measurable difference
to the failure it was cited as fixing.
"""

import sqlite3
import tempfile
import threading
import time
import unittest
from pathlib import Path

from src.services.sqlite_utils import (
    configure_busy_timeout,
    get_busy_timeout_ms,
    sqlite_connection,
    sqlite_transaction,
)


class BusyTimeoutTest(unittest.TestCase):
    def setUp(self):
        self._dir = tempfile.TemporaryDirectory()
        self.addCleanup(self._dir.cleanup)
        self.db_path = str(Path(self._dir.name) / "t.db")
        original = get_busy_timeout_ms()
        self.addCleanup(lambda: configure_busy_timeout(original))
        with sqlite_transaction(self.db_path) as conn:
            conn.execute("CREATE TABLE t (id INTEGER PRIMARY KEY, v TEXT)")
            conn.execute("INSERT INTO t VALUES (1, 'seed')")

    def test_the_timeout_is_readable_off_the_connection(self):
        # Asserting the pragma, not the `timeout=` argument: the argument is
        # what was already in force implicitly, and an argument cannot be read
        # back, so a test of it would pin the call rather than the contract.
        configure_busy_timeout(1234)
        with sqlite_connection(self.db_path) as conn:
            self.assertEqual(conn.execute("PRAGMA busy_timeout").fetchone()[0], 1234)

    def test_the_default_is_the_value_that_was_already_in_force(self):
        self.assertEqual(get_busy_timeout_ms(), 5000)

    def test_a_zero_or_negative_configuration_is_refused(self):
        configure_busy_timeout(7000)
        configure_busy_timeout(0)
        configure_busy_timeout(-1)
        self.assertEqual(get_busy_timeout_ms(), 7000)

    def test_a_writer_waits_for_the_configured_budget_and_then_succeeds(self):
        # The observable end state: a write that would have failed under a
        # shorter budget completes. Held for 0.4 s against a 4 s budget, so the
        # assertion is a lower bound on waiting and never a race.
        configure_busy_timeout(4000)
        released = threading.Event()

        def holder():
            conn = sqlite3.connect(self.db_path, timeout=10)
            conn.execute("BEGIN EXCLUSIVE")
            conn.execute("UPDATE t SET v = 'held' WHERE id = 1")
            released.wait(timeout=5)
            time.sleep(0.05)
            conn.commit()
            conn.close()

        thread = threading.Thread(target=holder, daemon=True)
        thread.start()
        time.sleep(0.1)
        released.set()

        with sqlite_transaction(self.db_path) as conn:
            conn.execute("UPDATE t SET v = 'second' WHERE id = 1")
        thread.join(timeout=5)

        with sqlite_connection(self.db_path) as conn:
            self.assertEqual(
                conn.execute("SELECT v FROM t WHERE id = 1").fetchone()[0], "second"
            )

    def test_a_short_budget_still_fails_fast_rather_than_hanging(self):
        # The other half of "tunable": lowering it has to take effect too, or
        # the knob is decorative.
        configure_busy_timeout(150)
        holding = threading.Event()
        finish = threading.Event()

        def holder():
            conn = sqlite3.connect(self.db_path, timeout=10)
            conn.execute("BEGIN EXCLUSIVE")
            conn.execute("UPDATE t SET v = 'held' WHERE id = 1")
            holding.set()
            finish.wait(timeout=5)
            conn.rollback()
            conn.close()

        thread = threading.Thread(target=holder, daemon=True)
        thread.start()
        self.assertTrue(holding.wait(timeout=5))

        started = time.monotonic()
        with self.assertRaises(sqlite3.OperationalError):
            with sqlite_transaction(self.db_path) as conn:
                conn.execute("UPDATE t SET v = 'second' WHERE id = 1")
        elapsed = time.monotonic() - started

        finish.set()
        thread.join(timeout=5)
        self.assertLess(elapsed, 3.0, "the configured budget was ignored")

    def test_the_journal_mode_is_still_the_default(self):
        # Pins the deferral, so re-adding WAL is a deliberate act with a test to
        # update rather than a quiet one-line change. See
        # docs/ANALYSIS_CORRECTIONS.md item 14 for the reopen condition.
        with sqlite_connection(self.db_path) as conn:
            self.assertEqual(
                conn.execute("PRAGMA journal_mode").fetchone()[0].lower(), "delete"
            )

    def test_the_pragma_does_not_open_a_transaction_or_break_rollback(self):
        # A pragma executed before the yield must not disturb the two
        # behaviours the seam already guarantees.
        try:
            with sqlite_transaction(self.db_path) as conn:
                conn.execute("INSERT INTO t VALUES (2, 'rolled back')")
                raise RuntimeError("boom")
        except RuntimeError:
            pass

        with sqlite_connection(self.db_path) as conn:
            self.assertEqual(
                conn.execute("SELECT COUNT(*) FROM t WHERE id = 2").fetchone()[0], 0
            )

    def test_the_connection_is_closed_on_the_way_out(self):
        with sqlite_connection(self.db_path) as conn:
            pass
        with self.assertRaises(sqlite3.ProgrammingError):
            conn.execute("SELECT 1")


if __name__ == "__main__":
    unittest.main()
