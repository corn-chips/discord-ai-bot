"""
Persistent Discord message index for local hybrid RAG.

The service keeps an additive SQLite schema beside the existing bot tables. It
stores message metadata, a local FTS5 index, and optional Gemini embeddings.
"""

import asyncio
import hashlib
import inspect
import json
import logging
import re
import sqlite3
import threading
import uuid
from collections import deque
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Iterable, Optional

import numpy as np

from ..models.data_models import MessageContext
from .context_collector import ContextCollector
from .sqlite_utils import sqlite_connection, sqlite_transaction

logger = logging.getLogger(__name__)

#: The key `_migrate_legacy_database` verifies each table's copy against.
#:
#: The post-condition is "no legacy row's key is absent from the target", which
#: is idempotent: a re-run finds every key present and reports success. The
#: count difference it replaced was not, and that is PPR-02.
#:
#: **Two of these four keys cannot witness a lost row, and it is worth knowing
#: which.** A key is a witness only if it identifies the same fact in both
#: databases:
#:
#: - `message_index.message_id` and `message_embeddings.message_id` are Discord
#:   snowflakes. The same id in both files is the same message, so the check is
#:   exact.
#: - `message_retrieval_events.id` is `INTEGER PRIMARY KEY AUTOINCREMENT` with
#:   no UNIQUE constraint of any kind. Both files start it at 1, so overlap is
#:   not a corner case but the norm: `INSERT OR IGNORE` keeps main's row, drops
#:   the legacy row carrying entirely different content, and the check still
#:   passes because the *id* is present. Demonstrated: 1,000 rows in main and
#:   1,500 in legacy loses 1,000 of them with the post-condition green.
#: - `message_backfill_progress.channel_id` is natural, but it names a mutable
#:   *cursor* rather than an immutable fact, so it has the same blind spot: a
#:   legacy channel's progress is silently discarded in favour of main's.
#:
#: Neither is fixed here. Both need main to already hold the row when the
#: migration runs *and* legacy to hold it too, which means an operator merging
#: two installations; `message_retrieval_events` is written but never read
#: anywhere under `src/`; and reconciling by content instead is a much larger
#: change than the defect warrants. The caveat is recorded because a green
#: post-condition over these two tables means "the ids are all there", not "the
#: data is all there".
_MIGRATION_KEYS = {
    "message_index": "message_id",
    "message_embeddings": "message_id",
    "message_retrieval_events": "id",
    "message_backfill_progress": "channel_id",
}

