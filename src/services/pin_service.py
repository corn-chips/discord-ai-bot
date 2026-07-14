"""
Pin service for the Discord bot.

Manages per-channel pinned messages stored in SQLite. Pinned messages
are injected into the AI prompt so the bot always remembers them.
"""

import logging
from datetime import datetime
from typing import List, Optional, Tuple

from .sqlite_utils import sqlite_connection, sqlite_transaction

logger = logging.getLogger(__name__)


class PinService:
    """Manages per-channel pinned messages persisted in SQLite."""

    def __init__(self, db_path: str = "data/token_usage.db"):
        self.db_path = db_path
        self._ensure_table()

    def _ensure_table(self):
        """Create the pinned_messages table if it doesn't exist."""
        try:
            with sqlite_transaction(self.db_path) as conn:
                conn.execute("""
                    CREATE TABLE IF NOT EXISTS pinned_messages (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        channel_id INTEGER NOT NULL,
                        guild_id INTEGER,
                        content TEXT NOT NULL,
                        author_name TEXT NOT NULL,
                        pinned_by TEXT NOT NULL,
                        pinned_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
                        message_id INTEGER
                    )
                """)
                conn.execute("""
                    CREATE INDEX IF NOT EXISTS idx_pinned_channel
                    ON pinned_messages (channel_id)
                """)
            logger.info("Pinned messages table ready")
        except Exception as e:
            logger.error(f"Failed to create pinned_messages table: {e}")

    def add_pin(
        self,
        channel_id: int,
        content: str,
        author_name: str,
        pinned_by: str,
        guild_id: Optional[int] = None,
        message_id: Optional[int] = None,
    ) -> Optional[int]:
        """
        Pin a message for a channel.

        Returns:
            The pin ID if successful, None otherwise.
        """
        try:
            with sqlite_transaction(self.db_path) as conn:
                cursor = conn.execute(
                    """
                    INSERT INTO pinned_messages
                        (channel_id, guild_id, content, author_name, pinned_by, pinned_at, message_id)
                    VALUES (?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        channel_id,
                        guild_id,
                        content,
                        author_name,
                        pinned_by,
                        datetime.utcnow().isoformat(),
                        message_id,
                    ),
                )
                pin_id = cursor.lastrowid
            logger.info(f"Pinned message #{pin_id} in channel {channel_id}")
            return pin_id
        except Exception as e:
            logger.error(f"Failed to pin message in channel {channel_id}: {e}")
            return None

    def get_pins(self, channel_id: int) -> List[Tuple[int, str, str, str, str]]:
        """
        Get all pinned messages for a channel.

        Returns:
            List of (id, content, author_name, pinned_by, pinned_at) tuples.
        """
        try:
            with sqlite_connection(self.db_path) as conn:
                cursor = conn.execute(
                    """
                    SELECT id, content, author_name, pinned_by, pinned_at
                    FROM pinned_messages
                    WHERE channel_id = ?
                    ORDER BY pinned_at ASC
                    """,
                    (channel_id,),
                )
                rows = cursor.fetchall()
            return rows
        except Exception as e:
            logger.error(f"Failed to get pins for channel {channel_id}: {e}")
            return []

    def delete_pin(self, pin_id: int, channel_id: int) -> bool:
        """
        Delete a pinned message by ID (scoped to channel for safety).

        Returns:
            True if a row was deleted, False otherwise.
        """
        try:
            with sqlite_transaction(self.db_path) as conn:
                cursor = conn.execute(
                    "DELETE FROM pinned_messages WHERE id = ? AND channel_id = ?",
                    (pin_id, channel_id),
                )
                deleted = cursor.rowcount > 0
            if deleted:
                logger.info(f"Deleted pin #{pin_id} from channel {channel_id}")
            return deleted
        except Exception as e:
            logger.error(f"Failed to delete pin #{pin_id}: {e}")
            return False

    def get_pins_for_prompt(self, channel_id: int) -> Optional[str]:
        """
        Format pinned messages for injection into the AI prompt.

        Returns:
            Formatted string of pinned messages, or None if no pins exist.
        """
        pins = self.get_pins(channel_id)
        if not pins:
            return None

        lines = ["=== PINNED MEMORIES FOR THIS CHANNEL ==="]
        for pin_id, content, author_name, _pinned_by, _pinned_at in pins:
            lines.append(f"[Pin #{pin_id}] {author_name}: {content}")
        lines.append("=== END PINNED MEMORIES ===")
        lines.append(
            "IMPORTANT: The above pinned messages are persistent memories for this channel. "
            "Always keep them in mind when responding."
        )
        return "\n".join(lines)
