#!/usr/bin/env python3
"""Offline retrieval-quality and latency harness for the message RAG index.

Builds a synthetic channel in a throwaway SQLite database, plants a labelled set of
facts about one person, embeds the corpus with a deterministic offline stand-in for
the Gemini embedding API, and scores both the individual ``MessageIndexService`` legs
and the real ``HybridContextRetriever.retrieve`` -- the only path that exercises
conversation expansion and character-budget packing.

The corpus is shaped around one confound, because that confound is what this harness
exists to measure. ``message_search_fts`` indexes ``author_name`` as a searchable
column, so a bare display name matches every message the person ever *sent*, and
BM25's short-field bias ranks that short chatter above the long sentences actually
*about* them. ``LEXa/b@k`` is the reading: ``authored`` versus ``about`` hits in the
top k lexical results.

Nothing here touches the network, an API key, ``data/``, or the repo tree.

Deliberate approximations, so a later run is compared against the same ruler:
configuration is ``BotConfig()`` defaults rather than the operator's ``config.yaml``
(``--compare`` flags any tracked setting that moved); estimated tokens are characters
/ 4; the Gemini client is a stub, so the reranker preserves the fused order; and the
per-leg section fuses without pins or reply anchors, neither of which exists without
a live message.
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import logging
import math
import random
import re
import statistics
import sys
import tempfile
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

import numpy as np

ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

# Before importing anything that logs: a missing API key is an expected state
# here, and the client reports it at ERROR.
logging.getLogger("src").setLevel(logging.CRITICAL)

from src.config import BotConfig  # noqa: E402
from src.services.context_pack_builder import ContextPackBuilder  # noqa: E402
from src.services.gemini_client import GeminiClient  # noqa: E402
from src.services.hybrid_context_retriever import HybridContextRetriever  # noqa: E402
from src.services.message_index_service import MessageIndexService  # noqa: E402

GUILD_ID = 900000000000000001
CHANNEL_ID = 900000000000000002
BASE_MESSAGE_ID = 800000000000000000
ASKER_ID = 700000000000000001
QUERY_MESSAGE_ID = BASE_MESSAGE_ID - 1

ENTITY_NAME = "alice"
ENTITY_NICK = "ali"
ENTITY_ID = 111222333444555666
ENTITY_MENTION = f"<@{ENTITY_ID}>"
ENTITY_FORMS = (ENTITY_NAME, ENTITY_NICK, ENTITY_MENTION)

#: Share of the corpus authored by the entity: enough to fill the lexical window.
ENTITY_AUTHOR_SHARE = 0.08
CORPUS_WINDOW_DAYS = 30
RECALL_KS = (5, 10, 30)
#: Tier the end-to-end run asks for; the per-leg section uses the same ceiling.
COMPLEXITY = "high"

OTHER_AUTHORS = ("bob", "carol", "dana", "erin", "frank", "grace", "heidi", "ivan")

#: Recorded so ``--compare`` can refuse a false equivalence between two runs.
TRACKED_SETTINGS = (
    "rag_lexical_candidates", "rag_semantic_candidates", "rag_cross_channel_enabled",
    "rag_recency_half_life_hours", "rag_max_context_messages_high",
    "rag_embedding_min_words", "rag_embedding_min_alphanumeric_chars",
    "rag_vector_cache_enabled", "rag_rerank_candidates", "rag_context_char_budget",
    "rag_query_rewrite_enabled", "rag_conversation_enabled", "rag_conversation_gap_minutes",
    "rag_conversation_expand_full_max_messages", "rag_conversation_expand_window_messages",
)

#: Every end-to-end field, so a failed ``retrieve`` still yields a full row.
E2E_KEYS = (
    "e2e_items", "e2e_hits", "e2e_filler", "e2e_blocks", "e2e_ctx_chars",
    "e2e_ctx_tokens", "ms_retrieve", *(f"e2e_recall@{k}" for k in RECALL_KS),
)

#: One fact per planted message, rendered against each identifier form in turn:
#: three ground-truth messages per form, none of them authored by the entity.
ENTITY_FACTS = (
    "{who} rewrote the caching layer last spring and cut our p99 read latency roughly in half",
    "the release tooling is owned by {who}, so send pipeline breakages there first",
    "the retry backoff in the queue consumer was designed by {who} after the october incident",
    "{who} wrote the migration that split the users table into two shards",
    "if the search index falls behind, {who} is the one who knows how to rebuild it safely",
    "{who} ran the postmortem for the january outage and wrote up every action item",
    "the on call rotation policy we use today was drafted by {who} two years ago",
    "{who} maintains the load test harness and keeps the baseline numbers honest",
    "most of the webhook worker code was written by {who} before the team grew",
)

#: Chatter sent BY the entity, mentioning no identifier form. Short on purpose:
#: BM25 rewards short documents, and short is what chat traffic is.
ENTITY_CHATTER = (
    "yeah that works for me", "on it now", "merging shortly", "sounds good",
    "will look after lunch", "done and pushed", "same here honestly",
    "give me ten minutes", "no idea sorry", "already fixed that one",
    "can you re run it", "nice catch",
)

CONTROL_TOPICS = {
    "espresso": (
        "the espresso machine in the kitchen needs descaling again this week",
        "someone left the portafilter full of old grounds overnight",
        "the grinder setting drifted coarse so the espresso is pulling too fast",
        "we ordered new descaling tablets for the espresso machine, they arrive tuesday",
        "you have to descale it before the light on the machine turns red",
        "the espresso machine steam wand is clogged and needs a long soak",
    ),
    "kubernetes": (
        "the kubernetes rollout is stuck waiting on a helm hook in staging",
        "we rolled back the staging namespace because the readiness probe never passed",
        "helm upgrade timed out again and the rollout never reached the second replica",
        "kubernetes evicted the pod in staging when the node ran out of memory",
        "the rollout strategy is surge one, so a stuck pod blocks the whole namespace",
        "check the helm release history before you retry the kubernetes rollout",
    ),
}

FILLER_SUBJECTS = (
    "the nightly build", "the metrics dashboard", "the schema migration", "the api gateway",
    "the mobile client", "the docs site", "the billing report", "the search index",
    "the login flow", "the webhook worker",
)
FILLER_PREDICATES = (
    "finished about an hour ago with no warnings at all", "needs another review pass before friday",
    "is timing out whenever the payload gets large", "looks fine on staging but not in production",
    "was reverted because the memory usage doubled", "picked up the new config without a restart",
    "still depends on that old library nobody wants to touch", "is blocked on the platform team",
    "should be faster now that the extra query is gone", "keeps logging the same warning hourly",
)  # 10 x 10 = 100 filler bodies, with enough shared tokens for the semantic leg


@dataclass
class Corpus:
    about: set[int] = field(default_factory=set)
    authored: set[int] = field(default_factory=set)
    controls: dict[str, set[int]] = field(default_factory=dict)
    #: Timeline density: below the conversation gap, messages group into blocks.
    spacing_minutes: float = 0.0


@dataclass
class Query:
    name: str
    text: str
    relevant: set[int]
    note: str


# --- deterministic offline embeddings ---------------------------------------

#: Words, plus snowflake-scale digit runs so a raw mention embeds to something.
#: Short numbers are dropped, so the document text's timestamp cannot swamp them.
_EMBED_TOKEN = re.compile(r"[a-z][a-z0-9_]+|\d{12,}")
_TOKEN_VECTORS: dict[tuple[str, int], np.ndarray] = {}


def _token_vector(token: str, dimensions: int) -> np.ndarray:
    """A stable pseudo-random direction for one token, memoised per run."""
    key = (token, dimensions)
    cached = _TOKEN_VECTORS.get(key)
    if cached is None:
        seed = int.from_bytes(hashlib.blake2b(token.encode("utf-8"), digest_size=8).digest(), "big")
        cached = np.random.default_rng(seed).standard_normal(dimensions).astype(np.float32)
        _TOKEN_VECTORS[key] = cached
    return cached


def fake_embedding(text: str, dimensions: int) -> list[float]:
    """Normalised sum of per-token directions: a bag-of-words stand-in that is
    identical on every machine and run, with no network and no API key."""
    vector = np.zeros(dimensions, dtype=np.float32)
    for token in _EMBED_TOKEN.findall(text.lower()):
        vector += _token_vector(token, dimensions)
    norm = float(np.linalg.norm(vector))
    if norm == 0.0:
        return _token_vector("\x00empty", dimensions).tolist()
    return (vector / norm).tolist()


class StubGeminiClient:
    """Offline stand-in for ``GeminiClient`` as the retriever uses it. ``client`` is
    truthy so the semantic leg runs and the reranker gate is reached, but the reranker
    preserves the fused order: what is measured is retrieval, not an LLM's opinion."""

    def __init__(self, dimensions: int):
        self.client = object()
        self.dimensions = dimensions

    async def embed_texts(self, texts, *, model_name=None, task_type=None) -> list[list[float]]:
        return [fake_embedding(text, self.dimensions) for text in texts]

    async def select_relevant_context(self, prompt, contexts, *, max_messages, anchor_message_ids=None):
        return list(contexts)[:max_messages]


