"""Guards for the lexical half of DAB-087: non-Latin search was not degraded, it was off.

`_build_fts_query` tokenised on `[A-Za-z0-9_@#./:-]`, so a query written in any
other script produced no tokens at all and the function returned `None` --
`search_lexical` then returned nothing without ever consulting the index.
Meanwhile `message_search_fts` already *held* that text, correctly tokenised by
SQLite's `unicode61`. The data was there and the query threw it away, which is
why widening the class is retroactive over all existing history and costs no
re-indexing, no FTS rebuild and no embedding.

Only the lexical half is fixed. `_embedding_is_trivial` still has Latin-only
classes, so non-Latin *semantic* retrieval is still blind; that half is deferred
behind a named prerequisite recorded in its docstring, because widening it
starts an unpaced re-embedding run and `mark_embedding_failed` is terminal.

`test_a_full_width_query_still_matches_a_full_width_document` is here to stop
the obvious follow-up. NFKC normalisation looks like the natural companion
change and it is not: the stored side is not normalised, so normalising only the
query takes that case from a hit to a miss.
"""

import re
import sqlite3
import tempfile
import unittest
from pathlib import Path

from src.services.message_index_service import MessageIndexService

# One document per script, each a realistic short message.
DOCUMENTS = {
    "cyrillic": "Привет как дела сегодня",
    "arabic": "مرحبا كيف حالك اليوم",
    "korean": "안녕하세요 오늘 어떻게 지내세요",
    "greek": "Γεια σου τι κανεις σημερα",
    "hebrew": "שלום מה שלומך היום",
    "latin": "hello how are you today",
    "accented": "naïve café résumé",
    "fullwidth": "ＡＢＣ ｄｅｆ",
}


class LexicalScriptCoverageTest(unittest.TestCase):
    def setUp(self):
        self._dir = tempfile.TemporaryDirectory()
        self.addCleanup(self._dir.cleanup)
        self.rag = str(Path(self._dir.name) / "rag.db")
        self.service = MessageIndexService(db_path=self.rag)
        if not self.service.fts_enabled:
            self.skipTest("SQLite build has no FTS5")

        with sqlite3.connect(self.rag) as conn:
            for index, text in enumerate(DOCUMENTS.values(), start=1):
                conn.execute(
                    "INSERT INTO message_index (message_id, channel_id, guild_id,"
                    " author_id, author_name, created_at, indexed_at, content_text,"
                    " content_hash) VALUES (?, 1, 2, 3, 'ada',"
                    " '2026-01-01T00:00:00', '2026-01-01T00:00:00', ?, ?)",
                    (index, text, f"h{index}"),
                )
                conn.execute(
                    "INSERT INTO message_search_fts(rowid, content_text, author_name,"
                    " attachment_summary) VALUES (?, ?, 'ada', '')",
                    (index, text),
                )

    def _search(self, term):
        return self.service.search_lexical(
            term, channel_id=1, guild_id=2, cross_channel=False, limit=5
        )

    def test_every_script_is_searchable(self):
        for script, text in DOCUMENTS.items():
            term = text.split()[0]
            with self.subTest(script=script, term=term):
                self.assertTrue(
                    self._search(term),
                    f"a {script} query found nothing in an index that holds it",
                )

    def test_a_non_latin_query_reaches_the_index_at_all(self):
        # The precise shape of the defect, asserted directly: the query was not
        # merely unlucky, it was never built.
        self.assertIsNotNone(MessageIndexService._build_fts_query("Привет"))
        self.assertIsNotNone(MessageIndexService._build_fts_query("今天天气很好"))

    def test_accented_latin_is_one_token_not_two_fragments(self):
        # The old class split on the diaeresis, leaving "na" and "ve" -- both
        # under the two-character floor in some words, and matching the wrong
        # thing in others.
        self.assertEqual(MessageIndexService._build_fts_query("naïve"), '"naïve"')
        self.assertTrue(self._search("naïve"))

    def test_a_full_width_query_still_matches_a_full_width_document(self):
        # Why NFKC is not applied here. Normalising the query alone would turn
        # this into a miss, because nothing normalises the stored side.
        self.assertTrue(self._search("ＡＢＣ"))

    def test_a_query_with_no_word_characters_still_yields_nothing(self):
        self.assertIsNone(MessageIndexService._build_fts_query("!!! ???"))
        self.assertEqual(self._search("!!! ???"), [])

    def test_ascii_queries_tokenise_byte_for_byte_as_before(self):
        # The invariant that matters for a widening: nothing that worked before
        # changes. Asserted against the previous character class directly rather
        # than against hand-written expectations -- the first draft of this test
        # guessed two of them wrong, in both cases describing behaviour that
        # predates the change ("..." is a token; a URL is one token, not three).
        previous = r"[A-Za-z0-9_@#./:-]{2,}"
        corpus = [
            "see https://example.com/docs",
            "@ada #general",
            "hello how are you today",
            "path/to/file.py and a.b.c:1234",
            "!!! ???",
            "...",
            "a",
            "",
            "MiXeD_Case-and-dashes",
        ]
        for query in corpus:
            with self.subTest(query=query):
                tokens = re.findall(previous, query)
                expected = (
                    " OR ".join(f'"{token}"' for token in dict.fromkeys(tokens[:24]))
                    if tokens
                    else None
                )
                # Through the real function, never through a copy of its regex.
                # The first draft compared two literal patterns inside the test,
                # so stripping the punctuation out of the shipped class left it
                # green -- `ANALYSIS_CORRECTIONS.md` item 13, in miniature.
                self.assertEqual(MessageIndexService._build_fts_query(query), expected)


if __name__ == "__main__":
    unittest.main()
