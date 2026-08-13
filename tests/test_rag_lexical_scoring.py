"""Lexical relevance, embedding-failure recovery, and semantic search cost.

Three defects, one service:

* `bm25()` ran with default column weights over `fts5(content_text,
  author_name, attachment_summary)`. `author_name` holds one token, and BM25's
  short-field normalisation makes a hit there outrank any hit in a message
  body, so a bare display name returned the messages that person SENT and none
  of the messages ABOUT them.
* `search_lexical` then threw the score away and replaced it with `1.0 / rank`.
* `mark_embedding_failed` was terminal at three attempts and nothing ever
  rescued a `failed` row, so a rate-limit storm during a backfill left rows
  permanently unembeddable.

The semantic tests are about cost, not behaviour: the cache now holds unit
vectors and the scope mask is applied to the scores instead of to the data, so
the ranking must come out byte-for-byte the same as the naive cosine it
replaced.
"""

import contextlib
import math
import sqlite3
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

import numpy as np

from src.services.message_index_service import MessageIndexService

GUILD_ID = 10
CHANNEL_ID = 20


class LexicalScoringTest(unittest.TestCase):
    def setUp(self):
        self._dir = tempfile.TemporaryDirectory()
        self.addCleanup(self._dir.cleanup)
        self.db_path = str(Path(self._dir.name) / "rag.db")
        self.service = MessageIndexService(self.db_path, embedding_model="test-embedding")
        if not self.service.fts_enabled:
            self.skipTest("SQLite build has no FTS5")
        self.now = datetime.now(timezone.utc)
        self._counter = 0

    def _upsert(self, message_id, content, *, author="bob", attachment=""):
        self._counter += 1
        self.service.upsert_message(
            message_id=message_id,
            guild_id=GUILD_ID,
            channel_id=CHANNEL_ID,
            author_id=hash(author) % 1000,
            author_name=author,
            is_bot=False,
            reply_to_message_id=None,
            created_at=self.now - timedelta(minutes=self._counter),
            content_text=content,
            attachment_summary=attachment,
        )

    def _search(self, query, *, limit=10):
        return self.service.search_lexical(
            query,
            guild_id=GUILD_ID,
            channel_id=CHANNEL_ID,
            cross_channel=False,
            limit=limit,
        )

    def _plant_author_confound(self):
        """Two facts about alice, buried under chatter she sent herself."""
        self._upsert(1, "alice rewrote the caching layer and halved read latency", author="bob")
        self._upsert(2, "the release tooling is owned by alice these days", author="carol")
        for offset in range(20):
            self._upsert(100 + offset, "on it now", author="alice")

    def test_a_body_match_outranks_every_message_the_person_sent(self):
        self._plant_author_confound()

        results = self._search("alice")

        self.assertEqual([item.message_id for item in results[:2]], [1, 2])
        self.assertEqual(
            [item.author_name for item in results[2:]],
            ["alice"] * (len(results) - 2),
            "the tail should be the author matches, ranked below the body matches",
        )

    def test_the_author_column_is_still_searchable_just_not_dominant(self):
        # The weight is 0.1, not 0. "who said this" must still work.
        self._plant_author_confound()

        results = self._search("alice", limit=30)

        self.assertEqual(len(results), 22)
        self.assertEqual(sum(1 for item in results if item.author_name == "alice"), 20)

    def test_the_weights_come_from_configuration(self):
        # Proves the SQL binds them rather than hardcoding the defaults: raise
        # the author weight above the content weight and the old ranking, the
        # defective one, comes back.
        self._plant_author_confound()
        author_first = MessageIndexService(
            self.db_path,
            embedding_model="test-embedding",
            bm25_weight_content=0.1,
            bm25_weight_author=1.0,
        )

        results = author_first.search_lexical(
            "alice", guild_id=GUILD_ID, channel_id=CHANNEL_ID, cross_channel=False, limit=5
        )

        self.assertEqual([item.author_name for item in results], ["alice"] * 5)

    def test_the_real_bm25_score_survives_onto_the_result(self):
        # It used to be overwritten with 1.0 / rank, which discards the only
        # measure of how good a lexical hit actually is.
        self._plant_author_confound()

        results = self._search("alice")

        scores = [item.lexical_score for item in results]
        self.assertTrue(all(score < 0 for score in scores), scores)
        self.assertEqual(scores, sorted(scores), "bm25 is negative; best is most negative")
        self.assertNotEqual(scores[:3], [1.0, 0.5, 1 / 3], "the 1/rank overwrite is back")

    def test_a_stopword_heavy_question_searches_only_its_content_words(self):
        self._upsert(1, "alice rewrote the caching layer and halved read latency")
        self._upsert(2, "what do you think about the weather")

        self.assertEqual(
            self.service._fts_terms("who is alice and what did they do", drop_stopwords=True),
            ['"alice"'],
        )
        self.assertEqual([item.message_id for item in self._search("who is alice and what did they do")], [1])

    def test_a_query_of_nothing_but_stopwords_keeps_them(self):
        # Dropping every term would turn a real question into no query at all.
        self._upsert(1, "what did he do about the outage")

        self.assertEqual(
            self.service._fts_terms("what did he do", drop_stopwords=True),
            ['"what"', '"did"', '"he"', '"do"'],
        )
        self.assertEqual([item.message_id for item in self._search("what did he do")], [1])

    def test_stopword_filtering_can_be_switched_off(self):
        plain = MessageIndexService(
            self.db_path, embedding_model="test-embedding", fts_stopwords_enabled=False
        )
        self.assertFalse(plain.fts_stopwords_enabled)
        self.assertEqual(
            plain._fts_terms("who is alice", drop_stopwords=plain.fts_stopwords_enabled),
            ['"who"', '"is"', '"alice"'],
        )

    def test_and_matching_is_preferred_when_it_returns_enough_rows(self):
        for offset in range(6):
            self._upsert(200 + offset, f"the kubernetes rollout stalled in staging run {offset}")
        for offset in range(6):
            self._upsert(300 + offset, f"the espresso machine rollout of new beans {offset}")

        results = self._search("kubernetes rollout")

        self.assertEqual(len(results), 6)
        self.assertTrue(
            all("kubernetes" in item.content_text for item in results),
            "the OR form leaked in even though AND was wide enough",
        )

    def test_a_thin_and_match_falls_back_to_or(self):
        self._upsert(1, "the kubernetes rollout stalled in staging")
        self._upsert(2, "the espresso machine needs descaling")
        for offset in range(6):
            self._upsert(300 + offset, f"rollout notes {offset}")

        results = self._search("kubernetes rollout")

        self.assertEqual(results[0].message_id, 1, "the AND match must still rank first")
        self.assertGreater(len(results), 1, "the OR fallback never ran")

    def test_the_and_or_threshold_comes_from_configuration(self):
        self._upsert(1, "the kubernetes rollout stalled in staging")
        for offset in range(6):
            self._upsert(300 + offset, f"rollout notes {offset}")
        strict = MessageIndexService(
            self.db_path, embedding_model="test-embedding", fts_min_and_results=1
        )

        results = strict.search_lexical(
            "kubernetes rollout", guild_id=GUILD_ID, channel_id=CHANNEL_ID,
            cross_channel=False, limit=10,
        )

        self.assertEqual([item.message_id for item in results], [1])

    def test_a_limit_below_the_threshold_does_not_disable_the_and_preference(self):
        # The threshold is compared against a result set `limit` already caps, so
        # a `lexical_candidates` below `fts_min_and_results` made every AND query
        # unreachably thin: it ran, was thrown away, and OR ran on every search.
        self._upsert(1, "the kubernetes rollout stalled in staging")
        self._upsert(2, "another kubernetes rollout that also stalled")
        for offset in range(6):
            self._upsert(300 + offset, f"rollout notes {offset}")

        results = self.service.search_lexical(
            "kubernetes rollout", guild_id=GUILD_ID, channel_id=CHANNEL_ID,
            cross_channel=False, limit=2,
        )

        self.assertEqual(sorted(item.message_id for item in results), [1, 2])

    @contextlib.contextmanager
    def _record_fts_queries(self):
        """Collect the MATCH expression of every FTS query `search_lexical` issues.

        `sqlite3.Connection` takes no attributes, so the recorder is a proxy
        around the real connection rather than a patched method on it.
        """
        issued = []
        original = self.service._connection

        class Recorder:
            def __init__(self, connection):
                self._connection = connection

            def execute(self, sql, parameters=()):
                if "message_search_fts MATCH ?" in sql:
                    issued.append(next(p for p in parameters if isinstance(p, str)))
                return self._connection.execute(sql, parameters)

            def __getattr__(self, name):
                return getattr(self._connection, name)

        @contextlib.contextmanager
        def recording(**kwargs):
            with original(**kwargs) as connection:
                yield Recorder(connection)

        self.service._connection = recording
        try:
            yield issued
        finally:
            self.service._connection = original

    def test_a_limit_below_the_threshold_still_runs_only_the_and_query(self):
        # The companion to the test above, which cannot see this on its own: both
        # AND matches also top the OR ranking, so the rows come back the same
        # either way and only the queries issued betray the defect. Uncapped, the
        # threshold is compared against a row set `limit` already truncated, so
        # every AND form is unreachably thin -- it runs, is discarded, and OR
        # runs after it on every single retrieval.
        self._upsert(1, "the kubernetes rollout stalled in staging")
        self._upsert(2, "another kubernetes rollout that also stalled")
        for offset in range(6):
            self._upsert(300 + offset, f"rollout notes {offset}")

        with self._record_fts_queries() as issued:
            results = self.service.search_lexical(
                "kubernetes rollout", guild_id=GUILD_ID, channel_id=CHANNEL_ID,
                cross_channel=False, limit=2,
            )

        self.assertEqual(issued, ['"kubernetes" AND "rollout"'])
        self.assertEqual(sorted(item.message_id for item in results), [1, 2])

    def test_a_thin_and_match_still_pays_for_the_or_query(self):
        # The recorder's own control: when AND really is too thin the OR form
        # must follow it, so the assertion above is about the cap and not about
        # the fallback having been removed.
        self._upsert(1, "the kubernetes rollout stalled in staging")
        for offset in range(6):
            self._upsert(300 + offset, f"rollout notes {offset}")

        with self._record_fts_queries() as issued:
            self.service.search_lexical(
                "kubernetes rollout", guild_id=GUILD_ID, channel_id=CHANNEL_ID,
                cross_channel=False, limit=10,
            )

        self.assertEqual(
            issued, ['"kubernetes" AND "rollout"', '"kubernetes" OR "rollout"']
        )

    def test_hidden_and_deleted_rows_stay_out_of_both_query_forms(self):
        self._upsert(1, "the kubernetes rollout stalled in staging")
        self._upsert(2, "another kubernetes rollout, this one deleted")
        self.service.mark_deleted(2)

        self.assertEqual([item.message_id for item in self._search("kubernetes rollout")], [1])

    def test_a_query_full_of_fts_syntax_is_still_a_literal_search(self):
        # DAB-094: every token is phrase-quoted, so operators in user text are
        # data. The AND join must not have opened that back up.
        self._upsert(1, 'a message about NEAR and "quoted" operators')

        for hostile in ('NEAR OR AND', 'foo" OR bar', "alice*", "(a OR b)"):
            with self.subTest(query=hostile):
                self.assertIsInstance(self._search(hostile), list)

    def test_lexical_search_is_skipped_when_fts_is_unavailable(self):
        self._upsert(1, "the kubernetes rollout stalled in staging")
        self.service.fts_enabled = False

        self.assertEqual(self._search("kubernetes"), [])

    def test_a_broken_fts_table_degrades_to_no_lexical_results(self):
        self._upsert(1, "the kubernetes rollout stalled in staging")
        with sqlite3.connect(self.db_path) as conn:
            conn.execute("DROP TABLE message_search_fts")
            conn.commit()

        self.assertEqual(self._search("kubernetes rollout"), [])


