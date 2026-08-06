# Improvement Analysis — `discord-ai-bot`

**Repo:** `<repo-root>`
**Branch:** `dev` · **HEAD:** `c83f740` · **Date:** 2026-07-29
**Baseline at time of analysis:** 125 tests pass in ~1.0 s via
`python -m unittest discover -s tests -p "test_*.py"` from the repo root. `git status` clean.
**Production size:** 19,405 lines across 54 files (`src/` + `main.py`); 23,973 Python lines
counting `tests/` and `scripts/`.

> **Read this before quoting any figure below. Re-dated 2026-08-05 at `083ebea`.**
>
> This is a **snapshot measured at `c83f740`**; 58 implementation commits (`c83f740..083ebea`)
> have landed since and the gate is now
> **401 tests in 37 files** with a **137-mutant** harness. Every present-tense figure below means
> "as at `c83f740`". Two corrections that a date alone does not cover, because the numbers are
> not stale — they are **wrong to act on**:
>
> - **I2-09's "1844x" (`:130`, `:206`, `:655`, `:981`, `:3073`) is REFUTED, not merely old.** It
>   was measured on an unscoped query production never issues. On the real channel-scoped form,
>   once I2-08/DAB-078 has landed, the rewrite is **111x slower** and reintroduces the temp
>   B-tree it exists to remove. `M-DAB212` exists to stop it being re-applied. Do not apply it:
>   [`ANALYSIS_CORRECTIONS.md`](ANALYSIS_CORRECTIONS.md) item 13 and
>   [`analysis-tickets/DAB-212.md`](analysis-tickets/DAB-212.md).
> - **I2-08/I3-04's "+46 % writes (52.3 -> 76.7 µs/row)" (`:132`, `:950`) overstates the cost
>   that was actually paid by 3x.** It landed by *replacing* the composite index rather than
>   adding to it, measured at **+15 %** with no size growth (`ANALYSIS_BACKLOG.md:98`).
> - I3-08's "11.67x" (`:133`, `:204`, `:1454`, `:3070`) re-measures at **6.72x** at `083ebea`
>   (1.278 s -> 0.190 s). The direction and the pixel-identity hold.


---

## 1. Header — scope and method

### 1.1 Scope

This document is the **improvement** half of a two-part analysis. The bug half produced a
canonical defect corpus of 211 findings (`DAB-001 … DAB-211`); this document does not re-report
defects, it designs the replacements and prices them. Every proposal cross-references the DAB
IDs it closes.

Six improvement lanes were run independently:

| Lane | Domain | Proposals | Prototyped | Designed only |
|---|---|---:|---:|---:|
| **I1** | Architecture — composition, decomposition, failure policy, seams, dead code | 20 | 4 | 16 |
| **I2** | Data layer — migrations, connections, indexing, retention, schema correctness | 18 | 13 | 5 |
| **I3** | Performance — CPU hot spots, event loop, memory, concurrency ceiling | 19 | 14 | 5 |
| **I4** | Cost accounting — token metering, pricing, budgets, spend controls | 12 | 3 | 9 |
| **I5** | Configuration — validation, fail-fast, runtime mutation, operability, secrets | 15 | 10 | 5 |
| **I6** | Testing & developer experience — coverage, mutation, CI, onboarding | 12 | 8 | 4 |
| | **Total** | **96** | **52** | **44** |

A further **26 ideas were evaluated and rejected or deprioritized**; they are recorded in §11
so they are not re-litigated.

### 1.2 Method

Every lane worked against a full copy of the repo under `/tmp` — `/tmp/i1proto`, `/tmp/i2lab`,
`/tmp/i3-perf`, `/tmp/i4`, `/tmp/i5proto`, `/tmp/i6` — including the repo's own `.venv`
(CPython **3.12.13**, numpy 2.5.1 / scipy-openblas 0.3.33, PyMuPDF 1.28.0 / MuPDF 1.29.0,
pydantic 2.13.4, sqlite3 3.53.1). Probe host: 64 logical CPUs, 117 GiB RAM, `/tmp` on tmpfs.
**No file in the repo tree was created, modified, or deleted by any lane**; `git status
--porcelain` was verified empty before and after each run.

Where a number here disagrees with a prior lane, the disagreement is stated and explained
rather than averaged away.

**Two caveats that apply to every number in this document:**

1. `/tmp` is tmpfs. Measured SQLite commit p50 was **0.1168 ms** and raw `fsync` p50
   **0.0013 ms** — effectively a no-op. Every SQLite figure is therefore a **floor**; a real
   SSD adds ~0.2–1.0 ms per commit. This understates the payoff of commit batching and of
   getting SQLite off the event loop.
2. **All dollar figures rest on an assumed price vector** (§10.2). The repo contains no price
   data of any kind. Token counts are assumption-free; dollars are not.

### 1.3 How to read the cost tags

Every proposal carries five tags. They are defined in §2. The single most important one is
the **evidence** tag:

- **[PROTOTYPED]** — the change was physically built in a `/tmp` copy, executed, and measured.
  The number quoted is a measurement with stated conditions.
- **[DESIGNED]** — the *problem* may have been measured, but the *fix* exists only as a design.
  Any payoff figure is an estimate and is labelled as one.

A [DESIGNED] proposal is never presented as measured. Where a designed fix sits next to a
measured problem, the measurement is attributed to the problem, not the fix.

---

## 2. Cost tagging scheme

### 2.1 Effort

| Tier | Meaning |
|---|---|
| **XS** | ≤ 1 hour. Typically a one-line or two-line diff. |
| **S** | ≤ ½ day. One file, or several mechanical edits. |
| **M** | 1–2 days. A new module, or a change threaded through 3–8 files. |
| **L** | 3–5 days. A structural change with a migration or a phased cutover. |
| **XL** | > 1 week. Nothing in this document is XL; two proposals (I4-01 + I4-02 + I4-03 together, I5-01 in full) approach it. |

### 2.2 Blast radius

Stated as **files touched** plus **tests broken**. "Tests broken" counts existing tests that
must be edited, not tests added. The distinction matters here because
`tests/test_command_registration.py` pins the entire command tree byte-for-byte
(`EXPECTED_SIGNATURE`, `len(...) == 22`, `len(EXPECTED_SIGNATURE) == 35`, and a positional
slice guard at line 239), so any tree change is a three-file change per AGENTS.md.

### 2.3 Payoff

Measured where a prototype exists — quoted with units and conditions (corpus size, thread
count, input shape). Estimated otherwise, and marked as such.

### 2.4 Risk if skipped

The consequence of not doing it, stated as a production failure mode rather than as a code
smell.

### 2.5 Dependencies

Hard prerequisites only. "Nice to have after X" is stated as a sequencing note, not a
dependency.

### 2.6 Evidence register — what was prototyped versus designed

| Lane | PROTOTYPED | DESIGNED ONLY |
|---|---|---|
| **I1** | I1-01, I1-30, I1-40 (seams proven, tests not yet written), I1-50 | I1-02, I1-03, I1-04, I1-10, I1-11, I1-12a, I1-13, I1-20, I1-21, I1-22, I1-23, I1-31, I1-41, I1-42, I1-43, I1-44 |
| **I2** | I2-01, I2-02, I2-04, I2-05, I2-06, I2-07, I2-08, I2-09, I2-10, I2-11, I2-14, I2-15, I2-18 | I2-03, I2-12, I2-13, I2-16, I2-17 |
| **I3** | I3-01, I3-02, I3-03, I3-04, I3-05, I3-06, I3-07, I3-08, I3-09, I3-10, I3-11, I3-14, I3-15, I3-17 | I3-19, I3-20, I3-21, I3-22, I3-CR |
| **I4** | I4-01 (DDL + backfill + rollup executed on sqlite 3.53.1), I4-05, I4-11 | I4-02, I4-03, I4-04, I4-06, I4-07, I4-08, I4-09, I4-10, I4-12 |
| **I5** | I5-01, I5-03, I5-04, I5-05, I5-06, I5-08, I5-09, I5-10, I5-11, I5-12 | I5-02, I5-07, I5-13, I5-14, I5-15 |
| **I6** | I6-01, I6-02, I6-03, I6-04, I6-05, I6-06, I6-07, I6-08 | I6-09, I6-10, I6-11, I6-12 |

---

## 3. Executive summary

### 3.1 The ten highest-value improvements

Ranked by measured payoff ÷ effort, dependencies respected. Every row in this table is
**[PROTOTYPED]** except where marked.

| # | ID | Improvement | Measured payoff | Effort | Blast radius |
|---:|---|---|---|---|---|
| 1 | **I3-01** | Single-parse message splitter (interval index, one markdown parse per call) | **59.96 s → 0.055 s** at 234 KB code-block-dense = **1088x**; 131/131 outputs byte-identical | S | 1 file, +35 LOC, 0 tests broken |
| 2 | **I2-09** | `get_pending_embeddings`: `ORDER BY m.created_at` → `ORDER BY e.message_id` | **127.666 → 0.069 ms = 1844x**, zero new indexes, two-line diff | S | 1 query |
| 3 | **I3-05 / I2-14** | Ledger the startup eligibility reconcile on a rules fingerprint | **1938.0 ms → 0.034 ms per boot at 100k = 57,134x**; boot ~2.67 s → ~0.06 s | S | 2 methods |
| 4 | **I2-08 / I3-04** | Two `(scope, created_at DESC)` indexes on `message_index` | channel **30.81 → 0.026 ms = 1200x** (partial) / 1.595 → 0.055 ms = 29x; guild **79.04 → 0.101 ms = 783x** / 104.742 → 0.056 ms = 1870x. Cost: writes +46 % (52.3 → 76.7 µs/row) | XS–S | 2–5 DDL lines |
| 5 | **I3-08** | Delete the PDF PNG encode→decode round-trip | **1.634 s → 0.140 s** per 20-page render = **11.67x**, verified **pixel-identical** (1190×1684) | S | 3 lines |
| 6 | **I3-02 + I3-03** | Lock-free vector snapshot + pre-normalised adaptive `search_semantic` | **0.4 → 97.5 ops/s at 16 threads, 350k = 244x**; latency **2225 → 62 ms = 35.8x**; peak memory **2056.5 → 8.4 MiB**; ids identical, scores within 2e-6 | M+M | 1 file, cache accessors |
| 7 | **I2-04** | `journal_mode=WAL` + explicit `busy_timeout` in `sqlite_utils.py` | contended read **fails after 5.01 s → succeeds in 3.3 ms**; write throughput **2,918 → 12,355/s = 4.2x** | S | ~15 lines, every DB call |
| 8 | **I3-06 / I2-05** | Batch backfill commits (batch 200–500) | **653 → 51,320 msg/s = 79x** at batch 1000; **12,880 → 178,330 rows/s = 13.8x** at batch 500 | M | backfill loop + cursor semantics |
| 9 | **I4-07 L1** | `discord.py` cooldowns on `/deepresearch`, `/summarize`, `/rag backfill` **[DESIGNED]** | worst-case single-user exposure **~$3,247/h → ~$0.90/h ≈ 3,600x** (assumed prices; token cap is assumption-free) | S | decorators + 1 error handler, no tree change |
| 10 | **I6-03** | Hoist delivery out of the hybrid-RAG `try`; gate on `rag_context is not None` | 6-line fix for a **live duplicate-reply / duplicate-spend defect** that all 125 tests are blind to; suite 125 → 159 green | S+S | 1 pipeline change + 9 tests |

**End-to-end effect of items 1–6 measured together** (`bench_budget.py`, 16 concurrent workers,
real seeded SQLite and a real vector cache):

| Indexed messages | Throughput before → after | p95 latency before → after | In-flight users at a 20 s budget |
|---|---|---|---|
| 100,000 | 1.46 → **143.57 req/s (98.4x)** | 10,833 → **133 ms (82x)** | 29 → **2,871** |
| 350,000 | 0.42 → **48.67 req/s (116.4x)** | 38,330 → **389 ms (99x)** | **8 → 973** |

The "before" concurrency ceilings (29 at 100k, 8 at 350k) independently reproduce the prior
lane's estimate of ~20 and ~8–10.

### 3.2 What is structurally wrong

Five things, in order of how much they cost.

**(a) There is no cost model, and the books are wrong by a factor of up to 6.7.** The README
advertises token accounting. What exists is a 9-column `token_usage` table that records
*successful main-model calls only*. It never stores the model name, never reads
`thoughts_token_count`, discards the entire event when the provider's reported total is less
than input+output, and misses five of the six SDK call sites entirely. Measured attribution
coverage is **69 % on the cheapest path and 15 % on the legacy-fallback path**. Meanwhile
`/deepresearch` has no cooldown, no permission check, and a 65,536-token output cap, giving a
single scripted user a **~363 million token / ~$3,247 per hour** exposure that would appear in
the books as **zero**. This is §10, and it is the only lane whose headline is a business risk
rather than an engineering one.

**(b) The performance profile has one stop-the-world hazard and one hard concurrency ceiling.**
`message_splitter.split_message` runs on the event loop and is quadratic: 59.96 s on a 234 KB
code-block-dense answer, which crosses Discord's 41.25 s gateway heartbeat budget at
**≈195 KB** and disconnects the bot. Separately, `_vector_lock` is held across CPU-bound numpy
scoring, so semantic retrieval delivers **literally zero parallel speedup** (measured 1.00x /
1.01x / 1.02x / 1.01x / 1.01x at 1/2/4/8/16 threads). Together with two missing SQLite indexes
these three things cap the bot at **8 simultaneous users at 350k indexed messages**.

**(c) Failures are invisible by construction.** 39 of 237 `except` handlers are invisible at
the default log level. Hybrid-RAG retrieval falls back to the legacy path on *any* exception
with one WARNING. Four services are constructed inside command registration, so an exception in
any of eleven registrars permanently disables live mode, personalities, pins and user
preferences — and the bot answers messages normally throughout. `validate_startup_connectivity`
always returns `True` on every path, including the `except`. `get_service_health_status`
hardcodes `"Available"` for five core services without checking anything.

**(d) The data layer has no versioning and no back-pressure.** No migration framework, no
`PRAGMA user_version`; each of seven services creates its own schema at construction, via three
mutually incompatible mechanisms. `sqlite_utils.py` sets no pragmas at all, so a contended read
fails after 5.01 s and a contended write is silently swallowed as a `False` that the RAG event
coordinator treats as a permanent tombstone. Nothing is ever deleted: the in-RAM vector cache
crosses **1 GB at day 9** and **42 GB at one year** on a 200-channel / 50k-messages-per-day
server, with a `_ensure_vector_capacity` doubling that makes the peak 3× the steady state.

**(e) 47 of 100 config fields have no validation, and the ones that break are the dangerous
ones.** `log_level: "LOUD"` boots. All four `safety_*` thresholds are unvalidated and an
unknown *category* defaults to `BLOCK_NONE` — a safety posture the operator did not choose.
`messages.split_length: 100` with `continuation_overhead: 100` yields
`effective_max_length == 0`, and a 20,000-character answer becomes **20,000 Discord messages**.
34 of 42 deliberately extreme values pass `validate_config` today. Six of eight malformed YAML
shapes crash with an uncaught `AttributeError`.

### 3.3 The cheapest path to a materially better system

Three commits' worth of work covers most of the risk.

**Hour one — six XS fixes, no shared blast radius, all one-file diffs:** hoist delivery out of
the RAG `try` (I6-03, 6 lines, fixes a live duplicate-reply bug), the PDF round-trip deletion
(I3-08, 3 lines, 11.67x), the two `created_at DESC` indexes plus the reconcile fingerprint
(I3-04 + I3-05, 2 DDL lines and a ledger row, 1200x and 57,134x), the `get_pending_embeddings`
`ORDER BY` rewrite (I2-09, two lines, 1844x), and cooldowns on `/deepresearch` (I4-07 L1,
~3,600x on the cost exposure). **Total: under a day, all measured, none requiring a migration.**

**Day one — the splitter and the pragmas:** I3-01 removes the gateway-disconnect hazard
entirely (worst measurement across every corpus and size drops to 0.0764 s, from 59.96 s), and
I2-04's three pragmas turn a 5.01 s read failure into a 3.3 ms success. Both are S.

**Week one — visibility:** I1-01 (eager construction of the four registration-attached
services, **prototyped, 125/125 green**) plus I1-21's presence badge closes the largest class of
silent whole-feature loss for roughly 30 lines. I6-01/I6-03's tests pin `on_message` before
anyone restructures it.

Everything after that — the composition root, the migration runner, the fact table, the
pydantic schema — is genuinely valuable and genuinely costs days. None of it should precede the
list above.

---

## 4. Lane I1 — Architecture

### 4.1 Diagnosis

`DiscordBot.__init__` (`discord_bot.py:302-439`, 138 lines) constructs ~16 services in a fixed,
undocumented order. Five module-level factories (`_get_or_create_*` at `:138`, `:193`, `:212`,
`:229`, `:248`) read attributes off `owner` that earlier constructor lines must already have
set; four are invoked from `__init__` itself at lines 324, 368, 388 and 437, interleaved with
the service construction. The ordering constraint is real and invisible.

Three distinct defects sit on top:

- **Registration-attached services (DAB-002, DAB-014).** `ChannelSettingsService`,
  `UserPreferencesService`, `MessageVisibilityService` and `PinService` re-wiring happen inside
  `register_personalization_commands` (`personalization.py:43,142,144,146,380`), which
  `setup_commands` calls **last** (`commands.py:91`), from `on_ready`, inside a single blanket
  `try/except`. Before `on_ready`, `_is_live_mode_enabled` returns `False` and
  `_get_personality_prompt` returns `None` — live mode reads as *off*, not as *not yet ready*.
  An exception in any of the eleven earlier registrars makes that permanent, and the only
  symptom is one ERROR line followed by a bot that answers normally.
- **The fifth coordinator has a third lifetime.** `_response_generation` is only ever built by
  `_get_or_create_response_generation` on first call, so it materialises during the **first user
  message**, capturing eight bound methods at that moment.
- **The pre-`on_ready` window has no name.** `image_processing_service` is constructed at `:398`
  but `start()`ed at `:486`; `report_web_server` at `:340` / `:497`. Nothing prevents a message
  arriving between the two.

Module size is not the pain. Measured: 101 functions exceed 50 lines and **30 exceed 100** — and
**the four largest functions in the repo are all command registrars**, not service code:
`register_personalization_commands` **423** lines, `register_usage_commands` **319**,
`create_config_group` **281**, `register_feature_commands` **261**. `message_index_service.py`
is 1,525 lines but already carries 40 tests across three files.

Failure policy: 237 `except` handlers, 39 invisible at the default log level.
`validate_startup_connectivity` (`config.py:310-329`) returns `True` on **every** path including
the `except`, so the caller's `if not connectivity_ok:` branch is dead code.
`get_service_health_status` (`:582-622`) hardcodes `"✅ Available"` for five services at
`:593-596`.

**The coordinator pattern is the one structural decision that demonstrably worked.** 21 of 125
tests (**17 %**) instantiate a coordinator with plain keyword arguments and no `DiscordBot`
anywhere — `test_discord_orchestration.py` (11), `test_response_generation.py` (4),
`test_media_extraction.py` (6). That is the *entire* unit-test coverage of the bot layer, and it
exists because the coordinators were extracted. Verdict: **KEEP**.

### 4.2 Proposals

#### I1-01 — Eager construction of the registration-attached services · **[PROTOTYPED]**

- **Closes:** DAB-002, DAB-014.
- **Design:** construct `ChannelSettingsService`, `UserPreferencesService` and
  `MessageVisibilityService` in `__init__` next to the existing `PinService` (`:364`); change
  `personalization.py:37` and `:376` to the `getattr(bot, "_x", None) or X(...)` idiom already
  used at `:138` and `:145`.
- **Effort:** S (~30 min). **Blast radius:** 2 files, +18 / −6 lines, **0 tests broken
  (prototype P1: 125/125 green)**.
- **Payoff:** removes an entire class of invisible whole-feature loss; makes
  `_is_live_mode_enabled` mean what it says. The four services now exist from construction, so
  a failing registrar merely fails to *adopt* them.
- **Risk if skipped:** the failure stays undetectable in production — the bot answers normally
  while three commands' worth of state is missing.
- **Depends on:** nothing. Everything else in §4 depends on it.

#### I1-02 — `BotServices` composition root + `build_services(config)` · **[DESIGNED]**

- **Closes:** the 138-line order-dependent `__init__`; leaf services untestable without a
  Discord client.
- **Design:** a new `src/bot/composition.py` (~250 lines) with a frozen `BotServices` dataclass
  holding every leaf service, and a `build_services(config) -> tuple[BotServices,
  StartupReport]` that is pure with respect to Discord (touches SQLite and the Gemini SDK,
  never the gateway). `HybridContextRetriever` is given its `pin_service` **once, here**, which
  deletes `set_pin_service()`. `DiscordBot.__init__` keeps `_expose_legacy_attributes()` as
  migration lubricant so `self.gemini_client`, `self.token_tracker`, `self._pin_service` etc.
  stay as plain aliases and no existing caller or test changes on day one.

```python
def build_services(config: BotConfig) -> tuple[BotServices, StartupReport]:
    report = StartupReport()
    with report.step("error_manager", Criticality.REQUIRED):
        error_manager = ErrorManager(config)
    with report.step("token_tracker", Criticality.CORE) as s:
        token_tracker = s.attempt(lambda: TokenTracker(config.token_db_path))
    ...
    report.raise_if_fatal()      # REQUIRED failures abort before Discord is touched
    return BotServices(...), report
```

- **Effort:** M. **Blast radius:** 1 new file (~250 lines); `__init__` shrinks 138 → ~35;
  `main.py` +4. **0 tests broken if the aliases are kept.**
- **Payoff (estimated):** construction order becomes one readable function; leaf services get a
  test entry point that costs nothing to use; gives the failure policy a home.
- **Risk if skipped:** every new service pays the "where in the 138 lines does this go?" tax.
- **Depends on:** I1-01.

#### I1-03 — Two-phase lifecycle: `construct → start_services()` · **[DESIGNED]**

- **Closes:** the unnamed pre-`on_ready` window; `_response_generation`'s third lifetime.
- **Design:** `Phase = {CONSTRUCTED, STARTING, READY, CLOSING}`; a single `_build_coordinators()`
  at the end of `__init__` building all five eagerly; `async start_services() -> StartupReport`
  owning every async start. `on_message` returns early with a "still starting" reply when
  `phase is not READY`.
- **Effort:** M. **Blast radius:** `discord_bot.py` only; 0 tests broken **provided the five
  `_get_or_create_*` functions survive as `getattr` lookups** — three tests
  (`test_bug_regressions.py:190-244`) drive coordinators through a `SimpleNamespace` owner and
  rely on on-demand materialisation.
- **Payoff (estimated):** the window becomes assertable; `close()` becomes symmetric with
  `start_services()` instead of tearing down five things started in three places.
- **Depends on:** I1-02.

#### I1-04 — Registrars stop mutating `bot` · **[DESIGNED]**

- **Closes:** command registration doubling as service wiring (`personalization.py` assigns 4
  attributes; `configuration.py:135` assigns `bot.image_generation_enabled`).
- **Effort:** M. **Blast radius:** 3 files; `test_command_registration.py::_register`
  (`:88-104`) must build a `BotServices` — **7 tests touched, none semantically**.
- **Payoff (estimated):** registration becomes side-effect-free.
- **Risk if skipped:** low once I1-01 lands. This is cleanup, not a hole. Rank it last.
- **Depends on:** I1-02.

#### I1-10 — Decompose `discord_bot.py` (1,452 lines; `DiscordBot` is 1,157) · **[DESIGNED]**

| New module | Responsibility | Moves | ~Lines |
|---|---|---|---|
| `discord_bot.py` | Discord event surface only | — | ~420 |
| `composition.py` | `BotServices`, `build_services`, `StartupReport`, `_build_coordinators` | the 5 `_get_or_create_*` (`:138-291`) | ~270 |
| `message_pipeline.py` | mention → context → generate | `_process_message_with_context` (`:884`, 185 lines), `_get_context_limit_for_complexity`, `_resolve_request_preferences`, `_extract_user_prompt`, `is_bot_mentioned` | ~300 |
| `pdf_rasterizer.py` | PyMuPDF + the single-worker executor and its drain | `_convert_pdf_to_images(_sync)`, `_convert_image_to_rgb`, the `_pdf_executor` lifecycle | ~120 |
| `rag_backlog.py` | startup backlog scan | `_get_accessible_rag_channels`, `_start_automatic_rag_backlog` | ~95 |
| *(deleted)* | 11 dead wrappers | see I1-30 | −78 |

- **Effort:** L. **Blast radius:** **~12 of 125 tests need an import or constructor edit; zero
  need new assertions.** `test_pdf_processing.py` (all 6) would construct `PdfRasterizer(config)`
  instead of `object.__new__(DiscordBot)` — an improvement, not a cost;
  `test_discord_orchestration.py` needs 2 import lines; `test_bug_regressions.py` follows 4
  tests to `message_pipeline`.
- **Payoff (estimated):** high. This is the split that makes `on_message`, the pipeline and
  shutdown independently testable.
- **Depends on:** I1-02, I1-03, I1-30 — **and I1-40**. Splitting 400 lines of unpinned
  `on_message`/pipeline behaviour is the single most likely way to turn this document into an
  outage.

#### I1-11 — Decompose `gemini_client.py` (1,836 lines; one class of 1,799, 49 methods) · **[DESIGNED]**

Worst functions: `format_prompt` **146** (`:1674`), `_generate_response_async` **139**
(`:1389`), `_interpret_provider_response` **136** (`:1048`). Hardcoded operational data is
buried inline: the free/paid rate-limit and pricing table at `:207-235`, search-trigger regex
sets at `:523-550`, a 22-line router prompt at `:719-740`, safety mappings at `:1431-1442`,
finish-reason maps at `:1574-1622`.

| Module | Responsibility | ~Lines |
|---|---|---|
| `gemini_client.py` | SDK facade + request execution | ~620 |
| `gemini_model_policy.py` | **pure policy, no SDK** — model, thinking level, search on/off, timeouts, token ceilings, pricing | ~430 |
| `gemini_prompting.py` | prompt and context assembly | ~290 |
| `gemini_response_pipeline.py` *(exists, 84 lines — proves the seam)* | provider response → `APIResponse` | ~440 |

- **Effort:** L. **Blast radius:** `tests/test_gemini_pipeline.py` — 15 tests; four use
  `object.__new__(GeminiClient)` (`:39, 83, 121, 149`) purely to dodge `_configure_api`'s real
  `genai.Client(...)`. After the split those become `GeminiModelPolicy(config)` with **real
  constructors** — the split *deletes* 4 of the repo's 8 `object.__new__` sites.
- **Payoff (estimated):** medium-high. `gemini_model_policy` is the valuable extraction: it
  holds the operational numbers most likely to drift and is pure logic over `BotConfig`.
- **Honest note:** if there is budget for only one of I1-10 / I1-11, **do I1-10**.
  `discord_bot.py` is where the untested behaviour lives; `gemini_client.py` is merely large.
- **Sequencing:** do I1-50 first so you split 1,780 lines instead of 1,836.

#### I1-12a — Extract `message_index/schema.py` · **[DESIGNED]**

Move `_ensure_schema` (**144** lines, `:126`), `_ensure_column` (`:113`),
`_migrate_legacy_database` (**96**, `:602`), `_reconcile_embedding_eligibility` (`:705`) and the
`rag_migrations` ledger (`:622, 629, 686`) — ~300 lines. This isolates the startup hazard:
`__init__:104-106` calls two methods that can raise and abort construction, and
`_reconcile_embedding_eligibility` runs a full-table scan on **every** construction.
**Effort M. 0 tests broken if the facade re-exports.** Prerequisite for I1-22.

#### I1-13 — Split `register_personalization_commands` (423 lines, the largest function in the repo) · **[DESIGNED]**

