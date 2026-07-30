"""Guards for DAB-115: splitting must not re-parse the message per split point.

`MarkdownParser.is_safe_split_point(text, position)` re-scanned the whole text
-- a full regex pass for code fences plus a full markdown parse -- and the
splitter called it once per candidate split point. Since the number of
candidates grows with the message, splitting was quadratic: a 139 KB response
took 30.1 s, all of it on the event loop, which is long enough for Discord's
60 s heartbeat to be at risk and far past the point where the bot stops
responding to anything else.

`MarkdownParser.build_split_index` now does that work once and answers each
query with a bisect. Measured on the same input: 30.08 s -> 0.0375 s.

These assertions are deliberately about CALL COUNTS, not wall time. This
repository already has one wall-clock test that flakes on a loaded machine
(tests/test_rag_optimization.py) and a second would be worse than none: a
timing threshold either flakes in CI or is set so loose it stops detecting the
regression. A call count is exact, fast, and fails for the right reason.
"""

import unittest

from src.services.message_splitter import MessageSplitter
from src.utils.markdown_utils import MarkdownParser


#: Whole-message scans permitted per split_message call, regardless of size.
#: The index needs one fence scan and one markdown parse; the headroom absorbs
#: an incidental extra without permitting anything that scales.
WHOLE_TEXT_SCAN_BUDGET = 4


def make_fenced_content(target_bytes):
    """A single huge fenced code block -- the worst case for the old algorithm."""
    line = "    value = compute(index)  # padding to make the line realistic\n"
    body = line * (target_bytes // len(line))
    return "```python\n" + body + "```"


class CountingParser(MarkdownParser):
    """Counts scans of the FULL message text.

    Scanning a single part's content is cheap and expected -- the pipeline does
    it while formatting each part. The regression is scanning the *whole
    message* repeatedly, so only those are counted, keyed on the length of the
    text handed in.
    """

    def __init__(self, full_text):
        super().__init__()
        self._full_length = len(full_text)
        self.whole_text_scans = 0
        self.whole_text_parses = 0

    def find_code_block_boundaries(self, text):
        if len(text) == self._full_length:
            self.whole_text_scans += 1
        return super().find_code_block_boundaries(text)

    def parse_markdown(self, text):
        if len(text) == self._full_length:
            self.whole_text_parses += 1
        return super().parse_markdown(text)


class SplitterScalingTest(unittest.TestCase):
    def test_whole_text_scans_do_not_grow_with_the_number_of_parts(self):
        # 30 KB rather than the 139 KB used for the timing measurement: large
        # enough that the old algorithm is hopeless here (measured ~18,500
        # scans), small enough that a FAILING run still finishes in seconds
        # instead of half a minute.
        content = make_fenced_content(30 * 1024)
        parser = CountingParser(content)
        splitter = MessageSplitter(max_length=2000)
        splitter.markdown_parser = parser

        parts = splitter.split_message(content)

        self.assertGreater(len(parts), 10, "precondition: content must actually split")

        # The budget is a small CONSTANT, deliberately not a multiple of
        # len(parts). The invariant being defended is that whole-text work does
        # not scale with the message at all -- the index is built once. A
        # per-part budget would still pass if someone reintroduced a rebuild
        # inside the per-part loop, which is itself quadratic in total work.
        # Measured here: 2 scans fixed, 16 with a per-part rebuild, ~18,500 with
        # the original per-candidate rescan.
        self.assertLessEqual(
            parser.whole_text_scans,
            WHOLE_TEXT_SCAN_BUDGET,
            f"{parser.whole_text_scans} whole-text fence scans for {len(parts)} parts "
            f"(budget {WHOLE_TEXT_SCAN_BUDGET}); the split index is being rebuilt "
            f"instead of reused",
        )
        self.assertLessEqual(
            parser.whole_text_parses,
            WHOLE_TEXT_SCAN_BUDGET,
            f"{parser.whole_text_parses} whole-text markdown parses for "
            f"{len(parts)} parts (budget {WHOLE_TEXT_SCAN_BUDGET})",
        )

    def test_a_large_message_still_splits_correctly_after_indexing(self):
        content = make_fenced_content(30 * 1024)
        splitter = MessageSplitter(max_length=2000)

        parts = splitter.split_message(content)

        self.assertTrue(parts)
        for part in parts:
            self.assertLessEqual(len(part.content), 2000)
        # Lossless: every non-whitespace character survives the round trip.
        rejoined = "".join(part.content for part in parts)
        self.assertEqual(
            "".join(rejoined.split()),
            "".join(content.split()),
            "splitting lost or altered content",
        )


class SplitIndexTest(unittest.TestCase):
    """The index must answer exactly as the original predicate did."""

    def test_index_agrees_with_the_direct_predicate_at_every_position(self):
        text = (
            "Some prose with **bold text** and `inline code` here.\n\n"
            "```python\nx = 1\ny = 2\n```\n\n"
            "More prose with *italics*, ~~strike~~, ||spoiler|| and "
            "[a link](http://example.com/path).\n"
        )
        parser = MarkdownParser()
        index = parser.build_split_index(text)

        for position in range(len(text) + 1):
            with self.subTest(position=position):
                self.assertEqual(
                    index.is_safe_split_point(position),
                    parser.is_safe_split_point(text, position),
                )

    def test_overlapping_spans_use_the_running_maximum_end(self):
        # A long span followed by a short one that starts later and ends sooner.
        # Comparing against only the immediately preceding span's end would call
        # a position inside the long span "safe".
        text = "```\n" + "a" * 200 + "\n```\n\n`x`\n"
        parser = MarkdownParser()
        index = parser.build_split_index(text)

        inside_long_span = 100
        self.assertFalse(index.is_safe_split_point(inside_long_span))
        self.assertEqual(
            index.is_safe_split_point(inside_long_span),
            parser.is_safe_split_point(text, inside_long_span),
        )

    def test_text_with_no_markdown_is_safe_everywhere(self):
        text = "just plain words with no formatting at all"
        index = MarkdownParser().build_split_index(text)

        self.assertTrue(all(index.is_safe_split_point(p) for p in range(len(text) + 1)))


if __name__ == "__main__":
    unittest.main()
