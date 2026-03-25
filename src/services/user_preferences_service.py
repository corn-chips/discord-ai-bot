"""
User preferences service for the Discord Grok Bot.

Manages per-user preferences (preferred model, language) stored in SQLite.
"""

import logging
import sqlite3
from dataclasses import dataclass
from datetime import datetime
from typing import Optional

logger = logging.getLogger(__name__)


@dataclass
class UserPreferences:
    """Per-user preference data."""
    user_id: int
    preferred_model: Optional[str] = None
    preferred_language: Optional[str] = None


class UserPreferencesService:
    """Manages per-user preferences persisted in SQLite."""

    VALID_MODELS = [
        "gemini-2.5-flash",
        "gemini-2.5-flash-lite",
        "gemini-2.5-pro",
        "gemini-2.0-flash-exp",
        "gemini-2.0-flash-lite",
    ]

    VALID_LANGUAGES = [
        "english", "spanish", "french", "german", "italian",
        "portuguese", "russian", "japanese", "korean", "chinese",
        "arabic", "hindi", "dutch", "swedish", "polish",
        "turkish", "vietnamese", "thai", "indonesian", "auto",
    ]

    def __init__(self, db_path: str = "data/token_usage.db"):
        self.db_path = db_path
        self._ensure_table()

    def _ensure_table(self):
        """Create the user_preferences table if it doesn't exist."""
        try:
            conn = sqlite3.connect(self.db_path)
            conn.execute("""
                CREATE TABLE IF NOT EXISTS user_preferences (
                    user_id INTEGER PRIMARY KEY,
                    preferred_model TEXT,
                    preferred_language TEXT,
                    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
                )
            """)
            conn.commit()
            conn.close()
            logger.info("User preferences table ready")
        except Exception as e:
            logger.error(f"Failed to create user_preferences table: {e}")

    def get_preferences(self, user_id: int) -> UserPreferences:
        """Get preferences for a user. Returns defaults if not set."""
        try:
            conn = sqlite3.connect(self.db_path)
            cursor = conn.execute(
                "SELECT preferred_model, preferred_language FROM user_preferences WHERE user_id = ?",
                (user_id,)
            )
            row = cursor.fetchone()
            conn.close()
            if row:
                return UserPreferences(
                    user_id=user_id,
                    preferred_model=row[0],
                    preferred_language=row[1],
                )
            return UserPreferences(user_id=user_id)
        except Exception as e:
            logger.error(f"Failed to get preferences for user {user_id}: {e}")
            return UserPreferences(user_id=user_id)

    def set_model(self, user_id: int, model: str) -> bool:
        """Set preferred model for a user."""
        if model not in self.VALID_MODELS:
            return False
        return self._upsert(user_id, "preferred_model", model)

    def set_language(self, user_id: int, language: str) -> bool:
        """Set preferred response language for a user."""
        if language.lower() not in self.VALID_LANGUAGES:
            return False
        return self._upsert(user_id, "preferred_language", language.lower())

    def clear_preferences(self, user_id: int) -> bool:
        """Clear all preferences for a user."""
        try:
            conn = sqlite3.connect(self.db_path)
            conn.execute("DELETE FROM user_preferences WHERE user_id = ?", (user_id,))
            conn.commit()
            conn.close()
            logger.info(f"Cleared preferences for user {user_id}")
            return True
        except Exception as e:
            logger.error(f"Failed to clear preferences for user {user_id}: {e}")
            return False

    def _upsert(self, user_id: int, column: str, value: str) -> bool:
        """Insert or update a single preference column."""
        # Whitelist column names to prevent SQL injection
        if column not in ("preferred_model", "preferred_language"):
            return False
        try:
            now = datetime.utcnow().isoformat()
            conn = sqlite3.connect(self.db_path)
            conn.execute(f"""
                INSERT INTO user_preferences (user_id, {column}, created_at, updated_at)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(user_id) DO UPDATE SET
                    {column} = excluded.{column},
                    updated_at = excluded.updated_at
            """, (user_id, value, now, now))
            conn.commit()
            conn.close()
            logger.info(f"Set {column}={value} for user {user_id}")
            return True
        except Exception as e:
            logger.error(f"Failed to set {column} for user {user_id}: {e}")
            return False