Into `src/bot/command_modules/personalization/`: `__init__.py` (~25), `personality.py` (~110),
`live_mode.py` (~60), `memory.py` (~180), `preferences.py` (~110).

- **Effort:** M. **Blast radius:** 1 file → 5, ~453 lines redistributed, **0 test edits** —
  *provided* `register_personalization_commands` keeps its name, signature and **registration
  order**. `EXPECTED_SIGNATURE` is order-sensitive and its last 12 entries all come from this
  function, so `__init__.py` must call the four sub-registrars in exactly the current order. The
  `22` and `35` guards are unaffected.
- **Depends on:** I1-01, so the split does not have to preserve the service-construction side
  effect.

#### I1-20 — `Criticality` + `StartupReport`: one failure-policy record · **[DESIGNED]**

```python
class Criticality(Enum):
    REQUIRED = "required"   # cannot run correctly -> abort before connecting to Discord
    CORE     = "core"       # a headline capability is gone -> loud + surfaced + presence badge
    OPTIONAL = "optional"   # a convenience is gone -> one WARNING + surfaced
    COSMETIC = "cosmetic"   # debug log, aggregate counter only
```

The invariant that makes it work: **`get_service_health_status()` stops deriving anything and
just renders `self.startup.as_status()`.** One record, two consumers. A subsystem cannot degrade
without the status surface knowing, because degrading *is* writing to the record.

Selected classifications:

| Subsystem | Today | Proposed |
|---|---|---|
| DB paths writable | not checked | **REQUIRED** — currently surfaces as a mid-startup `TokenTracker` traceback |
| `GeminiClient.client is None` (no/invalid key) | INFO-ish log, bot starts and fails every message | **CORE** |
| `EnhancedCommandHandler` construction failure | `error` log, `None` | **CORE** — complexity routing silently off for all text traffic |
| Image-service `start()` failure | nulls **both** the image service **and** the router (DAB-016) | **OPTIONAL** for the image service, and *an OPTIONAL failure may never disable a CORE subsystem* — encode that as an assertion |
| `TokenTracker` | `error` log, `None` | **CORE** |
| The 4 registration-attached services | absent, indistinguishable from off | **CORE**, constructed in `build_services` (I1-01) |
| FTS5 unavailable | silent | **OPTIONAL** |
| Hybrid-RAG → legacy fallback | one `warning` per occurrence | **COSMETIC** per event, **CORE** if the fallback rate exceeds a threshold over a window |

**Effort:** M. **Blast radius:** 1 new file (~130 lines) + `discord_bot.py`. **Depends on:**
nothing hard; pairs with I1-02.

#### I1-21 — Make degradation visible: presence badge + banner + `/config info` · **[DESIGNED]**

Three surfaces fed by the same record, cheapest first:

1. **Presence badge (~8 lines).** `on_ready:522-527` already sets an Activity. Change it to
   `Status.idle` with `f"DEGRADED: {', '.join(s.name for s in degraded[:3])}"` when anything is
   degraded. **This is the highest signal-per-line change in the entire document:** every user
   in every guild sees a yellow dot and the reason in the member list, with no log reading.
2. **One-shot startup banner.** Replace the 12 hand-written `logger.info` lines at `:502-520`
   with `logger.info(self.startup.banner())` plus one WARNING block. Net **−15 lines**, and the
   list can no longer drift from reality.
3. **`/config info` truth.** `get_service_health_status` becomes `return
   self.startup.as_status()` — **41 lines → ~4** — and automatically gains RAG, FTS5, token
   tracking, channel settings, prefs, visibility and schema state.

**Effort:** S once I1-20 exists. **Blast radius:** `discord_bot.py` only, net negative lines,
0 tests broken (nothing asserts on the status dict today — worth adding one).
**Risk if skipped:** the highest-frequency production failure mode in this codebase is "a
feature quietly turned itself off and nobody noticed for days".

#### I1-22 — A real migration ledger · **[DESIGNED]** — superseded by I2-01

Seven services each create their own schema at construction; adding a column requires *also*
remembering an idempotent `_ensure_column` call (11 such calls in
`message_index_service._ensure_schema` alone, plus one in `channel_settings_service.py:39`). The
`rag_migrations` ledger already exists but is **duplicated** — defined independently at
`pin_service.py:65` and `message_index_service.py:622` — and only guards one-shot data moves,
not DDL.

**I2-01 is the better-specified version of this proposal and has a 20/20 prototype. Prefer
I2-01; keep I1-22 only for the framing that the composition root should run migrations once,
before any service is built, and record the result as a REQUIRED subsystem.**
**Effort:** M. **Blast radius:** ~6 tests need a setup line. **Depends on:** I1-12a, I1-02.

#### I1-23 — Per-registrar isolation in `setup_commands` · **[DESIGNED]**

Wrap each of the 11 registrar calls in `report.step(name, Criticality.CORE)`; a failing
registrar loses only its own commands and is named in the degraded list. `tree.sync()` stays a
single CORE step. **Effort:** S, +~15 lines in `commands.py`, `test_command_registration.py`
unaffected. **Note:** once I1-01 lands, a failed registrar only costs its own commands, so this
becomes optional. Do I1-01 first.

#### I1-30 — Delete the 11 dead `DiscordBot` coordinator wrappers · **[PROTOTYPED]**

Reference-counting every wrapper across `src/` and `tests/` gives **11 with exactly one
reference (their own definition)**, totalling **exactly 78 lines**:

```
_send_split_response          _send_simple_split_response      _send_grounding_sources
_clean_split_part_for_embed   _get_user_friendly_error_message  _add_error_reaction
_update_progress_message      _create_image_context_entry       _append_live_context_entry
_run_live_channel_worker      _get_live_context_buffer
```

**Not dead despite appearances:** `_send_paginated_embed` (`:1411`) is called from
`command_modules/research.py:373`; `_get_live_channel_lock` (`:697`) is used by 2 tests.

**Effort:** S. **Blast radius:** `discord_bot.py` only, −78 lines, **0 tests broken (prototype
P3: 125/125)**. Ships inside I1-50 commit C2.

#### I1-31 — Replace coordinator callback-bags with protocol objects · **[DESIGNED]**

`LiveMessageCoordinator.__init__` takes **21 parameters**; `ResponseGenerationCoordinator` takes
**15**; `MediaExtractionCoordinator` 7, `ResponseDeliveryCoordinator` 6, `RagEventCoordinator` 5
— almost all bare callables. This is *why* `_get_or_create_live_coordinator` is 53 lines of
`getattr(owner, "x", default)`.

**Effort:** M. **Blast radius:** 5 coordinator files + `discord_bot.py`; **breaks the
`make_coordinator(**overrides)` helper (`test_discord_orchestration.py:33`) and
`_make_coordinator` (`test_response_generation.py:43`) — ~15 tests need their fake reshaped**,
none need new assertions. **Payoff:** low-medium. The bags are ugly but they work and the
`**overrides` helper is genuinely ergonomic. **Rank this last**; do it only if already inside
these files.

#### I1-40 — Write the missing hot-path tests · **[PROTOTYPED — seams proven]**

The headline finding of this lane: **the hot paths are untested by omission, not by
architecture.** Two probes proved it.

- **P5:** `DiscordBot.on_message` (130 lines) driven as an unbound method against a
  **10-attribute `SimpleNamespace`**, exercising three branches (normal routing, `author.bot`
  short-circuit, router `handled=True` short-circuit) in **35 lines of fake**.
- **P6:** `EnhancedCommandHandler.handle_message` constructed directly (4 constructor
  arguments) and driven through the `UNKNOWN`-passthrough and `IMAGE_EDIT`-with-no-service
  branches. One snag: `__init__:71-72` reaches through `bot.config.router_cache_size` /
  `.router_cache_ttl`, so the fake bot must carry a `config`.

`close()` (64 lines) is *partially* tested: `test_pdf_processing.py:230` already covers the
executor drain; the other five teardown paths are uncovered but the technique is proven.
`on_ready` (86 lines) is the one hot path where the seam genuinely does not exist — I1-03's
`start_services()` is what makes it testable.

**Proposal:** three new files, ~15 tests, in the existing house idiom.

| File | Coverage | Tests |
|---|---|---|
| `tests/test_on_message_routing.py` | bot-author skip, RAG-index failure non-fatal, live-mode fork returns before the mention gate, mention gate, rate-limit reply, router short-circuit, empty prompt, `Forbidden` / `HTTPException` / `CancelledError` | ~9 |
| `tests/test_router.py` | LRU eviction at `_router_cache_size`, TTL expiry, `IMAGE_EDIT` with no image service, `IMAGE_GENERATE` disabled, router exception → `(True, RoutingDecision("medium"))` | ~5 |
| extend `tests/test_pdf_processing.py` | each `close()` teardown path continues when the one before it raises | ~2 |

**Effort:** S–M. **Blast radius:** additive only, 0 existing tests touched.
**Payoff:** high, and it is the **prerequisite for every restructure in §4**.
**Caveat, stated honestly:** the duck-typed-`SimpleNamespace`-self idiom is fragile. Add one
attribute access to `on_message` and every such test fails with `AttributeError`, and the fake
silently drifts from the real bot. That is a real cost — but a cheaper one than 130 untested
lines, and I1-02's `BotServices` fixes it properly later by giving the fake a type.

*(I6-01 is the executed version of this proposal: 13 tests, 0.037 s, prototyped. Prefer I6-01's
concrete file.)*

#### I1-41 — `ContentRenderer(matplotlib_available: bool | None = None)` · **[DESIGNED]**

The best seam-fix ratio in the repo. `ContentRenderer.__init__` (`content_renderer.py:95-103`)
does nothing but `import matplotlib; matplotlib.use('Agg'); self._matplotlib_available = True`.
It costs a heavy import, mutates **process-global** matplotlib state, and makes the
Unicode-fallback branch unreachable through the public constructor — so
`test_content_renderer.py` bypasses it **3 times** (`:13, :227, :263`) and mocks a private method
**5 times**. **Effort:** S, +3 lines. **Blast radius:** 16 tests touched, none semantically;
removes 3 of the repo's 8 `object.__new__` sites and stops the suite mutating global matplotlib
state.

#### I1-42 / I1-43 / I1-44 — smaller seams · **[DESIGNED]**

- **I1-42** `GeminiClient(config, configure: bool = True)`: removes 4 `object.__new__` sites.
  **Prefer folding this into I1-11** — a `configure=False` parameter is a test-only escape hatch
  in production code, which is its own smell. **Effort:** S (flag) / L (as part of I1-11).
- **I1-43** `PdfRasterizer` extraction: removes the last `object.__new__(DiscordBot)`
  (`test_pdf_processing.py:15`) and the 4 attribute injections around it. Covered by I1-10;
  standalone at **S** if I1-10 slips.
- **I1-44** Decouple the router from `bot`: `EnhancedCommandHandler` reaches through `self.bot`
  five times (`:71-72`, `:183`, `:195-196`, `:541`, `:613`). Change the constructor to
  `(config, image_processing_service, error_manager, gemini_client, record_token_usage)`.
  **Effort:** S. Do it **with** I1-40 while writing the router tests.

#### I1-50 — Dead-code removal, eight commits · **[PROTOTYPED]**

Verified by two independent lanes on two independent `/tmp` copies:

| Scope | Before → after | Delta | Suite |
|---|---|---|---|
| C6 scope (`main.py` + `src` + `scripts` + `tests`, incl. 87 unused import bindings and 4 dead config fields) | 23,973 → 21,946 | **−2,027 lines (−8.5 %)** | 125/125 |
| I1 scope (code deletions only, imports and config fields deferred) | 19,405 → 17,650 | **−1,755 lines (−9.0 %)** | 125/125 |

**Adjudication (V3 §3.A): both are correct for their own scope.** The 272-line gap is fully
explained — 8 vs 6 `UserExperienceService` methods (125 lines), 4 dead `BotConfig` fields across
3 files each (25), 87 vs ~40 unused import bindings (47), 3 dead
`ResponseGenerationCoordinator` helpers (39), 3 `ErrorType` members (3), `RAW_LATEX_RE` (4), the
`_message_index_service` alias (1) — 244 lines, with ~28 lines of counting-convention noise.
**Publish −2,027 as the total and −1,755 as the code-only subtotal.** The authoritative list is
C6 §7, unmodified.

> ### The one thing that must be checked before deleting a single line
>
> A reference-counting dead-code scan produces false positives on framework-dispatched
> callbacks, and this repo is full of them. A naive scan flags **254 lines of live code**:
> `on_message` (130), `on_ready` (86), `on_error` (14), `on_raw_message_delete` +
> `on_raw_bulk_message_delete` (10), `on_disconnect` + `on_resumed` (6),
> `SplitResponsePaginatorView.on_timeout` (8).
>
> And a subtler class: **`image_queue` (`command_modules/general.py:264-315`, 52 lines) is a
> LIVE slash command with exactly one reference in the entire repo — its own definition.** It
> is registered inside `if hasattr(bot, 'image_processing_service') and
> bot.image_processing_service:` (`general.py:124`), and it is absent from `EXPECTED_SIGNATURE`
> only because `test_command_registration._register()` passes `image_processing_service=None`
> and then pins the **degraded** 22-command tree (DAB-203). **Deleting `image_queue` removes a
> user-facing command and no test fails.**
>
> **Commit C0 is therefore non-negotiable:** a deny-filter for (a) any `^on_[a-z_]+$` method on
> a `discord.Client` / `discord.ui.*` subclass, (b) any function defined inside a conditional
> registration block, (c) any `@discord.ui.button` callback. A dynamic-dispatch backstop was
> run separately: every string literal in a `getattr`/`hasattr`/`setattr` call across `src/` and
> `tests/` was cross-checked, and **none of the SAFE names appear**.

| # | Commit | Contents | Lines | Risk | Verified |
|---|---|---|---:|---|---|
| **C0** | deny-list guard for framework callbacks | no deletions | 0 | none | — |
| **C1** | delete `src/services/help_system.py` — zero importers anywhere | −636 | **zero** | P2 |
| **C2** | drop the 11 dead `DiscordBot` wrappers (I1-30) | −78 | **zero** | P3 |
| **C3** | remove unused logging helpers — `ImageProcessingLogger`, `MessageFormattingLogger`, 3 `PerformanceLogger` methods | −335 | very low | P4 |
| **C4** | remove 6 unused `ErrorManager` paths | −253 | low | P4 |
| **C5** | remove 6 unused UX-service methods | −250 | low | P4 |
| **C6** | remove 5 superseded `GeminiClient` accessors | −56 | low-med | P4 |
| **C7** | long-tail unused helpers (9 singles) | −162 | medium | P4 |
| **C8** | prune imports orphaned by C1–C7 | ~−40 (87 bindings in the C6-scope count) | low | verified separately, 125/125 |

Notes that belong in the commit messages: `show_typing_indicator`/`stop_typing_indicator` are
dead because production uses `message.channel.typing()` directly — say so, or someone will
"restore" them; do **not** touch `cleanup_typing_indicators`, which is live in `close()`.
`config.get_safety_threshold` is dead but the `safety:` config block is **live** — delete the
accessor, keep the config. Deleting a `BotConfig` field *without* its `config_helpers.py` parser
entry breaks 3 tests with `TypeError: BotConfig.__init__() got an unexpected keyword argument` —
sequence C6-10's four fields carefully, all three sites per field.

**Effort:** S. **Payoff:** the real value is not the line count — it deletes `help_system.py`,
which is the *only* consumer of 6 `UserExperienceService` methods and the source of 8 of the 13
dangling `/help` promises.

---

## 5. Lane I2 — Data layer

### 5.1 Diagnosis

**Test rig:** `/tmp/i2lab/message_rag_seed.db`, a byte-for-byte copy of the production
`message_rag.db` schema seeded with 100,000 messages across 200 channels / 4 guilds, 75,000
carrying a 768-d float32 vector. **391 MB.**

Measurement summary:

| Measurement | Result |
|---|---|
| `search_recent` channel scope, 10k rows/channel, before → after partial index | **30.81 → 0.026 ms = 1200x** (plain non-partial: 0.034 ms = 914x) |
| `search_recent` guild scope, 25k rows/guild | **79.04 → 0.101 ms = 783x** |
| `get_pending_embeddings`, `ORDER BY m.created_at` → `ORDER BY e.message_id` | **127.67 → 0.069 ms = 1844x**, zero new indexes |
| `search_lexical` worst case, FTS scope column | **49.84 → 8.89 ms = 5.6x** |
| Held write lock, `journal_mode=delete`: concurrent **read** | **FAILS after 5.01 s** ("database is locked") |
| Held write lock, `journal_mode=wal`: concurrent **read** | **OK in 3.3 ms** |
| Held write lock, either mode: concurrent **write** | **FAILS after 5.01 s** — WAL alone does not fix DAB-065 |
| Single-row commit throughput, delete vs WAL | 2,918 → **12,355 writes/s = 4.2x** |
| `synchronous=FULL` vs `NORMAL` under WAL | 12,355 → 12,626 writes/s (**+2 %, noise**) |
| Connection-per-call vs reused connection (read) | 0.295 → 0.029 ms; **0.265 ms/query of pure connect overhead — 9x the query** |
| Adding 4 pragmas to every per-call connection | **+0.322 ms/query** — per-call connections and correct pragmas are mutually exclusive |
| Event-loop max stall, inline 5 s DB call vs offloaded | **4,997.3 ms → 3.1 ms** |
| 400 concurrent reads: today vs thread-local pool(2) under WAL | 547.7 → **64.2 ms = 8.5x** |
| 500 writes: `to_thread` fan-out vs one writer thread | 240.9 → **71.8 ms = 3.4x** |
| Reads during a sustained write burst (WAL, pool 4) | med 0.206 ms, p95 0.304 ms, max 0.410 ms, **0 SQLITE_BUSY** |
| Backfill commit batching, 1 → 500 rows/commit | 12,880 → **178,330 rows/s = 13.8x** |
| Storage per message, all-in | **4,790 B** (index 452 + FTS 228 + embedding row 4,110) |
| FTS: duplicated content vs `content=` external | 22.85 → **6.71 MB = −71 %** |
| `VACUUM` on a 394 MB database | **3,980 ms blocking**, needs ~394 MB scratch |
| Migration-runner prototype | **20/20 assertions pass** across 6 scenarios |

**Growth model — 200 channels, 50,000 messages/day**, from measured per-row costs
(`message_index` 452 B, FTS 228 B, `message_embeddings` **4,110 B** of which 3,072 B is the
float32 vector and 1,038 B is row/page overhead — a **34 % overhead**, retrieval event 71 B,
`token_usage` ~120 B):

| Day | Messages | index MB | fts MB | vectors MB | events MB | **rag total MB** | token MB |
|---:|---:|---:|---:|---:|---:|---:|---:|
| 1 | 50,000 | 23 | 11 | 154 | 1 | **189** | 1 |
| 7 | 350,000 | 158 | 80 | 1,079 | 8 | **1,325** | 4 |
| 30 | 1,500,000 | 678 | 343 | 4,623 | 33 | **5,677** | 18 |
| 90 | 4,500,000 | 2,033 | 1,028 | 13,870 | 99 | **17,030** | 54 |
| 180 | 9,000,000 | 4,066 | 2,056 | 27,741 | 198 | **34,061** | 108 |
| 365 | 18,250,000 | 8,244 | 4,170 | 56,252 | 402 | **69,068** | 219 |

**What breaks first, in order:**

1. **The in-RAM vector cache, at roughly day 25.** `_load_vector_cache` (`:767`) materialises
   *every* completed vector into one `np.float32` matrix on the first semantic search. At 768
   dimensions that is 3,072 B/vector resident: **1 GB at ~350k vectors (day 9)**, **4 GB at day
   35**, **42 GB at one year.** `_ensure_vector_capacity` (`:724`) doubles the array, so the
   peak during a grow is **3× the steady state** — a 4 GB cache momentarily needs 12 GB. This is
   an OOM kill, not a slowdown.
2. `search_semantic`'s full-matrix cosine — linear in corpus size with no ANN index.
3. `_load_vector_cache` itself: 503 ms at 75k vectors; ~92 s at 13.7 M, all at once, at the
   first search.
4. Disk: 69 GB/yr, **81 % of it vectors**.
5. `_reconcile_embedding_eligibility`: a full join scan at *every* startup — 230 ms at 100k,
   2.61 s in the prior lane's environment, minutes at 18 M.
6. `message_retrieval_events` (402 MB) and `token_usage` (219 MB): pure garbage — nothing reads
   the former and the latter is only ever `SUM`med per guild.

### 5.2 Proposals

#### I2-01 — `PRAGMA user_version` migration runner, ~110 lines, no new dependency · **[PROTOTYPED]**

- **Closes:** DAB-066, DAB-083, DAB-099. Three mechanisms coexist today — per-service
  `_ensure_table`/`_ensure_schema`, a static `_ensure_column` (`:113`), and a named-row
  `rag_migrations` ledger (`:622`) — none ordered, none atomic, none able to express a table
  rewrite. Only `channel_settings` has an upgrade path; the other four `token_usage.db` tables
  have none.

**Alembic was evaluated and rejected.** It brings `alembic` + `SQLAlchemy` + `Mako` +
`typing-extensions` (~8 MB), forces a `constraints.txt` regeneration from a clean 3.12 env per
AGENTS.md, expects SQLAlchemy metadata against a codebase that is raw `sqlite3` + hand-written
SQL, needs two `alembic.ini`s for two databases, and requires a human `alembic stamp` for
adoption. Its one feature worth paying for — autogenerate — is unusable without ORM models.

```python
def migrate(conn, *, name, migrations, adoption_probes=()) -> int:
    current = conn.execute("PRAGMA user_version").fetchone()[0]
    target  = max((v for v, _d, _f in migrations), default=0)
    if current == 0 and adoption_probes:
        adopted = _detect_adopted_version(conn, adoption_probes)   # probes run high -> low
        if adopted:
            conn.execute(f"PRAGMA user_version = {adopted}"); conn.commit(); current = adopted
    if current > target:
        raise RuntimeError(f"{name}: database is at v{current} but this build only knows "
                           f"v{target}. Refusing to start.")
    for version, description, fn in sorted(migrations, key=lambda m: m[0]):
        if version <= current: continue
        conn.execute("BEGIN")                    # SQLite DDL is transactional
        try:
            fn(conn)
            conn.execute(f"PRAGMA user_version = {version}")
            conn.commit()                        # version bump commits WITH the DDL
        except BaseException:
            conn.rollback(); raise
        current = version
    return current
```

`sqlite_utils.py` gains one `ensure_migrated(db_path, ...)` entry point with a **process-wide
memo keyed by resolved path**, so migration runs **once per file per process** — today five
services each open `token_usage.db` and each runs its own DDL at construction.

- **Effort:** M (~110 lines runner + ~150 lines of migration bodies + ~120 lines of tests).
- **Blast radius:** every service constructor and `DiscordBot.__init__`. `tests/test_config.py`
  untouched; the `TemporaryDirectory`-based tests all pass fresh paths and land on the
  green-field path.
- **Payoff:** **20/20 prototype assertions.** Prerequisite for I2-08 / I2-11 / I2-12 / I2-15 /
  I2-16.
- **Rollback:** ship the previous binary; the downgrade guard then refuses to start rather than
  corrupt data, which is the correct failure mode.
- **Blocks:** I2-02, I2-08, I2-11, I2-12, I2-15, I2-16.

#### I2-02 — Adopt existing unversioned databases · **[PROTOTYPED]**

Fingerprint probes, highest version first, first hit wins. Every database in the wild has
`user_version = 0` and no marker; a naive runner would replay v1 against a populated database.

Verified, 20/20 across 6 scenarios:

```
CASE 1 green field                     -> v3, BLOB column, idx_message_index_live present
CASE 2 idempotent second run           -> v3, 0.037 ms no-op
CASE 3 wild rag db, user_version = 0   -> adopted at v1, upgraded to v3, 2/2 rows survived,
                                          float32 blob kept as ('blob', 768, 'done'),
                                          legacy JSON vector -> (None, None, 'pending')
CASE 4 wild token db, no live_enabled  -> adopted at v1 -> v2, personality 'snarky' preserved
CASE 5 migration raises mid-flight     -> version stays v1, partial DDL rolled back
CASE 6 db at v99, binary knows v3      -> RuntimeError, refuses to start
```

**Effort:** S. **Payoff:** zero-touch rollout — no operator `stamp` step, no data loss.
**Rollout:** ship the runner with v1 bodies and probes only (no schema change, fully
reversible), verify the adoption log line across deployments, then v2 = indexes, then v3 =
vector retype behind I2-18's automatic file copy.

#### I2-03 — Replace the legacy migration's column INTERSECTION with an explicit map · **[DESIGNED]**

- **Closes:** DAB-066. `_migrate_legacy_database` (`:642-659`) computes
  `target_columns & legacy_columns` and runs `INSERT OR IGNORE`. Both halves fail silently: an
  empty intersection `continue`s, and `OR IGNORE` swallows every constraint violation.
  **5,000 legacy messages became zero rows while the ledger recorded success.**
- **Design:** a declared `LEGACY_V1_COLUMNS` map (an added target column can no longer shrink
  the intersection), plain `INSERT` (a duplicate PK now raises), and a copied-vs-expected
  assertion *inside* the transaction so a short copy rolls back and never writes the ledger row.
- **Effort:** S. **Blast radius:** one function, runs at most once per deployment.
- **Payoff (estimated):** turns a silent 100 % data-loss path into a loud, retryable failure.
- **Depends on:** nothing — independent of I2-01.

#### I2-04 — Connection pragmas · **[PROTOTYPED]**

`sqlite_utils.py:20` is a bare `sqlite3.connect(db_path)`. No pragmas at all.

| Pragma | Set to | Why *this* workload |
|---|---|---|
| `journal_mode` | **WAL** (once, persistent) | Measured: with a write lock held, a rollback-journal read **fails after 5.01 s**; the WAL read **succeeds in 3.3 ms**. Writes go 2,918 → 12,355/s (4.2x), shrinking the lock-hold window. |
| `busy_timeout` | **5000 ms** explicit, **15000 ms** on the writer | Python's implicit 5 s is not a documented contract and is invisible in the code. Making it explicit is the precondition for I2-13's retry policy. |
| `synchronous` | **NORMAL** on `message_rag.db`, keep **FULL** on `token_usage.db` | Measured 12,355 → 12,626 writes/s = **+2 %, noise**. This is a durability argument, not a performance one: the message index is reconstructible, billing-adjacent data is not. |
| `foreign_keys` | **ON** | `message_embeddings.message_id REFERENCES message_index(message_id)` is **decorative today**. Enable it *with* I2-11's `ON DELETE CASCADE`, not before — pre-existing orphans would start raising. |
| `cache_size` | `-16000` (16 MB) on readers | Only worthwhile once readers are long-lived (I2-06). |
| `temp_store` | `MEMORY` | `USE TEMP B-TREE FOR ORDER BY` appears in three plans today. Cheap insurance. |
| `mmap_size` | **leave off** | Makes an I/O error a SIGBUS instead of a Python exception. Not worth it for a bot. |
| `wal_autocheckpoint` | 1000 pages (default) | Do not disable; the WAL would grow unbounded during backfill. |

**Four WAL implications that must be handled:**
readers stop being collateral damage (measured under sustained write: median 0.206 ms, p95
0.304 ms, **zero SQLITE_BUSY**); readers see a snapshot, not the newest commit (harmless — the
RAG path is best-effort); a long-lived reader with an **open transaction** pins the WAL and
prevents checkpointing, so the rule to enforce is **never hold a read transaction open across an
`await`**; and `-wal`/`-shm` files appear beside the database, so any backup that copies only
the `.db` silently loses the tail.

**WAL does *not* help writers** — measured, a second writer still fails after 5.01 s in WAL
mode. DAB-065 needs I2-13.

