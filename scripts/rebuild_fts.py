#!/usr/bin/env python3
"""Re-derive message_search_fts from the text already stored in message_index.

Run this after anything that changes how indexed text is written -- mention
resolution, for one -- or when the FTS table has drifted from the index.

    python scripts/rebuild_fts.py                        # every channel
    python scripts/rebuild_fts.py --channel 123          # one channel (repeatable)
    python scripts/rebuild_fts.py --dry-run              # report, write nothing
    python scripts/rebuild_fts.py --resume-from 987      # continue an interrupted run
    python scripts/rebuild_fts.py --db data/message_rag.db

LIMITATION: this only re-indexes text that message_index already holds. Rows
written before mention resolution landed still store the raw `<@123>` markup,
and no amount of rebuilding turns that into a display name -- the id-to-name
mapping is not in the database. Those rows are corrected only by re-indexing
them from Discord.

Messages are processed in ascending message_id windows, one transaction each,
so an interrupted run leaves a consistent prefix and prints the id to pass to
`--resume-from`. Hidden and deleted messages are dropped from the FTS table and
not re-inserted. Pure SQLite: no Discord, no network, no API key, and no
MessageIndexService, so nothing here can trigger an embedding pass.
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

import yaml

ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from src.services.sqlite_utils import sqlite_connection, sqlite_transaction  # noqa: E402

INSERT_COLUMNS = "rowid, content_text, author_name, attachment_summary"
SELECT_COLUMNS = "message_id, content_text, author_name, attachment_summary"


def load_rag_settings(config_path: Path) -> dict:
    if not config_path.exists():
        print(f"no config at {config_path}; using built-in defaults")
        return {}
    data = yaml.safe_load(config_path.read_text(encoding="utf-8")) or {}
    return dict(data.get("rag") or {})


def fts_available(db_path: str) -> bool:
    with sqlite_connection(db_path) as conn:
        return bool(
            conn.execute(
                "SELECT EXISTS(SELECT 1 FROM sqlite_master WHERE name = 'message_search_fts')"
            ).fetchone()[0]
        )


def rebuild(db_path: str, *, channels: list, batch_size: int, resume_from: int, dry_run: bool):
    """Yield (last_message_id, scanned, rewritten) once per committed window."""
    scope, scope_params = "", []
    if channels:
        scope = f" AND channel_id IN ({','.join('?' * len(channels))})"
        scope_params = list(channels)

    cursor = resume_from
    while True:
        opener = sqlite_connection if dry_run else sqlite_transaction
        with opener(db_path) as conn:
            window = conn.execute(
                f"SELECT message_id FROM message_index WHERE message_id > ?{scope} "
                "ORDER BY message_id LIMIT ?",
                (cursor, *scope_params, batch_size),
            ).fetchall()
            if not window:
                return
            low, high = window[0][0], window[-1][0]
            visible = (
                f"SELECT {SELECT_COLUMNS} FROM message_index "
                f"WHERE message_id BETWEEN ? AND ?{scope} AND hidden = 0 AND deleted_at IS NULL"
            )
            if dry_run:
                rewritten = conn.execute(
                    f"SELECT COUNT(*) FROM ({visible})", (low, high, *scope_params)
                ).fetchone()[0]
            else:
                # Clear the whole window first, so rows that have since been
                # hidden or deleted leave the FTS table instead of lingering.
                conn.execute(
                    "DELETE FROM message_search_fts WHERE rowid IN "
                    f"(SELECT message_id FROM message_index WHERE message_id BETWEEN ? AND ?{scope})",
                    (low, high, *scope_params),
                )
                rewritten = conn.execute(
                    f"INSERT INTO message_search_fts({INSERT_COLUMNS}) {visible}",
                    (low, high, *scope_params),
                ).rowcount
        cursor = high
        yield cursor, len(window), rewritten


def main() -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--db", help="path to message_rag.db (default: config.yaml / RAG_DATABASE_PATH)")
    parser.add_argument("--config", default=str(ROOT_DIR / "config.yaml"))
    parser.add_argument("--channel", type=int, action="append", help="only this channel id (repeatable)")
    parser.add_argument("--dry-run", action="store_true", help="report what would change, write nothing")
    parser.add_argument("--batch-size", type=int, default=5000, help="messages per transaction")
    parser.add_argument("--resume-from", type=int, default=0, help="skip message ids up to and including this")
    args = parser.parse_args()

    settings = load_rag_settings(Path(args.config))
    db_path = (
        args.db
        or os.getenv("RAG_DATABASE_PATH")
        or str(settings.get("database_path", "data/message_rag.db"))
    )
    if not Path(db_path).expanduser().exists():
        print(f"no database at {db_path}", file=sys.stderr)
        return 2
    if args.batch_size < 1:
        print("--batch-size must be at least 1", file=sys.stderr)
        return 2
    if not fts_available(db_path):
        print(f"{db_path} has no message_search_fts table; FTS5 is unavailable here", file=sys.stderr)
        return 3

    scope = f"channel(s) {', '.join(str(c) for c in args.channel)}" if args.channel else "every channel"
    print(f"{'dry run over' if args.dry_run else 'rebuilding'} {scope} in {db_path}")
    scanned = rewritten = 0
    cursor = args.resume_from
    try:
        for cursor, batch_scanned, batch_rewritten in rebuild(
            db_path,
            channels=args.channel or [],
            batch_size=args.batch_size,
            resume_from=args.resume_from,
            dry_run=args.dry_run,
        ):
            scanned += batch_scanned
            rewritten += batch_rewritten
            print(f"  through message {cursor}: {scanned:,} scanned, {rewritten:,} indexed")
    except KeyboardInterrupt:
        print(f"interrupted; resume with --resume-from {cursor}", file=sys.stderr)
        return 130

    print(
        f"done: {scanned:,} messages scanned, {rewritten:,} FTS rows written"
        + (" (dry run, nothing written)" if args.dry_run else "")
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
