"""Transcript-block rendering and the final character guard in `format_prompt`."""

import unittest
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

from src.models.data_models import MessageContext
from src.services.context_pack_builder import (
    RENDER_OVERHEAD_CHARS,
    ContextPackBuilder,
    context_cost_chars,
)
from src.services.gemini_client import GeminiClient


BASE_TIME = datetime(2026, 1, 1, 12, 0, tzinfo=timezone.utc)


def client(char_budget=8000):
    bot = object.__new__(GeminiClient)
    bot.config = SimpleNamespace(
        system_prompt_low_complexity="SYS",
        system_prompt_medium_complexity="SYS",
        system_prompt_high_complexity="SYS",
        rag_context_char_budget=char_budget,
    )
    bot._current_complexity_level = "low"
    return bot


def context(
    message_id,
    content="hello",
    *,
    author="Ada",
    conversation_id=None,
    filler=False,
    score=0.5,
    source="lexical",
    reason="fts match",
    pinned=False,
    minutes=None,
):
    return MessageContext(
        content=content,
        author=author,
        timestamp=BASE_TIME + timedelta(minutes=message_id if minutes is None else minutes),
        message_id=message_id,
        channel_id=2,
        retrieval_source=source,
        retrieval_score=score,
        retrieval_reason=reason,
        is_pinned_memory=pinned,
        conversation_id=conversation_id,
        is_conversation_filler=filler,
    )


def context_lines(prompt):
    body = prompt.split("--- Retrieved Discord Context (RAG) ---")[-1]
    body = body.split("--- End Context ---")[0]
    return [line for line in body.splitlines() if line.startswith("[")]


