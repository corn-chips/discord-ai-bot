"""Per-person profile memory: storage, incremental summarization, injection.

"Who is X and what did they do?" cannot be answered from snippets once a
person's history runs to hundreds of messages. `EntityProfileService` keeps one
summarized card per (guild, author), refreshed in the background, and
`HybridContextRetriever` injects it when the query names that person.
"""

import sqlite3
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import discord
from discord.ext import commands

from src.bot.commands import setup_commands
from src.config import BotConfig
from src.services.context_pack_builder import MIN_RETRIEVAL_SLOTS, ContextPackBuilder
from src.services.entity_profile_service import RETRY_MAX_SECONDS, EntityProfileService
from src.services.hybrid_context_retriever import HybridContextRetriever
from src.services.message_index_service import MessageIndexService

BASE = datetime(2026, 1, 1, 12, 0, tzinfo=timezone.utc)
GUILD = 10
CHANNEL = 20
ALICE = 111222333444555666
BOB = 222333444555666777


def elapse_backoff(db_path, author_id=ALICE):
    """Pretend a recorded failure's retry delay has run out."""
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            "UPDATE entity_profiles SET next_attempt_at = ? WHERE author_id = ?",
            ((datetime.now(timezone.utc) - timedelta(hours=1)).isoformat(), author_id),
        )


def seed(index, message_id, *, author_id=ALICE, author_name="alice", minutes=0.0, text=None):
    index.upsert_message(
        message_id=message_id,
        guild_id=GUILD,
        channel_id=CHANNEL,
        author_id=author_id,
        author_name=author_name,
        is_bot=False,
        reply_to_message_id=None,
        created_at=BASE + timedelta(minutes=minutes),
        content_text=text or f"message {message_id} about the deploy script rollout",
    )