class EmbeddingRetryResetTest(unittest.TestCase):
    def setUp(self):
        self._dir = tempfile.TemporaryDirectory()
        self.addCleanup(self._dir.cleanup)
        self.db_path = str(Path(self._dir.name) / "rag.db")
        self.service = MessageIndexService(self.db_path, embedding_model="test-embedding")
        self._upsert(self.service, 1)

    def _upsert(self, service, message_id):
        service.upsert_message(
            message_id=message_id,
            guild_id=GUILD_ID,
            channel_id=CHANNEL_ID,
            author_id=7,
            author_name="ada",
            is_bot=False,
            reply_to_message_id=None,
            created_at=datetime.now(timezone.utc),
            content_text=f"a message worth embedding number {message_id}",
        )

    def _exhaust(self, service=None, message_id=1):
        service = service or self.service
        for _ in range(service.max_embedding_attempts):
            service.mark_embedding_failed(message_id, "429 rate limited")

    def _row(self, message_id=1):
        with sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            return dict(
                conn.execute(
                    "SELECT embedding_status, embedding_attempts, next_retry_at, last_error"
                    " FROM message_embeddings WHERE message_id = ?",
                    (message_id,),
                ).fetchone()
            )

    def _pending_ids(self, service=None):
        service = service or self.service
        return [message_id for message_id, _text, _hash in service.get_pending_embeddings()]

    def test_a_terminal_failure_parks_the_row_for_one_reset_window(self):
        self._exhaust()

        row = self._row()
        self.assertEqual(row["embedding_status"], "failed")
        self.assertEqual(row["embedding_attempts"], 3)
        parked = datetime.fromisoformat(row["next_retry_at"]) - datetime.now(timezone.utc)
        self.assertAlmostEqual(parked.total_seconds() / 3600.0, 24.0, delta=0.1)
        self.assertEqual(self._pending_ids(), [], "a fresh failure must not be retried at once")

    def test_the_row_comes_back_once_the_window_has_passed(self):
        self._exhaust()
        with sqlite3.connect(self.db_path) as conn:
            conn.execute(
                "UPDATE message_embeddings SET next_retry_at = ? WHERE message_id = 1",
                ((datetime.now(timezone.utc) - timedelta(minutes=1)).isoformat(),),
            )
            conn.commit()

        self.assertEqual(self._pending_ids(), [1])

    def test_a_failure_recorded_before_this_change_is_retryable_now(self):
        # The recovery case: rows already terminal in an installed database
        # carry no retry timestamp at all, and hand-editing SQLite was the only
        # way back.
        with sqlite3.connect(self.db_path) as conn:
            conn.execute(
                "UPDATE message_embeddings SET embedding_status = 'failed',"
                " embedding_attempts = 3, next_retry_at = NULL WHERE message_id = 1"
            )
            conn.commit()

        self.assertEqual(self._pending_ids(), [1])

    def test_the_reset_window_comes_from_configuration(self):
        eager = MessageIndexService(
            self.db_path, embedding_model="test-embedding", embedding_retry_reset_hours=0.0
        )
        self._exhaust(eager)

        self.assertEqual(self._row()["embedding_status"], "failed")
        self.assertEqual(self._pending_ids(eager), [1])

    def test_a_rescued_row_that_succeeds_is_embedded_and_stops_returning(self):
        eager = MessageIndexService(
            self.db_path, embedding_model="test-embedding", embedding_retry_reset_hours=0.0
        )
        self._exhaust(eager)
        message_id, _text, content_hash = eager.get_pending_embeddings()[0]

        self.assertTrue(eager.store_embedding(message_id, [0.5, 0.5], content_hash))

        self.assertEqual(self._row()["embedding_status"], "done")
        self.assertEqual(self._pending_ids(eager), [])
        self.assertEqual(eager.get_status()["failed_embeddings"], 0)

    def _parked_hours(self, message_id=1):
        parked = datetime.fromisoformat(self._row(message_id)["next_retry_at"])
        return (parked - datetime.now(timezone.utc)).total_seconds() / 3600.0

    def test_each_terminal_failure_doubles_the_next_reset_window(self):
        # Parking always one window out made a permanently bad row cost a paid
        # embedding call every window for ever, with embedding_attempts climbing
        # without bound behind it.
        self._exhaust()
        windows = [self._parked_hours()]
        for _ in range(2):
            self.service.mark_embedding_failed(1, "content the model will never accept")
            windows.append(self._parked_hours())

        for expected, measured in zip([24.0, 48.0, 96.0], windows):
            self.assertAlmostEqual(measured, expected, delta=0.1)

    def test_the_window_grows_to_its_clamp_and_stops_there(self):
        self._exhaust()
        for _ in range(30):
            self.service.mark_embedding_failed(1, "content the model will never accept")
        clamped = self._parked_hours()

        self.service.mark_embedding_failed(1, "content the model will never accept")

        self.assertAlmostEqual(clamped, 24.0 * 1024, delta=1.0)
        self.assertAlmostEqual(self._parked_hours(), clamped, delta=1.0)
        self.assertEqual(self._pending_ids(), [])

    def test_a_rescued_row_that_fails_again_is_parked_again(self):
        self._exhaust()

        self.service.mark_embedding_failed(1, "429 rate limited")

        row = self._row()
        self.assertEqual(row["embedding_status"], "failed")
        self.assertEqual(row["embedding_attempts"], 4)
        self.assertEqual(self._pending_ids(), [], "a failed retry must not spin")

    def test_a_still_pending_row_keeps_its_short_backoff(self):
        # The pre-terminal path is unchanged: minutes, not a day.
        self.service.mark_embedding_failed(1, "transient")

        row = self._row()
        self.assertEqual(row["embedding_status"], "pending")
        delay = datetime.fromisoformat(row["next_retry_at"]) - datetime.now(timezone.utc)
        self.assertLess(delay.total_seconds(), 3600.0)

    def test_a_skipped_row_is_never_offered_for_embedding(self):
        self._upsert(self.service, 2)
        with sqlite3.connect(self.db_path) as conn:
            conn.execute(
                "UPDATE message_embeddings SET embedding_status = 'skipped' WHERE message_id = 2"
            )
            conn.commit()

        self.assertEqual(self._pending_ids(), [1])