class TranscriptBlockRenderTest(unittest.TestCase):
    def test_a_conversation_renders_as_one_headed_block(self):
        pack = [
            context(102, "the migration landed", conversation_id=42, score=0.812, source="lexical+semantic"),
            context(101, "morning", conversation_id=42, filler=True, score=0.0, author="Bob"),
            context(103, "thanks", conversation_id=42, filler=True, score=0.0, author="Bob"),
        ]

        lines = context_lines(client().format_prompt("what happened?", pack))

        self.assertEqual(
            lines[0],
            "[CTX_BLOCK_001 | conversation_id=42 | messages=3 | matches=1 "
            "| source=lexical+semantic, score=0.812, reason=fts match]",
        )
        self.assertEqual(lines[-1], "[CTX_BLOCK_001 END]")
        self.assertEqual(len(lines), 5)

    def test_the_hit_is_marked_and_the_filler_is_not_mistaken_for_evidence(self):
        pack = [
            context(101, "morning", conversation_id=42, filler=True, score=0.0),
            context(102, "the migration landed", conversation_id=42, score=0.812),
        ]

        lines = context_lines(client().format_prompt("what happened?", pack))

        self.assertIn(" | surrounding]", lines[1])
        self.assertIn("message_id=101", lines[1])
        self.assertIn(" | retrieved_match]", lines[2])
        self.assertIn("message_id=102", lines[2])

    def test_provenance_is_carried_by_the_header_and_not_repeated_per_message(self):
        pack = [
            context(101, "morning", conversation_id=42, filler=True, score=0.0),
            context(102, "landed", conversation_id=42, score=0.812),
        ]

        lines = context_lines(client().format_prompt("what happened?", pack))

        self.assertEqual(sum("source=" in line for line in lines), 1)
        self.assertEqual(sum("reason=fts match" in line for line in lines), 1)

    def test_messages_are_chronological_inside_a_block(self):
        pack = [
            context(103, "third", conversation_id=42, filler=True, minutes=30),
            context(101, "first", conversation_id=42, score=0.9, minutes=10),
            context(102, "second", conversation_id=42, filler=True, minutes=20),
        ]

        lines = context_lines(client().format_prompt("q", pack))

        self.assertEqual(
            [line.split("message_id=")[1].split(" ")[0] for line in lines[1:-1]],
            ["101", "102", "103"],
        )

    def test_a_gap_between_two_unioned_windows_is_marked(self):
        pack = [
            context(1000, "a", conversation_id=42, filler=True),
            context(1001, "b", conversation_id=42, score=0.9),
            context(1002, "c", conversation_id=42, filler=True),
            context(1400, "d", conversation_id=42, filler=True),
            context(1401, "e", conversation_id=42, score=0.8),
        ]

        lines = context_lines(client().format_prompt("q", pack))

        self.assertEqual(lines[4], "[... transcript gap: messages omitted ...]")
        self.assertEqual(sum(line.startswith("[... transcript gap") for line in lines), 1)

    def test_a_contiguous_block_carries_no_elision_marker(self):
        pack = [
            context(1000 + offset, "x", conversation_id=42, filler=offset != 0)
            for offset in range(5)
        ]

        lines = context_lines(client().format_prompt("q", pack))

        self.assertFalse(any("transcript gap" in line for line in lines))

    def test_two_messages_alone_give_no_yardstick_for_a_gap(self):
        pack = [
            context(1000, "a", conversation_id=42, score=0.9),
            context(9000, "b", conversation_id=42, filler=True),
        ]

        lines = context_lines(client().format_prompt("q", pack))

        self.assertFalse(any("transcript gap" in line for line in lines))

    def test_blocks_are_ordered_weakest_first_so_the_strongest_sits_last(self):
        pack = [
            context(101, "strong", conversation_id=42, score=0.9),
            context(102, "strong filler", conversation_id=42, filler=True, score=0.0),
            context(201, "weak", conversation_id=43, score=0.2),
            context(202, "weak filler", conversation_id=43, filler=True, score=0.0),
            context(300, "middling standalone", score=0.5),
        ]

        lines = context_lines(client().format_prompt("q", pack))
        headers = [line for line in lines if line.startswith("[CTX_BLOCK") and "END" not in line]

        self.assertEqual(
            [header.split("conversation_id=")[1].split(" ")[0] for header in headers],
            ["43", "42"],
        )
        self.assertEqual(lines[3], "[CTX_BLOCK_001 END]")
        self.assertIn("message_id=300", lines[4])
        self.assertIn("message_id=102", lines[-2])

    def test_pins_and_anchors_still_render_first_and_flat(self):
        pack = [
            context(101, "block hit", conversation_id=42, score=0.9),
            context(102, "block filler", conversation_id=42, filler=True, score=0.0),
            context(1, "pinned memory", pinned=True, source="pin", score=1.0, reason="pinned by Ray"),
            context(90, "anchor", source="reply_anchor", score=0.4, reason="direct reply"),
        ]

        lines = context_lines(client().format_prompt("q", pack))

        self.assertIn("message_id=1", lines[0])
        self.assertIn("source=pin", lines[0])
        self.assertIn("message_id=90", lines[1])
        self.assertIn("source=reply_anchor", lines[1])
        self.assertTrue(lines[2].startswith("[CTX_BLOCK_001"))

    def test_a_standalone_message_keeps_the_flat_line_format(self):
        pack = [context(300, "standalone", score=0.25, source="semantic", reason="knn")]

        lines = context_lines(client().format_prompt("q", pack))

        self.assertEqual(
            lines,
            [
                "[CTX_MSG_001 | message_id=300 | time=2026-01-01T17:00:00+00:00 "
                "| source=semantic, score=0.250, reason=knn] Ada: standalone"
            ],
        )

    def test_an_unexpanded_hit_is_not_dressed_up_as_a_one_message_transcript(self):
        # Every hit the retriever ran out of expansion slots for still carries its
        # conversation_id, and wrapping each in a CTX_BLOCK header and END line
        # tripled the context line count for no added information.
        pack = [context(300, "standalone", conversation_id=42, score=0.25, source="semantic", reason="knn")]

        lines = context_lines(client().format_prompt("q", pack))

        self.assertNotIn("CTX_BLOCK", "".join(lines))
        self.assertEqual(len(lines), 1)
        self.assertIn("source=semantic, score=0.250, reason=knn", lines[0])

    def test_sequence_numbers_run_across_blocks_without_repeating(self):
        pack = [
            context(101, "a", conversation_id=42, score=0.9),
            context(102, "b", conversation_id=42, filler=True, score=0.0),
            context(300, "c", score=0.5),
        ]

        lines = context_lines(client().format_prompt("q", pack))
        numbers = [line.split("[CTX_MSG_")[1][:3] for line in lines if line.startswith("[CTX_MSG_")]

        self.assertEqual(numbers, ["001", "002", "003"])

    def test_a_reply_indicator_survives_inside_a_block(self):
        reply = MessageContext(
            content="answering", author="Ada", timestamp=BASE_TIME + timedelta(minutes=200),
            message_id=102, channel_id=2, is_reply=True, replied_to_id=101,
            retrieval_source="lexical", retrieval_score=0.7, conversation_id=42,
        )
        pack = [context(101, "asking", conversation_id=42, filler=True, score=0.0), reply]

        lines = context_lines(client().format_prompt("q", pack))

        self.assertIn("Ada (replying): answering", lines[-2])


