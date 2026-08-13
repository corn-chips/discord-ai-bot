#!/usr/bin/env python3
"""Recompute conversation ids over an existing message RAG database.

Live indexing assigns a conversation inline from the preceding message alone.
This is the authoritative pass: it adds participant turnover and the union-find
reply merge, and it is deterministic, so running it twice yields identical ids.

Usage:
    python scripts/rebuild_conversations.py                 # every channel
    python scripts/rebuild_conversations.py --channel 123   # one channel
    python scripts/rebuild_conversations.py --dry-run       # report, write nothing
    python scripts/rebuild_conversations.py --resume        # skip fully assigned channels
    python scripts/rebuild_conversations.py --db data/message_rag.db

Each channel is one transaction, so an interrupted run leaves whole channels
done and the rest untouched; `--resume` skips the ones already assigned. Pure
SQLite: no Discord, no network, no API key.
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

from src.services.message_index_service import MessageIndexService  # noqa: E402
from src.services.sqlite_utils import sqlite_connection  # noqa: E402


def load_rag_settings(config_path: Path) -> dict:
    if not config_path.exists():
        print(f"no config at {config_path}; using built-in defaults")
        return {}
    data = yaml.safe_load(config_path.read_text(encoding="utf-8")) or {}
    return dict(data.get("rag") or {})


def build_service(settings: dict, db_path: str) -> MessageIndexService:
    # The embedding settings are passed through even though this script never
    # embeds: they decide the eligibility fingerprint and the stored model
    # name, and getting them wrong here resets every vector to 'pending'.
    return MessageIndexService(
        db_path,
        embedding_model=str(settings.get("embedding_model", "gemini-embedding-2")),
        embedding_dimensions=int(settings.get("embedding_dimensions", 768)),
        embedding_min_words=int(settings.get("embedding_min_words", 2)),
        embedding_min_alphanumeric_chars=int(settings.get("embedding_min_alphanumeric_chars", 12)),
        conversation_gap_minutes=float(settings.get("conversation_gap_minutes", 10.0)),
        conversation_max_messages=int(settings.get("conversation_max_messages", 40)),
        conversation_reply_merge_max_hours=float(
            settings.get("conversation_reply_merge_max_hours", 6.0)
        ),
        conversation_turnover_window=int(settings.get("conversation_turnover_window", 3)),
        conversation_turnover_min_gap_minutes=float(
            settings.get("conversation_turnover_min_gap_minutes", 3.0)
        ),
    )


def unassigned_count(db_path: str, channel_id: int) -> int:
    with sqlite_connection(db_path) as conn:
        return conn.execute(
            """
            SELECT COUNT(*) FROM message_index
            WHERE channel_id = ? AND conversation_id IS NULL
              AND hidden = 0 AND deleted_at IS NULL
            """,
            (channel_id,),
        ).fetchone()[0]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", help="path to message_rag.db (default: config.yaml / RAG_DATABASE_PATH)")
    parser.add_argument("--config", default=str(ROOT_DIR / "config.yaml"))
    parser.add_argument("--channel", type=int, action="append", help="only this channel id (repeatable)")
    parser.add_argument("--dry-run", action="store_true", help="report what would change, write nothing")
    parser.add_argument("--resume", action="store_true", help="skip channels with no unassigned messages")
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

    service = build_service(settings, db_path)
    channels = args.channel or service.list_indexed_channel_ids()
    if not channels:
        print("no indexed channels")
        return 0

    print(f"{'dry run over' if args.dry_run else 'rebuilding'} {len(channels)} channel(s) in {db_path}")
    totals = {"messages": 0, "conversations": 0, "changed": 0, "skipped": 0}
    for position, channel_id in enumerate(channels, start=1):
        if args.resume and not unassigned_count(db_path, channel_id):
            totals["skipped"] += 1
            print(f"[{position}/{len(channels)}] channel {channel_id}: already assigned, skipped")
            continue
        result = service.recompute_channel_conversations(channel_id, dry_run=args.dry_run)
        for key in ("messages", "conversations", "changed"):
            totals[key] += result[key]
        print(
            f"[{position}/{len(channels)}] channel {channel_id}: "
            f"{result['messages']:,} messages -> {result['conversations']:,} conversations "
            f"({result['changed']:,} reassigned)"
        )

    print(
        f"done: {totals['messages']:,} messages, {totals['conversations']:,} conversations, "
        f"{totals['changed']:,} reassigned, {totals['skipped']} channel(s) skipped"
        + (" (dry run, nothing written)" if args.dry_run else "")
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