#: Query-side only: the FTS index still holds every one of these, so this
#: narrows what a question asks for, never what is searchable.
_FTS_STOPWORDS = frozenset(
    """
    about all also am an and any are as at be been being but by can could did do does
    doing done for from had has have having he her hers him his how if in into is it
    its just me my no not of off on once only or other our out over own she should so
    some such than that the their them then there these they this those to too was we
    were what when where which while who whom why will with would you your
    """.split()
)


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
    conversation_id: Optional[int] = None
    lexical_score: float = 0.0
    semantic_score: float = 0.0

    def to_context(
        self,
        *,
        retrieval_source: Optional[str] = None,
        retrieval_score: Optional[float] = None,
        retrieval_reason: Optional[str] = None,
        is_conversation_filler: bool = False,
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
            conversation_id=self.conversation_id,
            is_conversation_filler=is_conversation_filler,
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
        embedding_retry_reset_hours: float = 24.0,
        bm25_weight_content: float = 1.0,
        bm25_weight_author: float = 0.1,
        bm25_weight_attachment: float = 0.5,
        fts_stopwords_enabled: bool = True,
        fts_min_and_results: int = 5,
        conversation_enabled: bool = True,
        conversation_gap_minutes: float = 10.0,
        conversation_max_messages: int = 40,
        conversation_reply_merge_max_hours: float = 6.0,
        conversation_turnover_window: int = 3,
        conversation_turnover_min_gap_minutes: float = 3.0,
        legacy_db_path: Optional[str] = None,
    ):
        self.db_path = Path(db_path).expanduser()
        self.embedding_api_model = embedding_model
        self.embedding_dimensions = int(embedding_dimensions)
        self.embedding_model = f"{embedding_model}@{self.embedding_dimensions}"
        self.embedding_min_words = max(0, int(embedding_min_words))
        self.embedding_min_alphanumeric_chars = max(0, int(embedding_min_alphanumeric_chars))
        self.vector_cache_enabled = bool(vector_cache_enabled)
        self.embedding_retry_reset_hours = max(0.0, float(embedding_retry_reset_hours))
        self.bm25_weights = (
            float(bm25_weight_content),
            float(bm25_weight_author),
            float(bm25_weight_attachment),
        )
        self.fts_stopwords_enabled = bool(fts_stopwords_enabled)
        self.fts_min_and_results = max(0, int(fts_min_and_results))
        self.conversation_enabled = bool(conversation_enabled)
        self.conversation_gap = timedelta(minutes=max(0.0, float(conversation_gap_minutes)))
        self.conversation_max_messages = max(1, int(conversation_max_messages))
        self.conversation_reply_merge_max = timedelta(hours=max(0.0, float(conversation_reply_merge_max_hours)))
        self.conversation_turnover_window = max(1, int(conversation_turnover_window))
        self.conversation_turnover_min_gap = timedelta(minutes=max(0.0, float(conversation_turnover_min_gap_minutes)))
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
                        deleted_at TEXT,
                        conversation_id INTEGER
                    )
                    """
                )
                # Before the indexes below, one of which is on this column: an
                # installed database predating it would otherwise fail the
                # CREATE INDEX with "no such column".
                self._ensure_column(
                    conn,
                    table_name="message_index",
                    column_name="conversation_id",
                    column_definition="INTEGER",
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
                # Conversation expansion reads a whole conversation in
                # chronological order; the same partial predicate keeps it off
                # tombstoned rows, as the two scope indexes above do. Named
                # without a `_time` suffix on purpose: the DAB-078 guard in
                # tests/test_rag_query_plans.py counts `idx_message_index_%_time`.
                conn.execute(
                    """
                    CREATE INDEX IF NOT EXISTS idx_message_index_conversation
                    ON message_index (channel_id, conversation_id, created_at)
                    WHERE hidden = 0 AND deleted_at IS NULL
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
                conn.execute(
                    """
                    CREATE TABLE IF NOT EXISTS rag_state (
                        id INTEGER PRIMARY KEY CHECK (id = 1),
                        eligibility_fingerprint TEXT
                    )
                    """
                )
                self._repair_empty_fts(conn)

                # The eligibility reconcile is a full-table scan joined across
                # message_index and message_embeddings, and it ran on every
                # construction (DAB-077). Measured here at ~132-character
                # bodies, median of 7: 6.9 ms at 1k rows, 64.7 ms at 10k,
                # 741.8 ms at 100k -- 50-65% of MessageIndexService.__init__ --
                # and until the previous commit it ran TWICE per boot, because
                # _migrate_legacy_database re-entered on every normal install
                # and called it again.
                #
                # It has work to do only when something that decides its answer
                # has changed, so it is gated on a fingerprint of exactly those
                # inputs. The fingerprint lives in a one-row `rag_state` table
                # rather than in `rag_migrations`, deliberately: five tests in
                # tests/test_rag_query_plans.py assert the whole ledger with
                # assertEqual and they are the DAB-066 and DAB-083 guards. The
                # only rewrite that admits a fingerprint row is `assertNotIn`,
                # which stops them catching a spurious ledger row -- putting it
                # there would trade two real guards for a table.
                #
                # A wrongly SKIPPED reconcile is permanent and silent: the row
                # stays `skipped`, `get_pending_embeddings` never returns it, it
                # never becomes `done`, and it is invisible to search_semantic
                # and to the vector cache forever. An identical re-upsert does
                # not repair it; only a real edit does. A wrongly RUN reconcile
                # costs one boot's scan. That asymmetry is why the rule inputs
                # are hashed from their own source rather than tracked by a
                # hand-maintained version constant somebody can forget to bump.
                fingerprint = self._eligibility_fingerprint()
                stored = conn.execute(
                    "SELECT eligibility_fingerprint FROM rag_state WHERE id = 1"
                ).fetchone()
                if stored is None or stored[0] != fingerprint:
                    self._reconcile_embedding_eligibility(conn)
                    conn.execute(
                        "INSERT INTO rag_state(id, eligibility_fingerprint) "
                        "VALUES (1, ?) ON CONFLICT(id) DO UPDATE SET "
                        "eligibility_fingerprint = excluded.eligibility_fingerprint",
                        (fingerprint,),
                    )
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
            conversation_id=row["conversation_id"] if "conversation_id" in row.keys() else None,
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
                    "SELECT content_hash, hidden, deleted_at, conversation_id FROM message_index WHERE message_id = ?",
                    (message_id,),
                ).fetchone()
                conversation_id = existing["conversation_id"] if existing is not None else None
                if conversation_id is None and self.conversation_enabled:
                    conversation_id = self._assign_conversation_inline(
                        conn,
                        message_id=message_id,
                        channel_id=channel_id,
                        reply_to_message_id=reply_to_message_id,
                        created_at=created_at,
                    )
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
                        content_hash, hidden, deleted_at, conversation_id
                    )
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, NULL, ?)
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
                        hidden = excluded.hidden,
                        conversation_id = excluded.conversation_id
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
                        conversation_id,
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
                    conn.execute(
                        f'INSERT OR IGNORE INTO main."{table_name}" ({columns_sql}) '
                        f'SELECT {columns_sql} FROM legacy."{table_name}"'
                    )

                    # The post-condition is "every legacy row is now present in
                    # main", tested on each table's own key -- not
                    # `after - before == expected`, which is what shipped and is
                    # not idempotent with respect to its own retry (PPR-02).
                    # On the second boot after a shortfall the rows are already
                    # in main, `INSERT OR IGNORE` copies nothing, and the
                    # comparison is `0 != N`: a table holding 100% of its legacy
                    # rows reported itself short, forever, so the ledger was
                    # never written and every boot re-ran the whole migration.
                    key = _MIGRATION_KEYS[table_name]
                    if key not in common_columns:
                        shortfalls.append(
                            f"{table_name}: no {key} column in common, so the copy "
                            f"cannot be verified"
                        )
                        continue
                    missing = conn.execute(
                        f'SELECT COUNT(*) FROM legacy."{table_name}" AS l '
                        f'WHERE NOT EXISTS (SELECT 1 FROM main."{table_name}" AS m '
                        f'WHERE m."{key}" = l."{key}")'
                    ).fetchone()[0]
                    if missing:
                        expected = conn.execute(
                            f'SELECT COUNT(*) FROM legacy."{table_name}"'
                        ).fetchone()[0]
                        shortfalls.append(
                            f"{table_name}: {missing} of {expected} legacy rows are "
                            f"still absent from the target"
                        )

                if not legacy_tables_found:
                    # A fresh install always has a token_usage.db -- TokenTracker
                    # builds it -- and it has never held RAG tables. Recording
                    # the migration as completed here burned the one shot, and
                    # logged "Copied legacy message RAG data" over a copy of
                    # nothing (DAB-083). Leaving the ledger unwritten costs one
                    # cheap ATTACH per boot and keeps the migration available
                    # for a database that really does hold legacy data.
                    #
                    # This return used to sit AFTER the three statements below,
                    # so "one cheap ATTACH" was not what it cost: on every boot
                    # of every normal install it also re-ran the embedding-model
                    # reset, a second full-table eligibility reconcile, and a
                    # complete FTS teardown and rebuild. Measured on a fresh
                    # install with the target already populated, whole
                    # constructor, median of 7: at 20,000 rows 859 ms -> 238 ms,
                    # at 100,000 rows 4,810 ms -> 1,243 ms. (An independent
                    # re-measurement on shorter content put the 100k saving at
                    # 2,245 ms rather than 3,568 ms -- the ratio reproduces, the
                    # absolute scales with content length, so quote it with a
                    # fixture.)
                    logger.debug(
                        "No legacy message RAG tables in %s; nothing to migrate "
                        "and the migration is left unrecorded",
                        legacy_path,
                    )
                    return

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
        """Decide whether a message is too slight to be worth embedding.

        **The Latin-only character classes below are a known defect (DAB-087),
        and widening them is deliberately deferred. Editing this function AT ALL
        starts a paid re-embedding run on the operator's own API key. Read this
        before you touch it.**

        `_eligibility_fingerprint` hashes this function's own source, so any
        change -- including a comment or a reformat -- bumps the fingerprint,
        and the next boot re-runs the full eligibility reconcile. That is
        correct behaviour and it is exactly the hazard: widening the classes
        flips every non-Latin row from `skipped` to `pending`, and
        `_start_automatic_rag_backlog` then drains them with **no inter-batch
        delay, no cap and no rate limiter**. Estimated at 100k indexed messages
        with half the corpus non-Latin: ~37,500 rows, ~4.4M tokens, ~2,400
        back-to-back embedding calls over 16-40 minutes of boot.

        The tokens are not the objection. `mark_embedding_failed` is terminal at
        three attempts, and the reconcile only ever rescues rows in `skipped`,
        never in `failed` -- so a rate-limit storm part-way through that run
        leaves rows permanently unembeddable, recoverable only by hand-editing
        SQLite. That would be unsafe at zero cost.

        **Reopen condition, both halves required:** the backfill drain needs
        inter-batch pacing and a cap, and `failed` needs to become recoverable
        by the reconcile. Then widen the classes, in a commit that expects the
        re-embedding and says so.

        The lexical half of DAB-087 is already fixed and cost nothing -- see
        `_build_fts_query`. Non-Latin *search* works today; only non-Latin
        *semantic* retrieval is still blind.
        """
        if (attachment_summary or "").strip():
            return False
        words = re.findall(r"[A-Za-z0-9]+", content_text or "")
        chars = len(re.findall(r"[A-Za-z0-9]", content_text or ""))
        return len(words) < self.embedding_min_words and chars < self.embedding_min_alphanumeric_chars

    def _eligibility_fingerprint(self) -> str:
        """Everything that can change what `_reconcile_embedding_eligibility` decides.

        Four inputs, and the fourth is the one a version constant gets wrong.

        `embedding_model` already carries the dimensions -- it is
        `rag_embedding_model` and `rag_embedding_dimensions` joined by `@` --
        and it belongs here for a reason beyond the eligibility rule: a model
        change resets **every** row to `pending`, and the reconcile is the only
        thing that puts the trivial ones back. Leave it out and one config edit
        bills the entire history.

        The rule itself lives in two places, not one: `_embedding_is_trivial`,
        whose Latin-only character classes DAB-087 exists to widen, and the
        `[Message type: ...]` strip inside `_reconcile_embedding_eligibility`
        that runs when `embedding_eligibility_text` is NULL. That second regex
        genuinely changes answers -- `"hi [Message type: default]"` is not
        trivial before the strip and is after it -- and it is easy to miss.
        Both are covered by hashing their own source rather than a constant,
        because the failure mode of forgetting to bump a constant is a
        permanently and silently unembeddable corpus, while the failure mode of
        hashing a comment change is one extra scan.
        """
        try:
            rules = inspect.getsource(type(self)._embedding_is_trivial) + inspect.getsource(
                type(self)._reconcile_embedding_eligibility
            )
        except (OSError, TypeError):
            # No source to read (frozen or zipped install). Fall back to a value
            # that never matches, so the reconcile runs every boot: slow is the
            # safe direction here, silence is not.
            rules = uuid.uuid4().hex
        material = "\u0000".join(
            (
                self.embedding_model,
                str(self.embedding_min_words),
                str(self.embedding_min_alphanumeric_chars),
                rules,
            )
        )
        return hashlib.sha256(material.encode("utf-8")).hexdigest()

    def _repair_empty_fts(self, conn: sqlite3.Connection) -> None:
        """Rebuild the FTS table when it is empty and the index is not.

        This is the one piece of `_migrate_legacy_database`'s unconditional
        tail that could not simply be skipped when the early return moved above
        it (PPR-02). Two of the three were provably redundant -- the
        embedding-model reset is byte-identical to the one in `_ensure_schema`
        and reports rowcount 0 at every size, and the eligibility reconcile is
        `_ensure_schema`'s own last statement -- but the FTS rebuild is the only
        full rebuild anywhere in the repository (DAB-071), and it was quietly
        repairing an empty FTS table on every boot of every normal install.
        Stale rows there are harmless, because `search_lexical` re-filters on
        `hidden = 0 AND deleted_at IS NULL`; *missing* rows are permanent, and
        measured, removing the rebuild without this took a database from
        5 lexical hits to 0 with no boot able to recover it.

        Only the empty case is repaired, and only when there is something to
        repair, which is what makes it affordable: 0.33 ms flat at 100,000 rows
        against 2,547 ms for the unconditional teardown-and-rebuild. Partial
        drift is still unhandled -- that remains DAB-071.
        """
        if not self.fts_enabled:
            return
        indexed = conn.execute(
            "SELECT EXISTS(SELECT 1 FROM message_index "
            "WHERE hidden = 0 AND deleted_at IS NULL)"
        ).fetchone()[0]
        if not indexed:
            return
        if conn.execute("SELECT EXISTS(SELECT 1 FROM message_search_fts)").fetchone()[0]:
            return
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
        logger.info("Rebuilt an empty message_search_fts from the existing index")

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

    @staticmethod
    def _unit_vector(vector: np.ndarray) -> np.ndarray:
        # The cache holds unit vectors, so a search is one dot product and no
        # per-query norm pass. A zero vector stays zero and scores 0.
        norm = float(np.linalg.norm(vector))
        return vector if norm == 0.0 else vector / norm

    def _cache_upsert(self, message_id: int, guild_id: Optional[int], channel_id: int, vector: np.ndarray) -> None:
        if not self.vector_cache_enabled or vector.size != self.embedding_dimensions:
            return
        unit = self._unit_vector(vector)
        with self._vector_lock:
            if not self._vector_loaded:
                return
            self._cache_remove(message_id); self._ensure_vector_capacity(self._vector_count + 1)
            index = self._vector_count
            self._vector_matrix[index] = unit; self._vector_message_ids[index] = int(message_id)
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
            loaded = self._vector_matrix[:self._vector_count]
            norms = np.linalg.norm(loaded, axis=1, keepdims=True)
            np.divide(loaded, np.where(norms > 0, norms, np.float32(1.0)), out=loaded)
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
    def _fts_terms(query: str, *, drop_stopwords: bool = False) -> list[str]:
        # `\w` rather than `A-Za-z0-9_`: the lexical half of DAB-087.
        #
        # The token class was Latin-only, so a query in any other script
        # produced no tokens at all and this returned None -- the lexical leg
        # was not degraded for those users, it was dead. Measured before the
        # change: "Privet", "marhaba", "annyeonghaseyo", "Geia" and a CJK
        # sentence, in their own scripts, all gave `fts_query=None` and 0 hits,
        # while `message_search_fts` already held their text, correctly
        # tokenised by `unicode61`. The data was there and the query threw it
        # away, so this is retroactive over all existing history with no
        # re-indexing, no FTS rebuild and no re-embedding.
        #
        # It also stops accented Latin being shredded: "naive" with a diaeresis
        # used to split into two two-letter fragments and match neither.
        #
        # NFKC normalisation was measured here and deliberately NOT added. The
        # stored side is not normalised, so normalising only the query takes a
        # full-width search from 1 hit to 0. Doing it correctly means
        # normalising at index time too, which is a full FTS rebuild.
        #
        # The punctuation set is unchanged, so mentions, URLs and paths
        # tokenise exactly as before.
        tokens = re.findall(r"[\w@#./:-]{2,}", query or "")
        if drop_stopwords:
            # Keep them when they are all there is: "what did he do" has to
            # search for something.
            tokens = [token for token in tokens if token.lower() not in _FTS_STOPWORDS] or tokens
        terms = []
        seen = set()
        for token in tokens[:24]:
            cleaned = token.replace('"', '""')
            lowered = cleaned.lower()
            if lowered in seen:
                continue
            seen.add(lowered)
            terms.append(f'"{cleaned}"')
        return terms

    @staticmethod
    def _build_fts_query(query: str) -> Optional[str]:
        """The OR form, unfiltered. `search_lexical` prefers AND; see there."""
        terms = MessageIndexService._fts_terms(query)
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
        terms = self._fts_terms(query, drop_stopwords=self.fts_stopwords_enabled)
        if not terms:
            return []
        # bm25() is unweighted by default, and author_name is a one-token
        # column: its short-field normalisation made a match on the AUTHOR
        # outrank every match in a message BODY. Measured on a 50k corpus, the
        # query "alice" returned 30 messages she had sent and none about her.
        # bm25 is negative and better is more negative, so ORDER BY ASC stays.
        scope_params: list = []
        scope = self._scope_clause(guild_id, channel_id, cross_channel, scope_params)
        exclude = self._exclude_clause(exclude_message_ids or [], scope_params)
        sql = f"""
            SELECT m.*, bm25(message_search_fts, ?, ?, ?) AS lexical_score, 0.0 AS semantic_score
            FROM message_search_fts
            JOIN message_index m ON m.message_id = message_search_fts.rowid
            WHERE message_search_fts MATCH ?
              AND {scope}
              AND m.hidden = 0
              AND m.deleted_at IS NULL
              {exclude}
            ORDER BY lexical_score ASC
            LIMIT ?
            """
        # AND first: joining every token with OR matched 29,385 of 50,000 rows
        # for one ordinary question. OR is the fallback when AND is too thin.
        #
        # Capped at `limit`, because the AND form cannot return more rows than
        # were asked for: a threshold above the caller's limit is unreachable, so
        # the AND query ran, was discarded and OR ran on every single retrieval.
        enough = min(self.fts_min_and_results, limit)
        forms = [" AND ".join(terms)]
        if len(terms) > 1:
            forms.append(" OR ".join(terms))
        try:
            rows = []
            with self._connection() as conn:
                for fts_query in forms:
                    rows = conn.execute(
                        sql, [*self.bm25_weights, fts_query, *scope_params, limit]
                    ).fetchall()
                    if len(rows) >= enough:
                        break
            return [self._row_to_indexed(row) for row in rows]
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
        # 'failed' is a pause, not a tombstone: a terminal failure parks
        # next_retry_at one reset window out and the row returns here on its
        # own. A NULL there on a failed row is a terminal failure recorded
        # before that was true, and is retryable now.
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
                      AND e.embedding_status IN ('pending', 'failed')
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
                # Terminal parks the row for one reset window instead of
                # forever: a rate-limit storm mid-backfill used to leave rows
                # unembeddable until somebody hand-edited SQLite.
                #
                # Policy: each terminal failure doubles the next reset window
                # (24h, 48h, 96h ...), capped at ten doublings, so a permanently
                # bad row costs a handful of embedding calls in total rather than
                # one per window forever, and a transient outage still recovers.
                resets = min(attempts - self.max_embedding_attempts, 10)
                retry_delay_seconds = (
                    self.embedding_retry_reset_hours * 3600.0 * 2 ** resets
                    if terminal
                    else min(60 * (2 ** max(0, attempts - 1)), 3600)
                )
                next_retry_at = (
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
    def _top_k(scores: np.ndarray, k: int) -> np.ndarray:
        """The k highest scores' indices, ties broken by ascending index.

        Everything at or above the k-th value is stable-sorted rather than the
        whole array, which reproduces `argsort(-scores)[:k]` exactly, including
        ties that straddle the cut.
        """
        if k >= scores.size:
            return np.argsort(-scores, kind="stable")[:k]
        cutoff = np.partition(-scores, k - 1)[k - 1]
        candidates = np.flatnonzero(-scores <= cutoff)
        return candidates[np.argsort(-scores[candidates], kind="stable")][:k]

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
            unit_query = query_vector / query_norm
            with self._vector_lock:
                count = self._vector_count
                if not count: return []
                mask = self._vector_channel_ids[:count] == int(channel_id)
                if cross_channel and guild_id is not None:
                    mask = self._vector_guild_ids[:count] == int(guild_id)
                excluded = set(int(value) for value in (exclude_message_ids or []))
                if excluded: mask &= ~np.isin(self._vector_message_ids[:count], list(excluded))
                matching = int(np.count_nonzero(mask))
                if not matching: return []
                # Both operands are unit vectors, so the dot product IS the
                # cosine. Scoring the live slice and masking the SCORES keeps
                # this copy-free: `matrix[positions]` was a 153 MB fancy-index
                # copy at 50k rows, and `matrix[valid]` a second one.
                scores = self._vector_matrix[:count] @ unit_query
                scores[~mask] = -np.inf
                ranked = [(int(self._vector_message_ids[i]), float(scores[i])) for i in self._top_k(scores, min(limit, matching)) if np.isfinite(scores[i]) and scores[i] > 0]
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
        # Scored exactly as the cached path scores: a dot product against the unit
        # query, divided by the stored vector's own norm. Zero-norm vectors score 0.
        unit_query = self._unit_vector(query_vector)
        scored = []
        for row in rows:
            vector = self._decode_vector(row["embedding_vector"])
            if vector is None or vector.size != query_vector.size:
                continue
            norm = float(np.linalg.norm(vector))
            score = 0.0 if norm == 0.0 else float(vector @ unit_query) / norm
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

    @staticmethod
    def _conversation_size(conn: sqlite3.Connection, *, channel_id: int, conversation_id: int) -> int:
        return conn.execute(
            """
            SELECT COUNT(*) FROM message_index
            WHERE channel_id = ? AND conversation_id = ?
              AND hidden = 0 AND deleted_at IS NULL
            """,
            (channel_id, conversation_id),
        ).fetchone()[0]

    def _assign_conversation_inline(
        self,
        conn: sqlite3.Connection,
        *,
        message_id: int,
        channel_id: int,
        reply_to_message_id: Optional[int],
        created_at: datetime,
    ) -> int:
        # Silence gap, size cap and a single-hop reply merge. Participant
        # turnover needs lookahead this path does not have, so
        # recompute_channel_conversations stays the authoritative pass.
        conversation_id = message_id
        previous = conn.execute(
            """
            SELECT conversation_id, created_at
            FROM message_index
            WHERE channel_id = ? AND created_at < ?
              AND hidden = 0 AND deleted_at IS NULL
            ORDER BY created_at DESC
            LIMIT 1
            """,
            (channel_id, created_at.isoformat()),
        ).fetchone()
        if previous is not None and previous["conversation_id"] is not None:
            within_gap = created_at - self._parse_datetime(previous["created_at"]) <= self.conversation_gap
            if within_gap and self._conversation_size(
                conn, channel_id=channel_id, conversation_id=previous["conversation_id"]
            ) < self.conversation_max_messages:
                conversation_id = previous["conversation_id"]

        if reply_to_message_id is None:
            return conversation_id
        target = conn.execute(
            "SELECT channel_id, conversation_id, created_at FROM message_index WHERE message_id = ?",
            (reply_to_message_id,),
        ).fetchone()
        if (
            target is None
            or target["conversation_id"] is None
            or target["conversation_id"] == conversation_id
            or int(target["channel_id"]) != int(channel_id)
        ):
            return conversation_id
        # Both merge bounds, exactly as the batch pass applies them: the cap is
        # checked against the SUM of the two conversations plus this message,
        # because what follows merges them both.
        if created_at - self._parse_datetime(target["created_at"]) >= self.conversation_reply_merge_max:
            return conversation_id
        merged_size = 1 + self._conversation_size(
            conn, channel_id=channel_id, conversation_id=target["conversation_id"]
        ) + self._conversation_size(
            conn, channel_id=channel_id, conversation_id=conversation_id
        )
        if merged_size > self.conversation_max_messages:
            return conversation_id
        # Union both conversations into the older one, which is what
        # _segment_channel_rows does. Moving the reply alone left the same data
        # segmented one way live and another way after a rebuild. The only
        # remaining difference is turnover, which needs lookahead this path does
        # not have, so these rules stay a subset of the batch pass and
        # recompute_channel_conversations stays authoritative.
        conn.execute(
            "UPDATE message_index SET conversation_id = ?"
            " WHERE channel_id = ? AND conversation_id = ?",
            (target["conversation_id"], channel_id, conversation_id),
        )
        return target["conversation_id"]

    def _segment_channel_rows(self, rows: Iterable[tuple]) -> list[tuple[int, int]]:
        """Map one channel's messages, oldest first, to (message_id, conversation_id)."""
        window = self.conversation_turnover_window
        cap = self.conversation_max_messages
        parent: dict[int, int] = {}
        size: dict[int, int] = {}
        order: dict[int, int] = {}
        # message_id -> (conversation at the time, created_at), for reply merges.
        placed: dict[int, tuple[int, datetime]] = {}
        assignments: list[tuple[int, int]] = []

        def find(conversation_id: int) -> int:
            root = conversation_id
            while parent[root] != root:
                root = parent[root]
            while parent[conversation_id] != root:
                parent[conversation_id], conversation_id = root, parent[conversation_id]
            return root

        source = iter(rows)
        upcoming: deque = deque()
        recent_authors: deque = deque(maxlen=window)
        current: Optional[int] = None
        previous_created: Optional[datetime] = None
        sequence = 0
        while True:
            while len(upcoming) < window:
                try:
                    upcoming.append(next(source))
                except StopIteration:
                    break
            if not upcoming:
                break
            lookahead = list(upcoming)[:window]
            next_authors = {row[1] for row in lookahead}
            message_id, author_key, created_at, reply_to = upcoming.popleft()

            start_new = current is None
            if not start_new:
                elapsed = created_at - previous_created
                if elapsed > self.conversation_gap or size[find(current)] >= cap:
                    start_new = True
                elif (
                    # Turnover only counts with a full window either side and a
                    # real pause: without the sub-gap a newcomer joining a live
                    # discussion would split it.
                    len(lookahead) == window
                    and len(recent_authors) == window
                    and elapsed >= self.conversation_turnover_min_gap
                    and not next_authors & set(recent_authors)
                ):
                    start_new = True
            if start_new:
                current = message_id
                parent[current] = current
                size[current] = 0
                order[current] = sequence
            root = find(current)
            size[root] += 1
            assignments.append((message_id, root))
            placed[message_id] = (root, created_at)
            recent_authors.append(author_key)
            previous_created = created_at
            sequence += 1

            if reply_to is not None and reply_to in placed:
                target_root, target_created = placed[reply_to]
                target_root = find(target_root)
                if (
                    target_root != root
                    and created_at - target_created < self.conversation_reply_merge_max
                    and size[target_root] + size[root] <= cap
                ):
                    winner, loser = (
                        (target_root, root) if order[target_root] <= order[root] else (root, target_root)
                    )
                    parent[loser] = winner
                    size[winner] += size[loser]
        return [(message_id, find(root)) for message_id, root in assignments]

    def list_indexed_channel_ids(self) -> list[int]:
        with self._connection() as conn:
            return [
                int(row[0])
                for row in conn.execute(
                    "SELECT DISTINCT channel_id FROM message_index ORDER BY channel_id"
                )
            ]

    def recompute_channel_conversations(self, channel_id: int, *, dry_run: bool = False) -> dict:
        """Authoritative segmentation for one channel; deterministic and idempotent."""
        channel_id = int(channel_id)
        previous_ids: list[Optional[int]] = []

        def stream(cursor):
            for row in cursor:
                previous_ids.append(row["conversation_id"])
                yield (
                    int(row["message_id"]),
                    row["author_key"],
                    self._parse_datetime(row["created_at"]),
                    row["reply_to_message_id"],
                )

        with self._connection() as conn:
            cursor = conn.execute(
                """
                SELECT message_id, COALESCE(author_id, author_name) AS author_key,
                       created_at, reply_to_message_id, conversation_id
                FROM message_index
                WHERE channel_id = ? AND hidden = 0 AND deleted_at IS NULL
                ORDER BY created_at ASC, message_id ASC
                """,
                (channel_id,),
            )
            assignments = self._segment_channel_rows(stream(cursor))

        changed = sum(
            1
            for (_message_id, conversation_id), was in zip(assignments, previous_ids)
            if conversation_id != was
        )
        if assignments and not dry_run:
            with self._connection(transaction=True) as conn:
                conn.executemany(
                    "UPDATE message_index SET conversation_id = ? WHERE message_id = ?",
                    ((conversation_id, message_id) for message_id, conversation_id in assignments),
                )
        return {
            "channel_id": channel_id,
            "messages": len(assignments),
            "conversations": len({conversation_id for _mid, conversation_id in assignments}),
            "changed": changed,
        }

    def get_conversation_window(
        self,
        conversation_id: int,
        *,
        center_message_id: int,
        full_max_messages: int,
        window_messages: int,
        channel_id: Optional[int] = None,
        exclude_message_ids: Optional[Iterable[int]] = None,
    ) -> list[IndexedMessage]:
        if conversation_id is None:
            return []
        try:
            with self._connection() as conn:
                # channel_id has to be bound or idx_message_index_conversation
                # cannot drive the read: without it the query full-scans
                # message_index and sorts into a temp B-tree. The caller knows
                # it; failing that the centre row does; failing that the
                # conversation's own root message, whose id IS the conversation
                # id. A window with no resolvable channel is not worth a scan.
                for candidate in (center_message_id, conversation_id):
                    if channel_id is not None:
                        break
                    row = conn.execute(
                        "SELECT channel_id FROM message_index WHERE message_id = ?",
                        (int(candidate),),
                    ).fetchone()
                    channel_id = None if row is None else int(row["channel_id"])
                if channel_id is None:
                    return []
                rows = conn.execute(
                    """
                    SELECT m.*, 0.0 AS lexical_score, 0.0 AS semantic_score
                    FROM message_index m
                    WHERE m.channel_id = ?
                      AND m.conversation_id = ?
                      AND m.hidden = 0
                      AND m.deleted_at IS NULL
                    ORDER BY m.created_at ASC
                    """,
                    (int(channel_id), int(conversation_id)),
                ).fetchall()
        except Exception as exc:
            logger.error("Conversation window query failed: %s", exc, exc_info=True)
            return []

        messages = [self._row_to_indexed(row) for row in rows]
        if len(messages) > max(1, int(full_max_messages)):
            span = max(1, int(window_messages))
            # An absent centre falls back to the tail, which is the recency
            # default the rest of retrieval uses.
            centre_index = next(
                (
                    index
                    for index, message in enumerate(messages)
                    if message.message_id == int(center_message_id)
                ),
                len(messages) - 1,
            )
            start = max(0, min(centre_index - span // 2, len(messages) - span))
            messages = messages[start:start + span]
        excluded = {int(message_id) for message_id in exclude_message_ids or []}
        return [message for message in messages if message.message_id not in excluded]

    async def get_conversation_window_async(self, conversation_id: int, **kwargs) -> list[IndexedMessage]:
        return await asyncio.to_thread(self.get_conversation_window, conversation_id, **kwargs)

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