class ContextCharGuardTest(unittest.TestCase):
    def test_long_messages_are_clipped_to_the_configured_budget(self):
        pack = [
            context(101, "a" * 4000, conversation_id=42, score=0.9),
            context(102, "b" * 4000, conversation_id=42, filler=True, score=0.0),
        ]

        lines = context_lines(client(char_budget=100).format_prompt("q", pack))
        rendered = "".join(line.split("] ", 1)[1] for line in lines if line.startswith("[CTX_MSG_"))

        self.assertLess(len(rendered), 400)
        self.assertIn("...[truncated]", rendered)
        self.assertEqual(len([line for line in lines if line.startswith("[CTX_MSG_")]), 1)

    def test_an_unaffordable_message_is_dropped_rather_than_labelled(self):
        # "...[omitted]" arrived under a full ~100 character header, so an
        # exhausted budget still spent about a line per message it dropped.
        pack = [
            context(101, "a" * 400, conversation_id=42, score=0.9),
            context(102, "b" * 400, conversation_id=42, filler=True, score=0.0),
        ]

        lines = context_lines(client(char_budget=520).format_prompt("q", pack))

        self.assertNotIn("...[omitted]", "".join(lines))
        self.assertEqual(len(lines), 1)
        self.assertIn("message_id=101", lines[0])

    def test_the_budget_is_spent_in_priority_order_so_pins_survive_intact(self):
        pack = [
            context(1, "pinned memory", pinned=True, source="pin", score=1.0),
            context(101, "z" * 500, conversation_id=42, score=0.9),
        ]

        # 112 pays for the pin whole (13 + 3 + 96); the rest leaves the second
        # message a body to be truncated in.
        lines = context_lines(client(char_budget=262).format_prompt("q", pack))

        self.assertTrue(lines[0].endswith("Ada: pinned memory"))
        self.assertIn("...[truncated]", lines[-1])

    def test_the_budget_reaches_the_strongest_block_before_the_weakest(self):
        # Blocks are emitted weakest-first, so spending the budget in emission
        # order let the weak block eat all of it and drop the strong one whole.
        pack = [
            context(201, "W" * 400, conversation_id=43, score=0.2),
            context(202, "w" * 400, conversation_id=43, filler=True, score=0.0),
            context(101, "S" * 400, conversation_id=42, score=0.9),
            context(102, "s" * 400, conversation_id=42, filler=True, score=0.0),
        ]

        lines = context_lines(client(char_budget=500).format_prompt("q", pack))

        self.assertTrue(any("S" * 400 in line for line in lines))
        self.assertFalse(any("message_id=201" in line for line in lines))

    def test_a_config_without_the_budget_key_falls_back_to_the_default(self):
        bot = object.__new__(GeminiClient)
        bot.config = SimpleNamespace(system_prompt_low_complexity="SYS")
        bot._current_complexity_level = "low"

        lines = context_lines(bot.format_prompt("q", [context(300, "standalone")]))

        self.assertIn("Ada: standalone", lines[0])

    def test_an_empty_message_body_does_not_break_rendering(self):
        pack = [
            context(101, "", conversation_id=42, score=0.9),
            context(102, "", conversation_id=42, filler=True, score=0.0),
        ]

        lines = context_lines(client().format_prompt("q", pack))

        self.assertTrue(lines[1].endswith("Ada: "))

    def test_a_budget_of_zero_means_unbounded_rather_than_everything_omitted(self):
        pack = [context(101, "z" * 500, conversation_id=42, score=0.9)]

        prompt = client(char_budget=0).format_prompt("q", pack)

        self.assertIn("z" * 500, prompt)


