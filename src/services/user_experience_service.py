"""
User Experience Service for enhanced Discord bot interactions.

This module provides enhanced user feedback including typing indicators,
rich embeds, reaction-based feedback, and progress updates.
"""

import asyncio
import logging
from datetime import datetime, timedelta, timezone
from enum import Enum
from typing import Dict, Any, Tuple

import discord

from ..config import BotConfig


logger = logging.getLogger(__name__)


class Status(Enum):
    """Status types for embed creation."""
    SUCCESS = "success"
    ERROR = "error"
    WARNING = "warning"
    INFO = "info"
    PROCESSING = "processing"


class ReactionType(Enum):
    """Types of reaction feedback."""
    SUCCESS = "✅"
    ERROR = "❌"
    WARNING = "⚠️"
    PROCESSING = "⏳"
    THINKING = "🤔"
    COMPLETED = "✨"


class UserExperienceService:
    """
    Service for providing enhanced user experience features.
    
    Implements requirements 4.1, 4.2, 4.3 for typing indicators,
    rich Discord embeds, and reaction-based feedback.
    """
    
    def __init__(self, config: BotConfig):
        """
        Initialize the user experience service.
        
        Args:
            config: Bot configuration containing UX settings
        """
        self.config = config
        self.active_typing_tasks: Dict[int, Tuple[Any, datetime]] = {}
        self._typing_entry_ttl = timedelta(minutes=5)
        
        # Color mapping for different status types
        self.status_colors = {
            Status.SUCCESS: discord.Color.green(),
            Status.ERROR: discord.Color.red(),
            Status.WARNING: discord.Color.orange(),
            Status.INFO: discord.Color.blue(),
            Status.PROCESSING: discord.Color.purple()
        }
        
        # Emoji mapping for status types
        self.status_emojis = {
            Status.SUCCESS: "✅",
            Status.ERROR: "❌",
            Status.WARNING: "⚠️",
            Status.INFO: "ℹ️",
            Status.PROCESSING: "⏳"
        }
    
    def _task_error_handler(self, task: asyncio.Task) -> None:
        """Log unhandled exceptions from detached tasks."""
        if task.cancelled():
            return
        try:
            exc = task.exception()
        except Exception as callback_exc:
            logger.error(f"Failed to inspect detached task exception: {callback_exc}")
            return
        if exc:
            logger.error(
                f"Unhandled detached task exception in UX service: {exc}",
                exc_info=(type(exc), exc, exc.__traceback__),
            )

    async def _close_typing_entry(self, entry: Any) -> None:
        """Close or cancel a stored typing entry."""
        if isinstance(entry, asyncio.Task):
            entry.cancel()
            try:
                await entry
            except asyncio.CancelledError:
                pass
        else:
            await entry.__aexit__(None, None, None)

    async def _sweep_stale_typing_entries(self) -> None:
        """Remove stale typing entries to avoid unbounded growth."""
        cutoff = datetime.now(timezone.utc) - self._typing_entry_ttl
        stale_channel_ids = [
            channel_id
            for channel_id, (_entry, created_at) in self.active_typing_tasks.items()
            if created_at < cutoff
        ]

        for channel_id in stale_channel_ids:
            entry, _created_at = self.active_typing_tasks.pop(channel_id, (None, None))
            if entry is None:
                continue
            try:
                await self._close_typing_entry(entry)
            except Exception as e:
                logger.debug(f"Failed to clean stale typing indicator for channel {channel_id}: {e}")

    async def cleanup_typing_indicators(self):
        """Clean up any remaining typing indicators."""
        for channel_id, (task_or_context, _created_at) in list(self.active_typing_tasks.items()):
            try:
                await self._close_typing_entry(task_or_context)
            except Exception as e:
                logger.error(f"Error cleaning up typing indicator: {e}")
        
        self.active_typing_tasks.clear()
