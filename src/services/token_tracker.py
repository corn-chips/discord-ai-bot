"""Token usage tracking backed by SQLite."""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional

from .sqlite_utils import sqlite_connection, sqlite_transaction


logger = logging.getLogger(__name__)


@dataclass
class TokenLeaderboardEntry:
    """Aggregated token usage info for a guild member."""

    user_id: int
    username: str
    guild_id: int
    guild_name: Optional[str]
    input_tokens: int
    output_tokens: int
    total_tokens: int
    request_count: int


class TokenTracker:
    """Persists per-request token usage information and exposes leaderboard queries."""

    def __init__(self, db_path: str):
        self._db_path = Path(db_path).expanduser()
        if not self._db_path.parent.exists():
            self._db_path.parent.mkdir(parents=True, exist_ok=True)
        self._initialize_db()

    def _initialize_db(self) -> None:
        with sqlite_transaction(self._db_path) as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS token_usage (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id INTEGER NOT NULL,
                    username TEXT NOT NULL,
                    guild_id INTEGER NOT NULL,
                    guild_name TEXT,
                    input_tokens INTEGER NOT NULL,
                    output_tokens INTEGER NOT NULL,
                    total_tokens INTEGER NOT NULL,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
                """
            )
            conn.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_token_usage_guild_user
                ON token_usage (guild_id, user_id)
                """
            )
            conn.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_token_usage_created
                ON token_usage (created_at DESC)
                """
            )
        logger.info("Token usage database ready at %s", self._db_path)

    async def record_usage(
        self,
        *,
        user_id: int,
        username: str,
        guild_id: int,
        guild_name: Optional[str],
        input_tokens: int,
        output_tokens: int,
        total_tokens: int,
    ) -> None:
        """Persist a single usage event asynchronously."""

        if total_tokens < 0 or input_tokens < 0 or output_tokens < 0:
            logger.warning("Skipping token usage with negative values (user_id=%s)", user_id)
            return

        await asyncio.to_thread(
            self._record_usage_sync,
            user_id,
            username,
            guild_id,
            guild_name,
            input_tokens,
            output_tokens,
            total_tokens,
        )

    def _record_usage_sync(
        self,
        user_id: int,
        username: str,
        guild_id: int,
        guild_name: Optional[str],
        input_tokens: int,
        output_tokens: int,
        total_tokens: int,
    ) -> None:
        with sqlite_transaction(self._db_path) as conn:
            conn.execute(
                """
                INSERT INTO token_usage (
                    user_id,
                    username,
                    guild_id,
                    guild_name,
                    input_tokens,
                    output_tokens,
                    total_tokens
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    int(user_id),
                    username,
                    int(guild_id),
                    guild_name,
                    int(input_tokens),
                    int(output_tokens),
                    int(total_tokens),
                ),
            )

    async def get_top_users(self, guild_id: int, limit: int = 10) -> List[TokenLeaderboardEntry]:
        """Return the top token consumers for a guild."""

        rows = await asyncio.to_thread(self._get_top_users_sync, guild_id, limit)
        return [
            TokenLeaderboardEntry(
                user_id=row[0],
                username=row[1],
                guild_id=row[2],
                guild_name=row[3],
                input_tokens=row[4],
                output_tokens=row[5],
                total_tokens=row[6],
                request_count=row[7],
            )
            for row in rows
        ]

    def _get_top_users_sync(self, guild_id: int, limit: int) -> list[tuple]:
        with sqlite_connection(self._db_path) as conn:
            cursor = conn.execute(
                """
                SELECT
                    user_id,
                    username,
                    guild_id,
                    guild_name,
                    COALESCE(SUM(input_tokens), 0) AS input_tokens,
                    COALESCE(SUM(output_tokens), 0) AS output_tokens,
                    COALESCE(SUM(total_tokens), 0) AS total_tokens,
                    COUNT(*) AS request_count
                FROM token_usage
                WHERE guild_id = ?
                GROUP BY user_id, username, guild_id, guild_name
                ORDER BY total_tokens DESC, username ASC
                LIMIT ?
                """,
                (int(guild_id), int(limit)),
            )
            return cursor.fetchall()