class SharedBudgetAccountingTest(unittest.TestCase):
    """One accounting for what a message costs, spent by the packer and the renderer.

    They disagreed: the packer charged content + author + render overhead and
    the renderer charged content alone. The packer's bill is strictly the larger
    of the two, so a pack it produced could never trip the renderer's clip --
    the last guard before the prompt goes out was unreachable code.
    """

    def test_a_pack_built_to_the_budget_renders_untouched(self):
        budget = 2000
        retrieved = [
            context(100 + offset, "m" * 60, conversation_id=7, score=1.0 - offset / 100)
            for offset in range(12)
        ]
        packed = ContextPackBuilder().build_context_pack(
            pinned_context=[],
            retrieved_context=retrieved,
            max_messages=14,
            char_budget=budget,
        )

        lines = context_lines(client(char_budget=budget).format_prompt("q", packed))

        self.assertEqual(len(packed), 12)
        self.assertNotIn("...[truncated]", "".join(lines))
        self.assertEqual(len([line for line in lines if "message_id=" in line]), 12)

    def test_the_renderer_still_clips_a_pack_that_overruns_the_budget(self):
        # And the guard stays reachable: `_fit_priority` admits its first item
        # whatever it costs, so one enormous pin still arrives over budget.
        builder = ContextPackBuilder()
        huge_pin = builder.build_pinned_context(
            [(1, "p" * 5000, "Ada", "Ray", BASE_TIME.isoformat())], channel_id=2
        )
        packed = builder.build_context_pack(
            pinned_context=huge_pin,
            retrieved_context=[context(10, "r" * 10, score=0.9)],
            max_messages=6,
            char_budget=1000,
        )

        lines = context_lines(client(char_budget=1000).format_prompt("q", packed))

        self.assertEqual(len(packed), 2)
        self.assertIn("...[truncated]", lines[0])
        self.assertEqual(len(lines), 1)

    def test_the_two_sides_charge_the_same_for_one_message(self):
        message = context(101, "a" * 40)

        self.assertEqual(context_cost_chars(message), 40 + len("Ada") + RENDER_OVERHEAD_CHARS)


class LegacyContextRenderTest(unittest.TestCase):
    """The non-RAG branch must be untouched by any of this."""

    def test_context_without_retrieval_metadata_renders_exactly_as_before(self):
        pack = [
            MessageContext(
                content="second", author="Bob",
                timestamp=BASE_TIME + timedelta(minutes=5), message_id=2, channel_id=2,
            ),
            MessageContext(
                content="first", author="Ada", timestamp=BASE_TIME, message_id=1, channel_id=2,
            ),
        ]

        prompt = client().format_prompt("q", pack)
        body = prompt.split("--- Selected Conversation Context (oldest to newest) ---")[-1]
        lines = [line for line in body.split("--- End Context ---")[0].splitlines() if line.startswith("[")]

        self.assertEqual(
            lines,
            [
                "[CTX_MSG_001 | message_id=1 | time=2026-01-01T12:00:00+00:00] Ada: first",
                "[CTX_MSG_002 | message_id=2 | time=2026-01-01T12:05:00+00:00] Bob: second",
            ],
        )

    def test_the_legacy_branch_is_never_clipped_or_blocked(self):
        pack = [
            MessageContext(
                content="c" * 5000, author="Ada", timestamp=BASE_TIME, message_id=1,
                channel_id=2, conversation_id=42,
            ),
        ]

        prompt = client(char_budget=100).format_prompt("q", pack)

        self.assertNotIn("CTX_BLOCK", prompt)
        self.assertNotIn("truncated", prompt)
        self.assertIn("c" * 5000, prompt)


if __name__ == "__main__":
    unittest.main()
