"""
Channel settings service for the Discord Grok Bot.

Manages per-channel settings such as personality/tone stored in SQLite.
"""

import logging
import sqlite3
from datetime import datetime
from typing import Optional

logger = logging.getLogger(__name__)

VALID_PERSONALITIES = {
    "default": "You are a helpful AI assistant. Respond naturally and informatively.",
    "professional": "Respond in a professional, formal tone. Be precise, structured, and business-appropriate. Avoid slang and humor.",
    "casual": "Respond in a casual, friendly tone with humor. Use conversational language, contractions, and feel free to joke around.",
    "sarcastic": "Respond with witty sarcasm and dry humor, but still be helpful. Think of yourself as a clever friend who can't resist a good quip.",
    "academic": "Respond in an academic, scholarly tone. Use precise terminology, cite reasoning, and structure responses like a knowledgeable professor.",
    "friendly": "Respond in a warm, encouraging, and supportive tone. Be enthusiastic and uplifting, like a cheerful friend who genuinely wants to help.",
}


class ChannelSettingsService:
    """Manages per-channel settings persisted in SQLite."""

    def __init__(self, db_path: str = "data/token_usage.db"):
        self.db_path = db_path
        self._ensure_table()

    def _ensure_table(self):
        """Create the channel_settings table if it doesn't exist."""
        try:
            conn = sqlite3.connect(self.db_path)
            conn.execute("""
                CREATE TABLE IF NOT EXISTS channel_settings (
                    channel_id INTEGER PRIMARY KEY,
                    personality TEXT NOT NULL DEFAULT 'default',
                    updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
                )
            """)
            conn.commit()
            conn.close()
            logger.info("Channel settings table ready")
        except Exception as e:
            logger.error(f"Failed to create channel_settings table: {e}")

    def get_personality(self, channel_id: int) -> str:
        """Get the personality setting for a channel. Returns 'default' if not set."""
        try:
            conn = sqlite3.connect(self.db_path)
            cursor = conn.execute(
                "SELECT personality FROM channel_settings WHERE channel_id = ?",
                (channel_id,)
            )
            row = cursor.fetchone()
            conn.close()
            return row[0] if row else "default"
        except Exception as e:
            logger.error(f"Failed to get personality for channel {channel_id}: {e}")
            return "default"

    def get_personality_prompt(self, channel_id: int) -> Optional[str]:
        """Get the system prompt for the channel's personality. Returns None for 'default'."""
        personality = self.get_personality(channel_id)
        if personality == "default":
            return None
        return VALID_PERSONALITIES.get(personality)

    def set_personality(self, channel_id: int, personality: str) -> bool:
        """
        Set the personality for a channel.

        Args:
            channel_id: Discord channel ID
            personality: One of the valid personality names

        Returns:
            True if successful, False otherwise
        """
        if personality not in VALID_PERSONALITIES:
            logger.error(f"Invalid personality: {personality}")
            return False

        try:
            conn = sqlite3.connect(self.db_path)
            conn.execute("""
                INSERT INTO channel_settings (channel_id, personality, updated_at)
                VALUES (?, ?, ?)
                ON CONFLICT(channel_id) DO UPDATE SET
                    personality = excluded.personality,
                    updated_at = excluded.updated_at
            """, (channel_id, personality, datetime.utcnow().isoformat()))
            conn.commit()
            conn.close()
            logger.info(f"Set personality for channel {channel_id} to '{personality}'")
            return True
        except Exception as e:
            logger.error(f"Failed to set personality for channel {channel_id}: {e}")
            return False

    @staticmethod
    def list_personalities() -> dict:
        """Return a dict of personality name -> description."""
        return {name: desc for name, desc in VALID_PERSONALITIES.items()}