class SemanticSearchCostTest(unittest.TestCase):
    DIMENSIONS = 8

    def setUp(self):
        self._dir = tempfile.TemporaryDirectory()
        self.addCleanup(self._dir.cleanup)
        self.db_path = str(Path(self._dir.name) / "rag.db")
        self.service = self._service()
        self.now = datetime.now(timezone.utc)

    def _service(self, **kwargs):
        return MessageIndexService(
            self.db_path,
            embedding_model="test-embedding",
            embedding_dimensions=self.DIMENSIONS,
            **kwargs,
        )

    def _store(self, message_id, vector, *, channel_id=CHANNEL_ID, guild_id=GUILD_ID):
        self.service.upsert_message(
            message_id=message_id,
            guild_id=guild_id,
            channel_id=channel_id,
            author_id=7,
            author_name="ada",
            is_bot=False,
            reply_to_message_id=None,
            created_at=datetime.now(timezone.utc),
            content_text=f"an eligible message numbered {message_id}",
        )
        with sqlite3.connect(self.db_path) as conn:
            content_hash = conn.execute(
                "SELECT content_hash FROM message_embeddings WHERE message_id = ?",
                (message_id,),
            ).fetchone()[0]
        self.assertTrue(self.service.store_embedding(message_id, list(vector), content_hash))

    def _search(self, query, *, limit=5, service=None, **kwargs):
        service = service or self.service
        return service.search_semantic(
            list(query),
            guild_id=kwargs.pop("guild_id", GUILD_ID),
            channel_id=kwargs.pop("channel_id", CHANNEL_ID),
            cross_channel=kwargs.pop("cross_channel", False),
            limit=limit,
            **kwargs,
        )

    @staticmethod
    def _cosine(left, right):
        dot = sum(a * b for a, b in zip(left, right))
        norms = math.sqrt(sum(a * a for a in left)) * math.sqrt(sum(b * b for b in right))
        return dot / norms if norms else 0.0

    def _reference(self, vectors, query, limit):
        """What the pre-optimisation implementation ranked, computed plainly."""
        scored = [
            (message_id, self._cosine(vector, query))
            for message_id, vector in sorted(vectors.items())
        ]
        best = sorted((item for item in scored if item[1] > 0), key=lambda item: -item[1])
        return [message_id for message_id, _score in best[:limit]]

    def test_the_ranking_matches_a_plain_cosine_over_the_same_vectors(self):
        rng = np.random.default_rng(11)
        vectors = {
            1000 + index: rng.standard_normal(self.DIMENSIONS).astype(np.float32).tolist()
            for index in range(60)
        }
        for message_id, vector in vectors.items():
            self._store(message_id, vector)

        for trial in range(20):
            query = rng.standard_normal(self.DIMENSIONS).astype(np.float32).tolist()
            for limit in (1, 5, 24, 500):
                with self.subTest(trial=trial, limit=limit):
                    self.assertEqual(
                        [item.message_id for item in self._search(query, limit=limit)],
                        self._reference(vectors, query, limit),
                    )

    def test_scores_are_cosines_whatever_magnitude_was_stored(self):
        self._store(1, [3.0, 4.0] + [0.0] * (self.DIMENSIONS - 2))

        result = self._search([30.0, 40.0] + [0.0] * (self.DIMENSIONS - 2))[0]

        self.assertAlmostEqual(result.semantic_score, 1.0, places=5)

    def test_a_vector_added_to_a_warm_cache_is_normalised_too(self):
        # _cache_upsert is the incremental path: a vector stored after the
        # first search never goes through the loader.
        self._store(1, [1.0] + [0.0] * (self.DIMENSIONS - 1))
        self.assertTrue(self._search([1.0] + [0.0] * (self.DIMENSIONS - 1)))

        self._store(2, [0.0, 500.0] + [0.0] * (self.DIMENSIONS - 2))

        result = self._search([0.0, 1.0] + [0.0] * (self.DIMENSIONS - 2))[0]
        self.assertEqual(result.message_id, 2)
        self.assertAlmostEqual(result.semantic_score, 1.0, places=5)

    def test_a_zero_vector_scores_nothing_instead_of_nan(self):
        self._store(1, [0.0] * self.DIMENSIONS)
        self._store(2, [1.0] + [0.0] * (self.DIMENSIONS - 1))

        results = self._search([1.0] + [0.0] * (self.DIMENSIONS - 1))

        self.assertEqual([item.message_id for item in results], [2])
        self.assertTrue(all(math.isfinite(item.semantic_score) for item in results))

    def test_the_scope_and_exclusion_masks_still_select_the_same_rows(self):
        self._store(1, [1.0] + [0.0] * (self.DIMENSIONS - 1))
        self._store(2, [0.9, 0.1] + [0.0] * (self.DIMENSIONS - 2))
        self._store(3, [0.8, 0.2] + [0.0] * (self.DIMENSIONS - 2), channel_id=21)
        query = [1.0] + [0.0] * (self.DIMENSIONS - 1)

        self.assertEqual([item.message_id for item in self._search(query)], [1, 2])
        self.assertEqual(
            [item.message_id for item in self._search(query, exclude_message_ids=[1])], [2]
        )
        self.assertEqual(
            [item.message_id for item in self._search(query, cross_channel=True)], [1, 2, 3]
        )
        self.assertEqual(self._search(query, channel_id=999), [])

    def test_excluding_everything_returns_nothing_rather_than_the_whole_channel(self):
        self._store(1, [1.0] + [0.0] * (self.DIMENSIONS - 1))

        self.assertEqual(self._search([1.0] + [0.0] * (self.DIMENSIONS - 1), exclude_message_ids=[1]), [])

    def test_the_sql_fallback_ranks_identically_to_the_cache(self):
        rng = np.random.default_rng(3)
        vectors = {
            1000 + index: rng.standard_normal(self.DIMENSIONS).astype(np.float32).tolist()
            for index in range(15)
        }
        for message_id, vector in vectors.items():
            self._store(message_id, vector)
        uncached = self._service(vector_cache_enabled=False)
        query = rng.standard_normal(self.DIMENSIONS).astype(np.float32).tolist()

        cached_ids = [item.message_id for item in self._search(query, limit=8)]
        fallback = uncached.search_semantic(
            query, guild_id=GUILD_ID, channel_id=CHANNEL_ID, cross_channel=False, limit=8
        )

        self.assertEqual([item.message_id for item in fallback], cached_ids)
        self.assertEqual(cached_ids, self._reference(vectors, query, 8))

    def test_a_query_of_the_wrong_width_takes_the_sql_fallback(self):
        self._store(1, [1.0] + [0.0] * (self.DIMENSIONS - 1))

        self.assertEqual(self._search([1.0, 0.0]), [], "a mismatched query must not score")
        self.assertEqual(self._search([]), [])
        self.assertEqual(self._search([0.0] * self.DIMENSIONS), [])

    def test_top_k_reproduces_a_full_argsort_including_ties(self):
        rng = np.random.default_rng(5)
        for trial in range(40):
            scores = rng.integers(-3, 4, size=25).astype(np.float32)  # dense ties
            if trial % 4 == 0:
                scores[rng.integers(0, 25, size=3)] = -np.inf
            for k in (1, 2, 7, 25):
                with self.subTest(trial=trial, k=k):
                    self.assertEqual(
                        list(MessageIndexService._top_k(scores, k)),
                        list(np.argsort(-scores, kind="stable")[:k]),
                    )


if __name__ == "__main__":
    unittest.main()
