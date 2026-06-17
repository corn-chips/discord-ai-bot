"""
Build prompt-ready context packs from hybrid retrieval candidates.
"""

from datetime import datetime, timezone
from typing import Iterable, Optional

from ..models.data_models import MessageContext
from .message_index_service import IndexedMessage


PIN_MESSAGE_ID_OFFSET = 9_000_000_000_000_000_000


class ContextPackBuilder:
    """Converts retrieval results into MessageContext objects with provenance."""

    @staticmethod
    def build_pinned_context(
        pins: Iterable[tuple],
        *,
        channel_id: int,
    ) -> list[MessageContext]:
        contexts: list[MessageContext] = []
        for pin_id, content, author_name, pinned_by, pinned_at in pins or []:
            try:
                timestamp = datetime.fromisoformat(str(pinned_at))
            except (TypeError, ValueError):
                timestamp = datetime.now(timezone.utc)
            if timestamp.tzinfo is None:
                timestamp = timestamp.replace(tzinfo=timezone.utc)

            contexts.append(
                MessageContext(
                    content=f"[Pin #{pin_id}] {content}",
                    author=f"Pinned memory from {author_name}",
                    timestamp=timestamp,
                    message_id=PIN_MESSAGE_ID_OFFSET + int(pin_id),
                    channel_id=channel_id,
                    retrieval_source="pin",
                    retrieval_score=1.0,
                    retrieval_reason=f"Persistent channel memory pinned by {pinned_by}",
                    is_pinned_memory=True,
                )
            )
        return contexts

    @staticmethod
    def build_message_context(
        message: IndexedMessage,
        *,
        source: str,
        score: float,
        reason: Optional[str] = None,
    ) -> MessageContext:
        return message.to_context(
            retrieval_source=source,
            retrieval_score=score,
            retrieval_reason=reason or source,
        )

    def build_context_pack(
        self,
        *,
        pinned_context: list[MessageContext],
        retrieved_context: list[MessageContext],
        max_messages: int,
    ) -> list[MessageContext]:
        """Return a bounded context pack with pins preserved ahead of retrieved messages."""
        max_messages = max(1, int(max_messages or 1))

        pinned = list(pinned_context or [])[:max_messages]
        retrieved = list(retrieved_context or [])

        seen_ids = {ctx.message_id for ctx in pinned}
        deduped_retrieved = []
        for ctx in retrieved:
            if ctx.message_id in seen_ids:
                continue
            seen_ids.add(ctx.message_id)
            deduped_retrieved.append(ctx)

        remaining_slots = max(0, max_messages - len(pinned))
        if remaining_slots:
            deduped_retrieved = deduped_retrieved[:remaining_slots]
        else:
            deduped_retrieved = []

        return pinned + deduped_retrieved
