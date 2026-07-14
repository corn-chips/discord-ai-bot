"""Discord message mutation handlers for the persistent RAG index."""

from __future__ import annotations

import logging
from typing import Any, Callable, Iterable

import discord


logger = logging.getLogger(__name__)


class RagEventCoordinator:
    """Keep indexed Discord content aligned with edit and delete events."""

    def __init__(
        self,
        *,
        message_index_service: Any,
        rag_enabled: Callable[[], bool],
        index_bot_responses: Callable[[], bool],
        get_bot_user: Callable[[], Any],
    ) -> None:
        self.message_index_service = message_index_service
        self.rag_enabled = rag_enabled
        self.index_bot_responses = index_bot_responses
        self.get_bot_user = get_bot_user

    async def handle_raw_delete(self, message_id: int) -> None:
        """Mark one deleted Discord message as unavailable for retrieval."""
        if not self.rag_enabled():
            return
        try:
            await self.message_index_service.mark_deleted_async(message_id)
        except Exception as exc:
            logger.debug("Failed to mark deleted message %s in RAG index: %s", message_id, exc)

    async def handle_raw_bulk_delete(self, message_ids: Iterable[int]) -> None:
        """Mark bulk-deleted messages serially so one failure cannot stop later IDs."""
        if not self.rag_enabled():
            return
        for message_id in message_ids:
            try:
                await self.message_index_service.mark_deleted_async(message_id)
            except Exception as exc:
                logger.debug(
                    "Failed to mark bulk-deleted message %s in RAG index: %s",
                    message_id,
                    exc,
                )

    async def handle_message_edit(
        self,
        before: discord.Message,
        after: discord.Message,
    ) -> None:
        """Refresh changed text or remove content no longer eligible for indexing."""
        if not self.rag_enabled():
            return

        bot_user = self.get_bot_user()
        author = getattr(after, "author", None)
        if getattr(author, "bot", False):
            if not self.index_bot_responses():
                return
            if not bot_user or getattr(author, "id", None) != bot_user.id:
                return
            # Paginator navigation and timeout only mutate embeds/components.
            # Preserve the canonical generated response when text is unchanged.
            if before.content == after.content:
                return

        try:
            indexed = await self.message_index_service.index_discord_message_async(
                after,
                include_bot_user_id=bot_user.id if bot_user else None,
            )
            if not indexed:
                await self.message_index_service.mark_deleted_async(after.id)
        except Exception as exc:
            logger.debug("Failed to refresh edited message %s in RAG index: %s", after.id, exc)
