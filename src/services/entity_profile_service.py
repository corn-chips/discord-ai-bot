"""Per-person profile memory: who someone is and what they have done here.

Retrieval returns conversation blocks, and a regular's history in a busy channel
is spread over hundreds of them. This keeps one incrementally summarized card
per (guild, author) in the RAG database, looked up by name rather than by vector
similarity.
"""

import asyncio
import json
import logging
import re
import sqlite3
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional

from .sqlite_utils import sqlite_connection, sqlite_transaction

logger = logging.getLogger(__name__)

MAX_ALIASES = 8
#: Names are matched in Python, so bound what one lookup reads: this runs on
#: the response path, and the people a query names are the active ones.
LOOKUP_SCAN_LIMIT = 200
#: Failure backoff, doubling from a minute and capped, matching the shape of the
#: embedding retry ladder. Without it a permanently failing profile is due again
#: on every pass, and every pass pays for a `generate_response` call.
RETRY_BASE_SECONDS = 60.0
RETRY_MAX_SECONDS = 6 * 3600.0

_MENTION_RE = re.compile(r"<@!?(\d+)>")
_QUERY_TOKEN_RE = re.compile(r"[\w'\-]{2,}")


@dataclass
class EntityProfile:
    guild_id: int
    author_id: int
    display_name: str
    message_count: int
    aliases: list[str] = field(default_factory=list)
    first_seen: Optional[str] = None
    last_seen: Optional[str] = None
    latest_message_id: int = 0
    summary: Optional[str] = None
    last_summarized_at: Optional[str] = None
    summarized_message_id: int = 0
    last_error: Optional[str] = None
    failure_count: int = 0
    next_attempt_at: Optional[str] = None


