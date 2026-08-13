"""Query rewriting: a follow-up gets a referent, and the extra work stays bounded.

The retrieval query used to be the raw message with the bot's mention stripped, so
"what did he do?" tokenised to an OR of four stopwords and matched nothing useful.
Two rewrites now join it -- one built locally from the recent window, one returned
by the router call that already runs -- and all three fuse through the existing RRF.
"""

import json
import unittest
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

from src.bot.enhanced_command_handler import EnhancedCommandHandler
from src.services.context_pack_builder import ContextPackBuilder
from src.services.hybrid_context_retriever import HybridContextRetriever
from src.services.message_index_service import IndexedMessage


def make_config(**overrides):
    values = dict(
        rag_gating_enabled=True,
        rag_max_context_messages_low=6,
        rag_max_context_messages_medium=8,
        rag_max_context_messages_high=12,
        rag_cross_channel_enabled=False,
        rag_lexical_candidates=30,
        rag_semantic_candidates=24,
        rag_rerank_candidates=0,
        rag_rerank_min_boundary_margin=0.15,
        rag_recency_half_life_hours=72,
        rag_embedding_model="test",
        rag_embedding_timeout_seconds=30.0,
        rag_conversation_enabled=False,
        rag_rrf_k=60.0,
        rag_fusion_weight_recent=0.7,
        rag_fusion_weight_lexical=2.0,
        rag_fusion_weight_semantic=2.0,
        rag_query_embedding_cache_size=128,
        rag_query_embedding_cache_ttl=900,
        rag_query_rewrite_enabled=True,
        rag_query_rewrite_history_turns=4,
    )
    values.update(overrides)
    return SimpleNamespace(**values)


def make_indexed(message_id, *, author="Ada", text="", is_bot=False, age_minutes=0):
    return IndexedMessage(
        message_id=message_id,
        guild_id=1,
        channel_id=2,
        author_id=3,
        author_name=author,
        is_bot=is_bot,
        reply_to_message_id=None,
        created_at=datetime.now(timezone.utc) - timedelta(minutes=age_minutes),
        content_text=text or f"message {message_id}",
    )


def make_message():
    return SimpleNamespace(
        id=99,
        guild=SimpleNamespace(id=1),
        channel=SimpleNamespace(id=2),
        author=SimpleNamespace(id=3),
        reference=None,
    )


def make_index(*, recent=(), lexical=None):
    return SimpleNamespace(
        search_recent_async=AsyncMock(return_value=list(recent)),
        search_lexical_async=(
            AsyncMock(return_value=[]) if lexical is None else AsyncMock(side_effect=lexical)
        ),
        search_semantic_async=AsyncMock(return_value=[]),
        record_retrieval_event_async=AsyncMock(),
    )


def make_retriever(config, index):
    # A client of None skips the semantic leg, so these tests read the lexical
    # calls directly rather than through a fake embedder.
    return HybridContextRetriever(
        config=config,
        message_index=index,
        context_collector=object(),
        gemini_client=SimpleNamespace(client=None),
        pack_builder=ContextPackBuilder(),
    )


def lexical_queries(index):
    return [call.args[0] for call in index.search_lexical_async.await_args_list]


class LocalQueryRewriteTest(unittest.TestCase):
    def setUp(self):
        self.retriever = HybridContextRetriever(
            config=make_config(),
            message_index=SimpleNamespace(),
            context_collector=object(),
            gemini_client=SimpleNamespace(client=None),
            pack_builder=ContextPackBuilder(),
        )

    def test_a_pronoun_follow_up_names_the_referent_from_recent_history(self):
        recent = [
            make_indexed(3, author="Grace", text="I think Turing settled that one"),
            make_indexed(2, author="Ada", text="Babbage never finished the engine"),
        ]

        rewrite = self.retriever._local_rewrite("what did he do?", recent)

        self.assertIsNotNone(rewrite)
        self.assertTrue(rewrite.startswith("what did he do?"))
        for referent in ("Grace", "Ada", "Turing", "Babbage"):
            self.assertIn(referent, rewrite)

    def test_a_self_contained_question_is_left_alone(self):
        recent = [make_indexed(2, author="Ada", text="Babbage never finished the engine")]

        self.assertIsNone(
            self.retriever._local_rewrite(
                "please summarise the deployment checklist for the staging cluster",
                recent,
            )
        )

    def test_a_short_query_is_rewritten_even_without_a_pronoun(self):
        recent = [make_indexed(2, author="Ada", text="the engine is done")]

        self.assertEqual(self.retriever._local_rewrite("any update", recent), "any update Ada")

    def test_the_window_is_bounded_by_the_configured_turn_count(self):
        self.retriever.config = make_config(rag_query_rewrite_history_turns=1)
        recent = [
            make_indexed(3, author="Grace", text="nothing here"),
            make_indexed(2, author="Ada", text="nothing here either"),
        ]

        rewrite = self.retriever._local_rewrite("what did he say", recent)

        self.assertEqual(rewrite, "what did he say Grace")

    def test_no_usable_terms_falls_back_to_the_raw_query(self):
        recent = [make_indexed(2, author="the", text="lower case only, nothing to name")]

        self.assertIsNone(self.retriever._local_rewrite("what did he do", recent))

    def test_bot_authors_are_not_offered_as_referents(self):
        recent = [make_indexed(2, author="Assistant", text="ok", is_bot=True)]

        self.assertIsNone(self.retriever._local_rewrite("what did he do", recent))


class QueryVariantFusionTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.retriever = None

    async def asyncTearDown(self):
        if self.retriever is not None:
            await self.retriever.close()

    async def test_the_rewrite_reaches_the_lexical_leg_as_a_second_query(self):
        index = make_index(recent=[make_indexed(2, author="Ada", text="Babbage built it")])
        self.retriever = make_retriever(make_config(), index)

        await self.retriever.retrieve(
            message=make_message(), user_prompt="what did he do", complexity_level="low",
        )

        queries = lexical_queries(index)
        self.assertEqual(queries[0], "what did he do")
        self.assertEqual(len(queries), 2)
        self.assertIn("Ada", queries[1])
        self.assertIn("Babbage", queries[1])

    async def test_a_self_contained_question_issues_one_lexical_query(self):
        index = make_index(recent=[make_indexed(2, author="Ada", text="Babbage built it")])
        self.retriever = make_retriever(make_config(), index)

        await self.retriever.retrieve(
            message=make_message(),
            user_prompt="please summarise the deployment checklist for the staging cluster",
            complexity_level="low",
        )

        self.assertEqual(
            lexical_queries(index),
            ["please summarise the deployment checklist for the staging cluster"],
        )

    async def test_identical_rewrites_do_not_multiply_database_queries(self):
        index = make_index(recent=[make_indexed(2, author="Ada", text="lower case body")])
        self.retriever = make_retriever(make_config(), index)

        # The router reproduces the local rewrite in different case and spacing,
        # and both normalise to the same string.
        await self.retriever.retrieve(
            message=make_message(),
            user_prompt="what did he do",
            complexity_level="low",
            search_query="  What did he do   ADA ",
        )

        self.assertEqual(lexical_queries(index), ["what did he do", "what did he do Ada"])

    async def test_a_router_query_equal_to_the_raw_query_costs_nothing(self):
        index = make_index()
        self.retriever = make_retriever(make_config(), index)

        await self.retriever.retrieve(
            message=make_message(),
            user_prompt="who deployed the staging cluster last night",
            complexity_level="low",
            search_query="Who deployed the staging cluster last night",
        )

        self.assertEqual(
            lexical_queries(index), ["who deployed the staging cluster last night"]
        )

    async def test_a_missing_router_query_degrades_to_the_raw_query(self):
        index = make_index()
        self.retriever = make_retriever(make_config(), index)

        await self.retriever.retrieve(
            message=make_message(),
            user_prompt="who deployed the staging cluster last night",
            complexity_level="low",
            search_query="   ",
        )

        self.assertEqual(
            lexical_queries(index), ["who deployed the staging cluster last night"]
        )

    async def test_a_rewrite_hit_ranks_below_an_equal_raw_query_hit(self):
        raw_hit, rewrite_hit = make_indexed(11), make_indexed(12)

        async def by_query(query, **_kwargs):
            return [raw_hit] if query == "what did he do" else [rewrite_hit]

        index = make_index(recent=[], lexical=by_query)
        self.retriever = make_retriever(make_config(), index)

        packed = await self.retriever.retrieve(
            message=make_message(),
            user_prompt="what did he do",
            complexity_level="low",
            search_query="what did Babbage do",
        )

        self.assertEqual([ctx.message_id for ctx in packed], [11, 12])
        self.assertEqual(packed[0].retrieval_source, "lexical")
        self.assertEqual(packed[1].retrieval_source, "lexical_router")
        # Both hits are rank 1 in their own leg and equally recent, so an equal
        # fusion weight would leave the two scores within float noise of each
        # other and the ordering would be luck. The margin is the assertion.
        self.assertLess(packed[1].retrieval_score, 0.9 * packed[0].retrieval_score)

    async def test_disabling_the_feature_restores_the_single_query_path(self):
        index = make_index(recent=[make_indexed(2, author="Ada", text="Babbage built it")])
        self.retriever = make_retriever(make_config(rag_query_rewrite_enabled=False), index)

        await self.retriever.retrieve(
            message=make_message(),
            user_prompt="what did he do",
            complexity_level="low",
            search_query="what did Babbage do",
        )

        self.assertEqual(lexical_queries(index), ["what did he do"])
        self.assertEqual(
            self.retriever._query_variants("what did he do", [], "what did Babbage do"),
            [("raw", "what did he do", 1.0)],
        )