# --- corpus ------------------------------------------------------------------

def build_corpus(index: MessageIndexService, *, size: int, seed: int) -> Corpus:
    rng = random.Random(seed)
    author_ids = {name: 200000000000000000 + offset for offset, name in enumerate(OTHER_AUTHORS)}
    author_ids[ENTITY_NAME] = ENTITY_ID

    planted: list[tuple[str, str, str]] = []
    for position, template in enumerate(ENTITY_FACTS):
        who = ENTITY_FORMS[position % len(ENTITY_FORMS)]
        planted.append((template.format(who=who), OTHER_AUTHORS[position % len(OTHER_AUTHORS)], "about"))
    for topic, lines in CONTROL_TOPICS.items():
        for offset, line in enumerate(lines):
            planted.append((line, OTHER_AUTHORS[(offset + 3) % len(OTHER_AUTHORS)], f"control:{topic}"))

    records = []
    for _ in range(size):
        if rng.random() < ENTITY_AUTHOR_SHARE:
            records.append((rng.choice(ENTITY_CHATTER), ENTITY_NAME, "authored"))
        else:
            body = f"{rng.choice(FILLER_SUBJECTS)} {rng.choice(FILLER_PREDICATES)}"
            records.append((body, rng.choice(OTHER_AUTHORS), "filler"))

    # Spread the planted messages over the oldest 90% of the timeline. Anything
    # inside search_recent's window would score for free and flatter recall.
    span = int(size * 0.9)
    for order, record in enumerate(planted):
        records[(order * span) // len(planted)] = record

    corpus = Corpus(controls={topic: set() for topic in CONTROL_TOPICS})
    now = datetime.now(timezone.utc)
    step = timedelta(seconds=max(1.0, CORPUS_WINDOW_DAYS * 86400.0 / size))
    corpus.spacing_minutes = step.total_seconds() / 60.0
    for offset, (content, author, label) in enumerate(records):
        message_id = BASE_MESSAGE_ID + offset
        index.upsert_message(
            message_id=message_id, guild_id=GUILD_ID, channel_id=CHANNEL_ID,
            author_id=author_ids[author], author_name=author, is_bot=False,
            reply_to_message_id=None, created_at=now - step * (size - offset),
            content_text=content,
        )
        if label == "about":
            corpus.about.add(message_id)
        elif label == "authored":
            corpus.authored.add(message_id)
        elif label.startswith("control:"):
            corpus.controls[label.split(":", 1)[1]].add(message_id)
    return corpus


def embed_corpus(index: MessageIndexService, dimensions: int, batch: int = 512) -> int:
    """Embed every pending row through the service's own document text, so the author
    name lands in the vector and the semantic leg inherits the same confound."""
    stored = 0
    while True:
        pending = index.get_pending_embeddings(limit=batch)
        if not pending:
            return stored
        written = 0
        for message_id, text, content_hash in pending:
            if index.store_embedding(message_id, fake_embedding(text, dimensions), content_hash):
                written += 1
        if not written:  # nothing progressed; stop rather than spin forever
            return stored
        stored += written


def build_queries(corpus: Corpus) -> list[Query]:
    about = corpus.about
    return [
        Query("who-is", "who is alice and what did they do", about, "full question, stopword heavy"),
        Query("alice", ENTITY_NAME, about, "bare display name: the author_name confound"),
        Query("ali", ENTITY_NICK, about, "nickname only"),
        Query("mention", ENTITY_MENTION, about, "raw discord mention"),
        Query("pronoun", "what did he do", about, "no referent in the query"),
        Query("ctl-espresso", "how do i descale the espresso machine", corpus.controls["espresso"], "control"),
        Query("ctl-k8s", "kubernetes rollout stuck in staging", corpus.controls["kubernetes"], "control"),
    ]


# --- metrics -----------------------------------------------------------------

def recall_at_k(ranked: list[int], relevant: set[int], k: int) -> float:
    if not relevant:
        return 0.0
    return len(relevant.intersection(ranked[:k])) / len(relevant)


def ndcg_at_k(ranked: list[int], relevant: set[int], k: int = 10) -> float:
    if not relevant:
        return 0.0
    gain = sum(1.0 / math.log2(rank + 2) for rank, mid in enumerate(ranked[:k]) if mid in relevant)
    ideal = sum(1.0 / math.log2(rank + 2) for rank in range(min(k, len(relevant))))
    return gain / ideal if ideal else 0.0


def mean_reciprocal_rank(ranked: list[int], relevant: set[int]) -> float:
    for rank, mid in enumerate(ranked, start=1):
        if mid in relevant:
            return 1.0 / rank
    return 0.0


# --- evaluation --------------------------------------------------------------

def _timed(call, repeats: int):
    """Run ``call`` ``repeats`` times; return its last result and the median ms."""
    samples = []
    result = None
    for _ in range(repeats):
        started = time.perf_counter()
        result = call()
        samples.append((time.perf_counter() - started) * 1000.0)
    return result, statistics.median(samples)


def _fuse(retriever: HybridContextRetriever, config, recent, lexical, semantic) -> list:
    """Fuse the three legs the way ``retrieve`` does, minus pins and reranking. Weights
    come off config with the same literals ``retrieve`` falls back to, so making them
    configurable later cannot leave this scoring an older ranking."""
    candidates: dict[int, object] = {}
    for source, messages, default in (
        ("recent", recent, 0.7),
        ("lexical", lexical, 2.0),
        ("semantic", semantic, 2.0),
    ):
        weight = float(getattr(config, f"rag_fusion_weight_{source}", default))
        retriever._add_candidates(candidates, messages, source=source, weight=weight)
    ordered = sorted(candidates.values(), key=lambda item: item.score, reverse=True)
    return [candidate.message for candidate in ordered]


def evaluate(index, config, retriever, corpus: Corpus, query: Query, repeats: int) -> dict:
    scope = dict(guild_id=GUILD_ID, channel_id=CHANNEL_ID, cross_channel=config.rag_cross_channel_enabled)
    recent_limit = max(config.rag_max_context_messages_high, config.rag_lexical_candidates // 2)
    vector = fake_embedding(query.text, config.rag_embedding_dimensions)
    recent, ms_recent = _timed(lambda: index.search_recent(limit=recent_limit, **scope), repeats)
    lexical, ms_lexical = _timed(
        lambda: index.search_lexical(query.text, limit=config.rag_lexical_candidates, **scope), repeats)
    semantic, ms_semantic = _timed(
        lambda: index.search_semantic(vector, limit=config.rag_semantic_candidates, **scope), repeats)

    fused = _fuse(retriever, config, recent, lexical, semantic)
    ranked = [message.message_id for message in fused]
    packed = fused[: config.rag_max_context_messages_high]
    chars = sum(len(message.author_name) + len(message.content_text) + 2 for message in packed)

    result = {
        "text": query.text, "note": query.note, "relevant": len(query.relevant),
        "ndcg@10": ndcg_at_k(ranked, query.relevant),
        "mrr": mean_reciprocal_rank(ranked, query.relevant),
        "lex_recall@10": recall_at_k([m.message_id for m in lexical], query.relevant, 10),
        "sem_recall@10": recall_at_k([m.message_id for m in semantic], query.relevant, 10),
        "ctx_messages": len(packed), "ctx_chars": chars, "ctx_tokens": chars // 4,
        "ms_recent": ms_recent, "ms_lexical": ms_lexical, "ms_semantic_warm": ms_semantic,
        "ms_total": ms_recent + ms_lexical + ms_semantic,
    }
    for k in RECALL_KS:
        result[f"recall@{k}"] = recall_at_k(ranked, query.relevant, k)
        head = lexical[:k]
        result[f"lex_authored@{k}"] = sum(1 for m in head if m.author_id == ENTITY_ID)
        result[f"lex_about@{k}"] = sum(1 for m in head if m.message_id in corpus.about)
    return result


def evaluate_end_to_end(retriever, run, render, query: Query, repeats: int) -> dict:
    """Score the pack ``retrieve`` returns, not the fusion: expansion and the
    character budget both add and drop items after fusion."""
    message = SimpleNamespace(
        id=QUERY_MESSAGE_ID, guild=SimpleNamespace(id=GUILD_ID), reference=None,
        channel=SimpleNamespace(id=CHANNEL_ID), author=SimpleNamespace(id=ASKER_ID))
    # `search_query` is deliberately not passed: it carries the router's rewrite, and
    # inventing one offline would measure the fabrication.
    kwargs = dict(message=message, user_prompt=query.text, complexity_level=COMPLEXITY)
    try:
        packed, ms = _timed(lambda: run(retriever.retrieve(**kwargs)), repeats)
    except Exception as exc:
        return {key: 0 for key in E2E_KEYS} | {"e2e_error": f"{type(exc).__name__}: {exc}"}

    hits = [ctx for ctx in packed if not getattr(ctx, "is_conversation_filler", False)]
    blocks = {getattr(ctx, "conversation_id", None) or ("solo", ctx.message_id) for ctx in packed}
    chars = render(query.text, packed)
    result = {
        "e2e_items": len(packed), "e2e_hits": len(hits), "e2e_filler": len(packed) - len(hits),
        "e2e_blocks": len(blocks), "e2e_ctx_chars": chars, "e2e_ctx_tokens": chars // 4,
        "ms_retrieve": ms}
    # The pack is emitted weakest-first on purpose, so the strongest item sits
    # nearest the user turn. recall@k assumes best-first, so rank by score here
    # or the metric scores the deliberately-weakest end of the pack.
    ranked = [
        ctx.message_id
        for ctx in sorted(packed, key=lambda c: -(c.retrieval_score or 0.0))
    ]
    for k in RECALL_KS:
        result[f"e2e_recall@{k}"] = recall_at_k(ranked, query.relevant, k)
    return result


def make_renderer(config) -> tuple:
    """Return ``(render, label)`` measuring the characters a pack adds to the prompt.
    The real formatter needs no live client; if that stops being true the label says
    the number is an approximation instead."""
    try:
        client = GeminiClient(config)
        client.format_prompt("probe", context=None, complexity_override=COMPLEXITY)
    except Exception as exc:
        return (lambda text, packed: sum(len(c.content or "") + len(c.author or "") + 2 for c in packed),
                f"APPROXIMATE, GeminiClient.format_prompt unavailable ({type(exc).__name__})")

    def render(text: str, packed: list) -> int:
        with_pack = client.format_prompt(text, context=packed, complexity_override=COMPLEXITY)
        return len(with_pack) - len(client.format_prompt(text, complexity_override=COMPLEXITY))

    return render, "real GeminiClient.format_prompt"


def measure_cold_semantic(index, config, repeats: int) -> float:
    """Median cost of the first semantic search after a restart. The vector cache has
    no public reset, so the load flag is cleared directly and ``_load_vector_cache``
    refills from SQLite exactly as it does on boot."""
    vector = fake_embedding("who is alice and what did they do", config.rag_embedding_dimensions)
    scope = dict(guild_id=GUILD_ID, channel_id=CHANNEL_ID, cross_channel=config.rag_cross_channel_enabled)
    samples = []
    for _ in range(repeats):
        with index._vector_lock:
            index._vector_loaded = False
            index._vector_count = 0
        started = time.perf_counter()
        index.search_semantic(vector, limit=config.rag_semantic_candidates, **scope)
        samples.append((time.perf_counter() - started) * 1000.0)
    return statistics.median(samples)


# --- reporting ---------------------------------------------------------------

def print_report(report: dict) -> None:
    meta = report["meta"]
    gap = meta["config"].get("rag_conversation_gap_minutes") or 0.0
    print()
    print(
        f"corpus {meta['size']} messages  seed {meta['seed']}  dim {meta['dimensions']}  "
        f"about {meta['about_count']}  authored-by-entity {meta['authored_count']}  "
        f"embedded {meta['embedded']}  build {meta['build_seconds']:.1f}s  embed {meta['embed_seconds']:.1f}s"
    )
    print(
        f"spacing {meta['spacing_minutes']:.1f} min/message vs conversation gap {gap} min: "
        + ("messages group into conversations" if meta["spacing_minutes"] < gap
           else "every message is its own conversation, so nothing expands")
    )
    print("config  " + "  ".join(f"{k.removeprefix('rag_')}={v}" for k, v in meta["config"].items()))
    print()
    header = (f"{'QUERY':<14}{'REL':>4}{'R@5':>8}{'R@10':>8}{'R@30':>8}{'nDCG10':>8}{'MRR':>7}"
              f"{'LEXR10':>8}{'SEMR10':>8}{'LEXa/b@10':>12}{'LEXa/b@30':>12}{'CTXTOK':>8}{'MS':>8}")
    print(header)
    print("-" * len(header))
    for name, row in report["queries"].items():
        split10 = f"{row['lex_authored@10']}/{row['lex_about@10']}"
        split30 = f"{row['lex_authored@30']}/{row['lex_about@30']}"
        print(f"{name:<14}{row['relevant']:>4}"
              f"{row['recall@5']:>8.3f}{row['recall@10']:>8.3f}{row['recall@30']:>8.3f}"
              f"{row['ndcg@10']:>8.3f}{row['mrr']:>7.3f}{row['lex_recall@10']:>8.3f}"
              f"{row['sem_recall@10']:>8.3f}{split10:>12}{split30:>12}"
              f"{row['ctx_tokens']:>8}{row['ms_total']:>8.2f}")
    print()
    print(f"END TO END  HybridContextRetriever.retrieve(complexity={COMPLEXITY}), "
          f"rendered by {meta['render']}")
    header = (f"{'QUERY':<14}{'R@5':>8}{'R@10':>8}{'R@30':>8}{'ITEMS':>7}{'HITS':>7}{'FILLER':>7}"
              f"{'BLOCKS':>8}{'CTXCHAR':>9}{'CTXTOK':>8}{'MS':>8}")
    print(header)
    print("-" * len(header))
    for name, row in report["queries"].items():
        print(f"{name:<14}{row['e2e_recall@5']:>8.3f}{row['e2e_recall@10']:>8.3f}"
              f"{row['e2e_recall@30']:>8.3f}{row['e2e_items']:>7}{row['e2e_hits']:>7}"
              f"{row['e2e_filler']:>7}{row['e2e_blocks']:>8}{row['e2e_ctx_chars']:>9}"
              f"{row['e2e_ctx_tokens']:>8}{row['ms_retrieve']:>8.2f}")
        if row.get("e2e_error"):
            print(f"  ^ retrieve() failed: {row['e2e_error']}")
    print()
    print(f"LATENCY (median of {meta['repeats']} runs, ms)")
    for leg, value in report["latency_ms"].items():
        print(f"  {leg:<22}{value:>10.3f}")
    print()
    print("LEXR10/SEMR10 are that leg's own recall@10; R@k, nDCG and MRR are over the fusion.")
    print("LEXa/b@k is lexical hits in the top k AUTHORED BY the entity / ABOUT it. Ground truth")
    print("is all nine planted facts across all three identifier forms, so a query matching one")
    print("form caps at 0.333. END TO END scores the pack retrieve() returns: HITS are matches,")
    print("FILLER is surrounding conversation, BLOCKS is distinct conversations, CTXCHAR is what")
    print("the pack adds to the prompt the response model would receive.")


def print_comparison(baseline: dict, current: dict) -> None:
    base_meta, now_meta = baseline.get("meta", {}), current["meta"]
    for name in ("size", "seed", "dimensions"):
        if base_meta.get(name) != now_meta.get(name):
            print(f"WARNING: baseline {name}={base_meta.get(name)} but this run {name}="
                  f"{now_meta.get(name)}; the comparison is not like for like.")
    base_config = base_meta.get("config", {})
    for name, value in now_meta["config"].items():
        if name not in base_config:
            print(f"note: config {name}={value} is new in this report; the baseline did not record it.")
        elif base_config[name] != value:
            print(f"WARNING: config {name} was {base_config[name]}, now {value}; a metric delta may just be that.")
    metrics = ("recall@10", "recall@30", "ndcg@10", "mrr", "lex_about@10", "lex_authored@10",
               "ctx_tokens", "ms_total", "e2e_recall@10", "e2e_items", "e2e_hits", "e2e_filler",
               "e2e_blocks", "e2e_ctx_tokens", "ms_retrieve")
    header = f"{'QUERY':<14}{'METRIC':<16}{'BASE':>10}{'NOW':>10}{'DELTA':>10}"
    print()
    print(header)
    print("-" * len(header))
    for name, row in current["queries"].items():
        base_row = baseline.get("queries", {}).get(name)
        if base_row is None:
            print(f"{name:<14}{'(new query)':<16}")
            continue
        for metric in metrics:
            now_value = float(row.get(metric, 0.0))
            if metric not in base_row:  # added since the baseline: new, not a regression
                print(f"{name:<14}{metric:<16}{'(new)':>10}{now_value:>10.3f}{'-':>10}")
                continue
            base_value = float(base_row[metric])
            print(f"{name:<14}{metric:<16}{base_value:>10.3f}{now_value:>10.3f}{now_value - base_value:>+10.3f}")
    print()
    print(f"{'LATENCY':<14}{'LEG':<16}{'BASE':>10}{'NOW':>10}{'DELTA':>10}")
    base_latency = baseline.get("latency_ms", {})
    for leg, now_value in current["latency_ms"].items():
        if leg not in base_latency:
            print(f"{'':<14}{leg:<16}{'(new)':>10}{now_value:>10.3f}{'-':>10}")
            continue
        base_value = float(base_latency[leg])
        print(f"{'':<14}{leg:<16}{base_value:>10.3f}{now_value:>10.3f}{now_value - base_value:>+10.3f}")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--size", type=int, default=2000, help="synthetic corpus size (default 2000)")
    parser.add_argument("--seed", type=int, default=1337)
    parser.add_argument("--queries", default="", help="comma-separated name filter (substring match)")
    parser.add_argument("--repeats", type=int, default=5, help="timing runs per leg (default 5)")
    parser.add_argument("--json", metavar="PATH", help="write the report as JSON; '-' for stdout")
    parser.add_argument("--compare", metavar="BASELINE.json", help="print a delta table against a saved report")
    parser.add_argument("--keep", action="store_true", help="keep the scratch database and print its path")
    args = parser.parse_args()

    if args.size < 200:
        parser.error("--size must be at least 200; the planted messages need room to spread out")

    config = BotConfig()
    holder = None if args.keep else tempfile.TemporaryDirectory(prefix="rag-benchmark-")
    workdir = Path(holder.name if holder else tempfile.mkdtemp(prefix="rag-benchmark-"))
    runner = asyncio.Runner()  # one loop for every retrieve(), so the timing is retrieval's
    try:
        index = MessageIndexService(
            db_path=str(workdir / "message_rag.db"),
            embedding_model=config.rag_embedding_model,
            embedding_dimensions=config.rag_embedding_dimensions,
            embedding_min_words=config.rag_embedding_min_words,
            embedding_min_alphanumeric_chars=config.rag_embedding_min_alphanumeric_chars,
            vector_cache_enabled=config.rag_vector_cache_enabled,
            # Conversation ids are assigned at upsert time, so grouping has to be on
            # the config's terms before the corpus is written.
            conversation_enabled=config.rag_conversation_enabled,
            conversation_gap_minutes=config.rag_conversation_gap_minutes,
            conversation_max_messages=config.rag_conversation_max_messages)
        if not index.fts_enabled:
            print("FTS5 is unavailable in this SQLite build; the lexical leg cannot be measured.", file=sys.stderr)
            return 2

        started = time.perf_counter()
        corpus = build_corpus(index, size=args.size, seed=args.seed)
        build_seconds = time.perf_counter() - started

        started = time.perf_counter()
        embedded = embed_corpus(index, config.rag_embedding_dimensions)
        embed_seconds = time.perf_counter() - started

        queries = build_queries(corpus)
        if args.queries:
            wanted = [part.strip().lower() for part in args.queries.split(",") if part.strip()]
            queries = [q for q in queries if any(part in q.name.lower() for part in wanted)]
            if not queries:
                parser.error(f"--queries {args.queries!r} matched nothing")

        retriever = HybridContextRetriever(
            config=config, message_index=index, context_collector=None,
            gemini_client=StubGeminiClient(config.rag_embedding_dimensions),
            pack_builder=ContextPackBuilder())
        render, render_label = make_renderer(config)
        rows = {}
        for query in queries:
            row = evaluate(index, config, retriever, corpus, query, args.repeats)
            row.update(evaluate_end_to_end(retriever, runner.run, render, query, args.repeats))
            rows[query.name] = row

        def median(key: str) -> float:
            return statistics.median([row[key] for row in rows.values()])

        report = {
            "meta": {
                "size": args.size, "seed": args.seed, "repeats": args.repeats,
                "dimensions": config.rag_embedding_dimensions,
                "about_count": len(corpus.about), "authored_count": len(corpus.authored),
                "embedded": embedded, "build_seconds": build_seconds, "render": render_label,
                "embed_seconds": embed_seconds, "spacing_minutes": corpus.spacing_minutes,
                "generated_at": datetime.now(timezone.utc).isoformat(),
                "config": {name: getattr(config, name, None) for name in TRACKED_SETTINGS},
            },
            "queries": rows,
            "latency_ms": {
                "search_recent": median("ms_recent"), "search_lexical": median("ms_lexical"),
                "semantic_warm": median("ms_semantic_warm"), "total_warm": median("ms_total"),
                "semantic_cold": measure_cold_semantic(index, config, min(args.repeats, 3)),
                "retrieve_end_to_end": median("ms_retrieve"),
            },
        }

        if args.json == "-":
            print(json.dumps(report, indent=2))
        else:
            print_report(report)
            if args.json:
                Path(args.json).write_text(json.dumps(report, indent=2))
                print(f"\nwrote {args.json}")
        if args.compare:
            print_comparison(json.loads(Path(args.compare).read_text()), report)
        if args.keep:
            print(f"\nkept scratch database in {workdir}")
        return 0
    finally:
        runner.close()
        if holder is not None:
            holder.cleanup()


if __name__ == "__main__":
    sys.exit(main())
