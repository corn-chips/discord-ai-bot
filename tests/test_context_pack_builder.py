"""Character-budget packing of expanded conversation blocks."""

import unittest
from datetime import datetime, timedelta, timezone

from src.models.data_models import MessageContext
from src.services.context_pack_builder import (
    ContextPackBuilder,
    MIN_RETRIEVAL_CHAR_SHARE,
    MIN_RETRIEVAL_SLOTS,
)


BASE_TIME = datetime(2026, 1, 1, 12, 0, tzinfo=timezone.utc)


def context(
    message_id,
    *,
    content="x",
    score=0.5,
    conversation_id=None,
    filler=False,
    source="lexical",
    minutes=0,
):
    return MessageContext(
        content=content,
        author="Ada",
        timestamp=BASE_TIME + timedelta(minutes=minutes or message_id),
        message_id=message_id,
        channel_id=2,
        retrieval_source=source,
        retrieval_score=score,
        retrieval_reason="match",
        conversation_id=conversation_id,
        is_conversation_filler=filler,
    )


def pins(count, channel_id=2):
    return ContextPackBuilder().build_pinned_context(
        [(pin_id, f"pin {pin_id}", "Ada", "Ray", BASE_TIME.isoformat()) for pin_id in range(1, count + 1)],
        channel_id=channel_id,
    )


def legacy_pack(builder, *, pinned_context, retrieved_context, max_messages):
    """The count-only packer as it stood before `char_budget` existed."""
    max_messages = max(1, int(max_messages or 1))
    pinned = list(pinned_context or [])
    retrieved = list(retrieved_context or [])
    anchors = [ctx for ctx in retrieved if ctx.retrieval_source == "reply_anchor"]
    ordinary = [ctx for ctx in retrieved if ctx.retrieval_source != "reply_anchor"]
    pin_slots, anchor_slots = builder._priority_slot_counts(len(pinned), len(anchors), max_messages)
    pinned = pinned[:pin_slots]
    anchors = anchors[:anchor_slots]
    seen = {ctx.message_id for ctx in [*pinned, *anchors]}
    deduped = []
    for ctx in ordinary:
        if ctx.message_id in seen:
            continue
        seen.add(ctx.message_id)
        deduped.append(ctx)
    remaining = max(0, max_messages - len(pinned) - len(anchors))
    return pinned + anchors + (deduped[:remaining] if remaining else [])


