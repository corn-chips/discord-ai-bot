"""
Build prompt-ready context packs from hybrid retrieval candidates.
"""

from datetime import datetime, timezone
from typing import Iterable, Optional

from ..models.data_models import MessageContext
from .message_index_service import IndexedMessage


PIN_MESSAGE_ID_OFFSET = 9_000_000_000_000_000_000

#: Retrieval slots that pinned memory may never consume (DAB-073).
#:
#: Pins previously took every slot but one, so a channel with enough pinned
#: memories drove `available_retrieval_slots` to zero: the index was queried,
#: scored and discarded, the model answered from pins alone, and the retrieval
#: was paid for regardless. Two slots is enough that a reply always has some
#: conversational grounding, and small enough that pins keep clear priority.
MIN_RETRIEVAL_SLOTS = 2


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

        pinned = list(pinned_context or [])
        retrieved = list(retrieved_context or [])
        anchors = [ctx for ctx in retrieved if ctx.retrieval_source == "reply_anchor"]
        ordinary = [ctx for ctx in retrieved if ctx.retrieval_source != "reply_anchor"]

        pin_slots, anchor_slots = self._priority_slot_counts(
            len(pinned),
            len(anchors),
            max_messages,
        )
        pinned = pinned[:pin_slots]
        anchors = anchors[:anchor_slots]

        seen_ids = {ctx.message_id for ctx in [*pinned, *anchors]}
        deduped_retrieved = []
        for ctx in ordinary:
            if ctx.message_id in seen_ids:
                continue
            seen_ids.add(ctx.message_id)
            deduped_retrieved.append(ctx)

        remaining_slots = max(0, max_messages - len(pinned) - len(anchors))
        if remaining_slots:
            deduped_retrieved = deduped_retrieved[:remaining_slots]
        else:
            deduped_retrieved = []

        return pinned + anchors + deduped_retrieved

    @staticmethod
    def _priority_slot_counts(pin_count: int, anchor_count: int, max_messages: int) -> tuple[int, int]:
        """Reserve room for a reply anchor without discarding pinned memory."""
        max_messages = max(1, int(max_messages or 1))
        reserve_anchor = 1 if pin_count and anchor_count and max_messages > 1 else 0
        pin_slots = min(pin_count, max_messages - reserve_anchor)
        anchor_slots = min(anchor_count, max_messages - pin_slots)
        return pin_slots, anchor_slots

    def available_retrieval_slots(
        self,
        *,
        pinned_context: list[MessageContext],
        reply_context: list[MessageContext],
        max_messages: int,
    ) -> int:
        """Return slots left after prioritized pins and reply anchors.

        This is the *pre-retrieval budget*, so it carries a floor (DAB-073).
        Pins may consume every packing slot -- that is a deliberate priority
        decision made in `build_context_pack` once the candidates are known --
        but they must not drive this number to zero, because zero here means the
        index is never searched at all. A channel with enough pinned memories
        therefore answered from pins alone, with the conversation invisible to
        it, and the operator still paid for the embedding call that produced
        nothing.

        The floor yields at small budgets: with `max_messages` of 1 or 2 there is
        nothing useful to divide, and pins keep absolute priority.
        """
        max_messages = max(1, int(max_messages or 1))
        pin_slots, anchor_slots = self._priority_slot_counts(
            len(pinned_context or []),
            len(reply_context or []),
            max_messages,
        )
        remaining = max(0, max_messages - pin_slots - anchor_slots)

        floor = min(MIN_RETRIEVAL_SLOTS, max(0, max_messages - 1))
        return max(remaining, floor)
