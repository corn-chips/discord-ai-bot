"""
Persistent Discord message index for local hybrid RAG.

The service keeps an additive SQLite schema beside the existing bot tables. It
stores message metadata, a local FTS5 index, and optional Gemini embeddings.
"""

import asyncio
import hashlib
import json
import logging
import math
import re
import sqlite3
import threading
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Iterable, Optional

import numpy as np

from ..models.data_models import MessageContext
from .context_collector import ContextCollector
from .sqlite_utils import sqlite_connection, sqlite_transaction

logger = logging.getLogger(__name__)


class MessageIndexWriteError(RuntimeError):
    """A write to the message index failed for an infrastructure reason.

    Distinct from a `False` return, which means the *content* is not indexable
    -- an out-of-scope bot message, or text that normalises to nothing. Callers
    are entitled to treat `False` as a statement about the message and this as a
    statement about the database, and DAB-065 is what happens when they cannot:
    a lock timeout was read as "no longer eligible" and permanently tombstoned a
    live message.
    """


@dataclass
class IndexedMessage:
    message_id: int
    guild_id: Optional[int]
    channel_id: int
    author_id: Optional[int]
    author_name: str
    is_bot: bool
    reply_to_message_id: Optional[int]
    created_at: datetime
    content_text: str
    attachment_summary: str = ""
    lexical_score: float = 0.0
    semantic_score: float = 0.0

    def to_context(
        self,
        *,
        retrieval_source: Optional[str] = None,
        retrieval_score: Optional[float] = None,
        retrieval_reason: Optional[str] = None,
    ) -> MessageContext:
        return MessageContext(
            content=self.content_text,
            author=self.author_name,
            timestamp=self.created_at,
            message_id=self.message_id,
            channel_id=self.channel_id,
            is_reply=self.reply_to_message_id is not None,
            replied_to_id=self.reply_to_message_id,
            retrieval_source=retrieval_source,
            retrieval_score=retrieval_score,
            retrieval_reason=retrieval_reason,
        )


@dataclass(frozen=True)
class _HistoryCursor:
    """Minimal Discord snowflake used to resume history after a message ID."""

    id: int