class ContextPackBudgetTest(unittest.TestCase):
    def setUp(self):
        self.builder = ContextPackBuilder()

    def test_char_budget_zero_reproduces_the_count_only_packer(self):
        anchor = context(50, source="reply_anchor", score=0.9)
        retrieved = [
            anchor,
            context(10, conversation_id=7, score=0.8),
            context(11, conversation_id=7, filler=True, score=0.0),
            context(12, score=0.4),
            context(12, score=0.4),  # duplicate id, must still be dropped
        ]
        for max_messages in (1, 2, 4, 6, 14):
            for pin_count in (0, 1, 3, 9):
                with self.subTest(max_messages=max_messages, pins=pin_count):
                    pinned = pins(pin_count)
                    self.assertEqual(
                        self.builder.build_context_pack(
                            pinned_context=pinned,
                            retrieved_context=retrieved,
                            max_messages=max_messages,
                        ),
                        legacy_pack(
                            self.builder,
                            pinned_context=pinned,
                            retrieved_context=retrieved,
                            max_messages=max_messages,
                        ),
                    )

    def test_char_budget_defaults_to_zero_for_callers_that_never_pass_it(self):
        # The retriever calls this positionally-free and without the new keyword.
        packed = self.builder.build_context_pack(
            pinned_context=[],
            retrieved_context=[context(1, content="a" * 5000), context(2, content="b" * 5000)],
            max_messages=6,
        )
        self.assertEqual([ctx.message_id for ctx in packed], [1, 2])

    def test_a_block_is_admitted_whole_or_not_at_all(self):
        block = [
            context(10, conversation_id=7, content="a" * 40, score=0.9),
            context(11, conversation_id=7, content="b" * 40, score=0.1, filler=True),
            context(12, conversation_id=7, content="c" * 40, score=0.1, filler=True),
        ]
        standalone = context(30, content="d" * 30, score=0.8)

        packed = self.builder.build_context_pack(
            pinned_context=[],
            retrieved_context=[*block, standalone],
            max_messages=6,
            char_budget=200,
        )

        # Every message also carries RENDER_OVERHEAD_CHARS. The block costs
        # 3 * (40 + 3 + 96) = 417 > 200, so none of it lands; the smaller
        # standalone item, at 30 + 3 + 96 = 129, still fits.
        self.assertEqual([ctx.message_id for ctx in packed], [30])

    def test_the_whole_block_lands_when_the_budget_allows_it(self):
        block = [
            context(11, conversation_id=7, content="b" * 20, score=0.1, filler=True),
            context(10, conversation_id=7, content="a" * 20, score=0.9),
        ]

        # 2 * (20 + 3 + RENDER_OVERHEAD_CHARS) = 238.
        packed = self.builder.build_context_pack(
            pinned_context=[],
            retrieved_context=block,
            max_messages=6,
            char_budget=400,
        )

        self.assertEqual([ctx.message_id for ctx in packed], [10, 11])

    def test_an_oversized_single_block_degrades_to_its_best_member(self):
        block = [
            context(10, conversation_id=7, content="a" * 500, score=0.2, filler=True),
            context(11, conversation_id=7, content="b" * 500, score=0.95),
            context(12, conversation_id=7, content="c" * 500, score=0.3, filler=True),
        ]

        packed = self.builder.build_context_pack(
            pinned_context=[],
            retrieved_context=block,
            max_messages=6,
            char_budget=120,
        )

        self.assertEqual([ctx.message_id for ctx in packed], [11])

    def test_messages_inside_a_block_are_chronological(self):
        block = [
            context(12, conversation_id=7, minutes=30, score=0.9),
            context(10, conversation_id=7, minutes=10, score=0.1, filler=True),
            context(11, conversation_id=7, minutes=20, score=0.1, filler=True),
        ]

        packed = self.builder.build_context_pack(
            pinned_context=[],
            retrieved_context=block,
            max_messages=6,
            char_budget=4000,
        )

        self.assertEqual([ctx.message_id for ctx in packed], [10, 11, 12])

    def test_blocks_are_admitted_best_first(self):
        retrieved = [
            context(10, conversation_id=7, content="a" * 10, score=0.2),
            context(11, conversation_id=7, content="a" * 10, score=0.1, filler=True),
            context(20, conversation_id=8, content="b" * 10, score=0.9),
            context(21, conversation_id=8, content="b" * 10, score=0.1, filler=True),
            context(30, content="c" * 10, score=0.5),
        ]

        packed = self.builder.build_context_pack(
            pinned_context=[],
            retrieved_context=retrieved,
            max_messages=6,
            char_budget=4000,
        )

        self.assertEqual([ctx.message_id for ctx in packed], [20, 21, 30, 10, 11])

    def _two_blocks_and_a_standalone(self):
        retrieved = [
            context(10 + offset, conversation_id=7, content="a" * 10, score=0.9, filler=offset > 0)
            for offset in range(5)
        ]
        retrieved += [
            context(20 + offset, conversation_id=8, content="b" * 10, score=0.8, filler=offset > 0)
            for offset in range(5)
        ]
        retrieved.append(context(30, content="c" * 10, score=0.7))
        return retrieved

    def test_max_messages_bounds_the_hits_and_filler_rides_along(self):
        packed = self.builder.build_context_pack(
            pinned_context=[],
            retrieved_context=self._two_blocks_and_a_standalone(),
            max_messages=2,
            char_budget=8000,
        )

        # Each block carries one hit, so both fit a two-result budget and bring
        # their four filler messages apiece. The standalone would be a third
        # result, so it is left out.
        self.assertEqual(
            [ctx.message_id for ctx in packed], [10, 11, 12, 13, 14, 20, 21, 22, 23, 24]
        )
        self.assertEqual(sum(1 for ctx in packed if not ctx.is_conversation_filler), 2)

    def test_a_block_with_too_many_hits_is_skipped_and_a_smaller_one_lands(self):
        crowded = [
            context(10 + offset, conversation_id=7, content="a" * 10, score=0.9)
            for offset in range(3)
        ]
        modest = [
            context(20, conversation_id=8, content="b" * 10, score=0.8),
            context(21, conversation_id=8, content="b" * 10, score=0.1, filler=True),
        ]

        packed = self.builder.build_context_pack(
            pinned_context=[],
            retrieved_context=[*crowded, *modest],
            max_messages=2,
            char_budget=8000,
        )

        self.assertEqual([ctx.message_id for ctx in packed], [20, 21])

    def test_nothing_fitting_the_cap_degrades_to_the_best_member(self):
        retrieved = [
            context(10 + offset, conversation_id=7, content="a" * 10, score=0.9)
            for offset in range(3)
        ]

        packed = self.builder.build_context_pack(
            pinned_context=[],
            retrieved_context=retrieved,
            max_messages=2,
            char_budget=8000,
        )

        self.assertEqual([ctx.message_id for ctx in packed], [12])

    def test_expansion_filler_is_not_charged_against_the_result_budget(self):
        # Filler is grounding, not a result. Charging it against max_messages
        # defeated expansion: at 14 the pack carried 7 hits where the
        # unexpanded pack carried 12.
        retrieved = [
            context(
                100 * conversation + offset,
                conversation_id=conversation,
                content="a" * 10,
                score=1.0 - conversation / 10,
                filler=offset > 0,
            )
            for conversation in range(1, 5)
            for offset in range(5)
        ]

        packed = self.builder.build_context_pack(
            pinned_context=[],
            retrieved_context=retrieved,
            max_messages=3,
            char_budget=100_000,
        )

        self.assertEqual(sum(1 for ctx in packed if not ctx.is_conversation_filler), 3)
        self.assertEqual(len(packed), 15)
        self.assertEqual({ctx.conversation_id for ctx in packed}, {1, 2, 3})

    def test_a_standalone_message_is_its_own_block(self):
        retrieved = [context(10, content="a" * 60, score=0.9), context(11, content="b" * 60, score=0.8)]

        packed = self.builder.build_context_pack(
            pinned_context=[],
            retrieved_context=retrieved,
            max_messages=6,
            char_budget=70,
        )

        self.assertEqual([ctx.message_id for ctx in packed], [10])

    def test_reply_anchors_keep_their_priority_under_a_budget(self):
        anchor = context(50, source="reply_anchor", content="anchor", score=0.1)
        pinned = pins(2)

        packed = self.builder.build_context_pack(
            pinned_context=pinned,
            retrieved_context=[anchor, context(10, conversation_id=7, score=0.9)],
            max_messages=6,
            char_budget=4000,
        )

        self.assertTrue(packed[0].is_pinned_memory)
        self.assertTrue(packed[1].is_pinned_memory)
        self.assertEqual(packed[2].message_id, 50)
        self.assertEqual(packed[3].message_id, 10)

    def test_degenerate_inputs_do_not_raise(self):
        for max_messages in (0, -3, None):
            for char_budget in (0, -50, None):
                with self.subTest(max_messages=max_messages, char_budget=char_budget):
                    self.assertEqual(
                        self.builder.build_context_pack(
                            pinned_context=None,
                            retrieved_context=None,
                            max_messages=max_messages,
                            char_budget=char_budget,
                        ),
                        [],
                    )

    def test_a_missing_retrieval_score_is_treated_as_the_weakest(self):
        scored = context(10, content="a" * 10, score=0.4)
        unscored = context(11, content="b" * 10, score=None)

        packed = self.builder.build_context_pack(
            pinned_context=[],
            retrieved_context=[unscored, scored],
            max_messages=6,
            char_budget=4000,
        )

        self.assertEqual([ctx.message_id for ctx in packed], [10, 11])


