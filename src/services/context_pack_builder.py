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

#: Share of a positive `char_budget` that pins and anchors may never consume.
#: The character form of the DAB-073 floor: a pack bounded by characters must
#: still leave retrieval something to spend, for the same reason.
MIN_RETRIEVAL_CHAR_SHARE = 0.25

#: Per-message cost of the `[CTX_MSG_nnn | message_id=... | time=...]` prefix
#: that `GeminiClient.format_prompt` emits. Measured at ~96 characters for a
#: snowflake id and an ISO timestamp.
RENDER_OVERHEAD_CHARS = 96

#: The one default for `rag_context_char_budget`, matching `BotConfig`. Every
#: reader that falls back to a literal must fall back to this.
DEFAULT_CONTEXT_CHAR_BUDGET = 8000


def context_cost_chars(ctx: MessageContext) -> int:
    """What one message costs in the prompt: body, author and the render prefix.

    The single accounting. The packer spends it and `format_prompt`'s final
    guard spends it, and they have to charge the same thing or whichever bound
    is the smaller of the two is the only one that ever fires.
    """
    return len(ctx.content or "") + len(ctx.author or "") + RENDER_OVERHEAD_CHARS


def group_conversation_blocks(messages: Iterable[MessageContext]) -> list[list[MessageContext]]:
    """Group messages into chronological conversation blocks, best-effort.

    A message with no `conversation_id` is a block of one. Shared with
    `GeminiClient.format_prompt`, which must render exactly the blocks the packer
    admitted or the character accounting and the rendering disagree.
    """
    grouped: dict[tuple, list[MessageContext]] = {}
    for ctx in messages:
        conversation_id = getattr(ctx, "conversation_id", None)
        key = ("msg", ctx.message_id) if conversation_id is None else ("conv", conversation_id)
        grouped.setdefault(key, []).append(ctx)
    return [
        sorted(block, key=lambda ctx: (ctx.timestamp, ctx.message_id))
        for block in grouped.values()
    ]


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
        is_conversation_filler: bool = False,
    ) -> MessageContext:
        return message.to_context(
            retrieval_source=source,
            retrieval_score=score,
            retrieval_reason=reason or source,
            is_conversation_filler=is_conversation_filler,
        )

    def build_context_pack(
        self,
        *,
        pinned_context: list[MessageContext],
        retrieved_context: list[MessageContext],
        max_messages: int,
        char_budget: int = 0,
    ) -> list[MessageContext]:
        """Return a bounded context pack with pins preserved ahead of retrieved messages.

        `char_budget` of 0 bounds the pack by message count, as before. A positive
        value bounds it by characters as well, and admits each expanded conversation
        whole or not at all; `max_messages` still caps the messages either way.
        """
        max_messages = max(1, int(max_messages or 1))
        char_budget = max(0, int(char_budget or 0))

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

        priority_chars = 0
        if char_budget:
            # Priority keeps its order, but never the whole budget (DAB-073).
            allowance = char_budget - self._retrieval_char_floor(char_budget)
            pinned, pin_chars = self._fit_priority(pinned, allowance)
            anchors, anchor_chars = self._fit_priority(anchors, allowance - pin_chars)
            priority_chars = pin_chars + anchor_chars

        seen_ids = {ctx.message_id for ctx in [*pinned, *anchors]}
        deduped_retrieved = []
        for ctx in ordinary:
            if ctx.message_id in seen_ids:
                continue
            seen_ids.add(ctx.message_id)
            deduped_retrieved.append(ctx)

        remaining_slots = max(0, max_messages - len(pinned) - len(anchors))
        if char_budget:
            floor = self._retrieval_char_floor(char_budget) if deduped_retrieved else 0
            budget = max(char_budget - priority_chars, floor)
            return pinned + anchors + self._admit_blocks(
                deduped_retrieved, budget, remaining_slots
            )

        if remaining_slots:
            deduped_retrieved = deduped_retrieved[:remaining_slots]
        else:
            deduped_retrieved = []

        return pinned + anchors + deduped_retrieved

    @staticmethod
    def _retrieval_char_floor(char_budget: int) -> int:
        return int(char_budget * MIN_RETRIEVAL_CHAR_SHARE)

    @classmethod
    def _fit_priority(cls, items: list[MessageContext], allowance: int) -> tuple[list[MessageContext], int]:
        """Admit a priority prefix within `allowance`; the first item always survives."""
        kept: list[MessageContext] = []
        spent = 0
        for ctx in items:
            cost = context_cost_chars(ctx)
            if kept and spent + cost > allowance:
                break
            kept.append(ctx)
            spent += cost
        return kept, spent

    @classmethod
    def _admit_blocks(
        cls,
        retrieved: list[MessageContext],
        char_budget: int,
        max_messages: int,
    ) -> list[MessageContext]:
        """Admit whole conversation blocks, best first, inside both bounds.

        `max_messages` bounds the RESULTS -- the retrieval hits. Surrounding
        conversation rides along with them and is bounded by `char_budget`
        instead. Counting filler against the result budget defeats expansion:
        at 14 it admitted 7 hits where the unexpanded pack carried 12.
        """
        blocks = group_conversation_blocks(retrieved)
        blocks.sort(key=lambda block: -max((c.retrieval_score or 0.0) for c in block))

        packed: list[MessageContext] = []
        hits = 0
        remaining = char_budget
        for block in blocks:
            cost = sum(context_cost_chars(ctx) for ctx in block)
            block_hits = sum(1 for ctx in block if not ctx.is_conversation_filler)
            if cost > remaining or hits + block_hits > max_messages:
                continue  # Never emit half a transcript; a later block may still fit.
            packed.extend(block)
            hits += block_hits
            remaining -= cost

        if not packed and blocks and max_messages > 0:
            # Nothing fits whole: the best block's strongest member beats nothing.
            packed = [max(blocks[0], key=lambda c: ((c.retrieval_score or 0.0), c.message_id))]
        return packed

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