class EntityProfileServiceTest(unittest.TestCase):
    def setUp(self):
        self._dir = tempfile.TemporaryDirectory()
        self.addCleanup(self._dir.cleanup)
        self.db_path = str(Path(self._dir.name) / "rag.db")
        self.index = MessageIndexService(self.db_path, embedding_model="test-embedding")
        self.profiles = EntityProfileService(self.db_path)

    def _seed_history(self, count, *, author_id=ALICE, author_name="alice", start=0):
        for offset in range(count):
            seed(
                self.index,
                start + offset + 1,
                author_id=author_id,
                author_name=author_name,
                minutes=offset,
            )

    def _profile(self, author_id=ALICE):
        return self.profiles.get_profile(GUILD, author_id)

    def test_schema_creation_is_idempotent_and_keeps_existing_rows(self):
        self._seed_history(3)
        self.profiles.observe_authors(GUILD)
        self.profiles.store_summary(GUILD, ALICE, summary="knows the deploy script", covered_message_id=3)

        # There is no migration framework here: every construction re-runs
        # CREATE TABLE IF NOT EXISTS against a database that may already hold
        # work nothing is allowed to discard.
        reopened = EntityProfileService(self.db_path)

        profile = reopened.get_profile(GUILD, ALICE)
        self.assertEqual(profile.summary, "knows the deploy script")
        self.assertEqual(profile.summarized_message_id, 3)
        with sqlite3.connect(self.db_path) as conn:
            self.assertEqual(
                conn.execute("SELECT COUNT(*) FROM entity_profiles").fetchone()[0], 1
            )

    def test_observation_records_names_span_and_counts_without_touching_the_summary(self):
        self._seed_history(2)
        self.profiles.observe_authors(GUILD)
        self.profiles.store_summary(GUILD, ALICE, summary="original", covered_message_id=2)
        seed(self.index, 3, author_name="alice_renamed", minutes=5)

        self.profiles.observe_authors(GUILD)

        profile = self._profile()
        self.assertEqual(profile.display_name, "alice_renamed")
        self.assertEqual(profile.aliases, ["alice"])
        self.assertEqual(profile.message_count, 3)
        self.assertEqual(profile.latest_message_id, 3)
        self.assertEqual(profile.summary, "original")
        self.assertEqual(profile.summarized_message_id, 2)

    def test_a_member_below_the_minimum_message_count_is_not_profiled(self):
        self._seed_history(5)
        self.profiles.observe_authors(GUILD)

        due = self.profiles.profiles_due(
            GUILD, min_messages=20, refresh_hours=24.0, limit=10
        )
        self.assertEqual(due, [])

        self._seed_history(20, start=100)
        self.profiles.observe_authors(GUILD)
        due = self.profiles.profiles_due(
            GUILD, min_messages=20, refresh_hours=24.0, limit=10
        )
        self.assertEqual([profile.author_id for profile in due], [ALICE])

    def test_a_fresh_summary_is_not_rebuilt_before_the_refresh_interval(self):
        self._seed_history(25)
        self.profiles.observe_authors(GUILD)
        self.profiles.store_summary(GUILD, ALICE, summary="a card", covered_message_id=10)
        seed(self.index, 500, minutes=90)
        self.profiles.observe_authors(GUILD)

        self.assertEqual(
            self.profiles.profiles_due(GUILD, min_messages=20, refresh_hours=24.0, limit=10),
            [],
        )
        self.assertEqual(
            [
                profile.author_id
                for profile in self.profiles.profiles_due(
                    GUILD, min_messages=20, refresh_hours=0.0, limit=10
                )
            ],
            [ALICE],
        )

    def test_a_profile_with_no_new_messages_is_never_due(self):
        self._seed_history(25)
        self.profiles.observe_authors(GUILD)
        self.profiles.store_summary(GUILD, ALICE, summary="a card", covered_message_id=25)

        self.assertEqual(
            self.profiles.profiles_due(GUILD, min_messages=20, refresh_hours=0.0, limit=10),
            [],
        )

    def test_collected_activity_skips_history_the_watermark_already_covers(self):
        seed(self.index, 1, minutes=0, text="alice wrote the first deploy script")
        seed(self.index, 2, author_id=BOB, author_name="bob", minutes=1, text="bob replied about it")
        seed(self.index, 3, minutes=90, text="alice rewrote the second rollout plan")
        self.profiles.observe_authors(GUILD)

        transcript, watermark = self.profiles.collect_new_activity(
            self._profile(),
            max_conversations=8,
            max_messages_per_conversation=30,
            max_chars=6000,
        )
        self.assertIn("first deploy script", transcript)
        self.assertIn("second rollout plan", transcript)
        self.assertEqual(watermark, 3)

        self.profiles.store_summary(GUILD, ALICE, summary="a card", covered_message_id=watermark)
        seed(self.index, 4, minutes=200, text="alice shipped the third release")
        transcript, watermark = self.profiles.collect_new_activity(
            self._profile(),
            max_conversations=8,
            max_messages_per_conversation=30,
            max_chars=6000,
        )

        self.assertIn("third release", transcript)
        self.assertNotIn("first deploy script", transcript)
        self.assertEqual(watermark, 4)

    def test_a_capped_pass_leaves_the_uncovered_conversations_behind_the_watermark(self):
        seed(self.index, 1, minutes=0, text="alice on the first topic")
        seed(self.index, 2, minutes=90, text="alice on the second topic")
        self.profiles.observe_authors(GUILD)

        transcript, watermark = self.profiles.collect_new_activity(
            self._profile(),
            max_conversations=1,
            max_messages_per_conversation=30,
            max_chars=6000,
        )

        self.assertIn("first topic", transcript)
        self.assertNotIn("second topic", transcript)
        # Message 2 stays ahead of the watermark, so the next pass reads it.
        self.assertEqual(watermark, 1)

    def test_a_capped_pass_advances_even_when_ids_run_against_the_clock(self):
        # Scanning in time order let the clamp land on the old watermark, so the
        # pass covered something, reported no progress, and was redone forever.
        seed(self.index, 10, minutes=0, text="alice on the earlier topic")
        seed(self.index, 1, minutes=90, text="alice on the later topic")
        self.profiles.observe_authors(GUILD)

        transcript, watermark = self.profiles.collect_new_activity(
            self._profile(),
            max_conversations=1,
            max_messages_per_conversation=30,
            max_chars=6000,
        )

        self.assertTrue(transcript)
        self.assertGreater(watermark, self._profile().summarized_message_id)

    def _due(self, refresh_hours=24.0):
        return [
            due.author_id
            for due in self.profiles.profiles_due(
                GUILD, min_messages=20, refresh_hours=refresh_hours, limit=10
            )
        ]

    def test_recording_a_failure_defers_the_row_without_skipping_it_for_good(self):
        self._seed_history(25)
        self.profiles.observe_authors(GUILD)

        self.profiles.record_failure(GUILD, ALICE, "quota exhausted")

        profile = self._profile()
        self.assertEqual(profile.last_error, "quota exhausted")
        self.assertEqual(profile.failure_count, 1)
        # The watermark is untouched, so no history is lost...
        self.assertIsNone(profile.summary)
        self.assertEqual(profile.summarized_message_id, 0)
        # ...but the row waits out its backoff before costing another call.
        self.assertEqual(self._due(), [])

        elapse_backoff(self.db_path)
        self.assertEqual(self._due(), [ALICE])

    def test_each_further_failure_waits_longer_up_to_a_ceiling(self):
        self._seed_history(25)
        self.profiles.observe_authors(GUILD)

        delays = []
        for _attempt in range(3):
            before = datetime.now(timezone.utc)
            self.profiles.record_failure(GUILD, ALICE, "quota exhausted")
            delays.append(
                (
                    datetime.fromisoformat(self._profile().next_attempt_at) - before
                ).total_seconds()
            )

        self.assertEqual(self._profile().failure_count, 3)
        self.assertEqual(delays, sorted(delays))
        self.assertLess(delays[0], delays[-1])

        for _attempt in range(15):
            self.profiles.record_failure(GUILD, ALICE, "quota exhausted")
        waited = (
            datetime.fromisoformat(self._profile().next_attempt_at)
            - datetime.now(timezone.utc)
        ).total_seconds()
        self.assertLessEqual(waited, RETRY_MAX_SECONDS)

    def test_a_stored_summary_clears_the_failure_backoff(self):
        self._seed_history(25)
        self.profiles.observe_authors(GUILD)
        self.profiles.record_failure(GUILD, ALICE, "quota exhausted")

        self.profiles.store_summary(GUILD, ALICE, summary="a card", covered_message_id=1)

        profile = self._profile()
        self.assertEqual(profile.failure_count, 0)
        self.assertIsNone(profile.next_attempt_at)
        self.assertEqual(self._due(refresh_hours=0.0), [ALICE])

    def test_clearing_a_summary_rewinds_the_watermark_for_a_rebuild(self):
        self._seed_history(25)
        self.profiles.observe_authors(GUILD)
        self.profiles.store_summary(GUILD, ALICE, summary="stale", covered_message_id=25)

        self.assertTrue(self.profiles.clear_summary(GUILD, ALICE))

        profile = self._profile()
        self.assertIsNone(profile.summary)
        self.assertEqual(profile.summarized_message_id, 0)
        self.assertEqual(
            len(self.profiles.profiles_due(GUILD, min_messages=20, refresh_hours=24.0, limit=10)),
            1,
        )

    def test_lookup_matches_a_name_an_alias_and_a_mention(self):
        self._seed_history(2)
        seed(self.index, 50, author_name="ali", minutes=10)
        self.profiles.observe_authors(GUILD)
        self.profiles.store_summary(GUILD, ALICE, summary="runs deploys", covered_message_id=50)

        for query in (
            "who is alice and what did she do",
            "what has ali been up to",
            f"tell me about <@{ALICE}>",
        ):
            with self.subTest(query=query):
                matches = self.profiles.find_profiles_for_query(GUILD, query, limit=2)
                self.assertEqual([match.author_id for match in matches], [ALICE])

    def test_lookup_ignores_unnamed_people_and_unsummarized_rows(self):
        self._seed_history(2)
        self._seed_history(2, author_id=BOB, author_name="bob", start=60)
        self.profiles.observe_authors(GUILD)
        self.profiles.store_summary(GUILD, ALICE, summary="runs deploys", covered_message_id=2)

        self.assertEqual(self.profiles.find_profiles_for_query(GUILD, "who is bob", limit=2), [])
        self.assertEqual(self.profiles.find_profiles_for_query(GUILD, "", limit=2), [])
        self.assertEqual(
            self.profiles.find_profiles_for_query(GUILD, "who is alice", limit=0), []
        )

    def test_a_card_is_clamped_to_the_configured_size(self):
        self._seed_history(2)
        self.profiles.observe_authors(GUILD)
        self.profiles.store_summary(GUILD, ALICE, summary="x" * 4000, covered_message_id=2)

        card = self.profiles.format_card(self._profile(), max_chars=200)

        self.assertLessEqual(len(card), 200 + len(" [truncated]"))
        self.assertTrue(card.endswith("[truncated]"))

    def test_an_unsummarized_profile_renders_no_card(self):
        self._seed_history(2)
        self.profiles.observe_authors(GUILD)

        self.assertEqual(self.profiles.format_card(self._profile(), max_chars=200), "")


class EntityProfileSummarizationTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self._dir = tempfile.TemporaryDirectory()
        self.addCleanup(self._dir.cleanup)
        self.db_path = str(Path(self._dir.name) / "rag.db")
        self.index = MessageIndexService(self.db_path, embedding_model="test-embedding")
        self.profiles = EntityProfileService(self.db_path)
        self.config = BotConfig(
            rag_database_path=self.db_path,
            rag_entity_profile_min_messages=2,
            rag_embedding_batch_delay_seconds=0.0,
        )
        self.gemini_client = SimpleNamespace(
            client=object(),
            generate_response=AsyncMock(
                return_value=SimpleNamespace(success=True, content="alice owns deploys")
            ),
        )
        self.retriever = HybridContextRetriever(
            config=self.config,
            message_index=self.index,
            context_collector=object(),
            gemini_client=self.gemini_client,
            pack_builder=ContextPackBuilder(),
            entity_profile_service=self.profiles,
        )
        self.addAsyncCleanup(self.retriever.close)

    async def test_a_background_pass_summarizes_and_advances_the_watermark(self):
        seed(self.index, 1, minutes=0, text="alice wrote the first deploy script")
        seed(self.index, 2, minutes=90, text="alice rewrote the rollout plan")

        summarized, failed = await self.retriever._drain_entity_profiles(guild_id=GUILD)

        self.assertEqual((summarized, failed), (1, 0))
        profile = self.profiles.get_profile(GUILD, ALICE)
        self.assertEqual(profile.summary, "alice owns deploys")
        self.assertEqual(profile.summarized_message_id, 2)
        self.assertIsNone(profile.last_error)

    async def test_a_second_pass_reads_only_what_the_first_did_not(self):
        seed(self.index, 1, minutes=0, text="alice wrote the first deploy script")
        seed(self.index, 2, minutes=90, text="alice rewrote the rollout plan")
        await self.retriever._drain_entity_profiles(guild_id=GUILD)
        seed(self.index, 3, minutes=300, text="alice shipped the third release")

        # Nothing new yet for the refresh window, so the pass must not bill a
        # second call at all.
        self.config.rag_entity_profile_refresh_hours = 24.0
        await self.retriever._drain_entity_profiles(guild_id=GUILD)
        self.assertEqual(self.gemini_client.generate_response.await_count, 1)

        self.config.rag_entity_profile_refresh_hours = 0.0
        await self.retriever._drain_entity_profiles(guild_id=GUILD)

        self.assertEqual(self.gemini_client.generate_response.await_count, 2)
        prompt = self.gemini_client.generate_response.await_args.args[0]
        self.assertIn("third release", prompt)
        self.assertNotIn("first deploy script", prompt)
        # The previous card is carried in so the rewrite stays cumulative.
        self.assertIn("alice owns deploys", prompt)
        self.assertEqual(self.profiles.get_profile(GUILD, ALICE).summarized_message_id, 3)

    async def test_a_member_below_the_minimum_is_never_sent_to_the_model(self):
        self.config.rag_entity_profile_min_messages = 5
        seed(self.index, 1, minutes=0)
        seed(self.index, 2, minutes=90)

        summarized, failed = await self.retriever._drain_entity_profiles(guild_id=GUILD)

        self.assertEqual((summarized, failed), (0, 0))
        self.gemini_client.generate_response.assert_not_awaited()

    async def test_a_model_failure_is_retryable_and_does_not_poison_the_row(self):
        seed(self.index, 1, minutes=0, text="alice wrote the first deploy script")
        seed(self.index, 2, minutes=90, text="alice rewrote the rollout plan")
        self.gemini_client.generate_response = AsyncMock(
            return_value=SimpleNamespace(success=False, content="", error_type="api_error")
        )

        summarized, failed = await self.retriever._drain_entity_profiles(guild_id=GUILD)

        self.assertEqual((summarized, failed), (0, 1))
        profile = self.profiles.get_profile(GUILD, ALICE)
        self.assertIsNone(profile.summary)
        self.assertEqual(profile.summarized_message_id, 0)
        self.assertEqual(profile.last_error, "api_error")

        self.gemini_client.generate_response = AsyncMock(
            return_value=SimpleNamespace(success=True, content="alice owns deploys")
        )
        elapse_backoff(self.db_path)
        summarized, failed = await self.retriever._drain_entity_profiles(guild_id=GUILD)

        self.assertEqual((summarized, failed), (1, 0))
        profile = self.profiles.get_profile(GUILD, ALICE)
        self.assertEqual(profile.summary, "alice owns deploys")
        self.assertIsNone(profile.last_error)

    async def test_a_raising_model_call_is_also_retryable(self):
        seed(self.index, 1, minutes=0, text="alice wrote the first deploy script")
        seed(self.index, 2, minutes=90, text="alice rewrote the rollout plan")
        self.gemini_client.generate_response = AsyncMock(side_effect=RuntimeError("boom"))

        summarized, failed = await self.retriever._drain_entity_profiles(guild_id=GUILD)

        self.assertEqual((summarized, failed), (0, 1))
        profile = self.profiles.get_profile(GUILD, ALICE)
        self.assertEqual(profile.summarized_message_id, 0)
        self.assertIn("boom", profile.last_error)
        elapse_backoff(self.db_path)
        self.assertEqual(
            len(self.profiles.profiles_due(GUILD, min_messages=2, refresh_hours=24.0, limit=5)),
            1,
        )

    async def test_a_permanently_failing_profile_is_not_rebilled_every_pass(self):
        # profiles_due gates on last_summarized_at, which a failure deliberately
        # leaves alone -- so without a backoff the row was due again on every
        # pass and every pass bought another generate_response call.
        seed(self.index, 1, minutes=0, text="alice wrote the first deploy script")
        seed(self.index, 2, minutes=90, text="alice rewrote the rollout plan")
        self.gemini_client.generate_response = AsyncMock(side_effect=RuntimeError("boom"))

        self.assertEqual(await self.retriever._drain_entity_profiles(guild_id=GUILD), (0, 1))
        for _pass in range(3):
            self.assertEqual(
                await self.retriever._drain_entity_profiles(guild_id=GUILD), (0, 0)
            )

        self.assertEqual(self.gemini_client.generate_response.await_count, 1)

    async def test_one_pass_cannot_buy_the_whole_embedding_drain_budget(self):
        # The profile drain used to read rag_embedding_drain_max_batches whole,
        # so a default configuration allowed 200 completions in one pass.
        self.config.rag_embedding_drain_max_batches = 200
        for author in range(60):
            seed(self.index, 1000 + author, author_id=author + 1, author_name=f"user{author}")
            seed(self.index, 2000 + author, author_id=author + 1, author_name=f"user{author}", minutes=90)

        summarized, failed = await self.retriever._drain_entity_profiles(guild_id=GUILD)

        self.assertEqual(
            summarized + failed, self.retriever.PROFILE_DRAIN_MAX_PROFILES
        )
        self.assertEqual(
            self.gemini_client.generate_response.await_count,
            self.retriever.PROFILE_DRAIN_MAX_PROFILES,
        )

    async def test_scheduling_is_debounced_per_guild(self):
        seed(self.index, 1, minutes=0)
        seed(self.index, 2, minutes=90)
        self.retriever.BACKGROUND_PROFILE_DELAY_SECONDS = 0

        self.retriever.schedule_entity_profiles(GUILD)
        first = self.retriever._profile_tasks[GUILD]
        self.retriever.schedule_entity_profiles(GUILD)
        second = self.retriever._profile_tasks[GUILD]
        await first

        self.assertIs(first, second)
        self.gemini_client.generate_response.assert_awaited_once()

    async def test_the_whole_pass_is_skipped_when_profiles_are_disabled(self):
        self.config.rag_entity_profiles_enabled = False
        seed(self.index, 1, minutes=0)
        seed(self.index, 2, minutes=90)

        self.retriever.schedule_entity_profiles(GUILD)
        self.assertEqual(self.retriever._profile_tasks, {})

        # And the drain refuses on its own, so no other caller can start one.
        self.assertEqual(await self.retriever._drain_entity_profiles(guild_id=GUILD), (0, 0))
        self.gemini_client.generate_response.assert_not_awaited()
        self.assertIsNone(self.profiles.get_profile(GUILD, ALICE))


class EntityProfileInjectionTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self._dir = tempfile.TemporaryDirectory()
        self.addCleanup(self._dir.cleanup)
        self.db_path = str(Path(self._dir.name) / "rag.db")
        self.index = MessageIndexService(self.db_path, embedding_model="test-embedding")
        self.profiles = EntityProfileService(self.db_path)
        for offset in range(6):
            seed(
                self.index,
                offset + 1,
                minutes=offset,
                text=f"alice discussed the deploy script rollout, part {offset}",
            )
        self.profiles.observe_authors(GUILD)
        self.profiles.store_summary(
            GUILD, ALICE, summary="alice maintains the deploy script", covered_message_id=6
        )
        self.config = BotConfig(rag_database_path=self.db_path)
        self.pins = []
        self.retriever = HybridContextRetriever(
            config=self.config,
            message_index=self.index,
            context_collector=object(),
            gemini_client=SimpleNamespace(client=None),
            pack_builder=ContextPackBuilder(),
            pin_service=SimpleNamespace(get_pins=lambda channel_id: self.pins),
            entity_profile_service=self.profiles,
        )
        self.addAsyncCleanup(self.retriever.close)

    def _set_pins(self, count):
        self.pins = [
            (index + 1, f"standing instruction {index}", "op", "op", BASE.isoformat())
            for index in range(count)
        ]

    async def _retrieve(self, prompt):
        message = SimpleNamespace(
            id=9999,
            guild=SimpleNamespace(id=GUILD),
            channel=SimpleNamespace(id=CHANNEL),
            author=SimpleNamespace(id=BOB),
            reference=None,
        )
        return await self.retriever.retrieve(
            message=message,
            user_prompt=prompt,
            complexity_level="low",
        )

    @staticmethod
    def _cards(packed):
        return [ctx for ctx in packed if ctx.retrieval_source == "entity_profile"]

    async def test_a_query_naming_a_person_gets_their_card(self):
        packed = await self._retrieve("who is alice and what did she do")

        cards = self._cards(packed)
        self.assertEqual(len(cards), 1)
        self.assertIn("alice maintains the deploy script", cards[0].content)
        self.assertIn(str(ALICE), cards[0].content)
        # Below pins, above ordinary retrieval.
        self.assertEqual(packed.index(cards[0]), 0)
        self.assertTrue(any(ctx.retrieval_source != "entity_profile" for ctx in packed))

    async def test_an_alias_and_a_mention_find_the_same_card(self):
        seed(self.index, 90, author_name="ali", minutes=200)
        self.profiles.observe_authors(GUILD)

        for prompt in ("what did ali build", f"what did <@{ALICE}> build"):
            with self.subTest(prompt=prompt):
                cards = self._cards(await self._retrieve(prompt))
                self.assertEqual(len(cards), 1)
                self.assertIn("deploy script", cards[0].content)

    async def test_a_query_naming_nobody_gets_no_card(self):
        self.assertEqual(self._cards(await self._retrieve("how do i restart the job")), [])

    async def test_cards_never_spend_the_retrieval_floor(self):
        # DAB-073: pins once drove available_retrieval_slots to zero, so the
        # index was searched, scored and paid for and then dropped. Cards may
        # only take slots ABOVE that floor, so once pins have reached it the
        # profile lookup does not even run.
        #
        # max_messages is 6 at "low" complexity and the floor is 2.
        for pin_count, expected_cards in ((0, 1), (3, 1), (4, 0), (6, 0)):
            with self.subTest(pins=pin_count):
                self._set_pins(pin_count)
                packed = await self._retrieve("who is alice")
                self.assertEqual(len(self._cards(packed)), expected_cards)

                slots = self.retriever.pack_builder.available_retrieval_slots(
                    pinned_context=[
                        ctx for ctx in packed if getattr(ctx, "is_pinned_memory", False)
                    ],
                    reply_context=[],
                    max_messages=6,
                )
                self.assertGreaterEqual(slots, MIN_RETRIEVAL_SLOTS)

    async def test_disabling_the_feature_injects_nothing(self):
        self.config.rag_entity_profiles_enabled = False

        self.assertEqual(self._cards(await self._retrieve("who is alice")), [])

    async def test_a_lookup_failure_degrades_to_ordinary_retrieval(self):
        self.retriever.entity_profile_service = SimpleNamespace(
            find_profiles_for_query_async=AsyncMock(side_effect=sqlite3.OperationalError("locked"))
        )

        packed = await self._retrieve("who is alice")

        self.assertEqual(self._cards(packed), [])
        self.assertTrue(packed)