class RetrievalFloorUnderBudgetTest(unittest.TestCase):
    """DAB-073 restated for the character budget: pins never leave retrieval nothing."""

    def setUp(self):
        self.builder = ContextPackBuilder()

    def test_the_slot_floor_is_unchanged(self):
        for max_messages in (4, 6, 10, 14):
            with self.subTest(max_messages=max_messages):
                self.assertGreaterEqual(
                    self.builder.available_retrieval_slots(
                        pinned_context=pins(max_messages * 3),
                        reply_context=[],
                        max_messages=max_messages,
                    ),
                    MIN_RETRIEVAL_SLOTS,
                )

    def test_the_slot_floor_still_yields_at_a_budget_too_small_to_divide(self):
        self.assertEqual(
            self.builder.available_retrieval_slots(
                pinned_context=pins(5), reply_context=[], max_messages=1
            ),
            0,
        )

    def test_pins_cannot_eat_the_whole_character_budget(self):
        # Every pin on its own would exhaust an 800 character budget.
        fat_pins = ContextPackBuilder().build_pinned_context(
            [(pin_id, "p" * 300, "Ada", "Ray", BASE_TIME.isoformat()) for pin_id in range(1, 6)],
            channel_id=2,
        )
        retrieved = [context(10, conversation_id=7, content="r" * 100, score=0.9)]

        packed = self.builder.build_context_pack(
            pinned_context=fat_pins,
            retrieved_context=retrieved,
            max_messages=6,
            char_budget=800,
        )

        self.assertIn(10, [ctx.message_id for ctx in packed])
        self.assertTrue(packed[0].is_pinned_memory)

    def test_one_enormous_pin_still_leaves_retrieval_its_floor(self):
        # The first pin always survives, so the floor is asserted rather than
        # subtracted: a retrieved item must still be reachable.
        huge_pin = ContextPackBuilder().build_pinned_context(
            [(1, "p" * 5000, "Ada", "Ray", BASE_TIME.isoformat())], channel_id=2
        )
        retrieved = [context(10, content="r" * 100, score=0.9)]

        packed = self.builder.build_context_pack(
            pinned_context=huge_pin,
            retrieved_context=retrieved,
            max_messages=6,
            char_budget=1000,
        )

        self.assertEqual([ctx.message_id for ctx in packed][1:], [10])
        self.assertGreater(int(1000 * MIN_RETRIEVAL_CHAR_SHARE), 0)

    def test_the_floor_buys_a_whole_block_and_not_just_a_degraded_message(self):
        # What the floor is actually for, which the test above cannot show: with
        # no floor the budget goes negative, nothing fits whole, and the
        # "best member beats nothing" fallback hands back one orphan message.
        # The pack still contains a retrieved item either way, so only the size
        # of what survives distinguishes the two.
        huge_pin = ContextPackBuilder().build_pinned_context(
            [(1, "p" * 5000, "Ada", "Ray", BASE_TIME.isoformat())], channel_id=2
        )
        block = [
            context(10, conversation_id=7, content="r" * 10, score=0.9),
            context(11, conversation_id=7, content="r" * 10, score=0.1, filler=True),
        ]

        packed = self.builder.build_context_pack(
            pinned_context=huge_pin,
            retrieved_context=block,
            max_messages=6,
            char_budget=1000,
        )

        self.assertEqual([ctx.message_id for ctx in packed][1:], [10, 11])

    def test_the_floor_is_not_spent_when_there_is_nothing_to_retrieve(self):
        packed = self.builder.build_context_pack(
            pinned_context=pins(3), retrieved_context=[], max_messages=6, char_budget=4000
        )

        self.assertEqual(len(packed), 3)
        self.assertTrue(all(ctx.is_pinned_memory for ctx in packed))


class MessageContextConversationFieldsTest(unittest.TestCase):
    def test_the_new_fields_default_so_existing_construction_sites_are_unchanged(self):
        ctx = MessageContext(
            content="hello", author="Ada", timestamp=BASE_TIME, message_id=1
        )

        self.assertIsNone(ctx.conversation_id)
        self.assertFalse(ctx.is_conversation_filler)

    def test_validation_still_rejects_a_malformed_context(self):
        with self.assertRaises(ValueError):
            MessageContext(
                content="hello", author="Ada", timestamp=BASE_TIME, message_id=1,
                conversation_id=7, is_reply=True,
            )


if __name__ == "__main__":
    unittest.main()
