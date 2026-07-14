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
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Iterable, Optional

from ..models.data_models import MessageContext
from .context_collector import ContextCollector
from .sqlite_utils import sqlite_connection, sqlite_transaction

logger = logging.getLogger(__name__)


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


class MessageIndexService:
    """SQLite-backed message index with FTS5 and local embedding storage."""

    def __init__(self, db_path: str = "data/token_usage.db", embedding_model: str = "gemini-embedding-2"):
        self.db_path = Path(db_path).expanduser()
        self.embedding_model = embedding_model
        self.max_embedding_attempts = 3
        self.fts_enabled = False
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._ensure_schema()

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
                        content_hash TEXT NOT NULL,
                        hidden INTEGER NOT NULL DEFAULT 0,
                        deleted_at TEXT
                    )
                    """
                )
                conn.execute(
                    """
                    CREATE INDEX IF NOT EXISTS idx_message_index_scope_time
                    ON message_index (guild_id, channel_id, created_at DESC)
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
        hidden: Optional[bool] = None,
    ) -> bool:
        content_text = self._normalize_text(content_text)
        if not content_text:
            return False

        content_hash = self._hash_text(content_text)
        created_at_iso = created_at.isoformat()
        indexed_at = self._now_iso()
        try:
            with self._connection(transaction=True) as conn:
                existing = conn.execute(
                    "SELECT content_hash, hidden, deleted_at FROM message_index WHERE message_id = ?",
                    (message_id,),
                ).fetchone()
                content_changed = existing is None or existing["content_hash"] != content_hash
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
                        attachment_summary, content_hash, hidden, deleted_at
                    )
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, NULL)
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
                            WHEN message_embeddings.content_hash != excluded.content_hash THEN excluded.embedding_status
                            ELSE message_embeddings.embedding_status
                        END,
                        embedding_vector = CASE
                            WHEN message_embeddings.content_hash != excluded.content_hash THEN NULL
                            ELSE message_embeddings.embedding_vector
                        END,
                        content_hash = excluded.content_hash,
                        embedded_at = CASE
                            WHEN message_embeddings.content_hash != excluded.content_hash THEN NULL
                            ELSE message_embeddings.embedded_at
                        END,
                        last_error = CASE
                            WHEN message_embeddings.content_hash != excluded.content_hash THEN NULL
                            ELSE message_embeddings.last_error
                        END,
                        embedding_attempts = CASE
                            WHEN message_embeddings.content_hash != excluded.content_hash THEN 0
                            ELSE message_embeddings.embedding_attempts
                        END,
                        next_retry_at = CASE
                            WHEN message_embeddings.content_hash != excluded.content_hash THEN NULL
                            ELSE message_embeddings.next_retry_at
                        END
                    """,
                    (
                        message_id,
                        self.embedding_model,
                        "pending" if content_changed and not is_deleted else "done",
                        content_hash,
                    ),
                )
            return True
        except Exception as exc:
            logger.error("Failed to index message %s: %s", message_id, exc, exc_info=True)
            return False

    async def backfill_channel(
        self,
        channel,
        *,
        limit: int,
        include_bot_user_id: Optional[int] = None,
    ) -> int:
        indexed_count = 0
        try:
            async for historical in channel.history(limit=max(0, limit)):
                if await self.index_discord_message_async(
                    historical,
                    include_bot_user_id=include_bot_user_id,
                ):
                    indexed_count += 1
        except Exception as exc:
            logger.warning("RAG backfill failed for channel %s: %s", getattr(channel, "id", "unknown"), exc)
        return indexed_count

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

    def get_pending_embeddings(self, limit: int = 16) -> list[tuple[int, str, str]]:
        try:
            with self._connection() as conn:
                rows = conn.execute(
                    """
                    SELECT m.message_id, m.author_name, m.created_at, m.content_text, e.content_hash
                    FROM message_embeddings e
                    JOIN message_index m ON m.message_id = e.message_id
                    WHERE e.embedding_model = ?
                      AND e.embedding_status = 'pending'
                      AND (e.next_retry_at IS NULL OR e.next_retry_at <= ?)
                      AND m.hidden = 0
                      AND m.deleted_at IS NULL
                    ORDER BY m.created_at DESC
                    LIMIT ?
                    """,
                    (self.embedding_model, self._now_iso(), limit),
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

    async def get_pending_embeddings_async(self, limit: int = 16) -> list[tuple[int, str, str]]:
        return await asyncio.to_thread(self.get_pending_embeddings, limit)

    def store_embedding(self, message_id: int, vector: list[float], content_hash: str) -> bool:
        try:
            vector_json = json.dumps([float(value) for value in vector], separators=(",", ":"))
            with self._connection(transaction=True) as conn:
                conn.execute(
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
                    (vector_json, self._now_iso(), message_id, content_hash),
                )
            return True
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
        if not query_embedding:
            return []
        params: list = []
        scope = self._scope_clause(guild_id, channel_id, cross_channel, params)
        exclude = self._exclude_clause(exclude_message_ids or [], params)
        try:
            with self._connection() as conn:
                rows = conn.execute(
                    f"""
                    SELECT m.*, e.embedding_vector, 0.0 AS lexical_score, 0.0 AS semantic_score
                    FROM message_embeddings e
                    JOIN message_index m ON m.message_id = e.message_id
                    WHERE e.embedding_model = ?
                      AND e.embedding_status = 'done'
                      AND e.embedding_vector IS NOT NULL
                      AND {scope}
                      AND m.hidden = 0
                      AND m.deleted_at IS NULL
                      {exclude}
                    """,
                    [self.embedding_model, *params],
                ).fetchall()
            scored: list[IndexedMessage] = []
            for row in rows:
                try:
                    vector = json.loads(row["embedding_vector"])
                except (TypeError, json.JSONDecodeError):
                    continue
                message = self._row_to_indexed(row)
                message.semantic_score = self._cosine_similarity(query_embedding, vector)
                if message.semantic_score > 0:
                    scored.append(message)
            scored.sort(key=lambda item: item.semantic_score, reverse=True)
            return scored[:limit]
        except Exception as exc:
            logger.error("Semantic RAG search failed: %s", exc, exc_info=True)
            return []

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

    async def get_messages_by_ids_async(self, message_ids: Iterable[int]) -> list[IndexedMessage]:
        return await asyncio.to_thread(self.get_messages_by_ids, message_ids)

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
    ) -> None:
        try:
            with self._connection(transaction=True) as conn:
                conn.execute(
                    """
                    INSERT INTO message_retrieval_events (
                        created_at, guild_id, channel_id, user_id, query_length,
                        selected_message_ids, fallback_reason, latency_ms
                    )
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?)
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
            return {
                "messages": total,
                "embedded": embedded,
                "pending_embeddings": pending,
                "failed_embeddings": failed,
                "fts_enabled": self.fts_enabled,
                "embedding_model": self.embedding_model,
            }
        except Exception as exc:
            logger.error("Failed to load RAG status: %s", exc, exc_info=True)
            return {
                "messages": 0,
                "embedded": 0,
                "pending_embeddings": 0,
                "failed_embeddings": 0,
                "fts_enabled": self.fts_enabled,
                "embedding_model": self.embedding_model,
            }

    async def get_status_async(self, *, channel_id: Optional[int] = None) -> dict:
        return await asyncio.to_thread(self.get_status, channel_id=channel_id)
