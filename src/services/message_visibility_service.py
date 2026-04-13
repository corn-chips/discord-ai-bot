"""
Message visibility service for the Discord bot.

Stores original content for hidden bot messages so /unhide can restore them.
"""

import logging
import sqlite3
from datetime import datetime
from typing import List, Optional, Tuple

logger = logging.getLogger(__name__)


class MessageVisibilityService:
    """Tracks hidden message content per channel in SQLite."""

    def __init__(self, db_path: str = "data/token_usage.db"):
        self.db_path = db_path
        self._ensure_table()

    def _ensure_table(self):
        """Create the hidden_messages table if it does not exist."""
        try:
            conn = sqlite3.connect(self.db_path)
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS hidden_messages (
                    message_id INTEGER PRIMARY KEY,
                    channel_id INTEGER NOT NULL,
                    guild_id INTEGER,
                    original_content TEXT NOT NULL,
                    hidden_by INTEGER,
                    hidden_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
                )
                """
            )
            conn.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_hidden_messages_channel_hidden_at
                ON hidden_messages (channel_id, hidden_at DESC)
                """
            )
            conn.commit()
            conn.close()
            logger.info("Hidden messages table ready")
        except Exception as exc:
            logger.error(f"Failed to create hidden_messages table: {exc}")

    def save_hidden_message(
        self,
        message_id: int,
        channel_id: int,
        original_content: str,
        hidden_by: Optional[int] = None,
        guild_id: Optional[int] = None,
    ) -> bool:
        """
        Save or update hidden message state.

        Returns:
            True if successful, otherwise False.
        """
        try:
            conn = sqlite3.connect(self.db_path)
            conn.execute(
                """
                INSERT INTO hidden_messages
                    (message_id, channel_id, guild_id, original_content, hidden_by, hidden_at)
                VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT(message_id) DO UPDATE SET
                    channel_id = excluded.channel_id,
                    guild_id = excluded.guild_id,
                    original_content = excluded.original_content,
                    hidden_by = excluded.hidden_by,
                    hidden_at = excluded.hidden_at
                """,
                (
                    message_id,
                    channel_id,
                    guild_id,
                    original_content,
                    hidden_by,
                    datetime.utcnow().isoformat(),
                ),
            )
            conn.commit()
            conn.close()
            return True
        except Exception as exc:
            logger.error(f"Failed to save hidden message {message_id}: {exc}")
            return False

    def get_recent_hidden_messages(
        self,
        channel_id: int,
        limit: int,
    ) -> List[Tuple[int, str, str]]:
        """
        Get the most recently hidden messages for a channel.

        Returns:
            List of tuples: (message_id, original_content, hidden_at), newest first.
        """
        try:
            conn = sqlite3.connect(self.db_path)
            cursor = conn.execute(
                """
                SELECT message_id, original_content, hidden_at
                FROM hidden_messages
                WHERE channel_id = ?
                ORDER BY hidden_at DESC
                LIMIT ?
                """,
                (channel_id, limit),
            )
            rows = cursor.fetchall()
            conn.close()
            return rows
        except Exception as exc:
            logger.error(f"Failed to get hidden messages for channel {channel_id}: {exc}")
            return []

    def remove_hidden_message(self, message_id: int) -> bool:
        """
        Remove a hidden-message record.

        Returns:
            True if a row was deleted, otherwise False.
        """
        try:
            conn = sqlite3.connect(self.db_path)
            cursor = conn.execute(
                "DELETE FROM hidden_messages WHERE message_id = ?",
                (message_id,),
            )
            deleted = cursor.rowcount > 0
            conn.commit()
            conn.close()
            return deleted
        except Exception as exc:
            logger.error(f"Failed to remove hidden message {message_id}: {exc}")
            return False