class QueryVariantEmbeddingTest(unittest.IsolatedAsyncioTestCase):
    def _retriever(self, index, embed_texts):
        retriever = HybridContextRetriever(
            config=make_config(),
            message_index=index,
            context_collector=object(),
            gemini_client=SimpleNamespace(client=object(), embed_texts=embed_texts),
            pack_builder=ContextPackBuilder(),
        )
        self.addAsyncCleanup(retriever.close)
        return retriever

    async def _retrieve(self, retriever):
        return await retriever.retrieve(
            message=make_message(),
            user_prompt="what did he do",
            complexity_level="low",
            search_query="what did Babbage do",
        )

    async def test_three_variants_cost_one_embedding_call(self):
        # The API takes a list, and each variant used to be a separate round
        # trip and a separate charge.
        embed_texts = AsyncMock(return_value=[[1.0, 0.0], [0.0, 1.0], [1.0, 1.0]])
        index = make_index(recent=[make_indexed(2, author="Ada", text="Babbage built it")])
        retriever = self._retriever(index, embed_texts)

        await self._retrieve(retriever)

        embed_texts.assert_awaited_once()
        self.assertEqual(len(embed_texts.await_args.args[0]), 3)
        self.assertEqual(index.search_semantic_async.await_count, 3)

    async def test_variants_already_cached_are_not_embedded_again(self):
        embed_texts = AsyncMock(return_value=[[1.0, 0.0], [0.0, 1.0], [1.0, 1.0]])
        index = make_index(recent=[make_indexed(2, author="Ada", text="Babbage built it")])
        retriever = self._retriever(index, embed_texts)

        await self._retrieve(retriever)
        await self._retrieve(retriever)

        embed_texts.assert_awaited_once()
        self.assertEqual(index.search_semantic_async.await_count, 6)


class RouterSearchQueryTest(unittest.IsolatedAsyncioTestCase):
    @staticmethod
    def _make_handler(generate_content):
        config = SimpleNamespace(
            router_model_name="router-model",
            router_temperature=0.1,
            router_max_output_tokens=100,
            router_cache_size=8,
            router_cache_ttl=60,
            response_timeout=30,
        )
        return EnhancedCommandHandler(
            bot=SimpleNamespace(config=config),
            image_processing_service=Mock(),
            error_manager=Mock(),
            gemini_client=SimpleNamespace(
                client=SimpleNamespace(
                    aio=SimpleNamespace(
                        models=SimpleNamespace(generate_content=generate_content),
                    ),
                )
            ),
        )

    @staticmethod
    def _payload(**overrides):
        body = {
            "intent": "text",
            "complexity": "low",
            "edit_type": None,
            "needs_context": True,
        }
        body.update(overrides)
        return SimpleNamespace(text=json.dumps(body))

    async def test_the_router_returns_a_standalone_search_query(self):
        generate_content = AsyncMock(
            return_value=self._payload(search_query="Ada Lovelace analytical engine")
        )
        handler = self._make_handler(generate_content)

        decision = await handler._check_intent_and_complexity("what did she build", False)

        self.assertEqual(decision.search_query, "Ada Lovelace analytical engine")

    async def test_a_blank_or_absent_search_query_becomes_none(self):
        for payload in (self._payload(search_query="   "), self._payload(), self._payload(search_query=7)):
            with self.subTest(payload=payload.text):
                handler = self._make_handler(AsyncMock(return_value=payload))
                decision = await handler._check_intent_and_complexity("what did she build", False)
                self.assertIsNone(decision.search_query)
                self.assertTrue(decision.needs_context)

    async def test_unparseable_router_output_still_yields_a_usable_decision(self):
        handler = self._make_handler(AsyncMock(return_value=SimpleNamespace(text="not json")))

        decision = await handler._check_intent_and_complexity("what did she build", False)

        self.assertIsNone(decision.search_query)
        self.assertEqual(decision.complexity, "low")

    async def test_the_router_cache_round_trips_the_search_query(self):
        generate_content = AsyncMock(
            return_value=self._payload(search_query="Ada Lovelace analytical engine")
        )
        handler = self._make_handler(generate_content)

        first = await handler._check_intent_and_complexity("what did she build", False)
        cached = await handler._check_intent_and_complexity("What did she build", False)

        generate_content.assert_awaited_once()
        self.assertEqual(cached.search_query, first.search_query)
        self.assertEqual(cached, first)

    async def test_the_cache_key_still_discriminates_between_messages(self):
        async def by_prompt(*, contents, **_kwargs):
            named = "Grace" if "grace" in contents.lower() else "Ada"
            return self._payload(search_query=f"{named} analytical engine")

        generate_content = AsyncMock(side_effect=by_prompt)
        handler = self._make_handler(generate_content)

        ada = await handler._check_intent_and_complexity("what did ada build", False)
        grace = await handler._check_intent_and_complexity("what did grace build", False)
        # Attachments are part of the key too, so the same text with an image
        # must not reuse the text-only decision.
        await handler._check_intent_and_complexity("what did ada build", True)

        self.assertEqual(ada.search_query, "Ada analytical engine")
        self.assertEqual(grace.search_query, "Grace analytical engine")
        self.assertEqual(generate_content.await_count, 3)


if __name__ == "__main__":
    unittest.main()