class DirectMessageProfileTest(unittest.IsolatedAsyncioTestCase):
    """Profiles are guild memory: a DM must never see another DM's card."""

    DM_A = 4001
    DM_B = 4002

    async def asyncSetUp(self):
        self._dir = tempfile.TemporaryDirectory()
        self.addCleanup(self._dir.cleanup)
        self.db_path = str(Path(self._dir.name) / "rag.db")
        self.index = MessageIndexService(self.db_path, embedding_model="test-embedding")
        self.profiles = EntityProfileService(self.db_path)
        self.config = BotConfig(
            rag_database_path=self.db_path, rag_entity_profile_min_messages=1
        )
        self.gemini_client = SimpleNamespace(
            client=object(),
            embed_texts=AsyncMock(return_value=[[]]),
            generate_response=AsyncMock(
                return_value=SimpleNamespace(success=True, content="alice's private life")
            ),
        )
        self.retriever = HybridContextRetriever(
            config=self.config,
            message_index=self.index,
            context_collector=object(),
            gemini_client=self.gemini_client,
            pack_builder=ContextPackBuilder(),
            entity_profile_service=self.profiles,
        )
        self.addAsyncCleanup(self.retriever.close)

    def _dm(self, message_id, *, channel_id, author_id, author_name, text):
        self.index.upsert_message(
            message_id=message_id,
            guild_id=None,
            channel_id=channel_id,
            author_id=author_id,
            author_name=author_name,
            is_bot=False,
            reply_to_message_id=None,
            created_at=BASE + timedelta(minutes=message_id),
            content_text=text,
        )

    def _leak_a_shared_dm_card(self):
        """Write the row DM profiling used to produce: guild 0, shared by all."""
        with sqlite3.connect(self.db_path) as conn:
            conn.execute(
                "INSERT INTO entity_profiles (guild_id, author_id, display_name, aliases, "
                "message_count, latest_message_id, summary, summarized_message_id, updated_at) "
                "VALUES (0, ?, 'alice', '[]', 9, 9, 'alice confided her salary', 9, ?)",
                (ALICE, BASE.isoformat()),
            )

    async def test_a_direct_message_is_never_profiled(self):
        self._dm(1, channel_id=self.DM_A, author_id=ALICE, author_name="alice", text="my salary is secret")
        self._dm(2, channel_id=self.DM_A, author_id=ALICE, author_name="alice", text="and so is my address")

        self.assertEqual(self.profiles.observe_authors(None), 0)
        self.assertEqual(await self.retriever._drain_entity_profiles(guild_id=None), (0, 0))
        self.gemini_client.generate_response.assert_not_awaited()
        self.assertIsNone(self.profiles.get_profile(None, ALICE))
        self.retriever.BACKGROUND_PROFILE_DELAY_SECONDS = 0
        self.retriever.schedule_entity_profiles(None)
        self.assertEqual(self.retriever._profile_tasks, {})

    async def test_one_dm_cannot_be_handed_another_dms_card(self):
        self._leak_a_shared_dm_card()
        self._dm(3, channel_id=self.DM_B, author_id=BOB, author_name="bob", text="hello there")

        packed = await self.retriever.retrieve(
            message=SimpleNamespace(
                id=9999,
                guild=None,
                channel=SimpleNamespace(id=self.DM_B),
                author=SimpleNamespace(id=BOB),
                reference=None,
            ),
            user_prompt="who is alice",
            complexity_level="low",
        )

        self.assertEqual([ctx for ctx in packed if ctx.retrieval_source == "entity_profile"], [])
        self.assertNotIn("salary", "\n".join(ctx.content for ctx in packed))

    def test_reopening_the_database_drops_the_shared_dm_namespace(self):
        self._leak_a_shared_dm_card()

        EntityProfileService(self.db_path)

        with sqlite3.connect(self.db_path) as conn:
            self.assertEqual(
                conn.execute("SELECT COUNT(*) FROM entity_profiles").fetchone()[0], 0
            )


class EntityProfileCommandTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self._dir = tempfile.TemporaryDirectory()
        self.addCleanup(self._dir.cleanup)
        self.db_path = str(Path(self._dir.name) / "rag.db")
        self.index = MessageIndexService(self.db_path, embedding_model="test-embedding")
        self.profiles = EntityProfileService(self.db_path)
        self.config = BotConfig(
            token_db_path=str(Path(self._dir.name) / "tokens.db"),
            rag_database_path=self.db_path,
        )
        self.bot = commands.Bot(command_prefix="!", intents=discord.Intents.none())
        self.bot.config = self.config
        self.bot.image_processing_service = None
        self.bot._entity_profile_service = self.profiles
        self.bot.hybrid_context_retriever = SimpleNamespace(
            schedule_entity_profiles=Mock(),
            set_pin_service=Mock(),
        )
        self.addAsyncCleanup(self.bot.close)
        await setup_commands(self.bot, self.config, object(), object(), None)

    def _command(self, name):
        group = self.bot.tree.get_command("profile")
        return group.get_command(name)

    @staticmethod
    def _interaction(manage_guild=True):
        return SimpleNamespace(
            guild_id=GUILD,
            guild=SimpleNamespace(id=GUILD),
            user=SimpleNamespace(
                id=1,
                guild_permissions=SimpleNamespace(manage_guild=manage_guild, administrator=False),
            ),
            response=SimpleNamespace(send_message=AsyncMock(), defer=AsyncMock()),
            followup=SimpleNamespace(send=AsyncMock()),
        )

    async def test_show_reports_a_stored_card(self):
        seed(self.index, 1, minutes=0)
        self.profiles.observe_authors(GUILD)
        self.profiles.store_summary(
            GUILD, ALICE, summary="alice maintains the deploy script", covered_message_id=1
        )
        call = self._interaction()

        await self._command("show").callback(call, SimpleNamespace(id=ALICE, display_name="alice"))

        embed = call.response.send_message.await_args.kwargs["embed"]
        self.assertIn("alice", embed.title)
        self.assertEqual(embed.description, "alice maintains the deploy script")

    async def test_show_says_so_when_there_is_no_profile_yet(self):
        call = self._interaction()

        await self._command("show").callback(call, SimpleNamespace(id=BOB, display_name="bob"))

        self.assertIn("No profile memory", call.response.send_message.await_args.args[0])

    async def test_rebuild_rewinds_the_watermark_and_queues_a_pass(self):
        seed(self.index, 1, minutes=0)
        self.profiles.observe_authors(GUILD)
        self.profiles.store_summary(GUILD, ALICE, summary="stale", covered_message_id=1)
        call = self._interaction()

        await self._command("rebuild").callback(call, SimpleNamespace(id=ALICE, display_name="alice"))

        profile = self.profiles.get_profile(GUILD, ALICE)
        self.assertIsNone(profile.summary)
        self.assertEqual(profile.summarized_message_id, 0)
        self.bot.hybrid_context_retriever.schedule_entity_profiles.assert_called_once_with(GUILD)

    async def test_rebuild_refuses_an_ordinary_member_and_changes_nothing(self):
        seed(self.index, 1, minutes=0)
        self.profiles.observe_authors(GUILD)
        self.profiles.store_summary(GUILD, ALICE, summary="stale", covered_message_id=1)
        call = self._interaction(manage_guild=False)

        await self._command("rebuild").callback(call, SimpleNamespace(id=ALICE, display_name="alice"))

        self.assertEqual(self.profiles.get_profile(GUILD, ALICE).summary, "stale")
        self.bot.hybrid_context_retriever.schedule_entity_profiles.assert_not_called()
        self.assertIn("Manage Guild", call.response.send_message.await_args.args[0])

    async def test_rebuild_says_nothing_will_happen_while_the_feature_is_off(self):
        self.config.rag_entity_profiles_enabled = False
        seed(self.index, 1, minutes=0)
        self.profiles.observe_authors(GUILD)
        self.profiles.store_summary(GUILD, ALICE, summary="stale", covered_message_id=1)
        call = self._interaction()

        await self._command("rebuild").callback(call, SimpleNamespace(id=ALICE, display_name="alice"))

        self.assertEqual(self.profiles.get_profile(GUILD, ALICE).summary, "stale")
        self.bot.hybrid_context_retriever.schedule_entity_profiles.assert_not_called()
        self.assertIn("disabled", call.response.send_message.await_args.args[0])

    async def test_the_group_ships_the_permission_gate_discord_enforces(self):
        payload = self.bot.tree.get_command("profile").to_dict(self.bot.tree)

        self.assertEqual(
            str(payload.get("default_member_permissions")),
            str(discord.Permissions(manage_guild=True).value),
        )
        self.assertFalse(payload.get("dm_permission", True))


if __name__ == "__main__":
    unittest.main()