class MessageIndexService:
    """SQLite-backed message index with FTS5 and local embedding storage."""

    def __init__(
        self,
        db_path: str = "data/message_rag.db",
        embedding_model: str = "gemini-embedding-2",
        embedding_dimensions: int = 768,
        embedding_min_words: int = 2,
        embedding_min_alphanumeric_chars: int = 12,
        vector_cache_enabled: bool = True,
        legacy_db_path: Optional[str] = None,
    ):
        self.db_path = Path(db_path).expanduser()
        self.embedding_api_model = embedding_model
        self.embedding_dimensions = int(embedding_dimensions)
        self.embedding_model = f"{embedding_model}@{self.embedding_dimensions}"
        self.embedding_min_words = max(0, int(embedding_min_words))
        self.embedding_min_alphanumeric_chars = max(0, int(embedding_min_alphanumeric_chars))
        self.vector_cache_enabled = bool(vector_cache_enabled)
        self._vector_lock = threading.RLock()
        self._vector_loaded = False
        self._vector_count = 0
        self._vector_capacity = 0
        self._vector_matrix = np.empty((0, self.embedding_dimensions), dtype=np.float32)
        self._vector_message_ids = np.empty(0, dtype=np.int64)
        self._vector_guild_ids = np.empty(0, dtype=np.int64)
        self._vector_channel_ids = np.empty(0, dtype=np.int64)
        self.max_embedding_attempts = 3
        self.fts_enabled = False
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._ensure_schema()
        if legacy_db_path:
            self._migrate_legacy_database(legacy_db_path)

    def _connection(self, *, transaction: bool = False):
        connection_manager = sqlite_transaction if transaction else sqlite_connection
        return connection_manager(self.db_path, row_factory=sqlite3.Row)

    @staticmethod
    def _ensure_column(
        conn: sqlite3.Connection,
        *,
        table_name: str,
        column_name: str,
        column_definition: str,
    ) -> None:
        cursor = conn.execute(f"PRAGMA table_info({table_name})")
        existing_columns = {row[1] for row in cursor.fetchall()}
        if column_name in existing_columns:
            return
        conn.execute(f"ALTER TABLE {table_name} ADD COLUMN {column_name} {column_definition}")

    def _ensure_schema(self) -> None:
        try:
            with self._connection(transaction=True) as conn:
                conn.execute(
                    """
                    CREATE TABLE IF NOT EXISTS message_index (
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
                # Two scope indexes, one per scope the retrieval path actually
                # uses, replacing the single (guild_id, channel_id, created_at)
                # composite that served neither (DAB-078).
                #
                # The default path is channel-scoped (`cross_channel_enabled:
                # false`), so the composite's leading column was never bound:
                # SQLite skip-scanned every distinct guild_id and then built a
                # temp B-tree to satisfy ORDER BY created_at DESC -- for a query
                # that wants twelve rows. Measured on 100k rows: channel scope
                # 24.9 ms, guild scope 61.4 ms, both linear in corpus size.
                #
                # Partial, because `hidden = 0 AND deleted_at IS NULL` is in
                # every one of these queries, and REPLACING the composite rather
                # than joining it: the composite serves no query these two do
                # not, and keeping all three costs +35% on writes and +8 MB
                # against +15% and +0.1 MB for the swap. Measured after:
                # channel 0.027 ms (924x), guild 0.027 ms (2273x), and the
                # pending-embeddings poll 38.4 -> 0.070 ms (549x) for free.
                conn.execute("DROP INDEX IF EXISTS idx_message_index_scope_time")
                conn.execute(
                    """
                    CREATE INDEX IF NOT EXISTS idx_message_index_channel_time
                    ON message_index (channel_id, created_at DESC)
                    WHERE hidden = 0 AND deleted_at IS NULL
                    """
                )
                conn.execute(
                    """
                    CREATE INDEX IF NOT EXISTS idx_message_index_guild_time
                    ON message_index (guild_id, created_at DESC)
                    WHERE hidden = 0 AND deleted_at IS NULL
                    """
                )
                conn.execute(
                    """
                    CREATE INDEX IF NOT EXISTS idx_message_index_reply
                    ON message_index (reply_to_message_id)
                    """
                )
                conn.execute(
                    """
                    CREATE TABLE IF NOT EXISTS message_embeddings (
                        message_id INTEGER PRIMARY KEY,
                        embedding_model TEXT NOT NULL,
                        embedding_vector TEXT,
                        embedding_status TEXT NOT NULL DEFAULT 'pending',
                        content_hash TEXT NOT NULL,
                        embedded_at TEXT,
                        last_error TEXT,
                        embedding_attempts INTEGER NOT NULL DEFAULT 0,
                        next_retry_at TEXT,
                        FOREIGN KEY(message_id) REFERENCES message_index(message_id)
                    )
                    """
                )
                self._ensure_column(
                    conn,
                    table_name="message_embeddings",
                    column_name="embedding_attempts",
                    column_definition="INTEGER NOT NULL DEFAULT 0",
                )
                self._ensure_column(
                    conn,
                    table_name="message_embeddings",
                    column_name="next_retry_at",
                    column_definition="TEXT",
                )
                self._ensure_column(
                    conn,
                    table_name="message_index",
                    column_name="embedding_eligibility_text",
                    column_definition="TEXT",
                )
                conn.execute(
                    """
                    UPDATE message_embeddings
                    SET embedding_model = ?,
                        embedding_vector = NULL,
                        embedding_status = 'pending',
                        embedded_at = NULL,
                        last_error = NULL,
                        embedding_attempts = 0,
                        next_retry_at = NULL
                    WHERE embedding_model != ?
                    """,
                    (self.embedding_model, self.embedding_model),
                )
                conn.execute(
                    """
                    CREATE INDEX IF NOT EXISTS idx_message_embeddings_status
                    ON message_embeddings (embedding_model, embedding_status)
                    """
                )
                conn.execute(
                    """
                    CREATE TABLE IF NOT EXISTS message_retrieval_events (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        created_at TEXT NOT NULL,
                        guild_id INTEGER,
                        channel_id INTEGER NOT NULL,
                        user_id INTEGER,
                        query_length INTEGER NOT NULL,
                        selected_message_ids TEXT NOT NULL,
                        fallback_reason TEXT,
                        latency_ms INTEGER NOT NULL DEFAULT 0
                    )
                    """
                )
                for column_name, definition in (
                    ("retrieval_mode", "TEXT"),
                    ("recent_candidates", "INTEGER NOT NULL DEFAULT 0"),
                    ("lexical_candidates", "INTEGER NOT NULL DEFAULT 0"),
                    ("semantic_candidates", "INTEGER NOT NULL DEFAULT 0"),
                    ("query_embedding_used", "INTEGER NOT NULL DEFAULT 0"),
                    ("reranker_used", "INTEGER NOT NULL DEFAULT 0"),
                    ("reranker_reason", "TEXT"),
                    ("selected_count", "INTEGER NOT NULL DEFAULT 0"),
                ):
                    self._ensure_column(conn, table_name="message_retrieval_events", column_name=column_name, column_definition=definition)
                conn.execute(
                    """
                    CREATE TABLE IF NOT EXISTS message_backfill_progress (
                        channel_id INTEGER PRIMARY KEY,
                        guild_id INTEGER,
                        last_message_id INTEGER,
                        scanned_messages INTEGER NOT NULL DEFAULT 0,
                        completed_at TEXT,
                        updated_at TEXT NOT NULL
                    )
                    """
                )
                # The one-shot-migration ledger belongs to the schema, not to
                # the migrations that read it (DAB-083). It used to be created
                # inside _migrate_legacy_database, so whether it existed on a
                # fresh install depended on which service constructed first --
                # reproduced both ways: absent for MessageIndexService alone,
                # present when TokenTracker ran first as it does in
                # DiscordBot.__init__. Any future ledger read added here, or to
                # any caller that runs before a migration, would otherwise raise
                # `no such table: rag_migrations` from inside a synchronous
                # constructor, which is fatal (DAB-067).
                conn.execute(
                    """
                    CREATE TABLE IF NOT EXISTS rag_migrations (
                        name TEXT PRIMARY KEY,
                        completed_at TEXT NOT NULL
                    )
                    """
                )
                try:
                    conn.execute(
                        """
                        CREATE VIRTUAL TABLE IF NOT EXISTS message_search_fts
                        USING fts5(content_text, author_name, attachment_summary, tokenize='unicode61')
                        """
                    )
                    self.fts_enabled = True
                except sqlite3.OperationalError as exc:
                    self.fts_enabled = False
                    logger.warning("SQLite FTS5 is unavailable; lexical RAG search disabled: %s", exc)
                self._reconcile_embedding_eligibility(conn)
            logger.info("Message RAG index schema ready")
        except Exception as exc:
            logger.error("Failed to initialize message RAG index: %s", exc, exc_info=True)
            raise

    @staticmethod
    def _now_iso() -> str:
        return datetime.now(timezone.utc).isoformat()

    @staticmethod
    def _normalize_text(text: str) -> str:
        return re.sub(r"\s+", " ", text or "").strip()

    @staticmethod
    def _hash_text(text: str) -> str:
        return hashlib.sha256(text.encode("utf-8", errors="ignore")).hexdigest()

    @staticmethod
    def _parse_datetime(value: str) -> datetime:
        parsed = datetime.fromisoformat(value)
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed

    @staticmethod
    def _attachment_summary(message) -> str:
        names = []
        for attachment in getattr(message, "attachments", []) or []:
            filename = getattr(attachment, "filename", "") or "attachment"
            content_type = getattr(attachment, "content_type", None) or "unknown"
            names.append(f"{filename} ({content_type})")
        return ", ".join(names)

    def _row_to_indexed(self, row: sqlite3.Row) -> IndexedMessage:
        return IndexedMessage(
            message_id=int(row["message_id"]),
            guild_id=row["guild_id"],
            channel_id=int(row["channel_id"]),
            author_id=row["author_id"],
            author_name=row["author_name"],
            is_bot=bool(row["is_bot"]),
            reply_to_message_id=row["reply_to_message_id"],
            created_at=self._parse_datetime(row["created_at"]),
            content_text=row["content_text"],
            attachment_summary=row["attachment_summary"] or "",
            lexical_score=float(row["lexical_score"]) if "lexical_score" in row.keys() and row["lexical_score"] is not None else 0.0,
            semantic_score=float(row["semantic_score"]) if "semantic_score" in row.keys() and row["semantic_score"] is not None else 0.0,
        )

    def index_discord_message(
        self,
        message,
        *,
        include_bot_user_id: Optional[int] = None,
        force_include_bot: bool = False,
    ) -> bool:
        """Store a Discord message if it is in scope for retrieval."""
        author = getattr(message, "author", None)
        is_bot = bool(getattr(author, "bot", False))
        author_id = getattr(author, "id", None)
        if is_bot and not force_include_bot and author_id != include_bot_user_id:
            return False

        content_text = self._normalize_text(ContextCollector._build_message_content(message))
        if not content_text:
            return False

        channel = getattr(message, "channel", None)
        guild = getattr(message, "guild", None)
        reference = getattr(message, "reference", None)
        return self.upsert_message(
            message_id=int(message.id),
            guild_id=getattr(guild, "id", None),
            channel_id=int(channel.id),
            author_id=author_id,
            author_name=getattr(author, "display_name", None) or getattr(author, "name", None) or str(author),
            is_bot=is_bot,
            reply_to_message_id=getattr(reference, "message_id", None) if reference else None,
            created_at=getattr(message, "created_at", datetime.now(timezone.utc)),
            content_text=content_text,
            attachment_summary=self._attachment_summary(message),
            embedding_eligibility_text=(
                (getattr(message, "content", "") or "").strip()
                or (getattr(message, "system_content", "") or "").strip()
            ),
        )

    async def index_discord_message_async(
        self,
        message,
        *,
        include_bot_user_id: Optional[int] = None,
        force_include_bot: bool = False,
    ) -> bool:
        return await asyncio.to_thread(
            self.index_discord_message,
            message,
            include_bot_user_id=include_bot_user_id,
            force_include_bot=force_include_bot,
        )

    def index_bot_response(
        self,
        *,
        message_id: int,
        channel_id: int,
        guild_id: Optional[int],
        author_id: Optional[int],
        author_name: str,
        content_text: str,
        reply_to_message_id: Optional[int],
        created_at: Optional[datetime] = None,
    ) -> bool:
        content_text = self._normalize_text(content_text)
        if not content_text:
            return False
        return self.upsert_message(
            message_id=message_id,
            guild_id=guild_id,
            channel_id=channel_id,
            author_id=author_id,
            author_name=author_name,
            is_bot=True,
            reply_to_message_id=reply_to_message_id,
            created_at=created_at or datetime.now(timezone.utc),
            content_text=content_text,
            attachment_summary="",
            embedding_eligibility_text=content_text,
        )

    async def index_bot_response_async(self, **kwargs) -> bool:
        return await asyncio.to_thread(self.index_bot_response, **kwargs)

    def upsert_message(
        self,
        *,
        message_id: int,
        guild_id: Optional[int],
        channel_id: int,
        author_id: Optional[int],
        author_name: str,
        is_bot: bool,
        reply_to_message_id: Optional[int],
        created_at: datetime,
        content_text: str,
        attachment_summary: str = "",
        embedding_eligibility_text: Optional[str] = None,
        hidden: Optional[bool] = None,
    ) -> bool:
        content_text = self._normalize_text(content_text)
        if not content_text:
            return False

        content_hash = self._hash_text(content_text)
        eligibility_text = (
            content_text
            if embedding_eligibility_text is None
            else self._normalize_text(embedding_eligibility_text)
        )
        embedding_status = "skipped" if self._embedding_is_trivial(eligibility_text, attachment_summary) else "pending"
        created_at_iso = created_at.isoformat()
        indexed_at = self._now_iso()
        try:
            with self._connection(transaction=True) as conn:
                existing = conn.execute(
                    "SELECT content_hash, hidden, deleted_at FROM message_index WHERE message_id = ?",
                    (message_id,),
                ).fetchone()
                resolved_hidden = (
                    bool(existing["hidden"])
                    if hidden is None and existing is not None
                    else bool(hidden)
                )
                is_deleted = existing is not None and existing["deleted_at"] is not None
                conn.execute(
                    """
                    INSERT INTO message_index (
                        message_id, guild_id, channel_id, author_id, author_name, is_bot,
                        reply_to_message_id, created_at, indexed_at, content_text,
                        attachment_summary, embedding_eligibility_text,
                        content_hash, hidden, deleted_at
                    )
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, NULL)
                    ON CONFLICT(message_id) DO UPDATE SET
                        guild_id = excluded.guild_id,
                        channel_id = excluded.channel_id,
                        author_id = excluded.author_id,
                        author_name = excluded.author_name,
                        is_bot = excluded.is_bot,
                        reply_to_message_id = excluded.reply_to_message_id,
                        created_at = excluded.created_at,
                        indexed_at = excluded.indexed_at,
                        content_text = excluded.content_text,
                        attachment_summary = excluded.attachment_summary,
                        embedding_eligibility_text = excluded.embedding_eligibility_text,
                        content_hash = excluded.content_hash,
                        hidden = excluded.hidden
                    """,
                    (
                        message_id,
                        guild_id,
                        channel_id,
                        author_id,
                        author_name[:120],
                        1 if is_bot else 0,
                        reply_to_message_id,
                        created_at_iso,
                        indexed_at,
                        content_text,
                        attachment_summary,
                        eligibility_text,
                        content_hash,
                        1 if resolved_hidden else 0,
                    ),
                )
                if self.fts_enabled:
                    conn.execute("DELETE FROM message_search_fts WHERE rowid = ?", (message_id,))
                    if not resolved_hidden and not is_deleted:
                        conn.execute(
                            """
                            INSERT INTO message_search_fts(rowid, content_text, author_name, attachment_summary)
                            VALUES (?, ?, ?, ?)
                            """,
                            (message_id, content_text, author_name[:120], attachment_summary),
                        )
                conn.execute(
                    """
                    INSERT INTO message_embeddings (
                        message_id, embedding_model, embedding_vector,
                        embedding_status, content_hash, embedded_at, last_error,
                        embedding_attempts, next_retry_at
                    )
                    VALUES (?, ?, NULL, ?, ?, NULL, NULL, 0, NULL)
                    ON CONFLICT(message_id) DO UPDATE SET
                        embedding_model = excluded.embedding_model,
                        embedding_status = CASE
                            WHEN message_embeddings.content_hash != excluded.content_hash
                              OR message_embeddings.embedding_model != excluded.embedding_model
                            THEN excluded.embedding_status
                            ELSE message_embeddings.embedding_status
                        END,
                        embedding_vector = CASE
                            WHEN message_embeddings.content_hash != excluded.content_hash
                              OR message_embeddings.embedding_model != excluded.embedding_model
                            THEN NULL
                            ELSE message_embeddings.embedding_vector
                        END,
                        content_hash = excluded.content_hash,
                        embedded_at = CASE
                            WHEN message_embeddings.content_hash != excluded.content_hash
                              OR message_embeddings.embedding_model != excluded.embedding_model
                            THEN NULL
                            ELSE message_embeddings.embedded_at
                        END,
                        last_error = CASE
                            WHEN message_embeddings.content_hash != excluded.content_hash
                              OR message_embeddings.embedding_model != excluded.embedding_model
                            THEN NULL
                            ELSE message_embeddings.last_error
                        END,
                        embedding_attempts = CASE
                            WHEN message_embeddings.content_hash != excluded.content_hash
                              OR message_embeddings.embedding_model != excluded.embedding_model
                            THEN 0
                            ELSE message_embeddings.embedding_attempts
                        END,
                        next_retry_at = CASE
                            WHEN message_embeddings.content_hash != excluded.content_hash
                              OR message_embeddings.embedding_model != excluded.embedding_model
                            THEN NULL
                            ELSE message_embeddings.next_retry_at
                        END
                    """,
                    (
                        message_id,
                        self.embedding_model,
                        embedding_status if not is_deleted else "skipped",
                        content_hash,
                    ),
                )
            self._cache_remove(message_id)
            return True
        except Exception as exc:
            # Raise, do not return False (DAB-065).
            #
            # False is a business decision -- "this content is not indexable" --
            # and the edit handler acts on it by tombstoning the row. Reporting
            # a failed WRITE the same way meant a five-second lock timeout was
            # read as "no longer eligible", and the tombstone it produced is
            # permanent: no code path clears deleted_at, every later re-index
            # still deletes the FTS row and forces embedding_status='skipped',
            # and /rag backfill goes through here too. The message stayed in the
            # index with its content faithfully updated while being invisible to
            # both the lexical and the semantic candidate sets, for good.
            #
            # Callers are deliberately left to decide: backfill_channel lets it
            # abort and resumes from its durable cursor, on_message logs and
            # moves on, and handle_message_edit now leaves the existing row
            # exactly as it was.
            logger.error("Failed to index message %s: %s", message_id, exc, exc_info=True)
            raise MessageIndexWriteError(
                f"failed to index message {message_id}: {exc}"
            ) from exc

    async def backfill_channel(
        self,
        channel,
        *,
        limit: Optional[int],
        include_bot_user_id: Optional[int] = None,
    ) -> int:
        """Persist channel history, resuming after the durable per-channel cursor."""
        indexed_count = 0
        scanned_count = 0
        channel_id = int(channel.id)
        guild_id = getattr(getattr(channel, "guild", None), "id", None)
        try:
            history_limit = None if limit is None else max(0, limit)
            progress = await self.get_backfill_progress_async(channel_id)
            history_kwargs = {
                "limit": history_limit,
                "oldest_first": True,
            }
            if progress and progress["last_message_id"] is not None:
                history_kwargs["after"] = _HistoryCursor(progress["last_message_id"])
                logger.info(
                    "Resuming RAG backlog for channel %s after message %s",
                    channel_id,
                    progress["last_message_id"],
                )

            async for historical in channel.history(**history_kwargs):
                if await self.index_discord_message_async(
                    historical,
                    include_bot_user_id=include_bot_user_id,
                ):
                    indexed_count += 1
                scanned_count += 1
                await self.advance_backfill_progress_async(
                    channel_id=channel_id,
                    guild_id=guild_id,
                    last_message_id=int(historical.id),
                )

            if history_limit is None or scanned_count < history_limit:
                await self.mark_backfill_complete_async(
                    channel_id=channel_id,
                    guild_id=guild_id,
                )
        except Exception as exc:
            logger.warning("RAG backfill failed for channel %s: %s", getattr(channel, "id", "unknown"), exc)
            raise
        return indexed_count

    def _migrate_legacy_database(self, legacy_db_path: str) -> None:
        """Copy legacy RAG tables once into the dedicated RAG database."""
        legacy_path = Path(legacy_db_path).expanduser()
        if (
            not legacy_path.exists()
            or legacy_path.resolve() == self.db_path.resolve()
        ):
            return

        migration_name = "legacy_shared_rag_v1"
        tables = (
            "message_index",
            "message_embeddings",
            "message_retrieval_events",
            "message_backfill_progress",
        )
        try:
            with self._connection(transaction=True) as conn:
                # The ledger table itself is created by _ensure_schema, which
                # has already run. It used to be created here, which made its
                # existence on a fresh install an accident of which service
                # constructed first (DAB-083): TokenTracker first and it was
                # there, MessageIndexService alone and it was not.
                if conn.execute(
                    "SELECT 1 FROM rag_migrations WHERE name = ?",
                    (migration_name,),
                ).fetchone():
                    return

                conn.execute("ATTACH DATABASE ? AS legacy", (str(legacy_path),))
                legacy_tables_found = 0
                shortfalls: list[str] = []
                for table_name in tables:
                    legacy_exists = conn.execute(
                        "SELECT 1 FROM legacy.sqlite_master WHERE type='table' AND name=?",
                        (table_name,),
                    ).fetchone()
                    if not legacy_exists:
                        continue
                    legacy_tables_found += 1
                    target_columns = {
                        row[1] for row in conn.execute(
                            f"PRAGMA main.table_info({table_name})"
                        ).fetchall()
                    }
                    legacy_columns = {
                        row[1] for row in conn.execute(
                            f"PRAGMA legacy.table_info({table_name})"
                        ).fetchall()
                    }
                    common_columns = sorted(target_columns & legacy_columns)
                    if not common_columns:
                        continue
                    columns_sql = ", ".join(f'"{column}"' for column in common_columns)

                    # Count before and after, and refuse to record success on a
                    # short copy (DAB-066).
                    #
                    # `INSERT OR IGNORE` extends conflict resolution to NOT NULL
                    # violations, so a legacy schema missing a column this table
                    # declares NOT NULL without a default drops EVERY row --
                    # silently, with no error -- and the ledger row below then
                    # gates every later boot, so it is never retried. Measured
                    # on the mechanism: 5,000 legacy rows in, 0 rows out, ledger
                    # written, "Copied legacy message RAG data" logged.
                    expected = conn.execute(
                        f'SELECT COUNT(*) FROM legacy."{table_name}"'
                    ).fetchone()[0]
                    before = conn.execute(
                        f'SELECT COUNT(*) FROM main."{table_name}"'
                    ).fetchone()[0]
                    conn.execute(
                        f'INSERT OR IGNORE INTO main."{table_name}" ({columns_sql}) '
                        f'SELECT {columns_sql} FROM legacy."{table_name}"'
                    )
                    after_count = conn.execute(
                        f'SELECT COUNT(*) FROM main."{table_name}"'
                    ).fetchone()[0]
                    copied = after_count - before
                    if copied != expected:
                        shortfalls.append(
                            f"{table_name}: expected {expected} rows, copied {copied}"
                        )

                conn.execute(
                    """
                    UPDATE message_embeddings
                    SET embedding_model = ?, embedding_vector = NULL,
                        embedding_status = 'pending', embedded_at = NULL,
                        last_error = NULL, embedding_attempts = 0,
                        next_retry_at = NULL
                    WHERE embedding_model != ?
                    """,
                    (self.embedding_model, self.embedding_model),
                )
                self._reconcile_embedding_eligibility(conn)
                if self.fts_enabled:
                    conn.execute("DELETE FROM message_search_fts")
                    conn.execute(
                        """
                        INSERT INTO message_search_fts(
                            rowid, content_text, author_name, attachment_summary
                        )
                        SELECT message_id, content_text, author_name, attachment_summary
                        FROM message_index
                        WHERE hidden = 0 AND deleted_at IS NULL
                        """
                    )
                if shortfalls:
                    # Leave the ledger unwritten so a fixed migration can run
                    # again, and RETURN rather than raise. MessageIndexService
                    # is constructed unguarded in DiscordBot.__init__, so
                    # raising here would abort startup -- and because schema
                    # drift is deterministic, not transient, it would abort
                    # every startup, forever, with no operator escape (DAB-067).
                    # Trading silent data loss for a permanent boot loop is not
                    # an improvement. The transaction still commits what it
                    # copied, which is a strict superset of today's behaviour;
                    # what changes is that the migration is not recorded as
                    # done and the operator is told.
                    logger.error(
                        "Legacy RAG migration from %s copied fewer rows than the "
                        "legacy database holds (%s). The migration is NOT being "
                        "recorded as complete, so it will be retried on the next "
                        "start once the cause is fixed. This usually means the "
                        "legacy schema lacks a column this version declares "
                        "NOT NULL.",
                        legacy_path,
                        "; ".join(shortfalls),
                    )
                    return

                if not legacy_tables_found:
                    # A fresh install always has a token_usage.db -- TokenTracker
                    # builds it -- and it has never held RAG tables. Recording
                    # the migration as completed here burned the one shot, and
                    # logged "Copied legacy message RAG data" over a copy of
                    # nothing (DAB-083). Leaving the ledger unwritten costs one
                    # cheap ATTACH per boot and keeps the migration available
                    # for a database that really does hold legacy data.
                    logger.debug(
                        "No legacy message RAG tables in %s; nothing to migrate "
                        "and the migration is left unrecorded",
                        legacy_path,
                    )
                    return

                conn.execute(
                    "INSERT INTO rag_migrations(name, completed_at) VALUES (?, ?)",
                    (migration_name, self._now_iso()),
                )
            logger.info("Copied legacy message RAG data from %s", legacy_path)
        except Exception as exc:
            logger.error(
                "Failed to copy legacy message RAG data from %s: %s",
                legacy_path,
                exc,
                exc_info=True,
            )
            raise
    def _embedding_is_trivial(self, content_text: str, attachment_summary: str = "") -> bool:
        if (attachment_summary or "").strip():
            return False
        words = re.findall(r"[A-Za-z0-9]+", content_text or "")
        chars = len(re.findall(r"[A-Za-z0-9]", content_text or ""))
        return len(words) < self.embedding_min_words and chars < self.embedding_min_alphanumeric_chars

    def _reconcile_embedding_eligibility(self, conn: sqlite3.Connection) -> None:
        rows = conn.execute("SELECT m.message_id,m.content_text,m.embedding_eligibility_text,m.attachment_summary,e.embedding_status FROM message_index m JOIN message_embeddings e ON e.message_id=m.message_id WHERE e.embedding_model=?", (self.embedding_model,)).fetchall()
        for row in rows:
            eligibility_text = row["embedding_eligibility_text"]
            if eligibility_text is None:
                # Additive migration fallback for rows written before the raw
                # eligibility text was persisted.
                eligibility_text = re.sub(
                    r"\s*\[Message type:.*\]\s*$",
                    "",
                    row["content_text"] or "",
                    flags=re.DOTALL,
                )
            trivial = self._embedding_is_trivial(eligibility_text, row["attachment_summary"])
            if trivial and row["embedding_status"] != "skipped":
                conn.execute("UPDATE message_embeddings SET embedding_status='skipped',embedding_vector=NULL,embedded_at=NULL,last_error=NULL,embedding_attempts=0,next_retry_at=NULL WHERE message_id=?", (row["message_id"],))
            elif not trivial and row["embedding_status"] == "skipped":
                conn.execute("UPDATE message_embeddings SET embedding_status='pending',embedding_vector=NULL,embedded_at=NULL,last_error=NULL,embedding_attempts=0,next_retry_at=NULL WHERE message_id=?", (row["message_id"],))

    def _ensure_vector_capacity(self, required: int) -> None:
        if required <= self._vector_capacity:
            return
        capacity = max(required, 64 if not self._vector_capacity else self._vector_capacity * 2)
        matrix = np.empty((capacity, self.embedding_dimensions), dtype=np.float32)
        ids = np.empty(capacity, dtype=np.int64); guilds = np.empty(capacity, dtype=np.int64); channels = np.empty(capacity, dtype=np.int64)
        if self._vector_count:
            matrix[:self._vector_count] = self._vector_matrix[:self._vector_count]
            ids[:self._vector_count] = self._vector_message_ids[:self._vector_count]
            guilds[:self._vector_count] = self._vector_guild_ids[:self._vector_count]
            channels[:self._vector_count] = self._vector_channel_ids[:self._vector_count]
        self._vector_matrix, self._vector_message_ids = matrix, ids
        self._vector_guild_ids, self._vector_channel_ids = guilds, channels
        self._vector_capacity = capacity

    def _cache_remove(self, message_id: int) -> None:
        if not self.vector_cache_enabled:
            return
        with self._vector_lock:
            if not self._vector_loaded:
                return
            found = np.flatnonzero(self._vector_message_ids[:self._vector_count] == int(message_id))
            if not len(found): return
            index, last = int(found[0]), self._vector_count - 1
            if index != last:
                self._vector_matrix[index] = self._vector_matrix[last]
                self._vector_message_ids[index] = self._vector_message_ids[last]
                self._vector_guild_ids[index] = self._vector_guild_ids[last]
                self._vector_channel_ids[index] = self._vector_channel_ids[last]
            self._vector_count -= 1

    def _cache_upsert(self, message_id: int, guild_id: Optional[int], channel_id: int, vector: np.ndarray) -> None:
        if not self.vector_cache_enabled or vector.size != self.embedding_dimensions:
            return
        with self._vector_lock:
            if not self._vector_loaded:
                return
            self._cache_remove(message_id); self._ensure_vector_capacity(self._vector_count + 1)
            index = self._vector_count
            self._vector_matrix[index] = vector; self._vector_message_ids[index] = int(message_id)
            self._vector_guild_ids[index] = -1 if guild_id is None else int(guild_id); self._vector_channel_ids[index] = int(channel_id)
            self._vector_count += 1

    def _load_vector_cache(self) -> None:
        if not self.vector_cache_enabled or self._vector_loaded: return
        with self._vector_lock:
            if self._vector_loaded: return
            with self._connection() as conn:
                rows = conn.execute("SELECT m.message_id,m.guild_id,m.channel_id,e.embedding_vector FROM message_embeddings e JOIN message_index m ON m.message_id=e.message_id WHERE e.embedding_model=? AND e.embedding_status='done' AND e.embedding_vector IS NOT NULL AND m.hidden=0 AND m.deleted_at IS NULL", (self.embedding_model,)).fetchall()
            self._ensure_vector_capacity(len(rows))
            for row in rows:
                vector = self._decode_vector(row["embedding_vector"])
                if vector is None or vector.size != self.embedding_dimensions:
                    continue
                index = self._vector_count; self._vector_matrix[index] = vector
                self._vector_message_ids[index] = int(row["message_id"]); self._vector_guild_ids[index] = -1 if row["guild_id"] is None else int(row["guild_id"]); self._vector_channel_ids[index] = int(row["channel_id"]); self._vector_count += 1
            self._vector_loaded = True

    @staticmethod
    def _decode_vector(raw_vector) -> Optional[np.ndarray]:
        """Decode both current float32 blobs and legacy JSON vectors."""
        try:
            if isinstance(raw_vector, (bytes, bytearray, memoryview)):
                return np.frombuffer(raw_vector, dtype=np.float32)
            return np.asarray(json.loads(raw_vector), dtype=np.float32)
        except (TypeError, ValueError, json.JSONDecodeError):
            return None
    def get_backfill_progress(self, channel_id: int) -> Optional[dict]:
        """Return the durable resume cursor and completion state for one channel."""
        with self._connection() as conn:
            row = conn.execute(
                """
                SELECT channel_id, guild_id, last_message_id, scanned_messages,
                       completed_at, updated_at
                FROM message_backfill_progress
                WHERE channel_id = ?
                """,
                (int(channel_id),),
            ).fetchone()
        return dict(row) if row is not None else None

    async def get_backfill_progress_async(self, channel_id: int) -> Optional[dict]:
        return await asyncio.to_thread(self.get_backfill_progress, channel_id)

    def advance_backfill_progress(
        self,
        *,
        channel_id: int,
        guild_id: Optional[int],
        last_message_id: int,
    ) -> None:
        """Advance one channel cursor after a history item has been handled."""
        now = self._now_iso()
        with self._connection(transaction=True) as conn:
            conn.execute(
                """
                INSERT INTO message_backfill_progress (
                    channel_id, guild_id, last_message_id, scanned_messages,
                    completed_at, updated_at
                ) VALUES (?, ?, ?, 1, NULL, ?)
                ON CONFLICT(channel_id) DO UPDATE SET
                    guild_id = COALESCE(excluded.guild_id, message_backfill_progress.guild_id),
                    last_message_id = excluded.last_message_id,
                    scanned_messages = message_backfill_progress.scanned_messages + 1,
                    completed_at = NULL,
                    updated_at = excluded.updated_at
                """,
                (int(channel_id), guild_id, int(last_message_id), now),
            )

    async def advance_backfill_progress_async(
        self,
        *,
        channel_id: int,
        guild_id: Optional[int],
        last_message_id: int,
    ) -> None:
        await asyncio.to_thread(
            self.advance_backfill_progress,
            channel_id=channel_id,
            guild_id=guild_id,
            last_message_id=last_message_id,
        )

    def mark_backfill_complete(
        self,
        *,
        channel_id: int,
        guild_id: Optional[int],
    ) -> None:
        """Mark a channel caught up without discarding its resume cursor."""
        now = self._now_iso()
        with self._connection(transaction=True) as conn:
            conn.execute(
                """
                INSERT INTO message_backfill_progress (
                    channel_id, guild_id, last_message_id, scanned_messages,
                    completed_at, updated_at
                ) VALUES (?, ?, NULL, 0, ?, ?)
                ON CONFLICT(channel_id) DO UPDATE SET
                    guild_id = COALESCE(excluded.guild_id, message_backfill_progress.guild_id),
                    completed_at = excluded.completed_at,
                    updated_at = excluded.updated_at
                """,
                (int(channel_id), guild_id, now, now),
            )

    async def mark_backfill_complete_async(
        self,
        *,
        channel_id: int,
        guild_id: Optional[int],
    ) -> None:
        await asyncio.to_thread(
            self.mark_backfill_complete,
            channel_id=channel_id,
            guild_id=guild_id,
        )

    def delete_rag_data(self, *, channel_id: Optional[int] = None) -> dict:
        """Delete RAG-owned state globally or for one channel.

        The database is shared with other bot services, so this deliberately
        clears rows from only the message RAG tables.
        """
        try:
            with self._connection(transaction=True) as conn:
                has_pins = conn.execute(
                    """
                    SELECT 1
                    FROM sqlite_master
                    WHERE type = 'table' AND name = 'pinned_messages'
                    """
                ).fetchone() is not None
                if channel_id is None:
                    deleted_messages = conn.execute(
                        "SELECT COUNT(*) FROM message_index"
                    ).fetchone()[0]
                    if self.fts_enabled:
                        conn.execute("DELETE FROM message_search_fts")
                    conn.execute("DELETE FROM message_embeddings")
                    conn.execute("DELETE FROM message_index")
                    conn.execute("DELETE FROM message_retrieval_events")
                    conn.execute("DELETE FROM message_backfill_progress")
                    deleted_pins = (
                        conn.execute("DELETE FROM pinned_messages").rowcount
                        if has_pins
                        else 0
                    )
                else:
                    channel_id = int(channel_id)
                    deleted_messages = conn.execute(
                        "SELECT COUNT(*) FROM message_index WHERE channel_id = ?",
                        (channel_id,),
                    ).fetchone()[0]
                    if self.fts_enabled:
                        conn.execute(
                            """
                            DELETE FROM message_search_fts
                            WHERE rowid IN (
                                SELECT message_id
                                FROM message_index
                                WHERE channel_id = ?
                            )
                            """,
                            (channel_id,),
                        )
                    conn.execute(
                        """
                        DELETE FROM message_embeddings
                        WHERE message_id IN (
                            SELECT message_id
                            FROM message_index
                            WHERE channel_id = ?
                        )
                        """,
                        (channel_id,),
                    )
                    conn.execute(
                        "DELETE FROM message_index WHERE channel_id = ?",
                        (channel_id,),
                    )
                    conn.execute(
                        "DELETE FROM message_retrieval_events WHERE channel_id = ?",
                        (channel_id,),
                    )
                    conn.execute(
                        "DELETE FROM message_backfill_progress WHERE channel_id = ?",
                        (channel_id,),
                    )
                    deleted_pins = (
                        conn.execute(
                            "DELETE FROM pinned_messages WHERE channel_id = ?",
                            (channel_id,),
                        ).rowcount
                        if has_pins
                        else 0
                    )
            if self.vector_cache_enabled:
                with self._vector_lock:
                    if self._vector_loaded and channel_id is None:
                        self._vector_count = 0
                    elif self._vector_loaded:
                        ids = self._vector_message_ids[:self._vector_count][self._vector_channel_ids[:self._vector_count] == int(channel_id)].copy()
                        for message_id in ids:
                            self._cache_remove(int(message_id))
            return {
                "messages": int(deleted_messages),
                "pins": int(deleted_pins),
            }
        except Exception as exc:
            scope = "all channels" if channel_id is None else f"channel {channel_id}"
            logger.error("Failed to delete RAG data for %s: %s", scope, exc, exc_info=True)
            raise

    async def delete_rag_data_async(self, *, channel_id: Optional[int] = None) -> dict:
        return await asyncio.to_thread(self.delete_rag_data, channel_id=channel_id)

    def mark_hidden(self, message_id: int, hidden: bool = True) -> bool:
        try:
            with self._connection(transaction=True) as conn:
                conn.execute(
                    "UPDATE message_index SET hidden = ? WHERE message_id = ?",
                    (1 if hidden else 0, message_id),
                )
                if self.fts_enabled:
                    conn.execute("DELETE FROM message_search_fts WHERE rowid = ?", (message_id,))
                    if not hidden:
                        row = conn.execute(
                            """
                            SELECT content_text, author_name, attachment_summary
                            FROM message_index
                            WHERE message_id = ?
                              AND deleted_at IS NULL
                            """,
                            (message_id,),
                        ).fetchone()
                        if row:
                            conn.execute(
                                """
                                INSERT INTO message_search_fts(rowid, content_text, author_name, attachment_summary)
                                VALUES (?, ?, ?, ?)
                                """,
                                (message_id, row["content_text"], row["author_name"], row["attachment_summary"] or ""),
                            )
            if hidden:
                self._cache_remove(message_id)
            elif self.vector_cache_enabled:
                with self._connection() as conn:
                    row = conn.execute("SELECT m.guild_id,m.channel_id,e.embedding_vector FROM message_index m JOIN message_embeddings e ON e.message_id=m.message_id WHERE m.message_id=? AND e.embedding_status='done'", (message_id,)).fetchone()
                if row and row["embedding_vector"]:
                    vector = self._decode_vector(row["embedding_vector"])
                    if vector is not None:
                        self._cache_upsert(message_id, row["guild_id"], row["channel_id"], vector)
            return True
        except Exception as exc:
            logger.error("Failed to update hidden state for indexed message %s: %s", message_id, exc)
            return False

    def mark_deleted(self, message_id: int) -> bool:
        """Mark an indexed message as deleted and remove it from FTS retrieval."""
        try:
            with self._connection(transaction=True) as conn:
                conn.execute(
                    """
                    UPDATE message_index
                    SET deleted_at = ?,
                        hidden = 1
                    WHERE message_id = ?
                    """,
                    (self._now_iso(), message_id),
                )
                if self.fts_enabled:
                    conn.execute("DELETE FROM message_search_fts WHERE rowid = ?", (message_id,))
            self._cache_remove(message_id)
            return True
        except Exception as exc:
            logger.error("Failed to mark indexed message %s deleted: %s", message_id, exc)
            return False

    async def mark_deleted_async(self, message_id: int) -> bool:
        return await asyncio.to_thread(self.mark_deleted, message_id)

    def _scope_clause(
        self,
        guild_id: Optional[int],
        channel_id: int,
        cross_channel: bool,
        params: list,
    ) -> str:
        if cross_channel:
            if guild_id is None:
                params.append(channel_id)
                return "m.channel_id = ?"
            params.append(guild_id)
            return "m.guild_id = ?"
        params.append(channel_id)
        return "m.channel_id = ?"

    @staticmethod
    def _exclude_clause(exclude_message_ids: Iterable[int], params: list) -> str:
        ids = [int(message_id) for message_id in exclude_message_ids or []]
        if not ids:
            return ""
        params.extend(ids)
        placeholders = ",".join("?" for _ in ids)
        return f" AND m.message_id NOT IN ({placeholders})"

    def search_recent(
        self,
        *,
        guild_id: Optional[int],
        channel_id: int,
        cross_channel: bool,
        limit: int,
        exclude_message_ids: Optional[Iterable[int]] = None,
    ) -> list[IndexedMessage]:
        params: list = []
        scope = self._scope_clause(guild_id, channel_id, cross_channel, params)
        exclude = self._exclude_clause(exclude_message_ids or [], params)
        params.append(limit)
        try:
            with self._connection() as conn:
                rows = conn.execute(
                    f"""
                    SELECT m.*, 0.0 AS lexical_score, 0.0 AS semantic_score
                    FROM message_index m
                    WHERE {scope}
                      AND m.hidden = 0
                      AND m.deleted_at IS NULL
                      {exclude}
                    ORDER BY m.created_at DESC
                    LIMIT ?
                    """,
                    params,
                ).fetchall()
            return [self._row_to_indexed(row) for row in rows]
        except Exception as exc:
            logger.error("Recent RAG search failed: %s", exc, exc_info=True)
            return []

    async def search_recent_async(self, **kwargs) -> list[IndexedMessage]:
        return await asyncio.to_thread(self.search_recent, **kwargs)

    @staticmethod
    def _build_fts_query(query: str) -> Optional[str]:
        tokens = re.findall(r"[A-Za-z0-9_@#./:-]{2,}", query or "")
        if not tokens:
            return None
        terms = []
        seen = set()
        for token in tokens[:24]:
            cleaned = token.replace('"', '""')
            lowered = cleaned.lower()
            if lowered in seen:
                continue
            seen.add(lowered)
            terms.append(f'"{cleaned}"')
        return " OR ".join(terms) if terms else None

    def search_lexical(
        self,
        query: str,
        *,
        guild_id: Optional[int],
        channel_id: int,
        cross_channel: bool,
        limit: int,
        exclude_message_ids: Optional[Iterable[int]] = None,
    ) -> list[IndexedMessage]:
        if not self.fts_enabled:
            return []
        fts_query = self._build_fts_query(query)
        if not fts_query:
            return []
        params: list = [fts_query]
        scope = self._scope_clause(guild_id, channel_id, cross_channel, params)
        exclude = self._exclude_clause(exclude_message_ids or [], params)
        params.append(limit)
        try:
            with self._connection() as conn:
                rows = conn.execute(
                    f"""
                    SELECT m.*, bm25(message_search_fts) AS lexical_score, 0.0 AS semantic_score
                    FROM message_search_fts
                    JOIN message_index m ON m.message_id = message_search_fts.rowid
                    WHERE message_search_fts MATCH ?
                      AND {scope}
                      AND m.hidden = 0
                      AND m.deleted_at IS NULL
                      {exclude}
                    ORDER BY lexical_score ASC
                    LIMIT ?
                    """,
                    params,
                ).fetchall()
            messages = [self._row_to_indexed(row) for row in rows]
            for rank, message in enumerate(messages, start=1):
                message.lexical_score = 1.0 / rank
            return messages
        except sqlite3.OperationalError as exc:
            logger.warning("FTS RAG query failed; lexical retrieval skipped: %s", exc)
            return []
        except Exception as exc:
            logger.error("Lexical RAG search failed: %s", exc, exc_info=True)
            return []

    async def search_lexical_async(self, query: str, **kwargs) -> list[IndexedMessage]:
        return await asyncio.to_thread(self.search_lexical, query, **kwargs)

    def get_pending_embeddings(
        self,
        limit: int = 16,
        *,
        channel_id: Optional[int] = None,
    ) -> list[tuple[int, str, str]]:
        try:
            channel_clause = ""
            params: list = [self.embedding_model, self._now_iso()]
            if channel_id is not None:
                channel_clause = " AND m.channel_id = ?"
                params.append(channel_id)
            params.append(limit)
            with self._connection() as conn:
                rows = conn.execute(
                    f"""
                    SELECT m.message_id, m.author_name, m.created_at, m.content_text, e.content_hash
                    FROM message_embeddings e
                    JOIN message_index m ON m.message_id = e.message_id
                    WHERE e.embedding_model = ?
                      AND e.embedding_status = 'pending'
                      AND (e.next_retry_at IS NULL OR e.next_retry_at <= ?)
                      AND m.hidden = 0
                      AND m.deleted_at IS NULL
                      {channel_clause}
                    ORDER BY m.created_at DESC
                    LIMIT ?
                    """,
                    params,
                ).fetchall()
            results = []
            for row in rows:
                text = (
                    f"title: Discord message by {row['author_name']} at {row['created_at']} | "
                    f"text: {row['content_text']}"
                )
                results.append((int(row["message_id"]), text, row["content_hash"]))
            return results
        except Exception as exc:
            logger.error("Failed to load pending embeddings: %s", exc, exc_info=True)
            return []

    async def get_pending_embeddings_async(
        self,
        limit: int = 16,
        *,
        channel_id: Optional[int] = None,
    ) -> list[tuple[int, str, str]]:
        return await asyncio.to_thread(
            self.get_pending_embeddings,
            limit,
            channel_id=channel_id,
        )

    def store_embedding(self, message_id: int, vector: list[float], content_hash: str) -> bool:
        try:
            vector_array = np.asarray(vector, dtype=np.float32)
            if vector_array.ndim != 1 or vector_array.size == 0:
                return False
            vector_blob = sqlite3.Binary(vector_array.tobytes())
            with self._connection(transaction=True) as conn:
                cursor = conn.execute(
                    """
                    UPDATE message_embeddings
                    SET embedding_vector = ?,
                        embedding_status = 'done',
                        embedded_at = ?,
                        last_error = NULL,
                        embedding_attempts = 0,
                        next_retry_at = NULL
                    WHERE message_id = ?
                      AND content_hash = ?
                    """,
                    (vector_blob, self._now_iso(), message_id, content_hash),
                )
                row = conn.execute("SELECT guild_id,channel_id FROM message_index WHERE message_id=?", (message_id,)).fetchone()
            if cursor.rowcount and row:
                self._cache_upsert(message_id, row["guild_id"], row["channel_id"], vector_array)
            return bool(cursor.rowcount)
        except Exception as exc:
            logger.error("Failed to store embedding for message %s: %s", message_id, exc)
            return False

    async def store_embedding_async(self, message_id: int, vector: list[float], content_hash: str) -> bool:
        return await asyncio.to_thread(self.store_embedding, message_id, vector, content_hash)

    def mark_embedding_failed(self, message_id: int, error: str) -> None:
        try:
            with self._connection(transaction=True) as conn:
                row = conn.execute(
                    """
                    SELECT embedding_attempts
                    FROM message_embeddings
                    WHERE message_id = ?
                    """,
                    (message_id,),
                ).fetchone()
                attempts = int(row["embedding_attempts"] or 0) + 1 if row else 1
                terminal = attempts >= self.max_embedding_attempts
                retry_delay_seconds = min(60 * (2 ** max(0, attempts - 1)), 3600)
                next_retry_at = None if terminal else (
                    datetime.now(timezone.utc) + timedelta(seconds=retry_delay_seconds)
                ).isoformat()
                conn.execute(
                    """
                    UPDATE message_embeddings
                    SET embedding_status = ?,
                        last_error = ?,
                        embedding_attempts = ?,
                        next_retry_at = ?
                    WHERE message_id = ?
                    """,
                    (
                        "failed" if terminal else "pending",
                        (error or "embedding failed")[:500],
                        attempts,
                        next_retry_at,
                        message_id,
                    ),
                )
        except Exception as exc:
            logger.error("Failed to mark embedding failure for message %s: %s", message_id, exc)

    async def mark_embedding_failed_async(self, message_id: int, error: str) -> None:
        await asyncio.to_thread(self.mark_embedding_failed, message_id, error)

    @staticmethod
    def _cosine_similarity(left: list[float], right: list[float]) -> float:
        if not left or not right or len(left) != len(right):
            return 0.0
        dot = sum(a * b for a, b in zip(left, right))
        left_norm = math.sqrt(sum(a * a for a in left))
        right_norm = math.sqrt(sum(b * b for b in right))
        if left_norm == 0.0 or right_norm == 0.0:
            return 0.0
        return dot / (left_norm * right_norm)

    def search_semantic(
        self,
        query_embedding: list[float],
        *,
        guild_id: Optional[int],
        channel_id: int,
        cross_channel: bool,
        limit: int,
        exclude_message_ids: Optional[Iterable[int]] = None,
    ) -> list[IndexedMessage]:
        if not query_embedding: return []
        try:
            query_vector = np.asarray(query_embedding, dtype=np.float32)
            query_norm = float(np.linalg.norm(query_vector))
            if query_vector.ndim != 1 or query_vector.size == 0 or query_norm == 0: return []
            if not self.vector_cache_enabled or query_vector.size != self.embedding_dimensions:
                return self._search_semantic_sql_compat(query_vector, guild_id=guild_id, channel_id=channel_id, cross_channel=cross_channel, limit=limit, exclude_message_ids=exclude_message_ids)
            self._load_vector_cache()
            with self._vector_lock:
                count = self._vector_count
                if not count: return []
                mask = self._vector_channel_ids[:count] == int(channel_id)
                if cross_channel and guild_id is not None:
                    mask = self._vector_guild_ids[:count] == int(guild_id)
                excluded = set(int(value) for value in (exclude_message_ids or []))
                if excluded: mask &= ~np.isin(self._vector_message_ids[:count], list(excluded))
                positions = np.flatnonzero(mask)
                if not len(positions): return []
                matrix = self._vector_matrix[positions]
                norms = np.linalg.norm(matrix, axis=1)
                scores = np.zeros(len(positions), dtype=np.float32)
                valid = norms > 0
                scores[valid] = (matrix[valid] @ query_vector) / (norms[valid] * query_norm)
                order = np.argsort(-scores, kind="stable")[:limit]
                ranked = [(int(self._vector_message_ids[positions[i]]), float(scores[i])) for i in order if np.isfinite(scores[i]) and scores[i] > 0]
            messages = {item.message_id: item for item in self.get_messages_by_ids(message_id for message_id, _score in ranked)}
            results = []
            for message_id, score in ranked:
                message = messages.get(message_id)
                if message is not None:
                    message.semantic_score = score; results.append(message)
            return results
        except Exception as exc:
            logger.error("Semantic RAG search failed: %s", exc, exc_info=True)
            return []

    def _search_semantic_sql_compat(self, query_vector: np.ndarray, *, guild_id: Optional[int], channel_id: int, cross_channel: bool, limit: int, exclude_message_ids: Optional[Iterable[int]]) -> list[IndexedMessage]:
        params: list = []
        scope = self._scope_clause(guild_id, channel_id, cross_channel, params)
        exclude = self._exclude_clause(exclude_message_ids or [], params)
        with self._connection() as conn:
            rows = conn.execute(f"SELECT m.*,e.embedding_vector,0.0 AS lexical_score,0.0 AS semantic_score FROM message_embeddings e JOIN message_index m ON m.message_id=e.message_id WHERE e.embedding_model=? AND e.embedding_status='done' AND e.embedding_vector IS NOT NULL AND {scope} AND m.hidden=0 AND m.deleted_at IS NULL {exclude}", [self.embedding_model, *params]).fetchall()
        scored = []
        for row in rows:
            vector = self._decode_vector(row["embedding_vector"])
            if vector is None or vector.size != query_vector.size:
                continue
            score = self._cosine_similarity(vector.tolist(), query_vector.tolist())
            if score > 0: scored.append((score, row))
        results = []
        for score, row in sorted(scored, key=lambda item: item[0], reverse=True)[:limit]:
            message = self._row_to_indexed(row); message.semantic_score = score; results.append(message)
        return results

    async def search_semantic_async(self, query_embedding: list[float], **kwargs) -> list[IndexedMessage]:
        return await asyncio.to_thread(self.search_semantic, query_embedding, **kwargs)

    def get_messages_by_ids(self, message_ids: Iterable[int]) -> list[IndexedMessage]:
        ids = [int(message_id) for message_id in dict.fromkeys(message_ids or [])]
        if not ids:
            return []
        placeholders = ",".join("?" for _ in ids)
        try:
            with self._connection() as conn:
                rows = conn.execute(
                    f"""
                    SELECT m.*, 0.0 AS lexical_score, 0.0 AS semantic_score
                    FROM message_index m
                    WHERE m.message_id IN ({placeholders})
                      AND m.hidden = 0
                      AND m.deleted_at IS NULL
                    """,
                    ids,
                ).fetchall()
            return [self._row_to_indexed(row) for row in rows]
        except Exception as exc:
            logger.error("Failed to load indexed messages by id: %s", exc, exc_info=True)
            return []

    def record_retrieval_event(
        self,
        *,
        guild_id: Optional[int],
        channel_id: int,
        user_id: Optional[int],
        query_length: int,
        selected_message_ids: list[int],
        fallback_reason: Optional[str],
        latency_ms: int,
        retrieval_mode: str = "full",
        recent_candidates: int = 0,
        lexical_candidates: int = 0,
        semantic_candidates: int = 0,
        query_embedding_used: bool = False,
        reranker_used: bool = False,
        reranker_reason: Optional[str] = None,
    ) -> None:
        try:
            with self._connection(transaction=True) as conn:
                conn.execute(
                    """
                    INSERT INTO message_retrieval_events (
                        created_at, guild_id, channel_id, user_id, query_length,
                        selected_message_ids, fallback_reason, latency_ms,
                        retrieval_mode, recent_candidates, lexical_candidates,
                        semantic_candidates, query_embedding_used, reranker_used,
                        reranker_reason, selected_count
                    )
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        self._now_iso(),
                        guild_id,
                        channel_id,
                        user_id,
                        query_length,
                        json.dumps(selected_message_ids),
                        fallback_reason,
                        latency_ms,
                        retrieval_mode,
                        recent_candidates,
                        lexical_candidates,
                        semantic_candidates,
                        1 if query_embedding_used else 0,
                        1 if reranker_used else 0,
                        reranker_reason,
                        len(selected_message_ids),
                    ),
                )
        except Exception as exc:
            logger.debug("Failed to record retrieval event: %s", exc)

    async def record_retrieval_event_async(self, **kwargs) -> None:
        await asyncio.to_thread(self.record_retrieval_event, **kwargs)

    def get_status(self, *, channel_id: Optional[int] = None) -> dict:
        params = []
        where = ""
        if channel_id is not None:
            where = "WHERE channel_id = ?"
            params.append(channel_id)
        try:
            with self._connection() as conn:
                total = conn.execute(
                    f"SELECT COUNT(*) FROM message_index {where}",
                    params,
                ).fetchone()[0]
                embedding_scope = ""
                embedding_scope_params = []
                if channel_id is not None:
                    embedding_scope = " AND m.channel_id = ?"
                    embedding_scope_params.append(channel_id)

                def count_embeddings(status: str) -> int:
                    return conn.execute(
                        f"""
                        SELECT COUNT(*)
                        FROM message_embeddings e
                        JOIN message_index m ON m.message_id = e.message_id
                        WHERE e.embedding_model = ?
                          AND e.embedding_status = ?
                          {embedding_scope}
                        """,
                        (self.embedding_model, status, *embedding_scope_params),
                    ).fetchone()[0]

                embedded = count_embeddings("done")
                pending = count_embeddings("pending")
                failed = count_embeddings("failed")
                skipped = count_embeddings("skipped")
            cached_count = self._vector_count if self._vector_loaded else 0
            return {
                "messages": total,
                "embedded": embedded,
                "pending_embeddings": pending,
                "failed_embeddings": failed,
                "skipped_embeddings": skipped,
                "cached_vectors": cached_count,
                "vector_cache_bytes": cached_count * self.embedding_dimensions * 4,
                "fts_enabled": self.fts_enabled,
                "embedding_model": self.embedding_api_model,
                "embedding_dimensions": self.embedding_dimensions,
                "database_path": str(self.db_path.resolve()),
            }
        except Exception as exc:
            logger.error("Failed to load RAG status: %s", exc, exc_info=True)
            # The counts stay numeric so every caller's formatting keeps
            # working, and an "error" key marks them as meaningless. Without it
            # this dict is byte-identical to a healthy, empty index: /rag status
            # rendered its normal embed reporting 0 indexed messages, which is
            # exactly what a brand-new deployment looks like, so an unreadable
            # or corrupt database was indistinguishable from "nothing to do
            # yet" (DAB-170). Callers test `status.get("error")`; there is no
            # separate healthy flag, because two sources of truth can disagree.
            return {
                "messages": 0,
                "embedded": 0,
                "pending_embeddings": 0,
                "failed_embeddings": 0,
                "skipped_embeddings": 0,
                "cached_vectors": self._vector_count if self._vector_loaded else 0,
                "vector_cache_bytes": (self._vector_count if self._vector_loaded else 0) * self.embedding_dimensions * 4,
                "fts_enabled": self.fts_enabled,
                "embedding_model": self.embedding_api_model,
                "embedding_dimensions": self.embedding_dimensions,
                "database_path": str(self.db_path.resolve()),
                "error": f"{type(exc).__name__}: {exc}",
            }

    async def get_status_async(self, *, channel_id: Optional[int] = None) -> dict:
        return await asyncio.to_thread(self.get_status, channel_id=channel_id)