**Effort:** S, ~15 lines. **Blast radius:** every DB call. **Rollback:**
`PRAGMA journal_mode=DELETE` with no other connection open — do it in I2-18's offline script,
not in the bot.

#### I2-06 — Thread-local connections, a bounded reader pool, one writer thread · **[PROTOTYPED]**

| Option | Measured (400 concurrent reads) | Verdict |
|---|---|---|
| A — today: `to_thread` + connect per call | 547.7 ms (730 reads/s) | baseline |
| B — one global connection + a lock | serialises everything behind the slowest query | rejected |
| C — **thread-local connections, bounded pool** | **pool 2: 64.2 ms (8.5x)**; pool 4: 114.5 ms (4.8x); pool 8: 315.3 ms (1.7x) | **recommended** |
| D — one dedicated DB thread + queue | 0.089 ms median single-op; 200 ops in 16 ms | recommended **for writes only** |

**Note the shape of C: pool 2 beats pool 4 beats pool 8.** These queries are ~0.03 ms of C and a
lot of Python-level row marshalling, so past two threads the GIL dominates. **Do not size this
by CPU count.**

**Why one writer thread rather than N:** SQLite permits exactly one writer regardless.
Funnelling through a single thread converts lock contention into queue wait — *bounded and
observable* instead of a 5 s timeout followed by a silent `False`. Measured: 500 writes
240.9 → **71.8 ms (3.4x)**, and self-inflicted `SQLITE_BUSY` becomes structurally impossible.

Keep `sqlite_connection` / `sqlite_transaction` as thin wrappers so the ~30 existing call sites
keep working. **Contract change:** the context manager no longer closes the connection — it
returns it to the thread. The `except BaseException: rollback()` in `sqlite_utils.py:25-30`
becomes **load-bearing** rather than belt-and-braces, because a leaked open transaction now
poisons every later use of that thread's connection.

**Effort:** M (~60 lines + classifying 30 call sites as read or write). **Rollback:**
`db.connection_pool_enabled: false` falls back to connect-per-call. **Depends on:** I2-04.

#### I2-07 — Move the 9–13 remaining synchronous call sites off the loop · **[PROTOTYPED]**

Independently reproduced: max loop gap **4,997.3 ms inline vs 3.1 ms offloaded**.

**Mechanism comparison.** `asyncio.to_thread` is fine as an interim but wrong as the
destination: it is unbounded (`min(32, cpu+4)` workers) so it can fan out 32 concurrent writers
into a 5 s lock pile-up. **`aiosqlite` was evaluated and rejected** — it is a new dependency
requiring a `constraints.txt` regeneration, it *is internally* a thread-per-connection with a
queue (precisely what I2-06 builds in 60 lines), and its real value is `async with`/`async for`
ergonomics, which would mean rewriting ~30 call sites. It pays a dependency and a rewrite for
syntax.

Call sites that must move:

| # | Call site | Path | Impact |
|---:|---|---|---|
| 1 | `_is_live_mode_enabled` → `get_live_enabled` | `discord_bot.py:710` | **worst offender: on `on_message`, every message, every channel** |
| 2–9 | pin add/get/delete, hidden-message save/remove/list, `mark_hidden` true/false | `personalization.py:159,180,205,259,273,277,314,336,347,348` | sites 6, 7 and 9 are **inside `for` loops**, so one `/hide` over 20 messages is 20 sequential blocking round-trips |
| 10–11 | `report_service.create_report` / `get_report` | `reports_usage.py:70,113` | `/report` |
| 12–13 | `report_service.list_reports` / `update_status` | `report_web_server.py:73,91` | aiohttp handlers that **share the bot's event loop** — a report page view stalls Discord |

**Latency change:** per call 0.258 ms inline → **0.089 ms** via a warm pooled thread (*faster*,
because the 0.265 ms connect disappears). Under a lock stall: **4,997.3 → 3.1 ms**. For
`_is_live_mode_enabled` at 50,000 messages/day that is **12.9 s/day of loop time reclaimed** on
the happy path, and the elimination of a whole-bot freeze on the unhappy one.

**Effort:** S per site, ~13 sites. **Blast radius:** `personalization.py` command bodies become
`await`-ing and `_is_live_mode_enabled` becomes `async`.
`tests/test_command_registration.py` pins the tree by `callback_name`, and converting a callback
to `async` does not change its name, **so `EXPECTED_SIGNATURE` should hold — but re-run it.**

#### I2-08 — The index set · **[PROTOTYPED]**

- **Closes:** DAB-078. The only `message_index` indexes are
  `(guild_id, channel_id, created_at DESC)` and `(reply_to_message_id)`. The default retrieval
  path is channel-scoped (`rag.cross_channel_enabled: false`), so `_scope_clause` emits
  `m.channel_id = ?` — a **skip-scan** on a leading `guild_id` the query never constrains,
  followed by `USE TEMP B-TREE FOR ORDER BY`.

```sql
CREATE INDEX IF NOT EXISTS idx_message_index_live
    ON message_index (channel_id, created_at DESC)
    WHERE hidden = 0 AND deleted_at IS NULL;
CREATE INDEX IF NOT EXISTS idx_message_index_guild_time
    ON message_index (guild_id, created_at DESC)
    WHERE hidden = 0 AND deleted_at IS NULL;
CREATE INDEX IF NOT EXISTS idx_message_embeddings_pending
    ON message_embeddings (embedding_model, message_id DESC)
    WHERE embedding_status = 'pending';
CREATE INDEX IF NOT EXISTS idx_message_retrieval_events_created
    ON message_retrieval_events (created_at);
CREATE INDEX IF NOT EXISTS idx_pinned_channel_at
    ON pinned_messages (channel_id, pinned_at);
```

`EXPLAIN QUERY PLAN`, verbatim, 100k rows:

```
Q1 search_recent, channel scope
BEFORE  SEARCH m USING INDEX idx_message_index_scope_time (ANY(guild_id) AND channel_id=?)
        USE TEMP B-TREE FOR ORDER BY                 >>> best 1.610 ms | median 1.664 ms
AFTER   SEARCH m USING INDEX idx_message_index_live (channel_id=?)
                                                     >>> best 0.104 ms | median 0.108 ms  15.4x

At realistic density (10 channels x 10,000 rows):
baseline (shipped indexes)             30.811 ms
plain  (channel_id, created_at DESC)    0.034 ms ->  914x
PARTIAL (recommended)                   0.026 ms -> 1200x

Q2 search_recent, guild scope (cross_channel_enabled: true)
BEFORE  SEARCH m USING INDEX idx_message_index_scope_time (guild_id=?)
        USE TEMP B-TREE FOR ORDER BY                 >>> best 79.042 ms | median 79.849 ms
AFTER   SEARCH m USING INDEX idx_message_index_guild_time (guild_id=?)
                                                     >>> best 0.101 ms | median 0.103 ms   783x

Q5 get_pending_embeddings, channel-filtered:  3.523 -> 1.972 ms  (1.8x, temp b-tree gone)
Q8 get_status: 1.50 -> 1.48 ms · Q9 delete_rag_data: 1.99 -> 1.95 ms  (no regression)
```

Index build + `ANALYZE` on the 391 MB seed: **779 ms**, one time, inside migration v2. The five
indexes cost **12.4 MB total at 100k rows, ~3 % of the file**.

**Two lanes measured this independently and reached different multipliers for the channel case
— 1200x (I2, 10k rows/channel, partial index) versus 29x (I3, 500 rows/channel, plain index).
Both are right.** The difference is row distribution and cache state; the plan change (temp
B-tree eliminated) is identical and unambiguous. The conservative published figure from the
prior lane was 136x; the two "before" measurements agree to within 3 % (30.8 vs 29.97 ms).

**Partial versus plain — a genuine disagreement between lanes, resolved:** I3 measured partial
and plain reading identically (0.055 vs 0.055 ms) at the same write cost (76.5 vs 76.7 µs/row)
and recommends **plain**, because a partial index is silently skipped if a future query's
`WHERE` clause does not match syntactically. I2 measured partial as 1200x versus plain 914x at
higher density and recommends **partial**. **Recommendation: ship plain first** (safe against
future query shapes, 914x is already overwhelming), and only add the partial predicate if a
measurement on the real corpus shows the extra 31 % is worth the fragility.

**Effort:** S (XS if shipped as two `CREATE INDEX IF NOT EXISTS` lines in `_ensure_schema` per
I3-04, which needs no migration ledger entry because they are idempotent).
**Honest cost:** writes go 52.3 → 76.7 µs/row (**+46 %**) and the DB grows ~5 MiB per 100k
messages. At the post-fix backfill rate that turns 51,320 msg/s into roughly 35,000 msg/s —
still 50x the shipped rate. **Do not add all four index variants: +40 % write cost for zero read
benefit (105.1 µs/row).**
**Note:** run `ANALYZE` after creation and again in I2-18's periodic job — the plans above are
chosen with `sqlite_stat1` present.

#### I2-09 — Query rewrites that need no index at all · **[PROTOTYPED]**

`get_pending_embeddings` (`:1188`) does `ORDER BY m.created_at DESC LIMIT 16`. `created_at` lives
on the *joined* table, so SQLite drives from `idx_message_embeddings_status`, joins every pending
row to `message_index`, materialises the lot, sorts it, and throws away all but 16. The
embedding worker calls this in a loop.

A Discord snowflake is monotonic in creation time, and `message_id` is the PK of both tables, so
ordering by `e.message_id DESC` is *semantically identical* and lets the existing index supply
the order:

```diff
-                      ORDER BY m.created_at DESC
+                      -- Snowflake IDs are chronological, and message_id is the PK of both
+                      -- tables, so this is the same order the index already provides.
+                      ORDER BY e.message_id DESC
                       LIMIT ?
```

```
Q4a current  ORDER BY m.created_at DESC   ... USE TEMP B-TREE FOR ORDER BY
             >>> best 127.666 ms | median 132.027 ms
Q4b rewrite  ORDER BY e.message_id DESC   (no temp b-tree)
             >>> best 0.069 ms | median 0.073 ms
Q4 speedup: 127.666 -> 0.069 ms = 1844x ; with COALESCE 0.070 = 1821x
```

**1844x from deleting one temp b-tree.** One behavioural caveat worth a code comment: bot
responses are indexed with `created_at=datetime.now()` while their `message_id` is Discord's.
These stay consistent because Discord assigns the ID at send time, but a caller passing an
explicit back-dated `created_at` would order differently. The embedding queue does not care
about exact order, only about draining.

**Effort:** S — a two-line diff. **Dependencies: none. This can ship today, ahead of everything
else.** **Rollback:** revert two lines.

#### I2-10 — FTS5 scope column + external content · **[PROTOTYPED]**

`search_lexical` applies `MATCH` first and the channel filter *after*. On a real corpus a 3-term
`OR` matched **79,285 of 100,000** docs; bm25 ranks all of them, then 200× more rows than needed
are discarded.

```sql
CREATE VIRTUAL TABLE message_search_fts_v2 USING fts5(
    content_text, author_name, attachment_summary,
    scope,                          -- 'c<channel_id> g<guild_id>'
    content='message_index',        -- external content: no duplicated text
    content_rowid='message_id',
    tokenize='unicode61');
```

```
BASELINE current FTS (channel filter after MATCH)   49.838 ms
SCOPE-COLUMN FTS  MATCH 'scope:cNNN AND (terms)'     8.887 ms      5.6x
SCOPE-COLUMN FTS, rare term                          0.091 ms
FTS storage: duplicated content  22.85 MB
             external content     6.71 MB            -71%
```

At the one-year model that is **2.9 GB saved**. The cost is real: with `content=`, FTS no longer
maintains itself — every `upsert_message`, `mark_hidden` and `mark_deleted` must emit the
matching `INSERT INTO fts(fts, rowid, ...) VALUES('delete', ...)` before updating the row.
Rebuilding the whole index takes **4.55 s at 100k rows**.

**A rejected alternative, recorded because it looks obvious and is wrong:**
`Q3-fix rowid IN (channel subquery) >>> best 1402.031 ms — 28x SLOWER`.

