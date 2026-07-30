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

        # DAB-065. `False` and an exception used to mean the same thing here,
        # and the difference between them is the whole defect. `False` means the
        # CONTENT is no longer indexable, which is a real reason to tombstone
        # the row. An exception means the DATABASE was unavailable, which says
        # nothing about the message -- and tombstoning on it is unrecoverable:
        # no code path clears `deleted_at`, every later re-index still deletes
        # the FTS row and forces embedding_status='skipped', and /rag backfill
        # goes through the same write. The message keeps its content faithfully
        # updated while being invisible to lexical and semantic retrieval, for
        # the life of the database.
        try:
            indexed = await self.message_index_service.index_discord_message_async(
                after,
                include_bot_user_id=bot_user.id if bot_user else None,
            )
        except Exception as exc:
            logger.warning(
                "Could not re-index edited message %s; leaving its existing index "
                "row untouched rather than reading a write failure as a deletion: %s",
                after.id,
                exc,
                exc_info=True,
            )
            return

        if indexed:
            return

        try:
            await self.message_index_service.mark_deleted_async(after.id)
        except Exception as exc:
            logger.warning(
                "Could not tombstone message %s whose content became ineligible: %s",
                after.id,
                exc,
                exc_info=True,
            )
