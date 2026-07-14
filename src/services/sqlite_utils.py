"""Shared SQLite connection and transaction lifecycle helpers."""

from contextlib import contextmanager
from os import PathLike
import sqlite3
from typing import Any, Callable, Iterator, Optional, Union


DatabasePath = Union[str, PathLike[str]]
RowFactory = Callable[[sqlite3.Cursor, tuple], Any]


@contextmanager
def sqlite_connection(
    db_path: DatabasePath,
    *,
    row_factory: Optional[RowFactory] = None,
) -> Iterator[sqlite3.Connection]:
    """Open a connection that rolls back on failure and always closes."""
    connection = sqlite3.connect(db_path)
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