**Effort:** L. **Payoff:** 5.6x worst-case lexical, −71 % FTS bytes.
**Sequencing:** do it **after** I2-08/09 — lower payoff, much higher risk. Note that **I3-17 (cap
the OR'd terms, 38x, effort M) is a cheaper attack on the same bottleneck** and should be tried
first. **Rollback:** keep v1 populated for one release; flip via `rag.fts_scope_enabled`.

#### I2-11 — `embedding_vector`: declared TEXT, holding BLOB · **[PROTOTYPED]**

- **Closes:** DAB-070, DAB-084. `embedding_vector TEXT` (`:167`) receives
  `sqlite3.Binary(vector.tobytes())` (`:1233`). SQLite's TEXT affinity leaves a BLOB alone, so
  the column is an **untagged BLOB|TEXT union** and `_decode_vector` sniffs the runtime type and
  tries JSON as a fallback. There is no width validation anywhere: a 512-d vector written into a
  768-d configuration is accepted, stored, marked `done`, and then invisibly ignored forever.

```sql
CREATE TABLE message_embeddings (
    message_id       INTEGER PRIMARY KEY
                     REFERENCES message_index(message_id) ON DELETE CASCADE,
    embedding_model  TEXT NOT NULL,
    embedding_vector BLOB,                     -- was TEXT
    embedding_dims   INTEGER,                  -- new: self-describing width
    embedding_status TEXT NOT NULL DEFAULT 'pending'
                     CHECK (embedding_status IN ('pending','done','failed','skipped')),
    ...
    -- 'done' iff there is a vector. No more half-states.
    CHECK ((embedding_status = 'done')
           = (embedding_vector IS NOT NULL AND embedding_dims IS NOT NULL)),
    -- the union is closed: blob only, width must match the declared dims
    CHECK (embedding_vector IS NULL
           OR (typeof(embedding_vector) = 'blob'
               AND length(embedding_vector) = embedding_dims * 4))
);
```

Plus a guard in `store_embedding` so a bad write fails at the source. Migration verified end to
end in CASE 3:

```
PASS  float32 blob carried over as BLOB with dims   ('blob', 768, 'done')
PASS  legacy JSON vector dropped and re-queued      (None, None, 'pending')
```

Legacy JSON-TEXT vectors cannot be reinterpreted in SQL, so they are dropped and re-queued as
`pending` — the embedding worker regenerates them from `content_text`.

**Effort:** M. **Blast radius:** the table is rewritten (391 MB → several seconds of I/O and 2×
peak disk). **Depends on:** I2-01. **Enables:** `foreign_keys=ON` (I2-04) via `ON DELETE
CASCADE`, which then deletes `delete_rag_data`'s hand-ordered deletes.
**Rollback:** file restore from I2-18's pre-migration copy.

#### I2-12 — Timestamp encodings, NOT NULL, CHECK · **[DESIGNED]**

- **Closes:** DAB-110. Three encodings for one logical column type:

| Writer | Produces | Example |
|---|---|---|
| `MessageIndexService._now_iso` | tz-aware ISO-8601 | `2025-07-29T12:00:00+00:00` |
| `PinService`, `MessageVisibilityService`, `ChannelSettingsService`, `UserPreferencesService` | **naive** `datetime.utcnow().isoformat()` (deprecated in 3.12) | `2025-07-29T12:00:00` |
| SQLite `DEFAULT CURRENT_TIMESTAMP` (4 tables) | space-separated, no `T`, no offset | `2025-07-29 12:00:00` |

`'2025-07-29 12:00:00' < '2025-07-29T12:00:00'` in a byte comparison, so
`hidden_messages ORDER BY hidden_at DESC` **puts every default-timestamped row after every
explicitly-timestamped one, regardless of actual time** — a live correctness bug hiding behind a
correctly-designed index. Same shape in `pinned_messages ORDER BY pinned_at ASC`.

Also missing: `CHECK (hidden IN (0,1))`, `CHECK (length(content_hash) = 64)` on `message_index`;
`CHECK (length(content) > 0)` on `pinned_messages`; status and type enums on `bot_reports`
(enforced only in Python today, so any other writer can insert junk); non-negative token counts
on `token_usage` (the Python guard silently *skips* negatives, so bad data is dropped without a
trace); `CHECK (json_valid(selected_message_ids))` on `message_retrieval_events`.

**Effort:** M — one migration with ~10 `UPDATE`s and several table rewrites (SQLite cannot add a
CHECK without one). **Blast radius:** wide but shallow. **Any latent bad row now fails the
rewrite loudly — which is the point, but it means the migration must be tested against a real
database first.** **Depends on:** I2-01.

#### I2-13 — Never return a silent `False` from a write · **[DESIGNED, problem measured]**

- **Closes:** DAB-065, the worst data-loss defect in the corpus. A write that exceeds the 5 s
  lock wait returns `False` with a log line. In `rag_event_coordinator.py` that `False` is a
  **permanent tombstone** — the message is never retried, so a transient lock becomes permanent
  data loss. Reproduced: with a write lock held, a concurrent write **fails after 5.01 s in both
  journal modes**. The same pattern appears at 13 call sites; `record_retrieval_event` logs its
  failure at **debug**.

Four parts: **classify** (`TransientDBError` vs `PermanentDBError` — a lock timeout must not
share a code path with a constraint violation); **retry transients inside the write lane** with
jittered backoff, where the caller cannot see them; **raise instead of returning `False`**,
reserving `False` for *a business decision not to write* (empty text, out-of-scope bot message,
invalid personality) and raising for *the write was attempted and failed*; and **quarantine**
what could not be written in a bounded in-memory `PendingWriteQueue(maxlen=1000)` drained every
60 s, with `_dropped` and `len(_q)` surfaced in `/ragstatus`. Keep it in memory: persisting it
needs a database, which is the thing that is broken.

Plus counters — `writes_ok`, `writes_retried`, `writes_failed`, `writes_deferred` — because
today **a bot silently dropping every message write looks identical to a bot with no traffic**.

**Effort:** M (~50 lines for the exception types and retry, ~13 call-site classifications, ~60
lines for the queue, plus counters). **Blast radius:** every writer; each of the 13 sites needs
a deliberate catch-or-propagate decision. **Depends on:** I2-04, I2-06 (the single writer makes
self-inflicted contention structurally impossible, so retries then only cover an external
process holding the lock). **Rollback:** `db.raise_on_write_failure: false` keeps the retries
while restoring `False` — **the retries alone recover most of the value and are safe to ship
first**.

#### I2-05 — Batch backfill commits · **[PROTOTYPED]**

`backfill_channel` calls `upsert_message` per message; each opens a connection, runs three
statements, and commits.

```
commit every     1 rows :  388.2 ms =  12,880 rows/s
commit every   100 rows :   37.9 ms = 132,081 rows/s   10.3x
commit every   500 rows :   28.0 ms = 178,330 rows/s   13.8x   <- the knee
commit every  5000 rows :   25.3 ms = 197,542 rows/s   15.3x
```

The cursor must advance in the **same transaction** as the batch, or a crash mid-backfill
re-scans. **Effort:** S. **Rollback:** `rag.backfill_batch_size: 1`. See also I3-06, which
measured the same change end to end at **653 → 51,320 msg/s = 79x** at batch 1000 and
recommends **batch 200 (41x)** for a smaller crash-replay window.

#### I2-14 — Gate the startup reconcile on a threshold fingerprint · **[PROTOTYPED]**

`_reconcile_embedding_eligibility` (`:705`) runs on **every construction** — a full
`message_index ⋈ message_embeddings` scan, then a Python-side `_embedding_is_trivial` per row.
Measured **230 ms at 100k rows** here; **2.61 s and 97.8 % of `DiscordBot.__init__`** in the
prior lane's environment. Called twice (`:265` and `:672`).

Its only input beyond the row is `(embedding_min_words, embedding_min_alphanumeric_chars)`,
which come from `config.yaml` and change ~never. Gate on a named row in the existing
`rag_migrations` ledger keyed by a fingerprint of those thresholds plus the model. New rows do
not need it: `upsert_message` already computes `embedding_status` with the current thresholds at
insert time. The scan exists solely to fix *old* rows after a threshold change — exactly what
the fingerprint detects. Second-order fix: when it *does* run, run it off the constructor as a
background task after `on_ready`.

See I3-05 for the executed timing: **1938.0 → 0.034 ms at 100k = 57,134x**.
**Effort:** S, ~10 lines. **Rollback:** delete the fingerprint row to force one re-run.

#### I2-15 — Retention for `message_retrieval_events` and `token_usage` · **[PROTOTYPED]**

`message_retrieval_events` is **write-only** — `record_retrieval_event` is the only code that
touches it apart from `delete_rag_data`. `token_usage` grows unbounded and is only ever read as
a per-guild `SUM`. Together: **402 MB/yr + 219 MB/yr of data nothing reads.**

A `retention:` config block (four places per AGENTS.md: the `config.yaml` key, a
`parse_retention_config`, a `RetentionConfig` field on `BotConfig`, a `validate_retention` rule,
plus parsed defaults in `tests/test_config.py`) drives a `RetentionService` with **bounded,
resumable deletes** — `LIMIT ?` in a subquery, `await asyncio.sleep(0.05)` between batches to
yield the write lock. Measured: deleting 50,000 event rows with the `created_at` index present
takes **46 ms**; at a 5,000-row batch that is ~5 ms of lock hold per batch.

**Do not delete `message_retrieval_events` outright**, tempting as "nothing reads it" sounds —
its columns (`recent_candidates`, `lexical_candidates`, `semantic_candidates`, `reranker_used`,
`latency_ms`) are exactly what a retrieval-quality dashboard needs. Add a `/ragstats` reader
over the last 14 days and the table earns its keep.

**Effort:** M (~120 lines + config plumbing + one `discord.ext.tasks` loop + tests).
**Rollback:** `retention.enabled: false`. **Deletes are irreversible** — ship with
`retrieval_events_days: 14`, `token_usage_days: 0`, and log a dry-run count for one release
before enabling deletion.

#### I2-16 — Message/embedding retention and the vector-cache ceiling · **[DESIGNED]**

Three layers: **(a)** hard-cap the in-RAM cache
(`rag.vector_cache_max_vectors: 200000` → 200k × 768 × 4 B = **614 MB resident**) by bounding
`_load_vector_cache` with `ORDER BY m.created_at DESC LIMIT ?` and evicting the oldest in
`_cache_upsert`; `search_semantic` already falls back to `_search_semantic_sql_compat`, so an
evicted-but-on-disk vector degrades to the SQL path rather than vanishing. **(b)** age out
message rows via `retention.messages_days` with `ON DELETE CASCADE` (I2-11) plus the FTS delete
— at `messages_days: 180` the one-year steady state halves to ~34 GB. **(c)** two-tier vectors
(int8-quantised for older rows, 768 B, 4× smaller, ~1 % recall loss) — **a real project; list
it, do not schedule it.**

**Effort:** L. **Payoff (estimated):** turns an OOM at ~day 25 into a fixed 614 MB ceiling.
**Rollback:** `vector_cache_max_vectors: 0` restores today's unbounded behaviour. Gate (b)
behind an explicit opt-in with a default of 0 — deleted messages are unrecoverable.

> **A caution from I3, which measured the recall cost of count-based bounding:** keeping the
> most-recent 100k of 200k vectors gave **recall@12 = 0.483**; 50k gave **0.267**; 20k gave
> **0.108**. Those figures come from synthetic vectors with no topic-recency correlation, so
> they are a *lower bound* — real conversation is topically autocorrelated. But the shape is
> unambiguous: recall falls roughly linearly with the fraction dropped. **Prefer bounding by
> *scope*** — the cache already carries `channel_id`, so evicting channels the bot has not been
> active in for N days is lossless for those channels' queries. If a hard count cap is
> unavoidable, make it configurable, default it off, and log the recall risk.

#### I2-17 — One path resolver · **[DESIGNED]**

- **Closes:** DAB-101. `expanduser()` is applied by 4 of 7 path-consuming services and by the
  config layer never. A `~` in `token_db_path` yields two physically different files:
  `./~/data/token_usage.db` (a literal `~` directory) and `$HOME/data/token_usage.db`. Worse,
  the validator that guarantees the two databases are distinct (`config_helpers.py:525`) uses
  `Path(...).resolve()` **without** `expanduser()` — so with `~` paths it compares two literal-`~`
  paths and can pass while the services disagree about the files.
- **Design:** `resolve_db_path(raw) -> Path` in `config_helpers.py`, applied **once at config
  load**, storing `Path` (not `str`) on `BotConfig`. Update the `:525` distinctness check to
  compare resolved paths; add a `tests/test_config.py` case asserting a `~`-prefixed path
  resolves to `$HOME`.
- **Effort:** S. **Blast radius:** config load + 7 constructors. `PinService` currently keeps a
  `str`; it becomes a `Path`, which `DatabasePath` already accepts.

#### I2-18 — Offline maintenance script · **[PROTOTYPED]**

`VACUUM` on a 394 MB database **blocks for 3,980 ms** and needs ~394 MB of scratch. At the
one-year model (69 GB) it would block for **minutes** and need 69 GB free. **It must never run
inside the bot.**

`scripts/db_maintenance.py` with `--backup` (uses `sqlite3.Connection.backup()`, which is
WAL-safe — a plain file copy is not), `--analyze` (safe with the bot running), `--vacuum`
(refuses if the bot holds the lock), `--integrity-check`, `--stats` (per-table bytes via
`dbstat`). The v3/v4 migrations call `--backup` automatically before a table rewrite.

Inside the bot, keep only what is cheap: `PRAGMA optimize` on clean shutdown (an incremental
`ANALYZE` in milliseconds) and `PRAGMA wal_checkpoint(TRUNCATE)` after the retention pruner.

Note that **even the retention pruner does not shrink the file** — pages go on the free list.
Either accept the steady-state high-water mark or set `PRAGMA auto_vacuum=INCREMENTAL` in
migration v1 so new databases get it (it must be set **before the first table is created**), and
let existing ones adopt it during a manual `--vacuum`.

**Effort:** S, ~150 lines, no new dependencies (`psutil` is already present for the lock check).
**Risk if skipped:** I2-11/I2-12's table rewrites have no undo.

---

## 6. Lane I3 — Performance

### 6.1 Diagnosis

The full ranked table, **strictly by measured payoff ÷ effort**. Every row is a measurement
unless the Evidence column says otherwise.

| # | ID | Title | Measured before → after | Effort | Blast radius | Risk |
|---:|---|---|---|---|---|---|
| 1 | **I3-01** | Single-parse message splitter | 59.96 s → **0.055 s** @234 KB code-block-dense (**1088x**); 2.84 → 0.038 s (75x) code-heavy; 4.18 → 0.076 s (55x) mixed | **S** | 1 file, +35 LOC | **Very low** — 131/131 byte-identical |
| 2 | **I3-02** | Lock-free vector snapshot | semantic search 0.4 → 97.5 ops/s @16 threads, 350k (**244x**); scaling 1.10x → 5.34x | **M** | 1 file, cache accessors | Low–med (write-path atomicity) |
| 3 | **I3-03** | Pre-normalised adaptive `search_semantic` | 2225 → **62 ms** (35.8x) and 2056 → **8.4 MiB** peak @350k; 5.94 → 0.88 ms narrow channel | **M** | 1 method + cache build | Low — ids+scores identical to 2e-6 |
| 4 | **I3-05** | Ledger the startup eligibility reconcile | 1938 ms → **0.034 ms** per boot @100k (**57,134x**) | **S** | 2 methods | Low — needs a rules fingerprint |
| 5 | **I3-04** | Two `created_at DESC` indexes | 1.595 → 0.055 ms (29x) channel; 104.7 → 0.056 ms (**1870x**) guild | **XS** | 2 DDL lines | Low — writes +46 % |
| 6 | **I3-08** | Delete the PDF PNG round-trip | 20-page render 1.634 s → **0.140 s** (**11.7x**), pixel-identical | **S** | 1 method, 3 lines | Very low |
| 7 | **I3-06** | Batch backfill commits | 653 → **51,320 msg/s** (**79x**) at batch 1000 | **M** | backfill loop + cursor semantics | **Medium** — coarser resume granularity |
| 8 | **I3-09** | Per-user rate-limit pruning | p95 22.1 ms → **0.003 ms** @20k users (**6320x**) | **S** | 1 file, 30 LOC | Low — same admit/deny decisions |
| 9 | **I3-14** | Bound `_completed_jobs` | 699.5 MiB → **1.9 MiB** (**373x**), 3.5 µs/job | **S** | 1 dict + eviction | Low — old job IDs 404 after TTL |
| 10 | **I3-07** | Offload + cheapen image encode | 26 images 2.130 s of loop-block → **0 s** loop, 0.575 s wall (4 workers); JPEG 14.2x cheaper | **M** | gemini_client, executor plumbing | Med — JPEG is lossy; keep PNG opt-in |
| 11 | **I3-15** | LRU+TTL the router cache | 96.2 MiB → **0.0 MiB**, 0.70 µs/put | **S** | 1 dict | Low — cache-miss rate rises |
| 12 | **I3-17** | Cap OR'd FTS terms *(the new binding constraint)* | 186.6 → **4.9 ms** (38x) @350k | **M** | `_build_fts_query` | **Medium** — recall changes |
| 13 | **I3-10** | TTL-cache the per-message settings reads | 146 → 7.3 µs/msg (20x) — but only **0.44 → 0.022 ms** per response | **S** | 3 services | Low (staleness window) |
| 14 | **I3-11** | Replace the O(n²) renderer splice with `join` | 5.38 → 0.074 ms (73x) — **5 ms absolute**, premature | **S** | content_renderer | Low |
| 15 | **I3-12** | Widen the PDF executor | 13.59 → 11.23 s (**1.21x only**) — *misdiagnosed*, see I3-08 | S | executor | **Deprioritized** |
| 16 | **I3-13** | float16 vector cache | 1025 → 513 MiB but **20x slower** (60 → 1214 ms) | — | — | **REJECTED** |

Plus five items whose *cost* was measured but whose *fix* is **[DESIGNED]** — I3-19 (`/hide` and
`/unhide` run ~20 serialised `connect`+`close` cycles per invocation ≈ **2.9 ms** plus fsyncs),
I3-20 (`report_web_server` runs a ≤500-row scan and a full HTML render **on the bot's own
loop**), I3-21 (`enhanced_command_handler.py:77` runs `re.sub(r"\s+"," ", content)` over the
**full** message *before* the 200-char slice — slice first), I3-22 (`media_extraction.py:473-484`
does up to **4 full decodes** of a 5 MB text attachment — try codecs on a 4 KB prefix), and
I3-CR (`to_thread` the whole `content_renderer.process_response`; matplotlib is not loop-safe to
leave inline).

**34 classes of synchronous work** reachable from `async def` without `to_thread` /
`run_in_executor` were inventoried (a prior lane counted 21; the extra 13 are slash-command and
startup classes).

### 6.2 The splitter rewrite (I3-01) · **[PROTOTYPED]**

`MessageSplitter._find_next_split_point` (`message_splitter.py:203-274`) walks candidate break
points in five priority tiers and calls `markdown_parser.is_safe_split_point(content, pos)` for
**each candidate** (`:237,244,253,261,270`). That method (`markdown_utils.py:295-320`) does a
full `find_code_block_boundaries(text)` **and** a full `parse_markdown(text)` — ten `finditer`
passes over the *entire* document — every time. With ~2 KB windows and up to a few hundred
candidates per window, an N-byte message triggers Θ(N²/2000) full-document parses. It runs
synchronously on the event loop from `response_delivery.py:281`.

**Fix:** build the parse **once per `split_message` call** into a `_SplitIndex` holding (a) the
single `find_code_block_boundaries` result, reused by tier 2 and by `_preserve_code_blocks`
(which re-scanned at `:383`), and (b) an interval index of every span `is_safe_split_point`
rejects, sorted by `start` with a **prefix-max of `end`**. `is_safe_split_point(p)` is then
exactly `¬∃ interval: start < p < end`, answered by one `bisect_left` plus one comparison —
O(log n). **This is a provably equivalent reformulation, not a heuristic:** the predicate is
identical, only the evaluation order changes.

| Corpus | 10 KB | 30 KB | 60 KB | 120 KB | 180 KB | 240 KB |
|---|---|---|---|---|---|---|
| **code_heavy** (65 % fenced) | 0.0045 → 0.0013 s **3.6x** | 0.0373 → 0.0039 **9.5x** | 0.1474 → 0.0080 **18.4x** | 0.6371 → 0.0170 **37.5x** | 1.5162 → 0.0271 **55.9x** | 2.8394 → 0.0377 **75.4x** |
| **mixed** (prose+code+tables+LaTeX+lists) | 0.0070 → 0.0027 **2.6x** | 0.0496 → 0.0079 **6.3x** | 0.1893 → 0.0168 **11.3x** | 0.8522 → 0.0350 **24.4x** | 2.1670 → 0.0529 **40.9x** | 4.1752 → 0.0764 **54.6x** |
| **prose_only** | 0.0030 → 0.0008 **3.6x** | 0.0195 → 0.0025 **7.8x** | 0.0759 → 0.0049 **15.5x** | 0.3073 → 0.0099 **31.1x** | 0.6638 → 0.0147 **45.0x** | 1.1749 → 0.0199 **59.0x** |
| **one_giant_code_block** (worst case) | 0.1002 → 0.0024 **41.7x** | 0.8936 → 0.0070 **126.8x** | 3.6850 → 0.0137 **269.2x** | 14.8615 → 0.0275 **539.5x** | 33.4588 → 0.0420 **796.4x** | **59.96 → 0.0551 s — 1088.2x** |
| **no_whitespace** (degenerate) | 1.2x | 1.0x | 0.9x | **0.5x** | 1.0x | 1.5x |

**Heartbeat.** Discord's 41.25 s gateway budget is crossed by the shipped implementation at
**≈195 KB** for code-block-dense content (33.46 s at 176 KB, 59.96 s at 234 KB) — *earlier* than
the prior lane's ~240 KB estimate, because that corpus was less fence-dense. **The prototype's
worst measurement across every corpus and every size is 0.0764 s.** The hazard is eliminated,
not moved.

**Behavioural equivalence.** The full observable output — `content`, `part_number`,
`total_parts`, `has_continuation`, every `MarkdownBlock` (type/positions/language/level) and
`metadata` — was compared across **11 hand-written edge cases + 10 corpora × 12 sizes**:

```
EQUIVALENCE: 131/131 byte-identical (0 mismatches)
```

Edge cases covered: empty, whitespace-only, exactly 1999/2000/2001 chars, a small fence,
`"**bold** " * 300`, an unmatched single fence, `"$$x^2$$ " * 400`, a 200-row markdown table,
CRLF text, text with no newlines, text with no whitespace, and an unterminated trailing fence.
Both implementations agree even on the pathological cases where the shipped code discards its
expensive result and falls back to `_hard_split_parts`.

> **The ablation — the half of the fix that does not pay.** The first prototype also replaced
> `len(content[a:b].strip())` with O(1) precomputed non-whitespace neighbour tables. Measured,
> that made things **worse**:
>
> | corpus @176 KB | shipped | interval index only | index + strip tables |
> |---|---|---|---|
> | code_heavy | 1.4879 s | **0.0265 s (56.2x)** | 0.0533 s (27.9x) |
> | prose_only | 0.6603 s | **0.0147 s (44.9x)** | 0.0365 s (18.1x) |
> | one_giant_code_block | 32.27 s | **0.0414 s (778.5x)** | 0.0649 s (497.5x) |
>
> **120,000 `str.isspace()` calls cost more than the slicing they eliminate.** Ship the interval
> index only: smaller patch, lower risk, strictly faster.

**One regression case:** `no_whitespace` @120 KB, 0.0156 → 0.0305 s (0.5x). Text with no
whitespace has almost no candidates, so the shipped code barely re-parses while the prototype
still pays one O(n) index build. Absolute cost 30 ms, growth linear. Acceptable.

**Effort:** S. **Blast radius:** `message_splitter.py` only; `markdown_utils.py` untouched (its
`is_safe_split_point` stays for other callers). **Watch:** `parse_markdown` must be called on the
*same* string the split points index into — the prototype builds the index from
`stripped_content`, matching the shipped code exactly.

### 6.3 The vector lock and `search_semantic` (I3-02, I3-03) · **[PROTOTYPED]**

`MessageIndexService._vector_lock` (`:93`) is a process-global `threading.RLock` held across the
*whole* of `search_semantic` (`:1329-1345`) — masking, `np.flatnonzero`, the fancy-index gather,
`np.linalg.norm`, the matmul, `np.argsort`, and the Python list comprehension that builds
`ranked`. `search_semantic_async` dispatches to `asyncio.to_thread`, so N concurrent requests
land on N pool threads and then queue on one lock. Inside the lock,
`matrix = self._vector_matrix[positions]` materialises a full copy of the selected rows and
`np.linalg.norm(matrix, axis=1)` recomputes row norms that never change.

Four separable steps: **v1** publish an immutable `(count, unit, ids, guilds, channels)` tuple
that readers rebind with a single atomic attribute read and no lock; **v2** store **L2-normalised
rows** so cosine is a plain dot product; **v3** score with one contiguous BLAS matvec over the
whole matrix and mask *after*, killing the copy; **v4** exact-tie-preserving top-k via
`argpartition` + threshold fix-up instead of a full `argsort`; **v5 adaptive** — gather when the
selection is ≤10 % of rows, whole-matrix matvec otherwise.

**100,000 vectors, cross-channel guild query (100,000 rows selected — the worst case):**

| variant | ms | vs v0 | peak alloc | equivalence |
|---|---|---|---|---|
| v0_current | 615.60 | 1.00x | **587.6 MiB** | (reference) |
| v1_nolock | 613.74 | 1.00x | 587.6 MiB | ids + scores identical |
| v2_prenorm | 219.95 | 2.80x | 294.2 MiB | ids + scores identical |
| v3_nogather | 26.22 | 23.48x | **3.2 MiB** | ids + scores identical |
| v4_partition | **15.48** | **39.77x** | **2.4 MiB** | ids + scores identical |

The 587.6 MiB peak independently confirms the prior lane's 586 MiB.

**Single-channel query (512 of 100,000 rows) — where v3 regresses:** v0 1.42 ms; v2_prenorm
**0.22 ms (6.42x)**; v3_nogather 13.97 ms = **0.10x, i.e. 14x *slower***. Scoring the whole
matrix to find 512 rows is a bad trade. Hence v5.

**Concurrency (BLAS pinned to 1 thread, so this measures request-level parallelism):**

| variant | 1 thr | 2 thr | 4 thr | 8 thr | 16 thr |
|---|---|---|---|---|---|
| **v0_current** | 1.00x | **1.01x** | **1.02x** | **1.01x** | **1.01x** |
| v1_nolock | 1.00x | 1.75x | 3.09x | 5.03x | 6.52x |
| v3_nogather | 1.00x | 2.06x | 3.65x | 5.93x | **7.77x** |

**The shipped lock delivers literally zero parallel speedup.** The 1.0x here is the pure
serialization result; the prior lane's 0.89 / 0.93 / 0.74 / 0.56 is the same phenomenon plus
contention overhead. Confirmed twice: this is *the* binding concurrency constraint.

**350,000 vectors (a busy 200-channel server after a week):**

| variant | cross-channel ms | vs v0 | peak alloc | narrow-channel ms | vs v0 |
|---|---|---|---|---|---|
| v0_current | **2225.42** | 1.00x | **2056.5 MiB** | 5.94 | 1.00x |
| v2_prenorm | 908.94 | 2.45x | 1029.7 MiB | 1.28 | 4.64x |
| v3_nogather | 107.47 | 20.71x | 11.0 MiB | 56.73 | 0.10x |
| v4_partition | 62.08 | 35.85x | 8.4 MiB | 57.28 | 0.10x |
| **v5_adaptive** | **62.23** | **35.76x** | **8.4 MiB** | **0.88** | **6.77x** |

v5 is the only variant that wins on both query shapes. Throughput: v0 **0.4 ops/s at 1 thread
and 0.4 at 16** (scaling 1.10x); v5 **18.3 → 97.5 ops/s** (scaling 5.34x). **0.4 → 97.5 ops/s =
244x** on the semantic stage alone.

**Equivalence:** every variant returns the **same message IDs in the same order** with scores
agreeing to **< 2 × 10⁻⁶** (float32 rounding from normalising up-front rather than dividing at
the end). `_exact_topk` deliberately reproduces `np.argsort(-scores, kind="stable")[:limit]`
including tie-breaking by ascending index — necessary because duplicate Discord messages produce
identical embeddings and therefore exact score ties.

**Memory:** storing unit vectors must **replace** the raw matrix, not sit alongside it. Measured
at 350k: raw f32 + 3 id arrays = **1028.3 MiB (3,081 B/msg**, matching the prior lane's
3,096 B/msg); keeping raw *and* unit = 6,153 B/msg. The raw vectors are already durable in
SQLite, so the cache only needs unit vectors — **zero net memory change**.

**Effort:** M + M, best landed together. **Blast radius:** `MessageIndexService` cache accessors
only; callers unaffected. **The real risk is write-path atomicity, not read correctness.** Under
copy-on-write, `_cache_upsert`/`_cache_remove` must build a new tuple and rebind it — they can no
longer mutate in place while a reader holds a reference. Writes are rare (one per embedded
message) so an O(n) rebuild per write is too slow at 350k: use a capacity-doubling append with a
version counter, rebuild only on compaction, and keep a **writer-side** lock. Readers stay
lock-free. The 10 % adaptive threshold should be re-tuned per deployment; it was derived from the
measured crossover at 100k (gather ≈ 2.2 µs/row vs a fixed ~26 ms full matvec).

### 6.4 Event-loop offloading

#### I3-08 — Delete the PDF PNG round-trip · **[PROTOTYPED]**

`_convert_pdf_to_images_sync` (`discord_bot.py:1178-1184`) does:

```python
pix      = page.get_pixmap(matrix=fitz.Matrix(scale, scale))
img_data = pix.tobytes("png")                 # PNG ENCODE
image    = Image.open(io.BytesIO(img_data))   # PNG DECODE of what we just encoded
```

`pix.samples` is already an RGB buffer. The round-trip is pure waste.

| Render | 20 pages @scale 2.0 | per page | 20 pages @1.5 | @1.0 |
|---|---|---|---|---|
| shipped (`tobytes("png")` → `Image.open`) | **1.634 s** | 81.7 ms | 0.949 s | 0.285 s |
| direct (`Image.frombytes("RGB", …, pix.samples)`) | **0.140 s** | **7.0 ms** | 0.069 s | 0.041 s |
| speedup | **11.67x** | | 13.80x | 6.98x |

`pixel-identical to the shipped path: True (1190×1684 both)` — verified by comparing
`img.tobytes()`.

**Effort:** S, 3 lines. **Risk:** very low.

#### I3-12 — Widening the PDF executor — **the misdiagnosis, corrected** · **[PROTOTYPED, DEPRIORITIZED]**

A prior lane flagged `ThreadPoolExecutor(max_workers=1)` as "globally serialized" and implied the
pool width was the bottleneck. Structurally it *is* serialised, but the width is **not** the
constraint — the work is GIL-bound inside the PNG round-trip:

| 8 concurrent jobs | w=1 | w=4 | w=8 |
|---|---|---|---|
| shipped | 13.59 s | 11.50 s | 11.23 s → **1.21x only** |
| **direct (post-I3-08)** | **1.42 s** | **0.84 s** | 0.89 s |
| direct, `ProcessPoolExecutor` | — | 0.46 s | **0.27 s** |

A thread-safety probe (8 threads × 3 concurrent conversions of independent documents) produced
**0 errors** on PyMuPDF 1.28.0 / MuPDF 1.29.0, so the `max_workers=1` comment ("concurrent
conversions are not safe within this process") does not reproduce for independent
`fitz.open(stream=…)` documents. **But one negative probe is not a safety proof.**
**Recommendation: land I3-08 (11.7x, zero risk) and leave the executor at 1 worker; revisit
widening separately with a proper stress test.**

#### I3-07 — Offload and cheapen image encoding · **[PROTOTYPED]**

| operation | 1024² | 2048² |
|---|---|---|
| `Image.save(PNG)` (shipped) | 66.9 ms | **266.3 ms** |
| `Image.save(PNG, compress_level=1)` | 56.1 ms (1.2x) | 224.1 ms (1.2x) |
| `Image.save(JPEG, quality=90)` | 5.3 ms (12.7x) | **18.8 ms (14.2x)** |

26 × 2048² serial on the loop: **2.130 s** (prior lane measured 2.09 s — confirmed). Offloaded: 2
workers 1.090 s, **4 workers 0.575 s**, 8 workers 0.343 s — with **zero** loop-block in all
three. **Recommendation: offload first** (removes 2.13 s of head-of-line blocking on the
gateway), then consider JPEG. **JPEG is lossy and changes what the model sees, so it must be
opt-in per content type, not a blanket switch.** **Effort:** M.

#### I3-09 — Rate limiter · **[PROTOTYPED]**

| tracked users | shipped p50 | shipped p95 | fixed p50 | fixed p95 | p95 speedup |
|---|---|---|---|---|---|
| 100 | 0.035 ms | 0.041 ms | 0.003 ms | 0.003 ms | 13x |
| 1,000 | 0.293 ms | 0.333 ms | 0.003 ms | 0.005 ms | 65x |
| 5,000 | 1.847 ms | 11.301 ms | 0.003 ms | 0.003 ms | 3,414x |
| **20,000** | **7.281 ms** | **22.119 ms** | **0.003 ms** | **0.003 ms** | **6,320x** |

Consistent with the prior lane's 5.27 ms p50 / 107.5 ms p95; the p95 spread is GC-sensitive.
Fix: per-user `deque` (timestamps are monotonically appended, so expiry is a bounded `popleft`
loop), minute count via a reverse scan that stops at `per_minute`, and the global sweep amortised
to once per 60 s. **Admit/deny decisions are unchanged**; only the retry-after message text needs
care (the shipped code uses `min(...)`, the deque's head is the same value). **Effort:** S,
~30 LOC.

### 6.5 Memory (I3-14, I3-15) · **[PROTOTYPED]**

| Container | Measured | Bounded alternative |
|---|---|---|
| `ImageProcessingService._completed_jobs` — 350 completed jobs × 2 MiB image, retained forever | **699.5 MiB** (prior lane: 700 MiB — confirmed); 719.5 MiB extrapolated over 1 day at 1 job/4 min | LRU cap 64 + 15-min TTL + `job.image_data = None` on completion → **1.9 MiB (373x less)**, at **3.51 µs/job** |
| `EnhancedCommandHandler` router cache, 200k entries × ~200 B key | **96.2 MiB** | 512-entry LRU + 5-min TTL → **0.0 MiB**, **0.70 µs/put** |
| `TextRateLimiter._requests`, 20k users × 6 timestamps | 6.5 MiB | already pruned (badly — see I3-09); a deque halves it |

Three separate wins in `_completed_jobs`, in order of value: (1) drop the input bytes once the
result is delivered — they are never read again; (2) an `OrderedDict` LRU cap; (3) a TTL so a
quiet bot does not hold 64 jobs indefinitely. **Only user-visible change:** a user polling a job
ID older than the TTL gets "not found" instead of a cached result. Cap 64 / TTL 15 min is
generous relative to any realistic poll window. **Effort:** S each.

### 6.6 The budget after — and the new binding constraint

Per-stage, single-threaded, **after** I3-01…05:

| Stage | 100,000 msgs | 350,000 msgs |
|---|---|---|
| `sqlite3.connect` + `close` | 0.069 ms | 0.069 ms |
| `search_recent` (channel) | 0.063 ms | 0.064 ms |
| `search_recent` (guild) | 0.063 ms | 0.064 ms |
| `search_semantic` (channel) | 0.227 ms | 0.658 ms |
| `search_semantic` (guild, worst case) | 18.121 ms | 57.631 ms |
| **`search_lexical` FTS5** | **53.746 ms** | **182.975 ms** |

> **The new binding constraint is `search_lexical`.** After I3-01…05 the FTS5 lexical query
> costs **182.975 ms at 350k — 3.2x more than the fixed semantic search (57.6 ms)** and 2,860x
> more than the now-indexed recency query. It is the only retrieval stage no fix above improves,
> and it is the one the two new SQLite indexes explicitly did **not** help (47.677 → 47.521 ms,
> 1.0x).

The cause is `_build_fts_query` (`message_index_service.py:1107-1122`): it ORs together up to
**24** quoted terms, and `ORDER BY bm25(...) ASC LIMIT 30` forces FTS5 to score every posting in
the union, not just 30. Cost is driven by term *frequency*, measured on a 350k-row index with a
30,000-word vocabulary:

| Query shape | Measured |
|---|---|
| 3 rare terms OR'd | **2.58 ms** |
| 3 common terms OR'd | 74.89 ms |
| 12 mixed terms OR'd (typical) | 145.31 ms |
| **24 common terms OR'd (the code's maximum)** | **186.55 ms** |
| **6 rarest terms OR'd (proposed guard)** | **4.88 ms — 38x** |

**Any real user question ("how do I fix the index lock") contains common words, so the expensive
shape is the normal case, not an edge case.**

**I3-17** ranks candidate tokens by document frequency — SQLite's `fts5vocab` virtual table gives
this for free — and keeps only the 6 rarest. **Measured 38x. Effort M; risk medium** — it
changes which documents match, so it needs a retrieval-quality check, not just a latency check.

**Projected ceiling after I3-17 [ESTIMATED]:** per-request thread CPU at 350k falls to roughly
57.6 (semantic worst case) + 4.9 (lexical) + 0.06 (recent) ≈ **63 ms**, against the ~329 ms
implied by today's 48.67 req/s at 16 threads. Applying the measured 5.34x scaling gives an
estimated **~135 req/s at 350k** — at which point the constraint moves to (1) `search_semantic`'s
O(N) matvec, which is memory-bandwidth-bound and whose measured scaling ceiling is 5.34–7.77x
regardless of thread count because 16 threads share one memory bus for a 1 GiB matrix (beyond
~1 M messages this needs an ANN index, not a full scan); (2) **Discord's own API rate limits** —
5 messages / 5 s per channel, capping a single channel at 1 req/s no matter what the bot can
compute; (3) **Gemini API latency**, seconds per response, which dwarfs every number in this
lane.

> **The honest framing for the whole performance lane: none of these fixes make a single
> idle-bot response faster in a way a user notices.** What they do is (a) remove a
> gateway-disconnect hazard that is reachable today with a 200 KB answer, and (b) raise the
> number of *simultaneous* users the bot can serve from **8 to ~1,000** at 350k indexed
> messages. Items 13–16 in the ranked table are premature and are labelled as such.

---

## 7. Lane I4 — Cost accounting

### 7.1 Diagnosis

| Fact | Evidence |
|---|---|
| **No price table, no cost arithmetic, anywhere** | `get_api_usage_info()` (`gemini_client.py:196-245`) returns `"pricing": {"free_tier": "Free up to rate limits", "paid_tier": "Pay-as-you-go pricing available"}` — two English strings. A repo-wide grep for any numeric `price`/`usd`/`cost` returns nothing. |
| Model name is never persisted | `token_usage` DDL (`token_tracker.py:44-56`) has 9 columns; none is a model. |
| `thoughts_token_count` never read | `extract_token_usage` (`token_extraction.py:48-56`) reads only prompt/candidates/total. |
| Usage recorded only on success | `APIResponse.__post_init__` (`data_models.py:87-88`) *raises* if usage is attached to a failure. |
| Router / selector / embed unmetered | 6 SDK entry points exist; only the answer path calls `record_token_usage`. |
| Whole event dropped when reported total < in+out | `TokenUsage.__post_init__` raises; `extract_token_usage` catches and returns `None`. |
| Leaderboard groups on `username` | `token_tracker.py:166` — a rename splits one human into several rows. |
| No retention | No `DELETE`, no rollup, no TTL in `token_tracker.py`. |
| Retrieval bounded by message *count* only | `rag.max_context_messages_low/medium/high: 4/6/8`. |
| All three complexity tiers name the same model | `config.yaml:114-123` — all `gemini-3-flash-preview`. |
| No slash-command cooldown anywhere | No `@app_commands.checks.cooldown` in `src/bot/command_modules/`. |
| **`/usage-report` never reads the database** | `reports_usage.py:467-509` reads `performance_logger.get_summary_stats()`, an in-memory dict whose "tokens" are `context_messages * 50` and `response_length // 4` (`logging_config.py:205-209`). It resets on restart and has never touched `token_usage.db`. |

**Measured baseline** (real `format_prompt`, real `ContextPackBuilder`):

```
system prompt low / medium / high   =   356 /   517 /   738 tok
router prompt template              =   472 tok  (+ user message)
context-selector template           =   190 tok  (+ up to 100 candidate lines)
```

| Call | Model | in | out | thinking | recorded today? |
|---|---|---|---|---|---|
| router — runs on **every** text message | `gemini-2.5-flash-lite` | 502 | 100 | 0 | **no** |
| RAG reranker (~35 % of turns, ≤12 candidates) | `gemini-2.5-flash-lite` | 820 | 600 | 0 | **no** |
| legacy context selector (100 candidates) | `gemini-2.5-flash-lite` | 5,470 | 600 | 0 | **no** |
| main answer | `gemini-3-flash-preview` | 1,615 | 400 | 300–4,800 | in+out only |
| RAG embedding, per indexed message | `gemini-embedding-2` | 50 | 0 | 0 | **no** |

**The attribution gap:**

| Scenario | True tokens | Recorded | Visibility |
|---|---|---|---|
| low tier, RAG path, no rerank | 2,917 | 2,015 | **69 %** |
| low tier, RAG path, rerank fires | 3,414 | 2,015 | **59 %** |
| high tier, RAG path, rerank fires | 7,914 | 2,015 | **25 %** |
| any tier, legacy fallback (selector runs) | 13,487 | 2,015 | **15 %** |

**The bot's own books understate real consumption by 1.4×–6.7× before counting a single retry,
timeout, safety block, or `/deepresearch`.**

### 7.2 Proposals

#### I4-01 — One `api_usage` fact table: model, purpose, thinking, outcome, retry index · **[PROTOTYPED]**

- **Closes:** DAB-102 (model never persisted), DAB-048 (thinking tokens unbilled), DAB-049
  (failures/retries unrecorded), DAB-050 (router/selector/embed unaccounted), DAB-105 (whole
  event dropped on a total mismatch), DAB-103 (rename splits a user), DAB-104 (unbounded
  growth). **All seven are the same root cause:** `token_usage` is a 9-column summary of *one*
  call type, not a fact table of *API calls*.
- **Design:** one row **per SDK call**, joined by a `request_id` shared by every call belonging
  to one user turn. Username moves out to a `usage_actor` dimension so identity is the id, never
  the string. Full DDL executed against sqlite3 3.53.1 and runs clean, including the backfill and
  the retention rollup.

Key columns: `request_id`, `attempt` (0 = first try), `purpose` (CHECK-constrained to 12 values
including `router`, `context_selector`, `rag_rerank`, `rag_embed_index`, `deepresearch_gather`),
`model` (never NULL for new rows), `complexity`, `thinking_level`, `outcome`
(`ok|truncated|max_tokens_no_content|safety|recitation|timeout|error|empty`), `finish_reason`,
`input_tokens`, `output_tokens`, `thoughts_tokens`, `cached_tokens`, `tool_tokens`,
`total_tokens` (reconciled, never dropped), `reported_total` (raw provider value, for audit),
`duration_ms`, `user_id`/`guild_id`/`channel_id`, `command`, `est_cost_usd`, `price_version`.
Five indexes on `created_at`, `(guild_id, user_id, created_at)`, `request_id`,
`(model, created_at)`, `(purpose, created_at)`.

**Historical rows are backfilled, never deleted, and never guessed at:**

```sql
SELECT created_at, 'legacy-' || id, 0, 'answer',
       'unknown:legacy',        -- explicit sentinel; do NOT impute a model
       NULL, 'ok',              -- legacy rows only ever existed for successes
       input_tokens, output_tokens, 0, total_tokens, total_tokens,
       user_id, guild_id, 'legacy',
       NULL,                    -- cost NULL, not 0: unpriceable must not read as free
       NULL
FROM token_usage;
```

Every report must render unpriced rows as a **separate line** —
`"4,950 tokens from before cost accounting (unpriceable)"` — never fold them into a `$` total,
**because a NULL summed as 0 is a lie that looks like a fact.**

The dataclass change that matters most:

```python
def __post_init__(self):
    ...
    # DAB-105: a provider under-report is DATA, not an error. Reconcile, never raise.
    floor = self.input_tokens + self.output_tokens + self.thoughts_tokens
    if self.total_tokens < floor:
        object.__setattr__(self, "total_tokens", floor)
```

**Verified prototype output:**

```
BEFORE — leaderboard as shipped:
   user_id=11 username=ray      total=2650   rows=2
   user_id=11 username=raymond  total=1900   rows=1     <-- same human, two rows
   user_id=22 username=kaden    total=400    rows=1

AFTER — GROUP BY user_id only, name from usage_actor:
   user_id=11 username=raymond  tokens=65290  cost=$0.032094  turns=4 calls=7
   user_id=22 username=kaden    tokens=400    cost=$0.0       turns=1 calls=1

Retry waste (invisible today):
   outcome=ok       attempt=1  33,219 tok  $0.023936
   outcome=timeout  attempt=0  26,869 tok  $0.008061

Reconciliation (DAB-105):
   row 8: reported=6350 but in+out+think=33219 -> stored 33219, row RETAINED
```

- **Effort:** L. **Blast radius:** `token_tracker.py` (rewritten), `data_models.py`,
  `token_extraction.py`, `response_generation.py:142-176`,
  `enhanced_command_handler.py:532-541`, `command_modules/reports_usage.py` (2 commands),
  `command_modules/context.py`. **The command tree does not change, so the 22/35 pins in
  `tests/test_command_registration.py` hold.** `tests/test_sqlite_services.py` needs new cases.
- **Payoff:** visibility 15–69 % → ~100 %. At 10k messages/day and the assumed prices that is
  the difference between believing you spend **$12.9 k/yr** and knowing you spend it.
- **Risk if skipped:** **every other item in this lane is unmeasurable. You cannot cap what you
  cannot count**, and the README's "Token accounting, usage reports" claim stays false.

#### I4-02 — Versioned price book, priced at write time · **[DESIGNED]**

Three rules. **(1) Prices are data**, in a new `pricing:` block in `config.yaml`, parsed by
`_parse_pricing_values`, landing on `BotConfig.model_prices`, validated by
`_validate_pricing_values` — the four-place pattern AGENTS.md mandates. The block carries a
required `as_of` date, a `stale_after_days: 90`, and an
`unknown_model_policy: record_null | estimate_with_default | refuse`. **(2) Prices are versioned
in the database** — an append-only `model_price` table keyed by `(model, effective_from)`, so a
price change does not silently restate history. **(3) Cost is computed once, at write time**, and
stamped with the price version used, so a report is a `SUM` and the hot-path budget check is a
single indexed aggregate.

Staying current — three mechanisms, none of which pretends to be automatic: `as_of` +
`stale_after_days` produces a startup WARNING and a footer on every spend surface (*"prices as of
2026-01-15 (112 days old — verify)"*); any model id seen in `api_usage` with no `model_price` row
raises a startup warning listing the missing ids, so adding a model to `models.valid` without a
price is caught immediately; and a `source` column distinguishes an operator's guess from a
transcribed vendor figure.

**Effort:** M. **Payoff (estimated):** turns every token count into a dollar. Without it,
I4-07's spend caps can only be *token* caps — and a token cap prices a `pro` token the same as a
`flash-lite` token, a **25× error** at the assumed prices. **Depends on:** I4-01.

#### I4-03 — One metering seam over all six SDK entry points · **[DESIGNED]**

| # | Site | SDK call | Purpose | Metered today |
|---|---|---|---|---|
| 1 | `gemini_client.py:743` | `aio.models.generate_content` | `context_selector` **and** `rag_rerank` | no |
| 2 | `gemini_client.py:831` | `aio.models.embed_content` | `rag_embed_index` / `rag_embed_query` | no |
| 3 | `gemini_client.py:1489` | `aio.models.generate_content_stream` | `answer`, streaming | success only |
| 4 | `gemini_client.py:1523` | `aio.models.generate_content` | `answer`, non-streaming | success only |
| 5 | `enhanced_command_handler.py:191` | `client.models.generate_content` via `to_thread` — **bypasses `GeminiClient` entirely** | `router`, every text message | no |
| 6 | `nano_banana_client.py:294` | `client.models.generate_content` via `to_thread` | `image_generate` / `image_edit` | partial |

Two moves. **(a) Close the bypasses.** Move the router body into
`GeminiClient.classify_intent(...)` and give `NanoBananaClient` the same recorder. The invariant
to enforce in review, and to test: *the only module allowed to touch `client.models` /
`client.aio.models` is `gemini_client.py`* — exactly the rule AGENTS.md already states for
`sqlite3.connect`. A test can assert this by grepping the tree, which is cheap and
self-maintaining.

**(b) Ambient request context** via a `contextvars.ContextVar` set once at each of 7 entry
points, so metering needs no signature churn through six layers of coordinators.
`asyncio.to_thread` copies the current context, **so sites 5 and 6 work unchanged.** Anything not
wrapped records as `command='unattributed'` with `user_id NULL` — visible, not silently dropped.
Startup RAG backfill legitimately has no user; `'system:backfill'` is the right label.

**(c) One recorder, called from a `finally`** so a row is written on **every** path including the
ones that raise. `record_nowait` pushes onto an `asyncio.Queue` drained by one writer task, so
metering never adds latency to a reply and never fails a response. **Bounded queue (say 10k) with
a drop counter — losing a usage row must be *counted*, not silent**, because silent degradation
is exactly the house style that hid this problem in the first place.

**Effort:** M. **Payoff (estimated):** closes the entire 902–11,472-tokens-per-message
attribution gap; on the legacy fallback path that is **85 % of consumption becoming visible for
the first time**. **Depends on:** I4-01.

#### I4-04 — Record usage on failure: unhook `TokenUsage` from `success` · **[DESIGNED]**

`APIResponse.__post_init__` *forbids* attaching usage to a failure. Combined with
`_interpret_provider_response`, which builds `build_error_response(...)` for
`max_tokens_no_content`, `safety_filter`, `recitation`, `empty_response` and
`unknown_finish_reason`, and with `_run_response_attempts`, which loops up to
`max_retries + 1 = 4` times, **every token burned on a non-final attempt, and every token burned
on a blocked or truncated final attempt, is discarded. A safety block still bills the full
prompt. A 120-second timeout still bills the prompt server-side.**

Three small edits: delete the guard and replace it with the honest invariant (successful
responses must have content; failed responses must have an `error_type`; **token usage is valid
on both**); grow `build_error_response` with `token_usage` and `outcome` parameters; thread the
attempt index into the recorder.

**Timeouts are the subtle case:** `asyncio.wait_for` cancels before a response object exists, so
there is no `usage_metadata`. Record the row anyway with `outcome='timeout'` and `input_tokens` =
the **locally estimated** prompt size, flagged by a new `estimated INTEGER NOT NULL DEFAULT 0`
column. An estimated row is enormously better than a missing one: the 100 KB-pin timeout case is
26,869 input tokens per attempt, ×4 attempts, and today it is worth exactly zero in the books.

**Effort:** S. **Blast radius:** `data_models.py:79-88`, `gemini_response_pipeline.py`,
`gemini_client.py:1048-1183` and `:1283-1316`. **`tests/test_gemini_pipeline.py` and
`tests/test_bug_regressions.py` assert the current guard and will need updating — check before
editing.** **Payoff (estimated):** on the measured worst case this is **3 of 4 attempts, i.e.
~75 % of the tokens on a failing turn**, currently unbooked. One 4-attempt 100 KB-pin turn is
**$0.26 of which $0.19 is invisible** at the assumed prices.

#### I4-05 — Real token budget for the context pack (replaces 4/6/8) · **[PROTOTYPED]**

- **Closes:** DAB-072 (retrieval bounded by count, which is not a bound on anything that costs
  money), DAB-073 (a 100 KB pin yields a ~100k-character prompt on every message forever; pins
  can occupy 100 % of the slots and evict all retrieved context).

**Measured today**, against the real builder and the real `format_prompt`:

| Pin scenario | low (cap 4) | medium (cap 6) | high (cap 8) |
|---|---|---|---|
| no pins | 653 | 926 | 1,317 |
| 1 pin 400 B | 754 | 1,013 | 1,369 |
| 3 pins 800 B | 1,268 | 1,509 | 1,828 |
| 6 pins 1.5 KB | 1,819 | 2,842 | 3,533 |
| **1 pin 100 KB** | **26,254** | **26,513** | **26,869** |

**A count cap gives a 41× spread between the cheapest and dearest pack at the same setting. That
is not a budget.**

`ContextBudget` is a ceiling with class shares (`pack_tokens`, `pin_share: 0.35`,
`anchor_share: 0.25`, `per_pin_tokens: 600`, `min_retrieved: 1`, `max_items`, `score_floor`).
Recommended values: low 900/6/0.55, medium 2,000/10/0.45, high 4,000/14/0.35.

**Admission order (the reverse is eviction order):** (1) system prompt + user message +
scaffolding — the floor, never budgeted, never evicted; (2) **reply anchors**, capped at 25 % —
the literal message the user replied to is the highest-value context there is, so it goes in
before pins; (3) **pins**, newest first, capped at 35 % and 600 tokens each, with a pin over its
cap **elided head+tail with an explicit marker** (`… [98,432 chars elided by token budget] …`)
rather than dropped; (4) **retrieved**, best fused score first, subject to `score_floor` and
`max_items`; (5) **starvation guard** — if step 4 admitted fewer than `min_retrieved`,
force-admit the single best retrieved item, evicting the largest pin. That is the direct fix for
pins starving retrieval.

**Prototype results:**

| Pin scenario | wt | low today→new | medium today→new | high today→new |
|---|---|---|---|---|
| no pins | .55 | 653 → 765 | 926 → 1,489 | 1,317 → 2,215 |
| 1 pin 400 B | .20 | 754 → 852 | 1,013 → 1,381 | 1,369 → 2,320 |
| 3 pins 800 B | .15 | 1,268 → 952 | 1,509 → 1,590 | 1,828 → 2,841 |
| 6 pins 1.5 KB | .07 | 1,819 → **765** | 2,842 → **1,665** | 3,533 → 3,393 |
| **1 pin 100 KB** | .03 | 26,254 → **765** | 26,513 → **1,838** | 26,869 → **2,778** |
| **weighted mean** | | 1,615 → **810** | 1,933 → **1,505** | 2,326 → 2,429 |
| **worst case** | | 26,254 → **952** | 26,513 → **1,838** | 26,869 → **3,393** |

**Mean across tiers: 1,958 → 1,582 tok (−19 %). Worst case: 26,869 → 3,393 tok (−87 %).**

And the pack is simultaneously *better*, not just cheaper — the starvation check:

| Scenario | Tier | Retrieved msgs today | Retrieved msgs new |
|---|---|---|---|
| 4 pins 2 KB | low | **1** | 6 |
| 12 pins 500 B | low | **1** | 5 |
| 12 pins 500 B | medium | **1** | 7 |
| 12 pins 500 B | high | **1** | 7 |

Today, twelve small pins reduce retrieval to a single message at *every* tier. Under the budget
retrieval never falls below 5.

**The honest caveat:** on the 55 % no-pin case the budget spends **more** (653 → 765 at low),
because it admits more small messages. That is a deliberate trade — the budget buys back
retrieval quality on the common case and pays for it out of the tail. If an operator wants strict
token neutrality, lower `pack_tokens`; the `max_items` ceiling binds before the budget does at
every value tested, so the scheme cannot be tuned into a spender.

**Effort:** M. **Blast radius:** `context_pack_builder.py` (rewritten),
`hybrid_context_retriever.py:505-650`, `config.yaml` + the four-place dance,
`tests/test_message_rag_services.py`, `tests/test_rag_optimization.py`, `tests/test_config.py`.
**Payoff:** on a channel with the pathological pin, at 60 messages/hour and the assumed flash
input price, that pin alone costs 26,869 × 60 × 24 × $0.30/1M ≈ **$11.6/day, forever**, and drops
to ≈ **$1.2/day**.

#### I4-06 — Bound pins at ingest, not just at render · **[DESIGNED]**

I4-05 stops a huge pin *poisoning the prompt*, but the pin itself is still stored unbounded and
re-elided on every single message. Fix the source: `pins.max_chars_per_pin: 4000`
(~1,000 tokens), `max_pins_per_channel: 25`, `max_total_chars_per_channel: 40000`.
`PinService.add_pin` **rejects with a clear message** (`"Pin is 102,400 characters; the limit is
4,000. Split it or link it."`) rather than silently truncating — **a silently truncated memory is
worse than a refused one**. A one-shot migration gated by a `rag_migrations` row *flags* (does
not delete) over-limit existing pins and surfaces them in `/pins` as
`⚠ 102,400 chars — exceeds the 4,000 limit, elided on every request`.

**Effort:** S. **Blast radius:** `pin_service.py`, `personalization.py:175-230` (copy only, **no
tree change**), config, `tests/test_sqlite_services.py`. **Payoff (estimated):** removes the class
of defect rather than mitigating it — a 100 KB pin becomes impossible to create. **Cheapest item
in this lane; ships independently.**

#### I4-07 — Spend controls · **[DESIGNED]**

See §10 for the full exposure analysis. Four layers, cheapest first.

**Layer 1 — command cooldowns (S, no schema, ship today).** `discord.py` ships this; the bot
simply never used it.

```python
@bot.tree.command(name="deepresearch", ...)
@app_commands.checks.cooldown(1, 900.0, key=lambda i: i.user.id)   # 1 per 15 min per user
@app_commands.checks.cooldown(3, 900.0, key=lambda i: i.guild_id)  # 3 per 15 min per guild
```

| Command | Per-user | Per-guild | Why |
|---|---|---|---|
| `/deepresearch` | 1 / 15 min | 3 / 15 min | 100,904 tok/call — the dominant vector |
| `/summarize` | 1 / 5 min | 6 / 5 min | 30,346 tok/call |
| `/rag backfill` | 1 / 6 h | 1 / 6 h + **`manage_guild` permission** | millions of embed tokens; it is an admin operation |
| `/edit-image` | already covered by `max_requests_per_user_per_hour: 10` | | |

Needs an `on_app_command_error` handler so `CommandOnCooldown` renders as a friendly ephemeral
message. **No tree change**, so the 22/35 pins hold.

**Layer 2 — token/dollar budgets on the fact table (M).** Cooldowns cap *frequency*; budgets cap
*magnitude*, which is what the pathological-pin vector needs (60 legal messages that each cost
20× normal). A `spend_window(scope, scope_id, bucket_hour, tokens, cost_usd, calls)` table
maintained incrementally in the same transaction as the `api_usage` insert, driven by a
`spend_limits:` config block. **`on_exceed: "degrade"` before `refuse` matters** — a hard refusal
turns a cost incident into an outage, and the degraded mode (force low tier: 4,096 output cap,
minimal thinking, no search, 400-token context) is ~8× cheaper, which is enough. Refusal is
reserved for the **global** daily cap, where an outage is the correct outcome.

**Layer 3 — pre-flight admission (S).** Estimate the prompt before calling: if
`estimated_input + max_output_tokens` would breach the remaining hourly budget, degrade the tier
*before* spending. **This is the only control that acts on the request that would cause the
breach rather than the one after it.**

**Layer 4 — a kill switch (S).** `/spend pause` (guild-admin) plus a `global_pause` flag that
short-circuits every Gemini call. When a bill is running away at 03:00, the operator needs one
lever, not a config edit and a restart.

**Payoff (estimated, assumed prices):** worst-case single-user hourly exposure
**$3,247 → ≈ $2 — roughly 1,600×**. Layer 1 alone caps `/deepresearch` at 4/h ≈ $0.90/h, a
**3,600× reduction** from the scripted worst case.

#### I4-08 — Circuit breaker for runaway retries · **[DESIGNED]**

`_run_response_attempts` retries up to 4 times with backoff capped at 30 s, each attempt with its
own `extended_timeout: 120` s — worst case one message occupies the bot ~488 s. **Critically, the
retry decision considers only *this* request's attempt count; it has no idea the provider has
been failing for the last five minutes.** When Gemini degrades, every concurrent user
independently pays 4× the tokens.

Four nested guards: a **per-turn wall-clock budget** (a request that has burned 180 s stops
retrying regardless of attempt count, bounding occupancy at ~180 s instead of 488 s); a **real
circuit breaker** keyed by `(model, purpose)` with `fail_threshold=8`, `window=60 s`,
`open=30 s`, then HALF_OPEN with one probe — while OPEN, `generate_response` returns
`build_error_response("provider_unavailable", ...)` **immediately with zero tokens spent** and
records an `api_usage` row with `outcome='circuit_open'`, `total_tokens=0`, **so the incident is
visible as a spike in refused calls rather than a hole in the data**; **don't retry what won't
succeed** — `safety_filter` and `recitation` are deterministic for a given prompt, so encode
retryability explicitly rather than relying on `ErrorManager`'s general classifier; and a
**retry budget per user per hour** drawn from the same `spend_window`, so a poison prompt cannot
be looped.

**Effort:** S–M. **Payoff (estimated):** cuts the worst case from 4 attempts to ~1.5 average
during an incident (≈ **60 % of retry tokens**) and turns a provider outage from an N×4× token
amplifier into a fast fail. **Depends on:** I4-04, so refused and failed attempts are countable.

#### I4-09 — Model routing: what the router is actually worth · **[DESIGNED, economics measured]**

All three complexity tiers in the shipped `config.yaml` are `gemini-3-flash-preview`, so "model
routing" changed the model in **0 of 16** tested combinations. Yet the router makes an extra
Gemini call on **every** text message.

**The nuance that changes the recommendation:** complexity does not only pick a model.

| Tier | Model | `thinking_level` | `max_output_tokens` | System prompt |
|---|---|---|---|---|
| low | `gemini-3-flash-preview` | `minimal` | 4,096 | 356 tok |
| medium | `gemini-3-flash-preview` | `low` | 16,384 | 517 tok |
| high | `gemini-3-flash-preview` | `high` | **65,536** | 738 tok |

**Identical models, 16× different output caps, and a 300 → 4,800-token swing in thinking
tokens.** Thinking is the single largest line item in a high-tier turn, so the router's economic
effect runs almost entirely through thinking level, not model choice — and it is a *cost
multiplier*, not a cost saver, relative to the cheapest baseline.

**$ per 1,000 text messages** (assumed prices, 70/25/5 complexity mix, 30 % router cache hit):

| Scenario | router $ | rerank $ | main $ | **total** | router share | thinking tok/msg |
|---|---|---|---|---|---|---|
| **R1 today** — router on every message | 0.063 | 0.113 | 3.359 | **3.535** | **1.8 %** | 750 |
| R2 — no router, always `low` | 0.000 | 0.113 | 2.234 | **2.347** | 0 % | 300 |
| R3 — no router, always `medium` | 0.000 | 0.113 | 4.484 | **4.597** | 0 % | 1,200 |
| R4 — conditional router (55 % skipped) | 0.028 | 0.113 | 3.359 | **3.501** | 0.8 % | 750 |

**Token share, price-independent: router 421 tok/msg = 11.4 % of per-message tokens** (reranker
13.5 %, main answer 75.1 %).

**Read it carefully.** The router's *own* consumption is 11.4 % of tokens but only **1.8 % of
dollars**, because it runs on `gemini-2.5-flash-lite` — 25× cheaper per output token than the
answer model at the assumed prices. That is the router working exactly as designed. But the
router **causes** $1.19/1k more than always-`low` (**+51 %**) by upgrading 30 % of messages to
more thinking; against always-`medium` it **saves** $1.06/1k (−23 %), i.e. it **pays for itself
17× over** ($0.063 of router tokens avoids $1.12 of thinking tokens).

**Recommendation: MAKE CONDITIONAL, do not remove.** Removal also loses intent detection
(`image_generate` / `image_edit`) and `needs_context` gating, which is what lets RAG skip
retrieval entirely on self-contained requests. Those are functional, not cosmetic. Concretely:
(1) skip the router on messages that cannot plausibly be non-low — under ~120 characters, no
attachments, no code fence, no URL, no question chain — a **local heuristic that cannot fail or
time out**, covering ~55 % of messages; (2) raise the cache hit rate — `router_cache_size: 256`
with `router_cache_ttl: 300` is a 5-minute window that a busy guild evicts constantly, and
4,096 entries / 3,600 s is still trivial memory; (3) **meter it** (I4-03) so `purpose='router'`
is a line item and this table can be recomputed from real data instead of an assumed mix;
(4) **fix the config so routing means something, or stop calling it model routing** — either
point `high` at `gemini-3.1-pro-preview` and accept ~4× more per high-tier turn (which makes the
router's *selectivity* genuinely valuable), or rename the concept to "effort routing", because
that is what it is; (5) **reconsider `max_output_tokens_high: 65536`** — it is the single largest
lever in the whole exposure analysis, and **nothing in a Discord bot that splits at 2,000
characters per message needs a 64k-token answer. 16,384 would cap the tail 4× with essentially no
user-visible loss.**

**Effort:** S (heuristic skip + cache sizing) / M (if `high` is repointed at pro and the cost
model is revalidated). **Payoff:** −55 % router calls ($0.035/1k, modest); **the real payoff is
making the +51 % thinking-cost decision explicit.** Capping `max_output_tokens_high` at 16,384
cuts the scripted worst case from $3,247/h to ~$1,000/h *before* any cooldown.

#### I4-10 — Rebuild `/usage-report`, `/api-usage`, `/token-leaderboard` on the fact table · **[DESIGNED]**

Five queries on `api_usage`: cost by model, **cost by user grouped on the id with the name joined
from `usage_actor`** (the DAB-103 fix — verified in the prototype: `ray` and `raymond` collapse
into one row), **cost by call purpose** (the router/selector/embed blind spot, finally visible),
**waste** (`outcome <> 'ok' OR attempt > 0` — tokens paid for with nothing delivered), and a
14-day trend.

Rendering rules that matter more than the SQL: **never sum a NULL cost as zero**; **every dollar
figure carries its price vintage**; **show cost per turn, not just totals** — `$0.0035/turn` is
the number an operator can reason about, `$412.19` is not; and **show the waste line
prominently** — *"$1.42 (8 %) spent on retries and blocked responses"* is the single most
actionable number in the report and today it does not exist.

`/api-usage` stops printing hard-coded free-tier limits it cannot verify and instead shows the
current model and thinking level, last-hour and last-24 h spend against the configured budgets as
a progress bar, circuit-breaker state per model, the price table with its `as_of` date and
staleness warning, and the top 3 purposes by cost.

**Effort:** M. **Blast radius:** 3 command bodies, `token_tracker.py` read methods,
`tests/test_report_service.py`. **No command tree change.** **Payoff (estimated):** also retires a
genuinely misleading artifact — today's "Total Tokens" is an invented number presented next to
real ones.

#### I4-11 — Retention: 90-day rollup, then prune · **[PROTOTYPED]**

With I4-01 the row rate goes **up** — one row per *call*, not per successful answer, roughly
**2.5–4× more rows** — so retention becomes mandatory rather than tidy. At 10k messages/day × ~3
calls that is **~11 M rows/year** in a SQLite file that also serves the hot-path budget query.

Daily rollup into `api_usage_daily(day, guild_id, user_id, model, purpose, calls, tokens,
cost_usd)` **before** the delete, **in one transaction**, so a crash mid-prune cannot lose data.
Gated by a named row in `usage_migrations` so it is idempotent. Trend queries read a `UNION ALL`
view over both tables so history past 90 days still charts, at daily granularity. Defaults:
`detail_days: 90`, `rollup_days: 730`.

**Effort:** S. **Payoff:** bounds the database at ~1 M detail rows regardless of runtime.
**Also privacy-relevant:** per-call rows carry `channel_id` and `user_id`, and 90 days is a
defensible retention period where "forever" is not.

#### I4-12 — Observability: where an operator sees spend, and what wakes them up · **[DESIGNED]**

**Four surfaces, four audiences.** (1) **One structured log line per turn**, at the end of every
`usage_scope` — `INFO usage.turn request_id=… user=… calls=3 tokens=7914 in=3037 out=400
think=4800 cost_usd=0.0138 purposes=router:1,rag_rerank:1,answer:1 attempts=2 outcome=ok
duration_ms=8412`. Greppable, no PII beyond ids the logs already carry, and per *turn* rather
than per call keeps volume sane. (2) **A new `/spend` command** (admin only) — the only surface
most operators of a Discord bot will ever look at. (3) **A `/spend.json` endpoint** on the
existing report web server — the scrape target, so the bot takes no monitoring dependency.
(4) **A startup summary** — rows in `api_usage`, price-book `as_of` and staleness, model ids seen
in usage with no price row, and the configured budgets.

> **`/spend` is the one item in this lane that changes the command tree.** Per AGENTS.md that
> means a registrar in `command_modules/`, a call in `setup_commands` in the correct position,
> and updating `tests/test_command_registration.py`: `EXPECTED_SIGNATURE` (byte-exact
> description), `22 → 23`, `35 → 36`, and re-checking the positional slice guard at line 239.
> **Append at the end to avoid the guard.**

**What should alert — five, in priority order.** Alerts are for *rate of change*, not level.

| # | Condition | Severity | Why this one |
|---|---|---|---|
| 1 | global spend > 80 % of `global_usd_per_day` | **page** | the only true budget breach |
| 2 | hourly spend > 3× the trailing 7-day same-hour median | **page** | catches a scripted-abuse run in the first hour, before the daily cap notices |
| 3 | a single user > 25 % of guild spend in an hour | warn | catches abuse and pathological pins |
| 4 | `outcome <> 'ok' OR attempt > 0` exceeds 15 % of tokens in an hour | warn | retry storm / provider brownout — pure waste, invisible today |
| 5 | any circuit breaker OPEN > 5 min, **or** usage-writer queue drops > 0 | warn | the second half matters: **dropped usage rows mean the numbers are lying, and that must never be silent** |

**Anti-alerts, deliberately excluded:** absolute token counts (meaningless without traffic
context) and per-request cost (too noisy — one `/deepresearch` is legitimately 50× a chat turn).

**Effort:** M. **Payoff (estimated):** alert #2 in particular reduces the scripted-abuse
detection window from "until the monthly bill arrives" to about an hour.

---

## 8. Lane I5 — Configuration

### 8.1 Diagnosis

Verified premises:

| Claim | Verified | Evidence |
|---|---|---|
| `pydantic` already installed, transitive via `google-genai` | **YES** | `google-genai` requires `pydantic<3.0.0,>=2.12.5`; installed **pydantic 2.13.4**, `pydantic_core` 2.46.4, both already pinned in `constraints.txt:43-44`. `google-genai>=1.0.0` is a **direct** dep, so pydantic is already load-bearing on every boot. |
| `pydantic-settings` also available | **NO** | `ModuleNotFoundError`. Any design must use plain `pydantic`. |
| `BotConfig` has ~100 fields | **YES — exactly 100** | `len(dataclasses.fields(BotConfig)) == 100` |
| 44–47 fields have zero validation | **YES — exactly 47** | 53 referenced inside `_validate_*`, 47 never mentioned |
| Malformed YAML → uncaught `AttributeError`/`TypeError` | **YES, 6 of 8 probes** | `load_and_validate_config` catches only `YAMLError`/`ValueError` |
| The split-length pathology | **YES, and worse than reported** | `MessageSplitter(max_length=100, continuation_overhead=100)` → `effective_max_length == 0`; **20,000 chars → 20,000 parts.** Note the offending pair is `split_length − continuation_overhead`, **not** `safe_split_length`, which is read in exactly one place. |
| `validate_service_connectivity` hardcodes `all_ok = True` | **YES** | `config.py:213-223`. It never touches `discord_token` or `gemini_api_key`. |
| `config.yaml.example` missing but pinned by a test | **YES** | `config_helpers.py:29` prints it; `tests/test_config.py:167` asserts the exact string; the file does not exist. |
| **New:** `validate_service_connectivity`, `validate_startup_connectivity`, `load_and_validate_config`, `get_feature_availability` have **zero test coverage** | **YES** | grep over `tests/` returns nothing |

**Before — measured, eight malformed configs through today's `BotConfig.from_yaml`:**

| Case | Today |
|---|---|
| `context:` (empty section) | `AttributeError: 'NoneType' object has no attribute 'get'` |
| `rag: true` | `AttributeError: 'bool' object has no attribute 'get'` |
| `generation:\n  - 1` | `AttributeError: 'list' object has no attribute 'get'` |
| `generation: null` | `AttributeError: 'NoneType' object has no attribute 'get'` |
| top-level scalar | `AttributeError: 'str' object has no attribute 'get'` |
| `embedding_dimensions: "many"` | `ValueError`, caught, but printed with **no key name** |
| bad YAML syntax | raw `yaml` traceback |
| `personalities:` as a list | **loads silently**; no error mentions personalities |

**Six of eight are uncaught, and every one stops at the first problem.**

### 8.2 The validation strategy decision

**(a) Hand-written validators extended to all 100 fields** — ~47 new `if`/`errors.append` blocks
plus ~10 relational ones, realistically **+250 lines** in a file already 744 lines long and 53 %
validated. Its one honest upside: it is the only option with a **zero-test-break** path. Its
honest downside: it does not fix the disease. Adding a setting stays at four places, and nothing
prevents the fifth (`config.yaml.example`) or sixth (docs) from drifting. **The 47 gaps were not
an accident of effort — they are what happens when validation is a separate artefact from
declaration.** Correct only as a stop-gap.

**(b) Dataclass metadata + a generic validator** — the right *shape*, the wrong *engine*. The
coercion layer is the trap: `test_config.py:100` pins `reports.web_port: "0"` → `int 0` (lax
string→int), and `:152` pins `{"model": 0}` → `"0"` (int→str). Getting the lax/strict matrix
right by hand for `int|float|bool|str|Optional[str]|List[str]|List[Dict]|Dict[str,str]` is a few
hundred lines that pydantic already ships, tested, at C speed.

**(c) Pydantic — RECOMMENDED. Marginal dependency cost: zero wheels.** pydantic 2.13.4 is already
imported into the process by `google-genai` on every boot. The only change to
`requirements.in` is one honesty line, `pydantic>=2.12.5`, promoting an existing transitive dep
to a declared one. **`constraints.txt` needs no regeneration**, so AGENTS.md's "never hand-edit a
version in constraints.txt" rule is not engaged. Import cost: zero.

**Two models generated from one declaration table.** `ParseModel` (lenient — types and coercion,
no bounds) preserves the pinned string-to-int tests; `CheckModel` (strict — types and every
bound) rejects `web_port: 0`. This is not a compromise bolted on afterwards; it is what the
existing tests already specify, and it is a better separation anyway — loading never throws for
an out-of-range value, so you always get a config object you can report against.

**Cross-field rules are a second pass, deliberately not `@model_validator(mode="after")`.** Built
with `mode="after"` first, it was wrong: pydantic **skips after-validators when *any* field
fails**, so a config with one bad integer hid *all* relational errors and the operator fixes
errors one boot at a time. Measured live: the extreme-value config reported **4 problems with the
after-validator and 7 with the two-pass design**.

**Proof that parsing behaviour is preserved byte-for-byte** (executed against the generated
`ParseModel`):

```
[PASS] report_web_port == 0 (int)          <- test_config.py:100, from string "0"
[PASS] rag_backfill_limit == 0             <- test_config.py:102
[PASS] rag_enabled is False                <- test_config.py:101
[PASS] token_db_path == 'yaml.db'          <- test_config.py:96
[PASS] log_file == 'yaml.log'              <- test_config.py:97
[PASS] empty doc -> web_port 8080 / rag_enabled True / rag_db default /
       embed model default / dims 768 / 5MB text cap / log_file None
12/12 parsing behaviours preserved
```

Two pydantic behaviours were probed directly and constrain the design: lax mode **does** coerce
`"0"` → `0`, so the pinned string-to-int tests survive; lax mode **does not** coerce `0` → `"0"`,
so `_normalize_model_complexity` (`config_helpers.py:119-147`) must be ported verbatim as a
`mode="before"` normalizer. **That is the single most fragile port in the migration and deserves
its own commit.**

**Adding a setting goes from 4 places to 1** — measured against the prototype: the
`config.yaml` key is *generated* into `config.yaml.example`; the parser is derived from
`yaml_path`; the `BotConfig` field from `field`+`type_`+`default`; the validation rule from
`constraints`; and two artefacts that do not exist today (the example file and the docs table)
also fall out. Net: **one `Decl(...)` line**.

**Incremental migration path** — five phases, 125 green until exactly one deliberate break:

| Phase | Change | Tests |
|---|---|---|
| **0** | Add `src/config_schema.py` with `DECLS` for all 100 fields. Nothing imports it. Add a test asserting the table covers every `dataclasses.fields(BotConfig)` name and every `config.yaml` key — **the anti-drift ratchet.** | 125 green, +1 new |
| **1** | Shadow mode: `from_yaml` keeps the legacy parser, then also runs `ParseModel` and logs divergence at WARNING. | 125 green |
| **2** | Cut over **parsing** only. Legacy `validate_config()` untouched, so all 46 strings still emit in order. | 125 green (proven) |
| **3** | Delete the legacy parsers (~300 lines of `config_helpers.py`). | 125 green |
| **4** | Cut over **validation** to `CheckModel` + `CROSS_RULES`. **`test_validate_preserves_error_messages_and_order` breaks here — by design.** | 124 green + 1 rewritten |
| **5** | `extra="forbid"` / unknown-key reporting, `--check-config`, generated example. | +new tests |

> **On the 46 pinned strings: there is no honest way to keep them.** Reproducing pydantic errors
> as those exact strings in that exact order requires a bespoke message table keyed by
> `(field, error_type)` — which re-creates the second source of truth the whole exercise exists
> to delete. Phase 4 is the one deliberate break, it is a single test, and the replacement
> assertion (an unordered set of `(yaml_key, rule_id)` pairs) is strictly better: it survives
> rewording, it names the key, and it does not encode validator call order as a public contract.
> **Say so in the CL description rather than hiding it.**

### 8.3 The rule set (I5-02) — the highest-severity gaps

The full table covers all 100 fields with status `OK` / `GAP` (one of the 47 with zero
validation) / `WEAK` (validated with a hole) / `DEAD` (parsed, stored, never read). The
highest-blast-radius entries:

| Field(s) | Rule | Sev | Status |
|---|---|---|---|
| `logging.level` | one of `DEBUG INFO WARNING ERROR CRITICAL` | E | **GAP** — `"LOUD"` boots today |
| `safety.harassment` / `hate_speech` / `sexually_explicit` / `dangerous_content` | ∈ `{BLOCK_NONE, BLOCK_LOW_AND_ABOVE, BLOCK_MEDIUM_AND_ABOVE, BLOCK_HIGH_AND_ABOVE, BLOCK_ONLY_HIGH, OFF}` | E | **GAP × 4 — the highest-severity gaps in the table.** A typo (`"BLOCK_MED"`) is accepted at boot and then either rejected by the SDK mid-request or silently coerced. `get_safety_threshold()` even `.get(category, 'BLOCK_NONE')`-defaults an unknown *category* to the most permissive value. |
| `messages.split_length` − `messages.continuation_overhead` | must leave ≥ 200 usable characters | E | **new cross-rule** — this is the 20,000-message pathology |
| `validation.pdf_render_scale` | `0.0 < x <= 8.0` | E | **GAP** — `0`, `-2`, `20` all accepted |
| `validation.max_text_file_size_bytes` | `1024 <= x <= 104_857_600` | E | **GAP** — `-1` rejects every upload |
| `validation.max_pdf_pages` | `1 <= x <= 500` | E | WEAK — `1e6` accepted |
| `generation.top_k` | `1 <= x <= 1000` | E | **GAP** — `0` accepted |
| `response.timeout <= response.extended_timeout` | ordering | E | **new** — an extended timeout shorter than the normal one is accepted today |
| `personalities` | mapping of non-empty str→str; must contain `"default"`; keys `^[a-z0-9\-]{1,32}$`; W if > 25 (Discord choice cap) | E | **GAP** — a *list* is accepted today and silently produces no error |
| `languages` | list of non-empty strings, deduplicated, must contain `"auto"` | E | **GAP** — a list of ints is accepted today |

**Twenty cross-field rules total, ten of them new:** `context.selection_within_fetch`
(`context_messages_high <= max_messages` — selecting more than you fetch is silently impossible
today), `rag.rerank_within_candidates` (reranking more candidates than were ever retrieved is a
typo, not a setting), `messages.safe_vs_split`, **`messages.usable_payload`**,
`response.timeout_ordering`, `generation.tier_within_global`, `models.thinking_coherent`
(`thinking_level != "off"` when the model's `thinking_backend == "none"`),
`rate_limiting.image_reachable`, `nano_banana.retry_budget` (`retries × delay < timeout`), and
`secrets.image_fallback`.

**Four DEAD fields** — `api_timeout_buffer`, `edit_detection_max_output_tokens`,
`system_prompt_thinking_addon`, `progress_update_threshold` — are parsed, stored, documented in
`config.yaml`, and never read (I5-07).

> **One rule is load-bearing for the migration:** `system_prompt_thinking_addon` **must allow
> empty**. `_valid_config()` in `test_config.py:28-47` leaves it at `""`, so any non-empty rule
> breaks `test_validate_accepts_complete_valid_config`. **Delete the field rather than validate
> it.**

### 8.4 Fail-fast, after (I5-03 + I5-04) · **[PROTOTYPED]**

Four ordered gates, all of which run, none of which raise:
`1 syntax → 2 structure → 3 unknown keys → 4 types/ranges + relational (two passes)`.
Every problem carries **file:line, dotted key, offending value, and a fix**.

```
A. empty section  (today: AttributeError: NoneType.get)
  config.yaml:1  [context]
      section 'context' is empty; delete it or fill it in
      fix: give the section its keys, or remove the section to use defaults

F. bad YAML syntax  (today: raw traceback)
  config.yaml:3  [<syntax>]
      expected ',' or ']', but got '<stream end>'
      fix: check indentation and that every [ { " is closed

C. wrong scalar types  (today: one ValueError, no key named)
  config.yaml:2  [response.timeout]
      Input should be a valid integer, unable to parse string as an integer (got: 'soon')
      fix: use a whole number, unquoted (e.g. 30, not "30s")
```

The extreme-value config, **every value of which passes validation today**, now reports all
seven problems at once:

```
Configuration invalid - 7 problems in config.yaml

  config.yaml:3  [messages.split_length]
      messages.split_length - messages.continuation_overhead must leave >= 200
      usable characters per part (MessageSplitter.effective_max_length)
      fix: lower continuation_overhead or raise split_length; otherwise a 20k
           answer fans out into thousands of Discord messages
  config.yaml:6  [validation.pdf_render_scale]
      Input should be greater than 0 (got: 0)
  config.yaml:7  [validation.max_text_file_size_bytes]
      Input should be greater than or equal to 1024 (got: -1)
  config.yaml:8  [validation.max_pdf_pages]
      Input should be less than or equal to 500 (got: 1000000)
  config.yaml:10 [generation.top_k]
      Input should be greater than or equal to 1 (got: 0)
  config.yaml:12 [rate_limiting.text_rate_limit_per_minute]
      cannot exceed text_rate_limit_per_hour
  config.yaml:15 [context.context_messages_low]
      context_messages_low <= context_messages_medium <= context_messages_high
```

Typo detection (silently ignored today), via `difflib` at cutoff 0.75 against the declared key
set:

```
  config.yaml:2  [context.max_messsages]   unknown configuration key
      fix: did you mean 'context.max_messages'?
  config.yaml:4  [logging.levl]            unknown configuration key
      fix: did you mean 'logging.level'?
```

Line numbers come from a **~40-line `yaml.SafeLoader` subclass** recording
`key_node.start_mark.line` per mapping key. **No new dependency; `ruamel.yaml` is not needed.**
Config loading keeps using `print` (AGENTS.md: deliberately visible before logging is
configured); the reporter returns a string and the caller prints it. Exit code stays `1`.

### 8.5 Runtime mutation semantics (I5-08) · **[PROTOTYPED]**

Seven runtime mutations are in-memory only; **five reply with wording implying durability**
("Successfully switched AI model!", "Developer mode has been **enabled**").

**Writeback to `config.yaml` was evaluated and rejected**, and the reasons are worth recording:
it is a hand-authored, comment-rich, 354-line file that `yaml.safe_dump` would strip and reorder
(preserving comments needs `ruamel.yaml`, a genuinely new dependency for the least valuable
option); it is version-controlled, so a `/dev` toggle in Discord producing a working-tree diff
is a nasty surprise and on a container deploy the file may be read-only; two admins toggling at
once silently loses one; a partial write during a crash leaves the bot unbootable — **the worst
possible failure mode for a config file**; and it conflates operator intent ("this is the
configuration") with operational state ("right now we're on Pro").

**Recommended: a `runtime_overrides` table in `data/token_usage.db`.** There is already
precedent — `channel_settings_service` persists per-channel personality and live mode to SQLite.
Created in `_ensure_table` at construction per AGENTS.md; all access via
`sqlite_connection`/`sqlite_transaction`.

**The overridable set is deliberately small** — only what a `/config` command already mutates:
`dev_mode_enabled`, `current_model`, `thinking_level_override`, `force_search`,
`image_generation_enabled`, `log_level`. Everything else stays file-only, which is also the
answer to "should `/config` be able to set arbitrary keys?": **no**.

**Precedence:** `BotConfig default < config.yaml < environment variable < runtime override`. Env
vars are deployment-time operator intent and should beat the file (this already holds for
`TOKEN_DB_PATH`, `RAG_DATABASE_PATH`, `LOG_FILE`). An override is **cleared**, never "unset to
the file value implicitly" — `/config model reset` deletes the row. **Overrides go through the
*same* `CheckModel` field rule before being written**, so `/config` cannot install a value that
`--check-config` would reject. On startup, an override that no longer validates is dropped with a
WARNING naming the key — never a boot failure.

Prototype output, including across a simulated restart:

```
current_model: gemini-3.1-pro-preview  (runtime override by raymond#0001 at 2026-07-29 07:10:49;
                                        config.yaml says gemini-3-flash-preview)
dev_mode_enabled: True                 (runtime override by raymond#0001 …; config.yaml says False)
log_level: INFO                        (from config.yaml)

-- after restart (new process, same db) --
current_model: gemini-3.1-pro-preview  (runtime override by raymond#0001 …)   <- survives
```

**Effort:** M. **Blast radius:** a new `runtime_override_service.py`, `discord_bot.__init__`
wiring (**construction order matters**), `command_modules/configuration.py`, and
`command_modules/context.py` — `CommandContext` is frozen, so adding a field touches every
registrar. **0 of the 125 tests break if the service is optional and `None`-tolerant, as
`token_tracker` and `report_service` already are.** The pinned tree is unaffected **unless** a
`/config reset` subcommand is added, which requires the three-file dance.

**Interim if it slips (I5-15):** do the honest wording anyway — five string edits, under an hour,
zero risk. `"Successfully switched AI model!"` → `"Switched AI model for this session. Reverts to
config.yaml (gemini-3-flash-preview) on restart."`

### 8.6 Operability (I5-09, I5-10, I5-12, I5-13) · **[PROTOTYPED]**

**I5-09 — a real `validate_service_connectivity`.** Today it sets `all_ok = True`, inspects only
`nano_banana_api_key`, and reports a string that is true by construction;
`validate_startup_connectivity` then swallows every exception and returns `True` regardless. The
README claims it validates connectivity. **It has zero tests.**

Two tiers under a hard 6-second budget. **Tier 0 (local, free, always runs, ~1 ms):** secrets
present and non-blank; **both DB parent directories exist and are writable** (`os.access(parent,
W_OK)`) — *a read-only mount is the single most common container failure and is invisible
today*; an FTS5 availability probe in `:memory:` when RAG is enabled — *today FTS5 degrades to
`fts_enabled = False` silently*; log-file parent writable. **Tier 1 (one bounded call per
service, skippable with `--no-network`):** Discord `GET /api/v10/users/@me` (distinguishes 401
from 429/5xx); Gemini `models.get(default_model)`, which validates the key **and** that the
configured model exists for that key — a real config error class today.

The probe is **injected** (`probe=` parameter) so tests never touch the network, per AGENTS.md.
Signature is unchanged, so `validate_startup_connectivity` needs no edit.

```
all_ok = False
  discord:  FAIL: DISCORD_BOT_TOKEN not set
  token_db: FAIL: directory /tmp/i5proto/data does not exist
  rag_db:   FAIL: directory /tmp/i5proto/data does not exist
  gemini:   OK: reachable in 0.21s (budget 6.0s)
```

**Policy: tier-0 failures are fatal** (they are configuration, not weather); **tier-1 failures
are non-fatal but loud**, matching today's "reduced functionality" stance — but `all_ok` is
finally meaningful and the message names the actual cause. **Also fix the README claim.**

**I5-10 — `python main.py --check-config`.** `main.py` is 71 lines with no argument parsing;
adding `argparse` before `asyncio.run(main())` is contained. Flags: `--check-config` (validate
and exit; never connects to Discord, never creates a database, exit 0/1), `--strict` (also fail
on warnings), `--emit-config-example`, `--no-network`. **`start.sh` should call `--check-config`
after building the venv and refuse to launch on a non-zero exit — the operator gets the error in
one second instead of after a Discord connection attempt.**

**I5-12 — generate and commit `config.yaml.example`.** `config_helpers.py:29` has told operators
to copy a file that does not exist since the repo's first commit, and `tests/test_config.py:167`
pins that string. **Hand-writing the file drifts by the second setting anyone adds — that is
exactly how the 47 validation gaps happened.** Generate it from `DECLS`, commit the output, and
add a test asserting that regenerating produces no diff **and** that the example round-trips
through the schema with zero problems. **The pinned string then becomes *true* rather than
needing to change.** Note the example is *not* `config.yaml`: the shipped file has
`generation.temperature: 2.0` (the ceiling) where the schema default is `0.7`, and that
divergence is itself worth a review comment.

**I5-13 — normalise paths once, at parse time.** A `normalizer=_as_path` on the five path
declarations. Services keep their `expanduser()` calls (idempotent) but stop being where
correctness lives. **0 tests break** — `test_config.py` uses relative paths and
`test_command_registration.py` uses absolute tempdir paths.

### 8.7 Secrets (I5-11) · **[PROTOTYPED]**

Three defects. **Inconsistent empty-string semantics:** `NANO_BANANA_API_KEY` uses
`os.getenv(name, default)`, so `NANO_BANANA_API_KEY=` yields `""` and the Gemini fallback **does
not** fire; the three path overrides use `os.getenv(...) or <yaml>`, so an empty string **does**
fall through. **Same-looking `.env` line, opposite behaviour** — and `tests/test_config.py:87`
actually pins the surprising branch. **Whitespace-only secrets boot the bot:**
`GEMINI_API_KEY=" "*40` passes both the truthiness check and the length check, and the bot starts
and fails on the first API call with an opaque SDK error. **No shape validation** — only length,
so a Discord token pasted into `GEMINI_API_KEY` boots.

One `resolve_secret(env, name, fallback)` helper — strip, empty ⇒ unset, then fall back — used
for all six variables. Verified: empty and whitespace now behave identically. Plus a
`SecretSpec` table (env var, required, min length, shape regex, **where to get it**):

```
-- whitespace-only GEMINI_API_KEY (boots the bot today)
 * GEMINI_API_KEY has leading/trailing whitespace - it was trimmed.
 * GEMINI_API_KEY is set but empty (or whitespace only). Either give it a real value or
   delete the line from .env. An empty value currently boots the bot and fails on the
   first API call.

-- missing both
 * DISCORD_BOT_TOKEN is not set. Add it to .env:
     DISCORD_BOT_TOKEN=<value>
     (https://discord.com/developers/applications -> Bot -> Reset Token)
 * GEMINI_API_KEY is not set. Add it to .env:
     GEMINI_API_KEY=<value>   (https://aistudio.google.com/app/apikey)
```

**Severity split, which protects the pinned test:** presence and length → **errors with today's
exact wording**; whitespace and shape → **warnings on a separate channel**.
`test_validate_tokens_uses_configured_thresholds_and_error_order` uses `"d"*50` / `"g"*30`, both
shape-invalid, so shape must be warn-only or it breaks. **Never echo a secret** — diagnostics
print length and prefix only (`AIza…` / `MTIz…`).

**Effort:** S. **Tests: 1 changes** — `test_config.py:87,95` pins
`NANO_BANANA_API_KEY: "" → ""`, which is the bug. Update those two lines and say so.

### 8.8 I5 ranked order

| Rank | ID | Title | Effort | Tests broken |
|---|---|---|---|---|
| 1 | **I5-03** | Structural fail-fast: stop the raw tracebacks | S | 0 |
| 2 | **I5-15** | Honest wording for in-memory mutations | S | 0 |
| 3 | **I5-11** | Secret validation + uniform env resolution | S | 1 line-level (pins a bug) |
| 4 | **I5-09** | A real `validate_service_connectivity` | M | 0 (adds first coverage) |
| 5 | **I5-01** | Two-model generated config schema (pydantic) | L | 1, at phase 4 |
| 6 | **I5-14** | Rewrite the 46-string ordering test | S | 1 rewritten |
| 7 | **I5-02** | Land the complete rule set | M | with I5-01 |
| 8 | **I5-06** | Cross-field rules as a second pass | S | with I5-01 |
| 9 | **I5-04** | Report every error at once, with line + fix | M | 0 |
| 10 | **I5-10** | `--check-config` / `--emit-config-example` | S | 0 |
| 11 | **I5-12** | Generate and commit `config.yaml.example` | S | 0 |
| 12 | **I5-05** | Unknown-key detection with did-you-mean | S | 0 |
| 13 | **I5-08** | Runtime override store + honest `/config info` | M | 0 |
| 14 | **I5-13** | Normalise paths once, at parse time | S | 0 |
| 15 | **I5-07** | Resolve the four dead settings | S | 0 |

**Two shippable slices.** *Slice A (1–4, all S/M, ~0 test churn):* stops the tracebacks, stops the
lying, fixes secrets, makes startup diagnostics real. **Worth doing this week regardless of the
schema decision.** *Slice B (5–15):* the schema and everything it generates. Costs exactly **one**
deliberate test rewrite, after which every new setting is one line.

---

## 9. Lane I6 — Testing & developer experience

### 9.1 Diagnosis

**Measured baseline (not estimated):** the suite covers **37.9 % of `src/` — 4,632 / 12,210
statements**. Prototyping **42 new tests across three files lifted it to 40.0 %** and moved
`enhanced_command_handler.py` from **0 % → 27.7 %** and `discord_bot.py` from **25.4 % → 41.2 %**.

```
tests run: 125  failures: 0  errors: 0
TOTAL: 4632/12210 statements = 37.9%

   pct   stmts     hit  file
  0.0%     332       0  src/bot/enhanced_command_handler.py
  0.0%     310       0  src/services/help_system.py
  0.0%      41       0  src/services/rate_limiter.py
  0.0%     110       0  src/services/report_web_server.py
  0.0%     288       0  src/services/user_experience_service.py
  0.0%     386       0  src/utils/error_manager.py
  0.0%     102       0  src/utils/image_utils.py
  0.0%     419       0  src/utils/logging_config.py
  0.0%      43       0  src/utils/token_extraction.py
  9.6%     156      15  src/services/context_collector.py
 25.4%     973     247  src/bot/discord_bot.py
 42.9%    1187     509  src/services/gemini_client.py
```

*Measured with a 60-line `sys.settrace` tracer because `coverage` is not installed and this
environment has no package index. CI should use `coverage.py`; expect it within ~1–2 points.*

**Line count is the wrong ranking axis.** The ranking used is `blast radius × silence × change
rate`. Silence matters because the house style is silent degradation — AGENTS.md itself says "a
broken RAG change looks like nothing happened" — so **silent code needs tests *more* than loud
code, because production will not tell you.** That is why `logging_config.py` (419 statements,
0 %) ranks **below** `rate_limiter.py` (41 statements, 0 %): a logging defect is loud and local;
a rate-limiter defect is silent and is the only thing standing between one user and the entire
API budget.

**Deliberately deprioritised:** `help_system.py` (310 statements, 0 %) — do not test it, **delete
it**. `report_web_server.py`, `logging_config.py`, `image_utils.py`, `token_extraction.py` — loud
failures, low change rate; pick them up opportunistically behind the coverage ratchet.

### 9.2 Proposals

#### I6-03 — Cover the silent hybrid-RAG fallback · **[PROTOTYPED] — found a live bug**

**This is the headline result of the lane.** `_process_message_with_context` wraps hybrid
retrieval in `except Exception` and reverts to `ContextCollector` with a single
`logger.warning`. Nine new tests assert the fallback is **audible** — `assertLogs(..., WARNING)`
with the cause embedded — because that log line is the *only* signal an operator gets.

**First run, against unmodified production code:**

```
FAIL: test_a_failure_after_delivery_is_not_retried_through_the_fallback
AssertionError: 2 != 1 : a delivery failure must not be retried through the legacy path
```

**Root cause:** `_generate_and_send_response` is called *inside* the same `try` that catches
retrieval errors (`discord_bot.py:911-940`). A delivery-time exception — **after a partial reply
may already have reached Discord** — is caught by the RAG handler, which falls through to the
legacy path and generates and sends a **second** response. Duplicate user-visible reply,
duplicate model spend, duplicate RAG indexing.

**A/B proof the existing suite is blind:**

```
=== A) unfixed prod + ORIGINAL 125 tests ===      Ran 125 tests ... OK
=== B) unfixed prod + NEW fallback test  ===      FAIL: 2 != 1
```

**The test is precise — a 6-line fix turns it green:**

```python
if self.config.rag_enabled and routed_intent != "image_generate":
    rag_context = None
    try:
        rag_context = await self.hybrid_context_retriever.retrieve(...)
    except Exception as exc:
        logger.warning("Hybrid RAG failed for message %s; using legacy "
                       "context fallback: %s", message.id, exc)

    # Only *retrieval* may fall back. A delivery failure below must reach the
    # outer handler, never re-enter the legacy path and answer a second time.
    if rag_context is not None:
        await self._generate_and_send_response(...)
        return
```

`rag_context is not None` (not truthiness) is **load-bearing**: an empty retrieval is a
*successful* retrieval and must not fall back. Result: 9 tests green, full suite 125 → **159 OK**.

**Effort:** S (tests) + S (fix). **Blast radius:** one 6-line change in the message pipeline.
**Payoff:** fixes a live duplicate-response defect and makes a silent failure mode auditable.
**Dependency note:** this fix must land before the mutation harness is useful — see I6-05's
baseline guard.

#### I6-01 — Cover `on_message` · **[PROTOTYPED]**

`tests/test_on_message_flow.py`, 13 tests in 3 classes, pure house idiom: unbound-method
invocation against a `SimpleNamespace` collaborator bag built by one `make_bot(**overrides)`
helper **local to the file**. **No `object.__new__`, no private-attribute injection, no
production seam changes.** Runs in **0.037 s**.

```
test_bot_authored_message_is_dropped_before_rag_indexing ... ok
test_embeddings_are_scheduled_only_when_a_row_was_indexed ... ok
test_live_mode_channel_bypasses_mention_gate_and_router ... ok
test_live_mode_requires_a_guild_so_dms_still_use_the_mention_gate ... ok
test_rag_indexing_failure_never_blocks_the_reply ... ok
test_unmentioned_guild_message_is_indexed_but_not_answered ... ok
test_rate_limit_is_checked_before_the_router_spends_a_token ... ok
test_rate_limited_user_gets_the_limiter_text_and_no_model_call ... ok
test_mention_only_message_is_answered_with_guidance_not_a_model_call ... ok
test_router_claiming_the_message_stops_the_pipeline ... ok
test_router_complexity_never_hardens_into_a_model_override ... ok
test_router_decision_is_forwarded_to_the_context_pipeline ... ok
test_unexpected_failure_is_routed_through_the_error_manager ... ok
Ran 13 tests in 0.037s — OK
```

**Proof it catches the bug it targets.** Reintroducing BUG-0002 at the real call site (hardening
router complexity into `model_override`):

```
=== BUG-0002 MUTANT vs ORIGINAL 125 (incl. its own regression test) ===
Ran 125 tests ... OK                    <-- SURVIVED

=== BUG-0002 MUTANT vs NEW on_message tests ===
FAIL: test_router_complexity_never_hardens_into_a_model_override
AssertionError: 'complexity-model' is not None : router complexity must stay
  advisory; a hard override bypasses /config model and /preferences model
Ran 13 tests ... FAILED (failures=1)    <-- KILLED
```

**Effort:** M. **Blast radius:** tests only. **Payoff:** closes the single largest coverage hole
and converts BUG-0002 from an untested claim into an enforced contract.

#### I6-02 — Cover the router LRU/TTL cache · **[PROTOTYPED]**

`enhanced_command_handler.py`: 332 statements, **0 %**. Every defect class here is silent — a
broken TTL doubles model spend without an error, a key collision mis-routes without an error.
12 tests, **0.041 s**, driving the clock with
`patch("src.bot.enhanced_command_handler.time.monotonic")` so **no test sleeps**. Covers
normalisation, the **200-char key collision** (a genuine hazard — two distinct prompts sharing a
200-char prefix resolve to one cache key and the second inherits the first's intent; pinning it
makes any change to the truncation policy a deliberate, reviewed change), cache hit avoids a
second API call, `has_images` in the key, TTL eviction at the boundary (59 s hit / 61 s miss),
true LRU ordering (`move_to_end` on read), `max(1, ...)` clamping of a `0` config, the
`image_edit` default, invalid complexity → `low`, unparseable JSON → default, `client is None`
short-circuits **without caching**, transport failure **is not cached**. Result: **0 % → 27.7 %**.

#### I6-04 — De-brittle `test_command_registration.py` · **[PROTOTYPED]**

The incumbent pins 35 ordered `(path, description, callback_name)` tuples **byte-for-byte,
including user-facing copy**; `len(...) == 22` (implied by the tuples); `len(EXPECTED_SIGNATURE)
== 35` (**a tautology over a literal list one line above it**); and an absolute `[5:10]`
positional slice. **Every legitimate change breaks it, the failure diff is a 2,600-character
tuple dump, and the fix is always "paste the new list" — so the signal carries no information
and reviewers stop reading it.**

Four layered assertions, each with a **distinct failure meaning**:

| Layer | Mechanism | Fails on | Tolerates |
|---|---|---|---|
| **WIRING** | `dict` subset: known path → callback | rename, deletion, rewiring | additions |
| **ORDER** | project the actual path list onto the known set, compare | reordering two existing commands | insertions anywhere |
| **ADJACENCY** | `names[index("features")+1 : index("clear-cache")]` | conditional commands landing in the wrong neighbourhood | shifts of the whole block |
| **STRUCTURE** | derived invariants: no duplicate paths, groups non-empty, Discord name regex, description non-empty and ≤ 100 chars, contract ⊆ tree | illegal or malformed trees | copy edits, additions |

**The ORDER layer is the key idea.** `relative_order(paths, known)` filters the live registration
order down to commands already in the contract; comparing that against the contract protects the
property AGENTS.md calls load-bearing while making insertion free. **Order is preserved;
ordering is not frozen.** Descriptions become *validated* rather than *pinned*.

Verified behaviour: passes clean (8 tests, 0.076 s); **correctly FAILS** on a real order
regression, with a diff containing *only command names* — the two that moved are legible at a
glance, where the incumbent reports the same regression as a 2,600-char tuple diff; **correctly
FAILS** on a rename, naming the exact casualty (`['pins'] != []`); and **correctly PASSES** on a
benign addition of a `/uptime` command *mid-tree* plus a `/stats` description reword, which
breaks the incumbent in two places.

> **Design note recorded during prototyping.** The first draft of the STRUCTURE layer asserted
> *set equality* between the contract's top-level entries and the live tree. It failed the
> benign-addition case — reintroducing exactly the brittleness the file exists to remove. It is
> now a subset assertion; deletion protection already lives in the WIRING layer's `missing`
> check, so set equality bought nothing but false failures.

#### I6-05 — `scripts/mutation_check.py` + `scripts/mutants.toml` · **[PROTOTYPED]**

Every regression test claims to prevent a specific bug from returning. **That claim is itself
untested — and for BUG-0002 it is false.**

**`mutmut` and `cosmic-ray` were evaluated and rejected:** both mutate every operator in the
tree (tens of minutes on 12,210 statements), add a dependency, require **a pytest runner this
repo deliberately does not have**, and produce hundreds of equivalent mutants that drown the one
real finding. Wrong tool for this question.

A targeted mutant catalogue instead: one TOML entry per fixed bug, each reintroducing the
original defect as a literal source edit, run against a scratch copy. Four design decisions make
it usable rather than ceremonial: an **anchor arity check** (if a `find` string matches ≠ 1 time,
the mutant errors with *"the catalogue has drifted from the source"* — **silent no-op mutants,
which always report KILLED and mean nothing, are impossible**); a **baseline guard** (the suite
runs unmutated first, and if it is red the script refuses with exit 2, because a mutation score
against a red suite is meaningless); **`expect = "known-survivor"`** (a documented gap fails CI if
it is *unexpectedly killed*, forcing promotion instead of rot); and **declared-guard reporting**
(if a mutant dies to a test other than its declared guard, the report says so — catching
regression tests being carried by an unrelated test's incidental coverage). The scratch tree
copies only `src`, `tests`, `scripts`, `main.py`, `config.yaml` (< 1 MB), so **the working tree
is never mutated** and an interrupted run cannot leave damage.

**Run 1 — against the existing 125 tests (the audit):**

```
MUTANT       BUG       RESULT    EXPECT     VERDICT
M-BUG0001    BUG-0001  KILLED    killed     ok
M-BUG0002    BUG-0002  SURVIVED  killed     >>> MISMATCH <<<
             nothing failed. Declared guard 'test_request_model_precedence' does not
             observe this defect.
M-BUG0003    BUG-0003  KILLED    killed     ok
M-BUG0004    BUG-0004  KILLED    killed     ok
M-BUG0005    BUG-0005  KILLED    killed     ok
4/5 mutants matched expectations.
```

**Exactly one survives.** The report explains *why*: `test_request_model_precedence` calls
`_resolve_request_preferences` directly, so it only ever observes the helper. The bug lives at
the **caller**, which passes `model_override` in. **The helper is correct; the wiring is not. A
unit test on the right function can be a perfect test of the wrong thing.**

**Run 2 — after adding I6-01's `on_message` tests:** `M-BUG0002 KILLED ... (NOTE: not the
declared guard)` → **5/5 mutants matched expectations.**

**Baseline guard, demonstrated** (run before the I6-03 fix, while the suite legitimately failed):

```
baseline suite is RED; mutation scores would be meaningless.
  pre-existing failures: test_a_failure_after_delivery_is_not_retried_through_the_fallback
  fix the suite (or the production defect it found) first.
```

**Cost: 5 mutants at `--jobs 5` → 7.2 s wall; a single mutant → 6.8 s** (dominated by the
baseline run). Cheap enough for every PR. **Practice:** adding a mutant is part of the
definition-of-done for a bug fix, alongside the regression test.

#### I6-06 — Guarded optional imports · **[PROTOTYPED]** — plus three deliberate SKIPs

**The self-contained-file convention has real merit and this analysis says so plainly:** any test
file can be read top-to-bottom without chasing a fixture, any file can be deleted without
breaking another, and an agent editing one file cannot break twelve others. Each proposal is
judged against it individually.

| Proposal | Verdict | Reasoning |
|---|---|---|
| **Guarded optional imports** | **ADOPT** | 4 lines per file. Costs nothing, stays self-contained, converts a 4-file collection *error* into an honest *skip*. |
| `tests/__init__.py` | **SKIP** | Measured: adds nothing, and does **not** fix the must-run-from-repo-root constraint. Pure churn. |
| `conftest.py`-equivalent | **SKIP** | pytest is not configured; there is nothing for a conftest to hook. |
| Shared fixtures/builders module | **SKIP** | The measured duplication is small. Prefer per-file helpers. |
| **Per-file `make_*` factory** | **ADOPT as convention** | All three prototypes use one local `make_bot(**overrides)`. Kills `object.__new__` without cross-file coupling. |

```
--- UNGUARDED (today): module-scope `import fitz` ---
COLLECTION ERROR -> No module named 'fitz'
   => tests/test_pdf_processing.py + 3 more files fail to load; 0 of their tests run.

--- GUARDED (proposed): 4 lines, still self-contained ---
test_renders_a_page ... skipped 'PyMuPDF (fitz) is not installed'
test_still_runs_in_the_same_file ... ok
OK (skipped=1)
```

Note the second line: **pure-logic tests in the same file keep running. Today they do not.**
Apply to `fitz`, `numpy`, `PIL` and `matplotlib` in 4 files (~16 lines). `discord` is a genuine
hard dependency — leave it unguarded.

`tests/__init__.py` was measured and rejected: with it, `discover` runs 167 tests OK, a focused
run works, and discovery from a subdir still fails with 17 errors. **A change that alters no
observable behaviour is churn.**

#### I6-07 — Delete `help_system.py` · **[PROTOTYPED]**

636 lines / 310 statements, **0 % covered, fully dead**. **Deleting dead code is the highest-ROI
coverage work available: +2.6 points of total coverage for zero tests written**, because 310
uncovered statements leave the denominator. Verify with a grep then `compileall`.
**Do this before setting the CI coverage floor, so the floor is set against the real
denominator.**

#### I6-08 — Replace the negative-timing assertion · **[PROTOTYPED]**

`tests/test_rag_optimization.py:104` — `self.assertFalse(completed.wait(0.05))`. Two defects: it
burns a fixed 50 ms (≈ 3 % of a 1.7 s suite), and **it passes vacuously if the worker thread was
never scheduled, so it can silently stop testing anything while staying green.**

Wrap the `RLock` in a proxy that sets an event the instant the worker *reaches* `acquire`, before
it can block. The precondition becomes positively established and the assertion becomes
zero-wait:

```python
self.assertTrue(lock.reached.wait(5), "worker never reached the lock")
self.assertFalse(completed.is_set(), "mutation was not blocked by the lock")
```

```
INCUMBENT: ok=True  wall=  55.3 ms
PROPOSED : ok=True  wall=   4.2 ms
```

**13× faster and strictly stronger:** the vacuous-pass hole is closed, and the remaining 5 s
bound is a generous *failure* timeout rather than a *success* wait.

#### I6-09 — GitHub Actions CI · **[DESIGNED]**

No CI configuration exists anywhere, so every check in AGENTS.md is manual and therefore
optional.

| Decision | Choice | Reasoning |
|---|---|---|
| Python matrix | **3.12 only** + a separate non-blocking 3.13 canary | `start.sh` refuses any other version and `constraints.txt` is a `pip freeze` from 3.12.10. **A matrix would test a configuration the project explicitly rejects.** The canary installs `requirements.in` (lower bounds only), *not* `requirements.txt`, because `constraints.txt` would pin unbuildable wheels. |
| No-network | **Runtime enforcement**, not convention | A job-level `NO_NETWORK_TESTS=1` is a promise. A `sitecustomize.py` that raises on `socket.socket.connect` is a **proof**. 10 lines, and accidental network access becomes a hard, attributable failure at the call site. |
| Secrets | **None** | Nothing in CI needs a token. |
| Coverage gate | **Ratchet, floor 38 %** | Measured baseline 37.9 %. A round 40 % would fail on day one; 80 % would be theatre. **Raise the floor in the same PR that raises coverage.** After I6-01/02/03 (measured 40.0 %) and the `help_system.py` deletion, the floor moves to **42 %**. |
| Mutation | PRs touching `src/` only | 7 s, but only meaningful when production changes. |
| Concurrency | `cancel-in-progress` | Cheap, avoids queue pile-up. |

The workflow must run tests **from the repo root** (there is no `tests/__init__.py` and no
`sys.path` shim) and cache on `requirements.in` + `constraints.txt`.

**Depends on:** I6-06 (so a missing PyMuPDF skips rather than errors), I6-07 (set the floor
against the real denominator), I6-03's fix + I6-05 for the mutation job.

#### I6-10 / I6-11 / I6-12 — DX · **[DESIGNED]**

**I6-10 — `--offline` mode for `scripts/health_check.py`.** It cannot pass offline, has no skip
flag, creates `logs/` and `temp/` as a side effect, and parses `config.yaml` three times per run.
Add `--offline` and `--no-side-effects`, parse config once. Then CI can run it as a genuine smoke
test of config parsing and service construction — **which today has no coverage at all.**

**I6-11 — fix the fresh clone.** A fresh clone cannot be brought up by following the README. Five
independent changes:

| # | Problem | Change |
|---|---|---|
| **a** | `start.sh` is committed `100644`. AGENTS.md says run `./start.sh` → **`Permission denied`** (reproduced). README says `sh start.sh`, which works. **The two docs contradict each other because of a file mode.** | `git update-index --chmod=+x start.sh` |
| **b** | `grok-prompts` is a gitlink (`160000 172d3a3f…`) with **no `.gitmodules` entry**. `git clone` leaves an empty directory; `git submodule update --init` cannot help — there is no URL. | Add the `.gitmodules` entry with a real URL, or `git rm --cached grok-prompts`. **An unregistered gitlink is never correct.** |
| **c** | **Partial-install poisoning.** `start.sh` has `set -e`, so a failed `pip install` aborts *after* `python -m venv .venv` has created `.venv/bin/python`. The next run takes the "Using existing virtual environment" branch and launches against a half-installed venv. | Build into `.venv.tmp` and `mv` only after `pip install` succeeds. Atomic: a failed install leaves no `.venv`. |
| **d** | **Interpreter-probe divergence.** `start.sh` probes 3 interpreters, `start.bat` probes 2. A machine where `python3` is 3.12 but `py -3.12` is absent works on Linux and fails on Windows, for no stated reason. | Align on one documented ordered policy. |
| **e** | **Cache cleanup reinvented, worse.** `start.sh` walks `.git`; `start.bat` does likewise. Meanwhile `scripts/cleanup_pycache.py` already exists and correctly excludes `.git`, `.venv`, `.mypy_cache`, `.pytest_cache`, `.ruff_cache`, `node_modules`. | Both scripts call `"$PYTHON" scripts/cleanup_pycache.py`. |
| **f** | `.gitignore` contains `!.env.example` — the file is *intended* and **does not exist**. README instead inlines placeholders (`replace_with_your_discord_token`) that **half-pass validation**, so the bot starts and fails later with a confusing error. | Add `.env.example`; tighten the validator to reject empty *and* `replace_with_*` values. |

**I6-12 — check-only, new-code-scoped `ruff`.** AGENTS.md explicitly forbids running
`black`/`ruff` across the repo — correctly, because a repo-wide reformat would produce an
unreviewable diff and destroy `git blame` across 14,697 lines. **The ban is on *reformatting*,
not on *checking*.** Never a formatter. Check-only, on **changed files**, with a deliberately
narrow rule set — `F` (pyflakes), `E9`, `B` (bugbear), `ASYNC` — and nothing else. **`F` alone
would have caught the `nano_banana_client` import defect at authoring time**, and `ASYNC` is
unusually valuable here because the bot is async end-to-end and does real blocking work behind
executors; a blocking call slipping into a coroutine stalls the whole event loop and is otherwise
invisible. A broader `select` would flag thousands of pre-existing lines and force either a mass
edit (forbidden) or a giant ignore list (pointless).

**AGENTS.md is respected without amendment.** Recommend adding one clarifying sentence: *"Repo-wide
reformatting is forbidden. Check-only linting scoped to changed files is permitted and runs in
CI."*

---

## 10. The cost story

> This section exists as its own chapter because it is the only lane whose headline is a
> **business** risk. Everything in it rests on token counts that were measured against the real
> `format_prompt` and the real `ContextPackBuilder`. **The dollar figures rest on an assumed
> price vector that the repo does not contain.**

### 10.1 The three facts

**Fact 1 — there is no monetary cost model anywhere, despite the README advertising token
accounting.** `get_api_usage_info()` returns the strings `"Free up to rate limits"` and
`"Pay-as-you-go pricing available"`. A repo-wide grep for any numeric price, USD value or cost
arithmetic returns **nothing**. The `token_usage` table has nine columns and none of them is a
model name, so **spend is unreconstructable after the fact even in principle**. And
`/usage-report`, the surface an operator would look at, **never reads the database** — it reads
an in-memory dict whose "Token Usage (Estimated)" is literally `context_messages * 50` and
`response_length // 4`, resets on every restart, and has never touched `token_usage.db`. The
report even admits it in a footnote: *"Notes: Token counts are estimates (~4 chars ≈ 1 token)."*

**Fact 2 — today's books capture only 15–69 % of real tokens.**

| Scenario | True tokens | Recorded | Visibility |
|---|---|---|---|
| low tier, RAG path, no rerank | 2,917 | 2,015 | **69 %** |
| low tier, RAG path, rerank fires | 3,414 | 2,015 | **59 %** |
| high tier, RAG path, rerank fires | 7,914 | 2,015 | **25 %** |
| any tier, legacy fallback (selector runs) | 13,487 | 2,015 | **15 %** |

That is **before** counting a single retry, timeout, safety block, or `/deepresearch`. Four
independent mechanisms cause it: five of six SDK call sites never call `record_token_usage`;
`thoughts_token_count` is never read (and thinking is 300–4,800 tokens per turn);
`APIResponse.__post_init__` *forbids* attaching usage to a failure, so all four retry attempts on
a failing turn are worth zero; and the whole event is discarded whenever the provider's reported
total is less than input+output.

**Fact 3 — worst-case single-user exposure is ~363 million tokens ≈ $3,247 per hour, via
uncapped `/deepresearch`.** There is no cooldown on any slash command. Only *text* messages are
rate-limited (`text_rate_limit_per_minute: 10`, `text_rate_limit_per_hour: 60`).
`/deepresearch`, `/summarize` and `/rag backfill` pass through **no limiter of any kind, and none
of them checks any permission**.

### 10.2 The assumed price vector — stated explicitly

**These are assumptions, not authoritative prices.** The configured model ids are preview ids
whose list price cannot be sourced from the repo. USD per 1M tokens:

| Model | in | out | thinking (billed as out) | cached in |
|---|---|---|---|---|
| `gemini-3-flash-preview` | **0.30** | **2.50** | 2.50 | 0.075 |
| `gemini-3.1-pro-preview` | **1.25** | **10.00** | 10.00 | 0.31 |
| `gemini-2.5-flash-lite` | **0.10** | **0.40** | 0.40 | 0.025 |
| `gemini-embedding-2` | **0.15** | — | — | — |
| `gemini-2.5-flash-image` | **0.30** | **30.00** | — | 0.075 |

Every dollar number below is of the form `tokens × price`. **Substitute real prices and re-run to
get real numbers.** The *token* counts are independent of this assumption and are the load-bearing
part of the analysis. I4-02's design is specifically built so this table is **data, not code**.

**Token-estimation assumption:** `sentencepiece` is not installed, so
`google.genai.local_tokenizer` cannot load and there is **no offline tokenizer**. All prompt-token
figures use **chars ÷ 4.0**, applied identically to "today" and "proposed", so the *ratios* are
robust even if the absolute constant is off. Other tagged assumptions: median user prompt
≈ 120 chars; median answer ≈ 1.6 KB (400 tok); complexity mix 70 % low / 25 % medium / 5 % high;
thinking tokens `high` = **4,800 (measured)**, `low` = 1,200, `minimal` = 300 (assumed); router
cache hit rate 30 %; reranker fires on ~35 % of turns; pin-population mix 55 % none / 20 % one
400 B / 15 % three 800 B / 7 % six 1.5 KB / 3 % one 100 KB.

### 10.3 The four exposure vectors

**Vector A — text messages (rate-limited to 60/h).** Worst case is a channel carrying the 100 KB
pin: 26,869 input tokens, `max_output_tokens_high` = 65,536, `max_retries: 3` → 4 attempts,
`extended_timeout: 120` s each.

```
ceiling   60 x 4 x (26,869 + 65,536) = 22,177,200 tok   ~ $41.26/h
plausible (full output on the final attempt only)       ~ $15.45/h
```

**Vector B — `/deepresearch`, no cooldown.** Per invocation: gather at `max_output_tokens_medium`
= 16,384 with Google Search, then synthesis at `max_output_tokens_high` = 65,536 over the
gathered text. **100,904 tokens = $0.2255** (×4 with retries = $0.9020).

| Rate | Plausibility | Tokens/h | $/h | With retries |
|---|---|---:|---:|---:|
| 60/h | a human clicking once a minute | 6,054,240 | **$13.53** | $54.12 |
| 600/h | a keyboard macro | 60,542,400 | **$135.30** | $541.19 |
| 3,600/h | a script | **363,254,400** | **$811.78** | **$3,247.13** |

**Vector C — `/summarize`, no cooldown**, up to `channel_history_limit: 500` messages in one
prompt: 30,346 tok = $0.0189/call → **$67.91/h** at 1/s.

**Vector D — `/rag backfill`, no cooldown, no permission check**, `rag.backfill_limit: 0` meaning
*the entire accessible history*: a 50,000-message channel is 2.5 M embedding tokens = $0.38;
across 20 channels, **$7.50 per sweep**. There *is* a per-channel "already running" guard —
**the only spend control that exists anywhere in the bot.**

> ### Headline
> **A single user with a script can burn ~363 million tokens ≈ $3,247 in one hour through
> `/deepresearch` alone** (assumed prices, `max_retries: 3`, no cooldown anywhere).
> A *human* doing it by hand at 10/minute reaches **$135/h ≈ $3.2k/day**.
> An **unlucky, non-malicious** user in a channel with a large pin reaches **$15/h** — at the
> rate limit the bot already enforces.
> **None of it appears in `token_usage`:** only successful main-model calls are recorded, i.e.
> ~25 % of a high-tier turn's tokens and **0 % of retries**.
>
> **Token count is assumption-free:** 3,600 invocations × 100,904 tokens × 4 attempts.

### 10.4 The router: 11.4 % of tokens, +51 % of spend

The router runs an extra Gemini call on **every** text message. Its *own* consumption is
**421 tok/msg = 11.4 %** of per-message tokens — but only **1.8 % of dollars**, because it runs
on `gemini-2.5-flash-lite`, 25× cheaper per output token than the answer model at the assumed
prices. **That is the router working exactly as designed.**

But the router **causes** $1.19 per 1,000 messages more than always-`low` — **+51 %** ($3.535 vs
$2.347) — by upgrading 30 % of messages to more thinking. Against always-`medium` it **saves**
$1.06/1k (−23 %) and **pays for itself 17× over**: $0.063 of router tokens avoids $1.12 of
thinking tokens.

**So the router's value is entirely a question of the counterfactual, and that decision should be
made deliberately with the number visible, not by accident.** Note that all three complexity
tiers name the *same model* in the shipped config, so the router's economic effect runs almost
entirely through **thinking level** (300 → 4,800 tokens) and **output cap** (4,096 → 65,536),
not through model choice. Either point `high` at a genuinely different model, or rename the
concept to "effort routing", because that is what it is.

### 10.5 What closes it, in cost order

| Control | Effort | Effect on exposure (assumed prices) | Evidence |
|---|---|---|---|
| **I4-07 L1** — cooldowns on `/deepresearch`, `/summarize`, `/rag backfill` | S | vector B **$3,247/h → ~$0.90/h ≈ 3,600×** | DESIGNED |
| **I4-09** — cap `max_output_tokens_high` 65,536 → 16,384 | S | vector B **$3,247/h → ~$1,000/h** *before* any cooldown | DESIGNED |
| **I4-06** — bound pins at ingest (4 KB / 25 / 40 KB) | S | makes the vector-A cost bomb impossible to create | DESIGNED |
| **I4-05** — token-budgeted context pack | M | prompt tokens **−19 % mean, −87 % p100** (26,869 → 3,393); the 100 KB pin goes from **$11.6/day to ~$1.2/day** | **PROTOTYPED** |
| **I4-07 L2–L4** — budgets, degrade mode, kill switch | M | vector A **$15–41/h → ~$0.40/h** | DESIGNED |
| **I4-08** — circuit breaker + per-turn wall clock | S–M | ~**60 % of retry tokens** during an incident; occupancy 488 s → ~180 s | DESIGNED |
| **I4-01 + I4-03 + I4-04** — the fact table, the metering seam, usage on failure | L + M + S | visibility **15–69 % → ~100 %** | I4-01 **PROTOTYPED**; the others DESIGNED |

**Suggested first PR — a day's work, no schema, no migration, no tree change:** I4-07 layer 1 +
I4-06 + capping `max_output_tokens_high`. **Together those cut worst-case single-user hourly
exposure from ~$3,247 to well under $10.**

---

## 11. Rejected and deprioritized

A first-class deliverable. Every idea below was evaluated and is **not** recommended. Recording
them stops re-litigation.

### 11.1 Architecture

| ID | Idea | Verdict | Reasoning |
|---|---|---|---|
| **I1-60** | Lightweight DI container | **REJECTED — cost L, payoff negative** | A container solves *resolution* and *lifetime scoping*. This codebase has neither problem: ~20 singletons, one deployment target, one composition site, no plugins, no request scope, no ambiguity about which implementation to use. It would add a framework, a registration DSL, and a new class of runtime `KeyError`s in exchange for solving nothing — **and it actively hurts, because the ordering constraints a reader can currently see by reading 138 sequential lines would become implicit in a resolution graph.** |
| **I1-61** | Freeze `BotConfig` / immutable config | **LEAVE ALONE — cost M, payoff low** | The brief flagged `BotConfig` as "a mutable process-global mutated at runtime by `/config`". A grep found **exactly one** runtime mutation of a `BotConfig` field in the entire `src/` tree: `configuration.py:357`, `config.dev_mode_enabled = not config.dev_mode_enabled`. Everything else people think of as runtime config lives on `GeminiClient` or in SQLite. **The "mutable global" is one boolean.** Freezing the dataclass would mean threading a `RuntimeSettings` object through everything that reads `config.*` — a wide, invasive change to protect against a single documented toggle. |
| **I1-62** | Collapse the coordinator layer | **REJECTED — cost L, payoff negative** | 21 of 125 tests (**17 %**) instantiate a coordinator directly with plain keyword arguments and no `DiscordBot` anywhere. That is the *entire* unit-test coverage of the bot layer, and it exists **because** the coordinators were extracted. Collapsing them means those 21 tests either die or regress into `object.__new__(DiscordBot)` + private-attribute injection. **The verdict is KEEP, not collapse.** Wrapper-as-seam is real but small — 6 usages, not the dominant idiom. |
| **I1-12b** | Full four-way split of `message_index_service.py` into `writes.py` / `search.py` / `embeddings.py` | **LEAVE ALONE — cost L, payoff low** | 40 tests already pin this service against real temp SQLite databases. The internal clusters share `self.db_path`, `self.fts_enabled`, the embedding config and the vector cache, so a split means either passing a shared state object everywhere (little gained) or four objects that all reach into the same DB (worse). The one genuine cleanup — the **16 of 17 `async` methods that are pure `asyncio.to_thread` passthroughs (~72 lines)** — is a decorator, not a module split. |
| — | Adding a formatter/linter as part of the architecture work | **LEAVE ALONE** | AGENTS.md is explicit. A formatting pass would also make every diff in this document unreviewable. |

### 11.2 Data layer

| Idea | Verdict | Reasoning |
|---|---|---|
| **Alembic** as the migration framework | **REJECTED** | Brings `alembic` + `SQLAlchemy` + `Mako` + `typing-extensions` (~8 MB) and forces a `constraints.txt` regeneration from a clean 3.12 env. Expects SQLAlchemy metadata against a codebase that is raw `sqlite3` + hand-written SQL. Two databases means two `alembic.ini`s. Adoption of unversioned databases needs a human `alembic stamp` run out-of-band. **Its one feature worth paying for — autogenerate — is unusable without ORM models.** The ~110-line hand-rolled runner has 20/20 prototype assertions and adds nothing to `constraints.txt`. |
| **`aiosqlite`** | **REJECTED** | A new dependency requiring a `constraints.txt` regeneration. Internally it *is* a thread-per-connection with a queue — precisely what I2-06 builds in 60 lines. Its real value is `async with` / `async for` ergonomics, which would mean rewriting ~30 call sites. **Pays a dependency and a rewrite for syntax.** |
| **One global connection + a lock** | **REJECTED** | Serialises everything behind the slowest query. |
| **A dedicated DB thread + hand-rolled futures** as the general mechanism | **REJECTED** | Measured identical to a 1-worker executor (0.089 ms median). More code, no gain. *(The single-writer *pattern* is adopted; the hand-rolled queue is not.)* |
| **Sizing the reader pool by CPU count** | **REJECTED — measured** | pool 2: 64.2 ms (8.5x); pool 4: 114.5 ms (4.8x); pool 8: **315.3 ms (1.7x)**. Past two threads the GIL dominates and more workers is **strictly worse**. |
| **`rowid IN (channel subquery)`** as the FTS scope fix | **REJECTED — measured 28x SLOWER** | `>>> best 1402.031 ms` versus a 49.838 ms baseline. Recorded because it looks obvious and is wrong. |
| **`PRAGMA mmap_size`** | **REJECTED** | Tempting on a read-heavy path, but it makes an I/O error a SIGBUS instead of a Python exception. Not worth it for a bot. |
| **`synchronous=NORMAL` as a performance change** | **RE-FRAMED, not rejected** | Measured +2 % (12,355 → 12,626 writes/s) — **noise**. It is a durability decision, not a performance one. Keep `FULL` on `token_usage.db`. |
| **Deleting `message_retrieval_events` outright** | **REJECTED** | "Nothing reads it" is true and tempting, but its columns are exactly what a retrieval-quality dashboard needs. Add retention plus a `/ragstats` reader and the table earns its keep. |
| **Bounding the vector cache by count** | **DEPRIORITIZED — measured recall cost** | Keeping the most-recent 100k of 200k gave recall@12 = **0.483**; 50k gave **0.267**; 20k gave **0.108**. Prefer bounding by *scope* (evict channels inactive for N days), which is lossless for those channels' queries. |

### 11.3 Performance

| ID | Idea | Verdict | Reasoning |
|---|---|---|---|
| **I3-13** | float16 vector cache | **REJECTED — measured** | Half the memory (1025 → **513 MiB**) but **20x slower: 60.17 → 1214.24 ms**. numpy has no float16 GEMM, so it upcasts element-wise outside BLAS. Recall is fine (0.997 vs 1.000); the latency is not. *(int8 with explicit int32 accumulation could work but needs a hand-written kernel — out of scope.)* |
| **I3-12** | Widen the PDF `ThreadPoolExecutor` | **DEPRIORITIZED — misdiagnosis corrected** | Measured **1.21x only** (13.59 → 11.23 s for 8 jobs at w=8). A prior lane flagged the `max_workers=1` pool as the bottleneck; **the real cost was the pointless PNG encode/decode round-trip.** Once I3-08 removes it, widening finally helps (1.42 → 0.84 s) — but a single 8-thread × 3-conversion probe with 0 errors **is not a safety proof**. Land I3-08 and leave the executor at 1 worker; revisit widening separately with a proper stress test. |
| — | The O(1) strip-table half of the splitter fix | **ABLATED OUT — measured net loss** | Replacing `len(content[a:b].strip())` with precomputed non-whitespace neighbour tables made things **worse**: code_heavy @176 KB 0.0265 s (index only) → 0.0533 s (index + tables); prose 0.0147 → 0.0365; one_giant 0.0414 → 0.0649. **120,000 `str.isspace()` calls cost more than the slicing they eliminate.** Ship the interval index only — smaller patch, lower risk, strictly faster. |
| **I3-23** | Offload config load / `yaml.safe_load` / logging setup | **NOT WORTH IT** | One-time, before the gateway connects. No user-visible impact. |
| **I3-24** | Optimise hybrid-retriever fusion CPU | **NOT WORTH IT** | ~70 candidates, dict + sort, sub-millisecond. Calling `datetime.now()` once instead of per candidate is a one-line tidy, not an optimization. |
| **I3-25** | Optimise slash-command report/stat string building | **NOT WORTH IT** | Small, and slash commands already have a 15-minute deferred budget. |
| **I3-10 / I3-11** | TTL-cache the per-message settings reads; replace the O(n²) renderer splice | **KEEP, BUT HONESTLY SMALL** | I3-10 is 20x on the microbenchmark but only **0.44 ms → 0.022 ms per response**; at 50 msg/s the live gate is **0.69 % of one core**. Its real merit is removing an `open()`/`close()` **before the mention gate**, i.e. paid for messages the bot ignores. I3-11 is 73x but **5 ms absolute** — bundle it with I3-CR; do not schedule it alone. Both are labelled premature in the ranked table. |
| — | Blanket JPEG for image encoding | **CONDITIONAL, not adopted** | 14.2x cheaper at 2048², but **JPEG is lossy and changes what the model sees.** Offload first (removes 2.13 s of head-of-line blocking); make JPEG opt-in per content type, never a blanket switch. |
| — | `to_thread` the splitter instead of fixing it | **REJECTED** | That hides a CPU bug and still burns a pool thread for a minute. Fix the algorithm. |

### 11.4 Configuration

| Idea | Verdict | Reasoning |
|---|---|---|
| **Hand-written validators scaled to all 100 fields** | **STOP-GAP ONLY** | +250 lines in a 744-line file whose only structure is four functions called in a fixed order. Its one honest upside is a zero-test-break path. It does not fix the disease: adding a setting stays at four places, and **the 47 gaps were not an accident of effort — they are what happens when validation is a separate artefact from declaration.** Doing it again produces the same drift in a year. |
| **Dataclass metadata + a hand-rolled generic validator** | **REJECTED — right shape, wrong engine** | The coercion layer is the trap: the pinned tests specify lax string→int *and* int→str behaviour. Getting the lax/strict matrix right by hand for eight type shapes is a few hundred lines that pydantic already ships, tested, at C speed. Rejected only because pydantic is the same shape with the engine already installed. |
| **`@model_validator(mode="after")` for cross-field rules** | **REJECTED — measured** | pydantic **skips after-validators when any field fails**, so a config with one bad integer hides *all* relational errors and the operator fixes errors one boot at a time. Measured: **4 problems reported with the after-validator, 7 with the two-pass design.** |
| **Writeback of runtime overrides to `config.yaml`** | **REJECTED** | Five reasons: `yaml.safe_dump` destroys every comment and reorders keys in a hand-authored 354-line file (preserving them needs `ruamel.yaml`, a new dependency for the least valuable option); the file is version-controlled, so a Discord toggle produces a working-tree diff, and on a container deploy it may be read-only; two admins toggling at once silently loses one; a partial write during a crash leaves the bot unbootable — **the worst possible failure mode for a config file**; and it conflates operator intent with operational state. |
| **`pydantic-settings`** | **UNAVAILABLE** | `ModuleNotFoundError` — verified. Any design must use plain `pydantic`. |
| **`ruamel.yaml` for line numbers** | **NOT NEEDED** | A ~40-line `yaml.SafeLoader` subclass recording `key_node.start_mark.line` does the job with no new dependency. |
| **Keeping the 46 pinned error strings through the migration** | **NOT POSSIBLE HONESTLY** | Reproducing pydantic errors as those exact strings in that exact order requires a bespoke message table keyed by `(field, error_type)` — **which re-creates the second source of truth the whole exercise exists to delete.** One deliberate test rewrite, stated in the CL description. |
| **Hand-writing `config.yaml.example`** | **REJECTED** | Instantly correct, drifts by the second setting anyone adds. **That is exactly how the 47 validation gaps happened.** Generate it and commit the output with a no-diff test. |

### 11.5 Testing & DX

| Idea | Verdict | Reasoning |
|---|---|---|
| **`mutmut` / `cosmic-ray`** | **REJECTED** | Both mutate every operator in the tree: tens of minutes on 12,210 statements, a new dependency, **a pytest runner this repo deliberately does not have**, and hundreds of equivalent mutants that drown the one real finding. A targeted 5-mutant catalogue runs in 7.2 s and found the one real gap. |
| **`tests/__init__.py`** | **SKIP — measured** | With it: `discover` 167 OK, focused run OK, discovery from a subdir still fails with 17 errors. **It neither breaks anything nor fixes the constraint AGENTS.md documents. A change that alters no observable behaviour is churn.** |
| **A `conftest.py` equivalent (`tests/_support.py`)** | **SKIP** | pytest is not configured; there is nothing for a conftest to hook. |
| **A shared fixtures/builders module** | **SKIP** | The measured duplication is small, and the self-contained-file convention has real merit worth defending: any file can be read top-to-bottom without chasing a fixture, any file can be deleted without breaking another, and an agent editing one file cannot break twelve others. **Prefer per-file `make_*(**overrides)` helpers**, which kill `object.__new__` without cross-file coupling. |
| **Set-equality between the registration contract and the live tree** | **REJECTED during prototyping** | The first draft of the STRUCTURE layer used it and it failed the benign-addition case — reintroducing exactly the brittleness the file exists to remove. Deletion protection already lives in the WIRING layer, so set equality bought nothing but false failures. |
| **Repo-wide `black` / `ruff format`** | **REJECTED — AGENTS.md, and correctly** | An unreviewable diff and destroyed `git blame` across 14,697 lines. Check-only, changed-files-only, `F`/`E9`/`B`/`ASYNC` is permitted and is the whole of I6-12. |
| **A Python version matrix in CI** | **REJECTED** | `start.sh` refuses any version but 3.12 and `constraints.txt` is a 3.12.10 `pip freeze`. **A matrix would test a configuration the project explicitly rejects.** A separate non-blocking 3.13 canary installing `requirements.in` surfaces the next migration early without ever blocking a merge. |
| **An 80 % coverage gate** | **REJECTED — theatre** | Measured baseline is 37.9 %. Even a round 40 % would fail on day one. Ratchet at 38 %, move to 42 % after the `help_system.py` deletion, and raise the floor in the same PR that raises coverage. |
| **Adding `config.yaml.example` (I6's view)** | **DISAGREEMENT WITH I5, RESOLVED IN I5's FAVOUR** | I6 argued it would create a second source of truth that drifts. That is correct **for a hand-written file** — but I5-12 *generates* it from the schema with a no-diff test, which is drift-proof and additionally makes the message at `config_helpers.py:29` true. **Adopt I5-12's generated form; do not hand-write one.** |

---

## 12. Sequencing

Effort tiers: **XS** ≤ 1 h · **S** ≤ ½ day · **M** 1–2 days · **L** 3–5 days.

### Phase 0 — Independent quick wins, shippable immediately (~1 day total)

No dependencies on each other, no migration, no schema change, no command-tree change. Six of
these are prototyped.

| ID | Change | Effort | Payoff |
|---|---|---|---|
| **I6-03** | Hoist delivery out of the RAG `try`; gate on `rag_context is not None` (+ 9 tests) | S | fixes a **live duplicate-reply / duplicate-spend defect**; 125 → 159 green |
| **I3-08** | Delete the PDF PNG round-trip (3 lines) | S | **11.67x**, pixel-identical |
| **I3-04** | Two `(scope, created_at DESC)` indexes in `_ensure_schema` | XS | **29x / 1870x** |
| **I3-05 / I2-14** | Ledger the startup reconcile on a rules fingerprint | S | **57,134x** per boot |
| **I2-09** | `get_pending_embeddings` `ORDER BY` rewrite (2 lines) | S | **1844x** |
| **I4-07 L1** | Cooldowns on `/deepresearch`, `/summarize`, `/rag backfill` + permission gate | S | exposure **~3,600×** |
| **I4-09 (partial)** | Cap `max_output_tokens_high` 65,536 → 16,384 | XS | tail cost **4×** |
| **I4-06** | Bound pins at ingest (4 KB / 25 / 40 KB) | S | removes the cost bomb permanently |
| **I5-03** | Structural fail-fast: stop the raw tracebacks | S | highest payoff-per-line in I5 |
| **I5-15** | Honest wording for in-memory mutations (5 strings) | S | removes a UX lie for ~free |
| **I6-07** | Delete `help_system.py` | S | +2.6 coverage points for zero tests |
| **I6-08** | Deterministic replacement for the negative-timing test | S | 55.3 → 4.2 ms, closes a vacuous-pass hole |

**Phase 0 total: ~1 day.** Nothing here depends on anything else here.

### Phase 1 — The measured performance and durability core (~4–5 days)

| ID | Change | Effort | Depends on |
|---|---|---|---|
| **I3-01** | Single-parse splitter (interval index only) | S | — |
| **I2-04** | WAL + explicit `busy_timeout` + `temp_store` in `sqlite_utils.py` | S | — |
| **I3-09** | Per-user rate-limit pruning | S | — |
| **I3-14 / I3-15** | Bound `_completed_jobs` and the router cache | S | — |
| **I1-01** | Eager construction of the 4 registration-attached services | S | — |
| **I6-01** | `on_message` coverage (13 tests) | M | — |
| **I6-06** | Guarded optional imports (4 files) | S | — |
| **I2-07** | Move the 13 remaining sync call sites off the loop | S ×13 | I2-04 (interim: `to_thread`) |
| **I3-07** | Offload image encode | M | — |

**Exit criterion:** the gateway-disconnect hazard is gone, a contended read no longer fails, the
hot path is pinned by tests, and no whole-feature can silently be off before `on_ready`.

### Phase 2 — Concurrency, visibility, and the connection layer (~5–6 days)

| ID | Change | Effort | Depends on |
|---|---|---|---|
| **I2-06** | Thread-local connections, reader pool(2), one writer thread | M | I2-04 |
| **I3-02 + I3-03** | Lock-free vector snapshot + adaptive `search_semantic` | M + M | — |
| **I2-13** | Write-failure semantics: classify, retry, raise, quarantine | M | I2-04, I2-06 |
| **I1-20** | `Criticality` + `StartupReport` | M | — |
| **I1-21** | Presence badge + banner + `/config info` truth | S | I1-20 |
| **I1-23** | Per-registrar isolation in `setup_commands` | S | I1-20 (optional after I1-01) |
| **I6-05** | Mutation harness + catalogue | S | I6-03's fix (green baseline) |
| **I6-04** | De-brittle `test_command_registration.py` | S | — |
| **I5-11** | Secret validation + uniform env resolution | S | — |
| **I5-09** | A real `validate_service_connectivity` | M | — |

**Exit criterion:** 244× concurrent semantic throughput, no self-inflicted `SQLITE_BUSY`, no
silent write loss, and no subsystem can be off without the member list, the log banner and
`/config info` all saying so.

### Phase 3 — The schema layer, behind a runner (~5–7 days)

| ID | Change | Effort | Depends on |
|---|---|---|---|
| **I2-01** | `PRAGMA user_version` migration runner | M | — |
| **I2-02** | Adoption probes; **ship runner + v1 only, soak for one release** | S | I2-01 |
| **I2-18** | `scripts/db_maintenance.py` (needed **before** any table rewrite) | S | — |
| **I2-08** | The index set as migration v2 | S | I2-01 |
| **I2-11** | `embedding_vector` → BLOB + width/status CHECKs (v3) | M | I2-01, I2-18 |
| **I2-12** | Timestamp normalisation + NOT NULL/CHECK (v4) | M | I2-01 |
| **I2-03** | Explicit legacy column map | S | — (independent) |
| **I2-17** | One path resolver | S | — |
| **I2-05 / I3-06** | Batch backfill commits (batch 200) | S–M | I2-06 preferred |
| **I1-12a** | Extract `message_index/schema.py` | M | — |

**Exit criterion:** schema changes are ordered, atomic and rollback-capable; the untagged
BLOB\|TEXT union is closed by a constraint the database enforces; the two live ordering bugs in
`/pins` and `/unhide` are fixed.

### Phase 4 — Cost accounting (~6–8 days)

| ID | Change | Effort | Depends on |
|---|---|---|---|
| **I4-01** | `api_usage` fact table + `usage_actor` + backfill | L | — |
| **I4-04** | Record usage on failures and retries | S | I4-01 |
| **I4-03** | One metering seam over all 6 SDK entry points | M | I4-01 |
| **I4-02** | Versioned price book, priced at write time | M | I4-01 |
| **I4-05** | Token-budgeted context pack | M | — technically; pointless to tune without I4-01 |
| **I4-07 L2–4** | Budgets, degrade mode, kill switch | M | I4-01, I4-02 |
| **I4-08** | Circuit breaker + per-turn wall clock | S–M | I4-04 |
| **I4-11** | 90-day rollup + prune | S | I4-01 |
| **I4-10** | Rebuild `/usage-report`, `/api-usage`, leaderboard | M | I4-01, I4-02 |
| **I4-12** | `/spend`, structured turn log, `/spend.json`, 5 alerts | M | I4-01/02/07 — **the one tree change; append at the end** |
| **I4-09** | Conditional router + cache sizing | S | I4-03 (to replace the assumed mix with measurements) |

**Exit criterion:** the README's token-accounting claim becomes true; worst-case single-user
hourly exposure is capped roughly 1,600×; retry waste is a visible line item.

### Phase 5 — Configuration schema (~5–7 days)

`I5-01` phases 0→5, with `I5-14` landing **with** phase 4 (not before), then `I5-02`, `I5-06`,
`I5-04`, `I5-10`, `I5-12`, `I5-05`, `I5-13`, `I5-07`, and `I5-08` (the runtime override store,
which supersedes I5-15).

**Exit criterion:** every new setting is one line; 34 previously-accepted extreme values become
boot-time errors with a named key and a fix; `python main.py --check-config` validates offline in
one second.

### Phase 6 — Structural refactors, only if the above landed (~2 weeks)

`I1-50` (the 8-commit dead-code sequence — **after I6-04, because C2-19's degraded-tree pin is
what makes it dangerous**) → `I1-02` → `I1-03` → `I1-13` → `I1-10` → `I1-11` → `I1-04` →
`I1-31` if budget remains. Plus `I3-17` (FTS term cap, the new binding constraint), `I2-10`
(FTS scope column — highest risk, lowest urgency), `I2-15`/`I2-16` (retention and the vector
ceiling), and `I6-09`/`I6-10`/`I6-11`/`I6-12`.

> **Do not start Phase 6 before Phase 1's tests land.** Splitting 400 lines of unpinned
> `on_message` and pipeline behaviour is the single most likely way to turn this document into
> an outage.

### Effort roll-up

| Phase | Contents | Approx. effort |
|---|---|---|
| **0** | 12 independent quick wins | **~1 day** |
| **1** | Performance and durability core | **4–5 days** |
| **2** | Concurrency, write semantics, visibility | **5–6 days** |
| **3** | Migration runner and schema correctness | **5–7 days** |
| **4** | Cost accounting | **6–8 days** |
| **5** | Configuration schema | **5–7 days** |
| **6** | Structural refactors and long-tail | **~2 weeks** |

### Items that are genuinely independent and can ship any time

`I2-09`, `I2-03`, `I2-17`, `I2-18`, `I3-04`, `I3-05`, `I3-08`, `I3-09`, `I3-14`, `I3-15`,
`I3-21`, `I3-22`, `I1-01`, `I1-30`, `I1-41`, `I4-06`, `I4-07 L1`, `I5-03`, `I5-11`, `I5-15`,
`I6-03`, `I6-06`, `I6-07`, `I6-08`.

---

## 13. What not to do

Places where the current simple approach is correct and should be left alone. This section is as
load-bearing as the proposals.

**1. Keep the coordinator layer.** It is the one structural decision in this codebase that
demonstrably bought testability, and it bought **17 % of the test suite** — 21 of 125 tests
instantiate a coordinator directly with no `DiscordBot` anywhere. Collapsing it is cost L, payoff
negative. Delete the 11 dead wrappers (I1-30) and leave the pattern alone.

**2. Do not add a DI container.** ~20 singletons, one deployment target, one composition site, no
plugins, no request scope. The wiring problem here is *ordering and visibility*, not resolution —
and a container makes ordering *worse* by turning 138 readable sequential lines into an implicit
graph.

**3. Do not freeze `BotConfig`.** There is **exactly one** runtime mutation of a `BotConfig` field
in the whole `src/` tree. Threading a `RuntimeSettings` object through everything that reads
`config.*` to protect one boolean is not a good trade.

**4. Do not split `message_index_service.py` four ways.** 40 tests already pin it against real
temp SQLite databases, and the internal clusters share too much state. The one real cleanup — 16
`asyncio.to_thread` passthroughs — is a decorator, not a module split.

**5. `CREATE TABLE IF NOT EXISTS` + `_ensure_column` genuinely works.** It is a valid poor-man's
migration. The payoff of I2-01 is *not* that the current scheme is broken; it is removing the
"remember the second edit" footgun, making schema state visible, and getting an ordered, atomic,
rollback-capable upgrade step. Frame it that way, and do not let the framing slide into "the
current code is wrong".

**6. Keep the self-contained test-file convention.** Every test file readable top-to-bottom
without chasing a fixture; any file deletable without breaking another; an agent editing one file
cannot break twelve. Do **not** add `tests/__init__.py` (measured: changes nothing), a conftest
equivalent (nothing to hook), or a shared fixtures module. Prefer per-file
`make_*(**overrides)` helpers.

**7. Keep `print` in the config-loading path.** AGENTS.md is right: it is deliberately visible
before logging is configured. The new reporter returns a string; the caller prints it.

**8. Keep the PDF executor at `max_workers=1` for now.** Widening it is measured at **1.21x** and
rests on a single negative thread-safety probe. Land I3-08 (11.7x, zero risk) and revisit widening
separately with a proper stress test.

**9. Do not use `to_thread` to hide a CPU bug.** The splitter must be *fixed*, not offloaded —
offloading still burns a pool thread for a minute and leaves the quadratic behaviour in place.

**10. Do not run repo-wide `black` or `ruff format`.** AGENTS.md forbids it and is right: an
unreviewable diff and destroyed `git blame` across 14,697 lines. Check-only, changed-files-only,
correctness rules only.

**11. Do not chase the premature optimizations.** I3-10 (settings TTL cache, 20x but 0.44 ms →
0.022 ms per response), I3-11 (renderer splice, 73x but **5 ms absolute**), I3-24 and I3-25 are
all correct fixes to things that are not hot. Bundle them opportunistically with work already
touching those files; do not schedule them.

**12. Do not remove the router.** It costs 11.4 % of tokens but only 1.8 % of dollars, and
removing it also loses intent detection (`image_generate` / `image_edit`) and `needs_context`
gating, which is what lets RAG skip retrieval entirely on self-contained requests. Those are
functional, not cosmetic. Make it conditional, and make the +51 %-versus-always-low decision
explicit.

**13. Do not delete `message_retrieval_events`.** "Nothing reads it" is true today, and its
columns are exactly what a retrieval-quality dashboard needs tomorrow. Add retention and a
reader.

**14. Do not run `VACUUM` inside the bot.** 3,980 ms blocking on a 394 MB database, and minutes
at the one-year model. It belongs in an offline script.

**15. Do not bound the vector cache by count.** Measured recall@12 falls to 0.483 at half, 0.267
at a quarter, 0.108 at a tenth. Bound by *scope* — evict channels the bot has not been active in
— which is lossless for those channels' queries.

**16. Do not run a Python version matrix in CI.** `start.sh` refuses anything but 3.12 and
`constraints.txt` is a 3.12.10 freeze. A matrix would test a configuration the project explicitly
rejects. Use a non-blocking 3.13 canary on `requirements.in` instead.

**17. Do not set an aspirational coverage gate.** Baseline is 37.9 %. Ratchet at 38 %, raise it in
the same PR that raises coverage.

**18. Never run a dead-code scan without the C0 deny-filter.** It flags 254 lines of live
discord.py dispatch, and it will delete the live `image_queue` slash command — 52 lines with
exactly one reference in the entire repo, its own definition — **and no test will fail**, because
`test_command_registration._register()` passes `image_processing_service=None` and pins the
degraded 22-command tree.

---

## Appendix — where the measurements live

All prototypes were built outside the repo tree and the repo was never modified.

| Lane | Prototype root | Key artefacts |
|---|---|---|
| I1 | `/tmp/i1proto` | P1–P6; cumulative dead-code run 19,405 → 17,650, 125/125 green, `compileall` clean |
| I2 | `/tmp/i2lab` | `seed.py` (100k-row / 391 MB copy of the real schema), `eqp.py`/`eqp2.py`/`eqp3.py`, `conn_bench.py`, `lock_bench2.py`, `growth_retention.py`, `offload_bench.py`, `final_design_bench.py`, `migrations_proto.py`, `test_migrations.py` (20/20) |
| I3 | `/tmp/i3-perf` | `corpus.py` (10 deterministic corpora), `fast_splitter_final.py`, `ablate.py`, `final_check.py` (131/131), `bench_vector.py`/`bench_vector2.py`, `bench_db.py`, `bench_loop.py`, `bench_pdf2.py`, `bench_memory.py`, `bench_budget.py`, `decompose.py`, `fts_probe.py` |
| I4 | `/tmp/i4` | `measure.py`, `budget_proto.py`, `final_numbers.py`, `cost_model.py` (contains `USD_PER_MTOK` — **substitute real prices here**), `schema_proto.py`, `calibrate.py` |
| I5 | `/tmp/i5proto` | `lineinfo.py`, `schema_proto.py`, `reporter_proto.py`, `twomodel_proto.py` (12/12), `ops_proto.py`, `check_config_cli.py`, `repro_before.py`, `run_demo.py` |
| I6 | `/tmp/i6` | `tests/test_on_message_flow.py` (13), `tests/test_router_cache.py` (12), `tests/test_context_fallback.py` (9), `tests/test_command_registration_v2.py` (8), `scripts/mutation_check.py` + `mutants.toml`; plus `/tmp/measure_cov.py`, `/tmp/guard_demo.py`, `/tmp/flaky_fix.py` |

Final consolidated state of `/tmp/i6`: **167 tests OK in 1.256 s; 5/5 mutants matched
expectations.** Real repo: `git status --porcelain` empty, verified before and after every lane.
