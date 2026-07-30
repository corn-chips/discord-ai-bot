"""Shared SQLite connection and transaction lifecycle helpers."""

from contextlib import contextmanager
from os import PathLike
import sqlite3
from typing import Any, Callable, Iterator, Optional, Union


DatabasePath = Union[str, PathLike[str]]
RowFactory = Callable[[sqlite3.Cursor, tuple], Any]

# Python's sqlite3.connect defaults to 5 s. That value was already in force
# everywhere; what it was not was *stated*, so no caller could reason about it
# and nothing could tune it (DAB-095). Setting it explicitly makes it a
# contract. `configure_busy_timeout` lets startup apply the configured value
# once, because the helper is called from services that hold no config object.
_busy_timeout_seconds: float = 5.0


def configure_busy_timeout(milliseconds: int) -> None:
    """Set the lock-wait budget every later connection will use."""
    global _busy_timeout_seconds
    if milliseconds > 0:
        _busy_timeout_seconds = milliseconds / 1000.0


def get_busy_timeout_ms() -> int:
    """Report the lock-wait budget in milliseconds, for diagnostics and tests."""
    return int(round(_busy_timeout_seconds * 1000))


@contextmanager
def sqlite_connection(
    db_path: DatabasePath,
    *,
    row_factory: Optional[RowFactory] = None,
) -> Iterator[sqlite3.Connection]:
    """Open a connection that rolls back on failure and always closes.

    Deliberately no `journal_mode=WAL`, against DAB-095's recommendation.

    WAL is the right answer once connections are pooled and the wrong one now.
    Measured on this per-call-connection architecture, connect+query+close goes
    from 0.230 ms to 0.573 ms with the pragmas set, and the cost is WAL's
    *connect* rather than the pragma statement -- a database already in WAL mode
    costs the same with the pragmas removed. That is paid on every SQLite call
    the bot makes, including `get_live_enabled` on every inbound message.
    Against it, WAL made **no difference at all** to the failure DAB-095 is
    cited as fixing: driving the real `upsert_message` under a six-second lock
    returned False at ~5008 ms in both journal modes, and succeeded at ~6036 ms
    in both once the timeout was raised. The busy timeout is the entire effect.

    Reopen when connections are pooled or long-lived: that is when WAL's
    reader-concurrency win arrives without a per-call connect to pay for it, and
    it is a real win -- a reader contending with an EXCLUSIVE lock measured
    5005.9 ms failure against 0.1 ms success.
    """
    # `timeout=` IS the busy timeout: CPython's sqlite3 turns it into
    # `PRAGMA busy_timeout` on the connection, so it reads back off it and a
    # test can assert the contract rather than the argument. An additional
    # explicit pragma was written here first and removed -- it changed nothing
    # observable, and its mutant survived, which is the correct verdict on a
    # line that only restates the one above it.
    connection = sqlite3.connect(db_path, timeout=_busy_timeout_seconds)
    if row_factory is not None:
        connection.row_factory = row_factory
    try:
        yield connection
    except BaseException:
        try:
            connection.rollback()
        except sqlite3.Error:
            pass
        raise
    finally:
        connection.close()


@contextmanager
def sqlite_transaction(
    db_path: DatabasePath,
    *,
    row_factory: Optional[RowFactory] = None,
) -> Iterator[sqlite3.Connection]:
    """Commit a successful unit of work or roll it back before closing."""
    with sqlite_connection(db_path, row_factory=row_factory) as connection:
        yield connection
        connection.commit()