class EntityProfileService:
    """Stores and looks up per-author profile cards in the RAG database."""

    def __init__(self, db_path: str = "data/message_rag.db"):
        self.db_path = Path(db_path).expanduser()
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._ensure_schema()

    def _connection(self, *, transaction: bool = False):
        manager = sqlite_transaction if transaction else sqlite_connection
        return manager(self.db_path, row_factory=sqlite3.Row)

    @staticmethod
    def _ensure_column(
        conn: sqlite3.Connection, *, column_name: str, column_definition: str
    ) -> None:
        cursor = conn.execute("PRAGMA table_info(entity_profiles)")
        if column_name in {row[1] for row in cursor.fetchall()}:
            return
        conn.execute(f"ALTER TABLE entity_profiles ADD COLUMN {column_name} {column_definition}")

    def _ensure_schema(self) -> None:
        try:
            with self._connection(transaction=True) as conn:
                conn.execute(
                    """
                    CREATE TABLE IF NOT EXISTS entity_profiles (
                        guild_id INTEGER NOT NULL,
                        author_id INTEGER NOT NULL,
                        display_name TEXT NOT NULL DEFAULT '',
                        aliases TEXT NOT NULL DEFAULT '[]',
                        first_seen TEXT,
                        last_seen TEXT,
                        message_count INTEGER NOT NULL DEFAULT 0,
                        latest_message_id INTEGER NOT NULL DEFAULT 0,
                        summary TEXT,
                        last_summarized_at TEXT,
                        summarized_message_id INTEGER NOT NULL DEFAULT 0,
                        last_error TEXT,
                        failure_count INTEGER NOT NULL DEFAULT 0,
                        next_attempt_at TEXT,
                        updated_at TEXT NOT NULL,
                        PRIMARY KEY (guild_id, author_id)
                    )
                    """
                )
                # Editing the CREATE TABLE body alone leaves existing databases
                # without the column: there is no migration framework here.
                self._ensure_column(
                    conn,
                    column_name="failure_count",
                    column_definition="INTEGER NOT NULL DEFAULT 0",
                )
                self._ensure_column(
                    conn, column_name="next_attempt_at", column_definition="TEXT"
                )
                conn.execute(
                    """
                    CREATE INDEX IF NOT EXISTS idx_entity_profiles_name
                    ON entity_profiles (guild_id, display_name)
                    """
                )
                # Guild 0 is what DM profiling used to write, and every DM in the
                # process shared it. Those cards are cross-user leaks; drop them.
                conn.execute("DELETE FROM entity_profiles WHERE guild_id = 0")
        except Exception as exc:
            logger.error("Failed to create entity_profiles table: %s", exc)

    @staticmethod
    def _now_iso() -> str:
        return datetime.now(timezone.utc).isoformat()

    @staticmethod
    def _scope(guild_id: Optional[int]) -> Optional[int]:
        """Guild key, or None in a DM.

        Profiles are guild memory and are not kept for direct messages at all.
        Every DM used to share one namespace (guild 0), so a card built from one
        person's DMs was returned for someone else's.
        """
        return int(guild_id) if guild_id else None

    @staticmethod
    def _row_to_profile(row: sqlite3.Row) -> EntityProfile:
        try:
            aliases = json.loads(row["aliases"] or "[]")
        except (TypeError, ValueError):
            aliases = []
        return EntityProfile(
            guild_id=int(row["guild_id"]),
            author_id=int(row["author_id"]),
            display_name=row["display_name"] or "",
            aliases=[str(alias) for alias in aliases if alias],
            first_seen=row["first_seen"],
            last_seen=row["last_seen"],
            message_count=int(row["message_count"]),
            latest_message_id=int(row["latest_message_id"]),
            summary=row["summary"],
            last_summarized_at=row["last_summarized_at"],
            summarized_message_id=int(row["summarized_message_id"]),
            last_error=row["last_error"],
            failure_count=int(row["failure_count"] or 0),
            next_attempt_at=row["next_attempt_at"],
        )

    def observe_authors(self, guild_id: Optional[int]) -> int:
        """Refresh names, counts and activity span from the message index."""
        scope = self._scope(guild_id)
        if scope is None:
            return 0
        try:
            with self._connection() as conn:
                rows = conn.execute(
                    """
                    SELECT author_id, author_name, COUNT(*) AS seen,
                           MIN(created_at) AS first_seen, MAX(created_at) AS last_seen,
                           MAX(message_id) AS latest_message_id
                    FROM message_index
                    WHERE guild_id = ?
                      AND author_id IS NOT NULL
                      AND is_bot = 0
                      AND hidden = 0
                      AND deleted_at IS NULL
                    GROUP BY author_id, author_name
                    """,
                    (scope,),
                ).fetchall()
        except Exception as exc:
            logger.warning("Entity profile observation failed for guild %s: %s", guild_id, exc)
            return 0

        merged: dict[int, dict] = {}
        for row in rows:
            author_id = int(row["author_id"])
            entry = merged.setdefault(
                author_id,
                {"names": [], "count": 0, "first": None, "last": None, "latest_id": 0},
            )
            entry["names"].append((int(row["seen"]), row["author_name"] or "", row["last_seen"]))
            entry["count"] += int(row["seen"])
            entry["first"] = min(x for x in (entry["first"], row["first_seen"]) if x)
            entry["last"] = max(x for x in (entry["last"], row["last_seen"]) if x)
            entry["latest_id"] = max(entry["latest_id"], int(row["latest_message_id"] or 0))

        now = self._now_iso()
        payload = []
        for author_id, entry in merged.items():
            # Newest name is what the person is called now; the rest are aliases
            # they were called under, most-used first.
            by_recency = sorted(entry["names"], key=lambda item: (item[2] or "", item[0]), reverse=True)
            display_name = by_recency[0][1]
            aliases = [
                name
                for _count, name, _last in sorted(entry["names"], reverse=True)
                if name and name != display_name
            ][: MAX_ALIASES]
            payload.append(
                (
                    scope, author_id, display_name, json.dumps(aliases),
                    entry["first"], entry["last"], entry["count"], entry["latest_id"], now,
                )
            )
        if not payload:
            return 0

        try:
            with self._connection(transaction=True) as conn:
                # Everything summarization owns -- summary, watermark, error --
                # is left alone here, so an observation pass never discards work
                # a model was already billed for.
                conn.executemany(
                    """
                    INSERT INTO entity_profiles (
                        guild_id, author_id, display_name, aliases, first_seen,
                        last_seen, message_count, latest_message_id, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(guild_id, author_id) DO UPDATE SET
                        display_name = excluded.display_name,
                        aliases = excluded.aliases,
                        first_seen = excluded.first_seen,
                        last_seen = excluded.last_seen,
                        message_count = excluded.message_count,
                        latest_message_id = excluded.latest_message_id,
                        updated_at = excluded.updated_at
                    """,
                    payload,
                )
        except Exception as exc:
            logger.warning("Entity profile upsert failed for guild %s: %s", guild_id, exc)
            return 0
        return len(payload)

    async def observe_authors_async(self, guild_id: Optional[int]) -> int:
        return await asyncio.to_thread(self.observe_authors, guild_id)

    def get_profile(self, guild_id: Optional[int], author_id: int) -> Optional[EntityProfile]:
        scope = self._scope(guild_id)
        if scope is None:
            return None
        try:
            with self._connection() as conn:
                row = conn.execute(
                    "SELECT * FROM entity_profiles WHERE guild_id = ? AND author_id = ?",
                    (scope, int(author_id)),
                ).fetchone()
        except Exception as exc:
            logger.warning("Entity profile read failed for %s: %s", author_id, exc)
            return None
        return self._row_to_profile(row) if row else None

    def profiles_due(
        self, guild_id: Optional[int], *, min_messages: int, refresh_hours: float, limit: int
    ) -> list[EntityProfile]:
        """Profiles with enough history, unread activity, a stale summary, and no
        pending failure backoff."""
        scope = self._scope(guild_id)
        if scope is None:
            return []
        now = self._now_iso()
        cutoff = (
            datetime.now(timezone.utc) - timedelta(hours=max(0.0, float(refresh_hours)))
        ).isoformat()
        try:
            with self._connection() as conn:
                rows = conn.execute(
                    """
                    SELECT * FROM entity_profiles
                    WHERE guild_id = ?
                      AND message_count >= ?
                      AND latest_message_id > summarized_message_id
                      AND (last_summarized_at IS NULL OR last_summarized_at < ?)
                      AND (next_attempt_at IS NULL OR next_attempt_at <= ?)
                    ORDER BY message_count DESC
                    LIMIT ?
                    """,
                    (scope, max(1, int(min_messages)), cutoff, now, max(1, int(limit))),
                ).fetchall()
        except Exception as exc:
            logger.warning("Entity profile due query failed for guild %s: %s", guild_id, exc)
            return []
        return [self._row_to_profile(row) for row in rows]

    async def profiles_due_async(self, guild_id: Optional[int], **kwargs) -> list[EntityProfile]:
        return await asyncio.to_thread(self.profiles_due, guild_id, **kwargs)

    def collect_new_activity(
        self,
        profile: EntityProfile,
        *,
        max_conversations: int,
        max_messages_per_conversation: int,
        max_chars: int,
    ) -> tuple[str, int]:
        """Render the conversations this person joined since the watermark.

        Returns `(transcript, watermark)`. The watermark only ever advances to
        what the transcript actually covers, so a capped pass leaves the rest
        for the next one instead of skipping it.
        """
        max_conversations = max(1, int(max_conversations))
        scope = self._scope(profile.guild_id)
        if scope is None:
            return "", int(profile.summarized_message_id)
        try:
            with self._connection() as conn:
                groups = conn.execute(
                    """
                    SELECT conversation_id, MIN(created_at) AS started,
                           MIN(message_id) AS first_id, MAX(message_id) AS last_id
                    FROM message_index
                    WHERE guild_id = ?
                      AND author_id = ?
                      AND message_id > ?
                      AND hidden = 0
                      AND deleted_at IS NULL
                    GROUP BY conversation_id
                    ORDER BY first_id ASC
                    LIMIT ?
                    """,
                    (
                        scope, int(profile.author_id),
                        int(profile.summarized_message_id), max_conversations + 1,
                    ),
                ).fetchall()
                covered = groups[:max_conversations]
                if not covered:
                    return "", int(profile.summarized_message_id)

                watermark = max(int(row["last_id"]) for row in covered)
                if len(groups) > max_conversations:
                    # An id ordering that disagrees with time ordering must not
                    # let an uncovered conversation fall behind the watermark.
                    # Ordering the groups by first_id is what keeps that clamp
                    # ahead of the old watermark: clamping to a boundary that is
                    # already behind it stalls the profile forever.
                    watermark = min(watermark, int(groups[max_conversations]["first_id"]) - 1)

                blocks: list[str] = []
                used = 0
                for row in covered:
                    conversation_id = row["conversation_id"]
                    lines = self._conversation_lines(
                        conn,
                        profile,
                        conversation_id=conversation_id,
                        limit=max(1, int(max_messages_per_conversation)),
                    )
                    if not lines:
                        continue
                    block = "\n".join(lines)
                    if used + len(block) > max_chars and blocks:
                        break
                    blocks.append(block)
                    used += len(block)
        except Exception as exc:
            logger.warning(
                "Entity profile activity read failed for %s: %s", profile.author_id, exc
            )
            return "", int(profile.summarized_message_id)

        if not blocks:
            return "", int(profile.summarized_message_id)
        return "\n\n".join(blocks), max(watermark, int(profile.summarized_message_id))

    def _conversation_lines(
        self, conn: sqlite3.Connection, profile: EntityProfile, *, conversation_id, limit: int
    ) -> list[str]:
        params: list = [self._scope(profile.guild_id)]
        if conversation_id is None:
            # Unsegmented messages have no conversation to expand into.
            clause = "conversation_id IS NULL AND author_id = ? AND message_id > ?"
            params.extend([int(profile.author_id), int(profile.summarized_message_id)])
        else:
            clause = "conversation_id = ?"
            params.append(int(conversation_id))
        params.append(limit)
        rows = conn.execute(
            f"""
            SELECT author_name, created_at, content_text
            FROM message_index
            WHERE guild_id = ? AND {clause}
              AND hidden = 0
              AND deleted_at IS NULL
            ORDER BY created_at ASC
            LIMIT ?
            """,
            params,
        ).fetchall()
        return [
            f"[{(row['created_at'] or '')[:10]}] {row['author_name']}: {row['content_text']}"
            for row in rows
            if (row["content_text"] or "").strip()
        ]

    async def collect_new_activity_async(self, profile: EntityProfile, **kwargs) -> tuple[str, int]:
        return await asyncio.to_thread(self.collect_new_activity, profile, **kwargs)

    def store_summary(
        self, guild_id: Optional[int], author_id: int, *, summary: str, covered_message_id: int
    ) -> bool:
        scope = self._scope(guild_id)
        if scope is None:
            return False
        try:
            with self._connection(transaction=True) as conn:
                cursor = conn.execute(
                    """
                    UPDATE entity_profiles
                    SET summary = ?,
                        last_summarized_at = ?,
                        summarized_message_id = ?,
                        last_error = NULL,
                        failure_count = 0,
                        next_attempt_at = NULL,
                        updated_at = ?
                    WHERE guild_id = ? AND author_id = ?
                    """,
                    (
                        summary, self._now_iso(), int(covered_message_id), self._now_iso(),
                        scope, int(author_id),
                    ),
                )
                return cursor.rowcount > 0
        except Exception as exc:
            logger.warning("Failed to store entity profile for %s: %s", author_id, exc)
            return False

    async def store_summary_async(self, guild_id: Optional[int], author_id: int, **kwargs) -> bool:
        return await asyncio.to_thread(self.store_summary, guild_id, author_id, **kwargs)

    def record_failure(self, guild_id: Optional[int], author_id: int, error: str) -> None:
        """Note why a pass failed and back the row off before its next attempt.

        The watermark and `last_summarized_at` stay where they were, so the row
        is never permanently skipped; `next_attempt_at` is what stops a
        permanently failing profile from buying a model call on every pass.
        """
        scope = self._scope(guild_id)
        if scope is None:
            return
        try:
            with self._connection(transaction=True) as conn:
                row = conn.execute(
                    "SELECT failure_count FROM entity_profiles "
                    "WHERE guild_id = ? AND author_id = ?",
                    (scope, int(author_id)),
                ).fetchone()
                if row is None:
                    return
                failures = int(row["failure_count"] or 0) + 1
                # The exponent is clamped as well: a long-dead row would overflow
                # the float long before the ceiling stopped mattering.
                delay = min(RETRY_BASE_SECONDS * 2 ** min(failures - 1, 20), RETRY_MAX_SECONDS)
                next_attempt_at = (
                    datetime.now(timezone.utc) + timedelta(seconds=delay)
                ).isoformat()
                conn.execute(
                    "UPDATE entity_profiles SET last_error = ?, failure_count = ?, "
                    "next_attempt_at = ?, updated_at = ? "
                    "WHERE guild_id = ? AND author_id = ?",
                    (
                        str(error)[:500], failures, next_attempt_at, self._now_iso(),
                        scope, int(author_id),
                    ),
                )
        except Exception as exc:
            logger.debug("Failed to record entity profile error for %s: %s", author_id, exc)

    async def record_failure_async(self, guild_id: Optional[int], author_id: int, error: str) -> None:
        await asyncio.to_thread(self.record_failure, guild_id, author_id, error)

    def clear_summary(self, guild_id: Optional[int], author_id: int) -> bool:
        """Drop the summary and rewind the watermark so the next pass rebuilds it."""
        scope = self._scope(guild_id)
        if scope is None:
            return False
        try:
            with self._connection(transaction=True) as conn:
                cursor = conn.execute(
                    """
                    UPDATE entity_profiles
                    SET summary = NULL,
                        last_summarized_at = NULL,
                        summarized_message_id = 0,
                        last_error = NULL,
                        failure_count = 0,
                        next_attempt_at = NULL,
                        updated_at = ?
                    WHERE guild_id = ? AND author_id = ?
                    """,
                    (self._now_iso(), scope, int(author_id)),
                )
                return cursor.rowcount > 0
        except Exception as exc:
            logger.warning("Failed to clear entity profile for %s: %s", author_id, exc)
            return False

    def find_profiles_for_query(
        self, guild_id: Optional[int], query: str, *, limit: int
    ) -> list[EntityProfile]:
        """Match summarized people named in `query` by mention, name or alias."""
        limit = max(0, int(limit))
        scope = self._scope(guild_id)
        if scope is None or limit == 0 or not (query or "").strip():
            return []
        mentioned = {int(value) for value in _MENTION_RE.findall(query)}
        tokens = {token.lower() for token in _QUERY_TOKEN_RE.findall(query)}
        if not mentioned and not tokens:
            return []

        try:
            with self._connection() as conn:
                rows = conn.execute(
                    """
                    SELECT * FROM entity_profiles
                    WHERE guild_id = ? AND summary IS NOT NULL AND summary != ''
                    ORDER BY message_count DESC
                    LIMIT ?
                    """,
                    (scope, LOOKUP_SCAN_LIMIT),
                ).fetchall()
        except Exception as exc:
            logger.warning("Entity profile lookup failed for guild %s: %s", guild_id, exc)
            return []

        matches: list[EntityProfile] = []
        for row in rows:
            profile = self._row_to_profile(row)
            if profile.author_id in mentioned or any(
                self._name_matches(name, tokens)
                for name in (profile.display_name, *profile.aliases)
            ):
                matches.append(profile)
            if len(matches) >= limit:
                break
        return matches

    @staticmethod
    def _name_matches(name: str, tokens: set[str]) -> bool:
        parts = [part.lower() for part in _QUERY_TOKEN_RE.findall(name or "")]
        # Every part of the name must appear, so "sam" does not answer for
        # "samantha" and a two-word name needs both words.
        return bool(parts) and all(part in tokens for part in parts)

    async def find_profiles_for_query_async(
        self, guild_id: Optional[int], query: str, **kwargs
    ) -> list[EntityProfile]:
        return await asyncio.to_thread(self.find_profiles_for_query, guild_id, query, **kwargs)

    @staticmethod
    def format_card(profile: EntityProfile, *, max_chars: int) -> str:
        if not profile or not (profile.summary or "").strip():
            return ""
        header = f"{profile.display_name} (user {profile.author_id})"
        if profile.aliases:
            header += f", also seen as {', '.join(profile.aliases[:3])}"
        span = " to ".join(
            value[:10] for value in (profile.first_seen, profile.last_seen) if value
        )
        facts = f"{profile.message_count} messages" + (f", {span}" if span else "")
        summary = profile.summary.strip()
        card = f"{header}\n{facts}\n{summary}"
        limit = max(1, int(max_chars))
        if len(card) > limit:
            card = card[:limit].rstrip() + " [truncated]"
        return card
