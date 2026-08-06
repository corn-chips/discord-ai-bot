# Bug analysis — discord-ai-bot

| | |
|---|---|
| Repository | `<repo-root>` (remote `github.com/corn-chips/discord-ai-bot`) |
| Branch | `dev` |
| HEAD | `c83f7402e182d474586e5f635e6e0ae55a26ed39` ("added agent.md", 2026-07-28), identical to `origin/dev` |
| Analysis date | 2026-07-29 |
| Test baseline | 125 tests, OK — re-confirmed 7 consecutive times, 0 flakes |
| Repo mutation during analysis | none (`git status --porcelain` empty at start and end of every agent run) |

## 1. Scope, method and headline counts

### Scope

Everything reachable from `main.py`: the Discord orchestration layer (`src/bot/`), the service
layer (`src/services/`), models (`src/models/`), utilities (`src/utils/`), configuration
(`config.yaml`, `src/config.py`, `src/config_helpers.py`), the two SQLite databases, the report
web server, the test suite, the packaging/bootstrap scripts, the dependency manifests, and the
two historical documents in `docs/`. Static reading plus executed probes against the real code;
no live Discord gateway and no real Gemini credentials were used (see
[Coverage and limits](#9-coverage-and-limits)).

### Method

Roughly 25 agents, each starting from a fresh context and sharing nothing with the others,
produced 29 reports totalling ~26,700 lines. The structure was:

| Phase | Agents | Output |
|---|---|---|
| Subsystem maps | 7 | `discord-orchestration-layer-map`, `command-layer-analysis`, `ai-model-service-layer-analysis`, `rag_subsystem_analysis`, `config-and-token-db-audit`, `render-util-layer-audit`, `test-suite-and-tooling-audit`, plus `feature-inventory` |
| Bug lanes | 8 | B1 orchestration, B2 concurrency, B3 Gemini, B4 RAG, B5 config/persistence, B6 rendering, B7 security, B8 observability |
| Cross-cutting sweeps | 7 | C1 model precedence, C2 failure injection, C3 cold start, C4 adversarial inputs, C5 performance, C6 docs/dead code/deps, C7 historical claims |
| Improvement lanes | 6 | I1 architecture, I2 data layer, I3 performance, I4 cost accounting, I5 config, I6 testing/DX (prototypes, not findings) |
| Verification | 4 | V1 and V2 independently re-derived 20 claimed-S1 findings; V3 reconciled the whole corpus; V4 verified worktree integrity and the test baseline |

V3 deduplicated 311 raw finding IDs into **211 canonical findings** (`DAB-001` … `DAB-211`),
resolving 40 duplicate clusters and adjudicating 14 factual contradictions against the source
tree. V1 and V2 were run blind: neither read any prior report, both located every line reference
themselves, and both were free to refute. Where a blind verifier contradicted a lane, the
verifier wins on fact; that is recorded per finding and collected in
[Refuted and overstated claims](#7-refuted-and-overstated-claims).

### Headline counts

| | |
|---|---|
| Raw finding IDs across the 8 lanes + 7 sweeps | 311 |
| **Canonical findings after dedup** | **211** |
| Raw IDs folded in as duplicates or co-reports | 100 (32%) |
| Duplicate clusters | 40 |
| Contradictions adjudicated (10 dissolved, 4 genuine) | 14 |
| **S1** | **9** |
| **S2** | **72** |
| S3 | 82 |
| S4 | 43 |
| Informational (negative findings, refutations, 1 duplicate marker) | 5 |
| Canonical findings that carried an S1 claim from at least one lane | 25 |
| …of those, demoted below S1 by the recalibration pass | 17 |
| …promoted **into** S1 (DAB-001) | 1 |
| Features enumerated by the inventory | 137 (its own header says 131 — stale, see §9) |

---

## 2. How to read this

### Severity scale

Severity is **not** the lanes' original number. Every lane calibrated in isolation and it showed:
one lane rated a memory leak S2 and a message-loss bug S1 on the same page, another rated five
performance findings S1, a third rated a gracefully-degrading render failure S1. A single scale
was applied to all of them afterwards, under a single stated threat model.

| Sev | Definition |
|---|---|
| **S1** | Permanent silent data loss; total silent outage of a headline feature; a one-click irreversible destructive action available to an unprivileged user; or a stop-the-world stall of the whole bot reachable from ordinary traffic. |
| **S2** | User-visible wrong output or wrong behaviour on a realistic trigger; unrecoverable-but-loud data loss; unintended disclosure outside the guild; meaningful unbudgeted cost; or a resource blowup that degrades the host. |
| **S3** | Nuisance, ops friction, cost amplification an operator would notice, narrow disclosure inside the guild, or a latent defect needing an unusual configuration. |
| **S4** | Hygiene, dead code, cosmetics, and defects with no reachable trigger today. |

### Confidence scale

| Confidence | Meaning |
|---|---|
| **VERIFIED** | Reproduced with a probe that ran against this tree — a script, a profile, a mutation test, or a runtime introspection of the real object graph. |
| **SUSPECTED** | Reasoned from the source and consistent with it, but not executed end to end. |
| **THEORETICAL** | Needs a live Discord gateway, live Gemini credentials, or a multi-guild deployment to settle. |

The corpus is unusually probe-heavy: only four canonical entries are not VERIFIED by their owning
lane, and one of those (the duplicate-delivery half of DAB-001) was upgraded to VERIFIED when an
improvement lane wrote a failing test for it. Findings that remain THEORETICAL are listed in §9.

### Threat model (this is what sets security severity)

The deployment this bot is built for: **self-hosted** (`start.sh`, `.env`, SQLite files under
`data/`), typically **one guild or a handful**, run by a **hobbyist operator** who is also a guild
admin and who pays the Gemini bill personally. Guild members are known to the operator — "any
guild member" is a friend, not an adversary. The report web server binds **loopback by default**
(`config.yaml:16`, `web_host: "127.0.0.1"`). There is no multi-tenant SLA, no on-call rotation,
no backup, and no security team.

What that model changes, applied consistently:

- **Authorization findings lose a level.** "Any member can flip a setting" is a nuisance among
  three friends. It stays serious only when the action is destructive, irreversible, or
  exfiltrates data outside the guild.
- **Data-loss findings gain a level.** No backup, no replica, no DBA, and — per the repo's own
  `AGENTS.md` — "a broken RAG change looks like nothing happened". A silently dropped write is
  permanent and unnoticed.
- **Cost findings gain weight.** Duplicate model calls and unmetered call sites come out of one
  person's pocket and there is no budget alarm anywhere in the system.
- **Performance findings lose weight unless they block the event loop.** A 30 ms query at a
  hobbyist's corpus size is irrelevant; a 20-second synchronous stall that drops the gateway
  heartbeat is not.
- **Startup-loud findings lose a level.** Anything that crashes before the banner is a
  five-minute diagnosis, not an outage.

### Severities were recalibrated, and most S1 claims did not survive

State this plainly: the lanes over-claimed S1. **25 canonical findings carried an S1 claim from
at least one lane. The recalibration pass demoted 17 of them** and promoted one finding into S1
that no lane had rated above S2, giving the 9 in §4. Separately, two blind verifiers re-derived
20 claimed-S1 findings from scratch. Of those 20: **one was refuted outright, seven came back
OVERSTATED, twelve came back CONFIRMED — and 18 of the 20 were assigned a lower severity than
claimed. Exactly one (DAB-141) was left at S1** by its verifier. Every one of those judgements is
recorded in §7 and cross-referenced from the finding it touches. Where V3's register and a blind
verifier disagree on severity, both numbers are printed; the register number orders the document,
the verifier number is the one to trust when they conflict on a matter of fact (an unreachable
precondition, a wrong count, a missing mitigation).

### Reading the tables

- `DAB-nnn` is the canonical id. "Merged raw IDs" lists the lane findings folded into it — one
  fix closes all of them.
- Every finding carries a location. 179 of the 211 give a specific line or line range; the other
  32 name a module, a symbol, or a file set — those are mostly the cross-cutting ones whose whole
  point is that the defect is repeated (`DAB-096` nine sync-SQLite call sites across four services,
  `DAB-169` 39 of 237 `except` handlers, `DAB-194` the dead-code inventory).
- Line numbers are as of `c83f740`. Paths in the §5 and §6 tables are recorded as the owning lane
  wrote them — usually a bare module name, which resolves under `src/bot/`, `src/services/`,
  `src/utils/` or `src/`. §4 gives full paths for the nine S1 findings.

---

## 3. Executive summary

### The nine S1 findings

1. **DAB-001 — the hybrid-RAG fallback `try` also wraps generation and delivery**
   (`discord_bot.py:911-940`). A delivery-time exception is misread as "hybrid RAG failed", so the
   bot collects legacy context and generates *and sends* a second full response: duplicate reply,
   duplicate Gemini bill, duplicate RAG write. Hoist `_generate_and_send_response` out of the
   `try` and gate on `rag_context is not None`; a 9-test file exists that fails against unmodified
   code (`AssertionError: 2 != 1`) and passes after the 6-line change. Fix this first — it is also
   why a completely dead RAG stack stays invisible.

2. **DAB-002 — `on_ready` swallows a `setup_commands` failure** (`discord_bot.py:530-546`). An
   exception in any of the ten registrars leaves live mode, per-channel personality and per-user
   preferences inert — until a restart, or until a reconnect for a transient cause — while the bot
   logs `Bot is ready` and reports healthy; a probe
   that broke registrar 7 of 10 lost 14 of 22 top-level commands with one mislabelled ERROR line
   as the only signal. Wrap each registrar individually, build the three late-attached services in
   `__init__` next to `PinService` (`:364`), and surface the degraded state in the presence badge
   and `/config info`.

3. **DAB-019 — a live-mode batch is destroyed when `process_messages` raises**
   (`live_message_coordinator.py:118-137`). The worker pops the whole batch at `:119` and the bare
   `except` at `:127-137` logs and moves on with no requeue and no user-facing error; 25 queued
   messages died to one simulated 503. Requeue at the head under the lock with a bounded attempt
   counter — the same file already does exactly that at `:216-220` for the attachment split.

4. **DAB-039 — the response-text fallback can post the model's chain-of-thought as the answer**
   (`gemini_client.py:1529-1555`). The fallback loop returns the first part with `.text` and never
   checks `part.thought`, which is precisely the case in which the SDK's own `.text` returns
   `None`. Add `if getattr(part, "thought", False): continue`. Note the blind verifier's
   refutation: the trigger is unreachable at HEAD because nothing sets `include_thoughts`, which
   makes this a two-line guard against a latent trap rather than a live leak.

5. **DAB-065 — a transient DB error during a message edit permanently tombstones the message**
   (`message_index_service.py:449-463,548-550`; `rag_event_coordinator.py:75-80`). `upsert_message`
   returns a silent `False` for a locked database, the edit handler reads that as "no longer
   eligible" and calls `mark_deleted`, and nothing in the codebase ever clears `deleted_at`. Make
   write failures distinguishable from ineligibility, and add `deleted_at = NULL` to the
   `ON CONFLICT` list; the SQLite pragma work (DAB-095) removes most of the trigger.

6. **DAB-066 — the legacy RAG migration can discard every row and then record success**
   (`message_index_service.py:642-659,685-688`). Column-intersection + `INSERT OR IGNORE` means a
   schema-drifted legacy database migrates 5,000 rows to 0, writes `legacy_shared_rag_v1` to the
   ledger, logs "Copied legacy message RAG data", and can never retry. Compare row counts and
   refuse the ledger write on a mismatch. The blind verifier established that no database this
   git history could have produced actually triggers it — treat it as a loaded gun aimed at the
   next `NOT NULL` column, not as a present-tense loss.

7. **DAB-115 — `split_message` is O(n²) and runs on the event loop**
   (`markdown_utils.py:295-320` driven from `message_splitter.py:203-274`, called synchronously at
   `response_delivery.py:281`). A 120 KB code-heavy reply blocks the entire bot for **20.4 s**
   (measured three times independently: 20.49 s, 20.38 s, 18.7 s), with a measured heartbeat gap of
   20.36 s against a 50 ms target. Hoist the fence-boundary computation out of the per-candidate
   loop — a prototype does 234 KB in 0.055 s instead of 59.96 s with 131/131 outputs byte-identical.

8. **DAB-141 — `/rag delete scope:all` is ungated, unconfirmed and cross-guild**
   (`research.py:242-289` → `message_index_service.py:883-912`). Any member, including via DM, can
   run six unqualified `DELETE`s that wipe the index, the embeddings, the FTS table and
   **`pinned_messages`** — human-authored memories with no upstream source of truth — across every
   guild. Add `default_permissions`, a guild predicate, and a confirmation view. This is the one
   authorization finding that survives the hobbyist threat model at S1, and the only one of the 20
   independently re-derived claims that its verifier left at S1.

9. **DAB-198 — a 3.6 KB PDF costs 105 s and 1.63 GB and returns nothing**
   (`discord_bot.py:1152-1200`). An 8000×8000 pt MediaBox at `pdf_render_scale: 2.0` asks MuPDF for
   a 256-megapixel pixmap per page; the per-page `except … continue` swallows the failure and pays
   the cost 20 times on the single PDF worker, with no timeout anywhere on that path. Cap total
   page area before `get_pixmap`, wrap the executor call in `asyncio.wait_for`, and stop iterating
   after N consecutive page failures.

### What kinds of defect dominate, and what that implies

**Silent degradation is the house style, and it is the single largest theme in the corpus.**
39 of 237 `except` handlers (16%) are invisible at the shipped `log_level: INFO` (DAB-169). The
same defect appears at every layer: a write failure returns `False` (DAB-065), a migration logs
success after copying nothing (DAB-066), `get_status` returns an all-zeros dict on a DB error so
`/rag status` shows a healthy empty index (DAB-170), live-mode RAG failure is logged at DEBUG
while the identical mention-path failure is a WARNING (DAB-168), and the one lever an operator has
to turn DEBUG on — `/config debug` — is a no-op because handler levels are pinned at startup
(DAB-166). A `%` character in any structured-logging extra silently deletes the whole record from
the log file (DAB-165). The compounding effect is what makes this a strategic problem rather than
a list of small ones: **the system is designed so that its own failures are unobservable**, which
is why the corpus repeatedly found bugs that had been live for months. Investment here is cheap
and unblocks everything else — fix the logging pipeline before trusting any other instrumentation.

**Unbounded state is the second theme.** Six get-or-create containers never evict and are not
cleared in `close()` (DAB-029; one of them retains raw input and output image bytes for 100 jobs,
measured at 699.5 MiB). Three tables grow forever with no retention: `token_usage` (~219 MB/yr,
DAB-104), `message_retrieval_events` (~402 MB/yr, written from three sites and read by nothing,
DAB-086), and embedding rows for deleted messages (DAB-085). The live queue and the pin store are
uncapped, so a single 100 KB pin is injected verbatim into every prompt forever (DAB-073) and a
1.5 M-character prompt is constructible (DAB-021). None of these has an eviction policy because
none was ever specified; one policy decision closes most of them.

**Event-loop blocking is the third, and it is the only performance class that matters at this
deployment size.** Message splitting (20.4 s, DAB-115), matplotlib rendering (DAB-129), PIL PNG
encoding of up to 26 images per request (2.13 s, DAB-138), the startup embedding reconcile
(2.6 s = 97.8% of `DiscordBot.__init__`, DAB-077), the rate limiter's O(users) rebuild per call
(DAB-032), and nine synchronous SQLite call sites including one on every single inbound message
(DAB-035, DAB-096) all run on the loop. Under lock contention with no WAL, a single `get_live_enabled`
freezes the whole bot for 5.01 s. Everything else the corpus measured — 30 ms queries, 586 MiB
matrix copies — is real but only bites at corpus sizes this deployment will not reach.

**Missing authorization is a shape, not a list.** Exactly **1 of 35 command nodes** carries any
gate (`/clear-cache`, which clears an in-memory cache), all 35 are DM-invocable, and `/dev`'s
`@app_commands.default_permissions(administrator=True)` is commented out at `configuration.py:352`
while its docstring at `:354` still says "(admin only)" and its log line calls every invoker
"Admin". Under the hobbyist model most of that is a nuisance, which is why 4 of the 5 security
findings the lanes filed as S1 are S2 here. The exceptions are worth separating out: the
destructive one (DAB-141) and the disclosure ones that reach outside the guild (DAB-143,
DAB-147, DAB-157).

**There is no cost accounting at all, and the operator pays personally.** Thinking tokens are
never billed (`thoughts_token_count` is read nowhere, DAB-048); usage is attached only to
*successful* responses so every retry and failure is unrecorded (DAB-049); five to six Gemini call
sites — router, context selector, embeddings, image — bypass accounting entirely (DAB-050); a
whole usage event is discarded when the API's reported total is less than input+output (DAB-105);
and `token_usage` has no model column and no price book, so spend cannot be reconstructed after
the fact even from what *is* recorded (DAB-102). Combined with DAB-001 (duplicate paid generation
on a delivery failure) and DAB-042 (no whole-sequence deadline: one message can occupy the bot for
488 s of paid retries), the honest summary is that **the operator cannot currently tell what the
bot costs or why**. A small fact table plus a versioned price list closes all six.

**Where to invest.** The cheap, high-yield block is: fix the observability pipeline (DAB-165,
DAB-166, DAB-168, DAB-169), make writes fail loudly (DAB-065, DAB-095, DAB-096), and take the six
XS one-file fixes that close three S1s (DAB-001, DAB-141, DAB-039) plus the LaTeX one-keyword fix
(DAB-114) and the two-index/one-fingerprint startup fix (DAB-077/DAB-078). The expensive,
deferrable block is the architectural work: a migration runner with `PRAGMA user_version`, a
pydantic config schema, an `api_usage` fact table, and the vector-store rewrite. Do not start the
dead-code programme (DAB-194, −2,027 lines, proven green twice) until DAB-203 is fixed: the
command-registration test pins the *degraded* 22-command tree, so a reference-counting pass will
happily delete the live `/image-queue` command and no test will fail.

---

## 4. The S1 findings

Each entry gives the canonical id, the recalibrated severity, the blind-verifier severity where
one exists, the confidence, the mechanism as a causal chain, the trigger, the observable symptom,
measured evidence, the fix, a regression test, and the raw lane IDs merged into it.

Summary table first:

| ID | Title | Sev | Blind verifier | Confidence | File:line |
|---|---|---|---|---|---|
| DAB-001 | Hybrid-RAG fallback `try` spans generation and delivery | S1 | not in the V1/V2 batches | VERIFIED (failing test) | `discord_bot.py:911-940` |
| DAB-002 | `on_ready` swallows `setup_commands` failure | S1 | V2: **S2** | VERIFIED (2 probes) | `discord_bot.py:530-546`; `commands.py:73-91` |
| DAB-019 | Live batch destroyed when `process_messages` raises | S1 | V1: **S3** | VERIFIED (2 probes) | `live_message_coordinator.py:118-137` |
| DAB-039 | Chain-of-thought returned as the answer | S1 | V1: **S4 latent** | VERIFIED mechanism / trigger unreachable | `gemini_client.py:1529-1555` |
| DAB-065 | Transient DB error permanently tombstones a message | S1 | V1: **S2** | VERIFIED (2 probes) | `message_index_service.py:449-463,548-550` |
| DAB-066 | Legacy migration discards all rows, records success | S1 | V1: **S3** (latent S1) | VERIFIED mechanism / precondition unreachable | `message_index_service.py:642-659,685-688` |
| DAB-115 | `split_message` O(n²) on the event loop | S1 | V2: **S2** trending S1 | VERIFIED (3 independent profiles) | `markdown_utils.py:295-320`; `message_splitter.py:203-274`; `response_delivery.py:281` |
| DAB-141 | `/rag delete scope:all` ungated, cross-guild, irreversible | S1 | V1: **S1** (upheld) | VERIFIED (runtime tree probe) | `research.py:242-289`; `message_index_service.py:883-912` |
| DAB-198 | PDF resource bomb: 105 s / 1.63 GB from 3.6 KB | S1 | V2: **S2** (S1 under 2 GB RAM) | VERIFIED (2 independent probes) | `discord_bot.py:1152-1200` |

Read that "Blind verifier" column carefully. Two of the nine (DAB-001, DAB-141) are uncontested.
For the other seven the verifier who re-derived the claim from scratch, without seeing the
original reasoning, assigned a lower severity — in two cases (DAB-039, DAB-066) because the
trigger or the precondition does not exist at HEAD. The mechanisms are all real and all
reproduced; the disagreement is about how much they currently cost. §7 gives each argument in
full.

---

### DAB-001 — Hybrid-RAG fallback `try` spans response generation *and* delivery

| | |
|---|---|
| Severity | **S1** (promoted from S3 / S2 — the only promotion into S1) |
| Confidence | **VERIFIED** — reproduced by a failing test against unmodified code |
| Affected feature | Every mention-triggered response while `rag_enabled` is true (the default) |
| File:line | `src/bot/discord_bot.py:911-940` (try at `:913`, `_generate_and_send_response` at `:922-933`, `return` at `:934`, handler at `:935-940`); legacy path's extra LLM call at `:1016`; the coordinator's blanket sink at `src/bot/response_generation.py:486` |
| Merged raw IDs | `B1-01` (S3, "S2 latent"), `B8-06` (S2, structure VERIFIED / double-send SUSPECTED), reproduced by `I6-03` |

**Mechanism.** The `try` at `:913` exists to provide the documented legacy `ContextCollector`
fallback when hybrid retrieval fails. It also encloses `await self._generate_and_send_response(...)`
at `:922-933`. That call is not a pure computation: it sends the status message, calls Gemini, and
delivers the reply. Any `Exception` escaping it is therefore misread as "hybrid RAG failed". The
handler at `:935` logs one WARNING, collects legacy context — which costs an additional
`select_relevant_context` LLM call at `:1016` — and calls `_generate_and_send_response` a **second
time** with the same message. Nothing records that the first attempt already produced side effects.

**Trigger conditions.** Any `Exception` (not `BaseException`) escaping
`ResponseGenerationCoordinator.generate_and_send_response`. B1's probe 5 mapped exactly what
escapes today: the coordinator's `except Exception` at `response_generation.py:486` is a total
sink for anything raised after the status message is sent, but
`discord.abc.Messageable.typing().__aenter__` awaits `http.send_typing`, and non-`discord` network
errors from there — `aiohttp.ClientOSError` (an `OSError`) and `aiohttp.ServerDisconnectedError`
(a `ClientError`, neither retried nor an `OSError`) — match neither outer handler (`:494`
`Forbidden`, `:509` `HTTPException`) and escape. `CancelledError` escapes too but is a
`BaseException`, so it does not trigger the fallback.

**Observable symptom.** Two full generation cycles for one user message: two status messages, two
Gemini calls, two replies, two RAG index writes, and a `WARNING Hybrid RAG failed for message …`
that names RAG for a failure that had nothing to do with RAG. That last part matters on its own:
this log line is the only RAG-health signal an operator has, and it has a large false-positive
surface.

**Evidence.**

- B1 probe: `generate_and_send_response call count: 2`; deliveries `REPLY#1 ctx=['RAG_CTX']`,
  `REPLY#2 ctx=[namespace(message_id=1, content='legacy')]`.
- I6's test `test_a_failure_after_delivery_is_not_retried_through_the_fallback` fails against the
  unmodified tree with `AssertionError: 2 != 1 : a delivery failure must not be retried through
  the legacy path`, while all 125 existing tests stay green — an A/B the improvement lane ran
  explicitly. The 9-test file passes after a 6-line fix.
- Three lanes found this defect; two hedged (B1 called the user-visible double reply "partially
  REFUTED" for the current tree because of the `:486` sink, B8 marked it SUSPECTED). Only the lane
  that wrote a reproduction settled it. That is the strongest single argument in this corpus for
  preferring a probe to a read.

**Recommended fix** (6 lines, verified):

    if self.config.rag_enabled and routed_intent != "image_generate":
        rag_context = None
        try:
            rag_context = await self.hybrid_context_retriever.retrieve(...)
        except Exception as exc:
            logger.warning("Hybrid RAG failed for message %s; using legacy "
                           "context fallback: %s", message.id, exc)
        if rag_context is not None:
            await self._generate_and_send_response(...)
            return

`rag_context is not None` rather than truthiness is load-bearing: an empty retrieval is a
*successful* retrieval and must not fall back. Separately, widen the handlers at
`response_generation.py:494-516` to `except Exception`, or acquire `typing()` outside them, so
aiohttp-level errors are reported once instead of silently re-running.

**Suggested regression test.** `tests/test_context_fallback.py` (the I6 prototype): stub
`hybrid_context_retriever.retrieve` to succeed and `_generate_and_send_response` to raise on the
first call; assert `_generate_and_send_response` was awaited exactly once and
`context_collector.get_channel_context` never was. Pair it with an `assertLogs(..., WARNING)`
assertion that the fallback is *audible* and names the cause — that log line is the only signal
the operator gets.

---

### DAB-002 — `on_ready` swallows a `setup_commands` failure, leaving three features silently dead

| | |
|---|---|
| Severity | **S1** · blind verifier V2: **S2** |
| Confidence | **VERIFIED** — two independent probes |
| Affected feature | `/live` mention-free mode, per-channel personality, per-user model and language preferences, `/hide` and `/unhide`, and most of the command tree |
| File:line | swallow at `src/bot/discord_bot.py:530-546` (`except` at `:541`); ordering at `src/bot/commands.py:73-91`; attach sites at `src/bot/command_modules/personalization.py:43,146,380`; consumption at `discord_bot.py:705-708` and `:1089-1090` |
| Merged raw IDs | `B1-02` (S2), `C2-06` (S1) |

**Mechanism.** `register_personalization_commands` is the last of ten registrars
(`commands.py:91`) and the only place that attaches `_channel_settings_service`,
`_user_prefs_service` and `_message_visibility_service` to the bot. Any earlier registrar that
raises aborts `setup_commands`. `on_ready` catches at `:541`, writes one `logger.error("Failed to
sync slash commands: …")` — mislabelling a *registration* failure as a *sync* failure — then falls
straight through to `_start_automatic_rag_backlog(self)` at `:544` and
`logger.info("Bot is ready and listening for mentions!")` at `:546`. Absence then reads as "off",
silently: `_is_live_mode_enabled` short-circuits to `False` for every channel (`:705-708`),
`_get_personality_prompt` returns `None`, and `_resolve_request_preferences` skips the user's model
and language (`:1089-1090`).

Note that `_pin_service` is **not** in this set: it is built in `__init__` at `discord_bot.py:364`
and merely re-bound at `personalization.py:138-142`. `AGENTS.md` says otherwise and is wrong; six
other documents in the corpus agree with the code. Pinned memories therefore keep working through
this failure while the other three features do not.

**Trigger conditions.** An exception in any of `register_ping_command`, `register_report_commands`,
`create_config_group`, `register_statistics_commands`, `register_feature_commands`,
`register_admin_commands`, `register_usage_commands`, `register_deepresearch_command`,
`create_rag_group`, `bot.tree.add_command`, or `register_summarize_command`. Realistic causes: a
jinja2 template error in the report/usage registrars, a duplicate command name after a refactor, a
`sqlite3.OperationalError` from a service constructed inside a registrar, or `app_commands`
rejecting a description longer than 100 characters.

**Observable symptom.** The bot looks healthy — presence set, `Bot is ready` logged, `/config
info` and `/status` both hard-code `gemini_client: available` (DAB-015). Live channels go quiet,
personalities have no effect, preferences are ignored, and most slash commands are simply absent.

**Evidence.**

- B1 probe (break `register_usage_commands`): all three service attributes `False`,
  `_is_live_mode_enabled(bot, 123) -> False`, 8 of 22 top-level commands registered.
- V2 probe (break registrar 7 of 10, run blind): `commands registered: 8` versus 22 on the happy
  path; all four service attributes `False`; live mode `False`; operator-visible log output is
  exactly two lines — `ERROR … Failed to sync slash commands: simulated registrar bug` followed by
  `INFO … Bot is ready and listening for mentions!`. V2 adds that the claim *understates* the
  blast radius: **14 of 22 top-level commands** silently vanish, which is a larger user-visible
  loss than the services.

**Verifier correction (V2, S2).** "Permanently for the process lifetime" is imprecise: discord.py
re-fires `on_ready` on every re-IDENTIFY, so `setup_commands` is retried on reconnect. For a
deterministic bug (a bad decorator, a typo — the realistic case) every retry fails identically and
it is effectively permanent; for a transient cause (one of the four `_ensure_table()` calls hitting
a locked SQLite file, 5 s timeout) a later reconnect self-heals. V2 rates it S2 because there is no
data loss and no crash — just a silent, mislabelled, fail-open degradation.

**Recommended fix.** Three independent changes, all small:
1. Wrap each registrar in its own `try` so one failure costs one command group, not the tail of
   the list.
2. Construct `ChannelSettingsService`, `UserPreferencesService` and `MessageVisibilityService` in
   `DiscordBot.__init__` next to `PinService` (`:364`) and have the registrar consume them with
   `getattr(bot, ..., None) or ...`, exactly as it already does for `_pin_service`. This removes
   the coupling between command registration and runtime capability, and closes the pre-`on_ready`
   window (DAB-014) at the same time. An improvement-lane prototype did this and stayed 125/125.
3. Make the failure loud: set `self._command_setup_failed = True`, surface it in
   `get_service_health_status()` (`:582-622`) and in the presence badge, and replace the
   unconditional success banner with a degraded-mode warning.

**Suggested regression test.** Extend `tests/test_command_registration.py` from "services attached
on the happy path" to "services attached regardless of registration outcome": patch a mid-list
registrar in `src.bot.commands` to raise, run the `on_ready` path, and assert the three services
are present and that the health status reports degraded.

---

### DAB-019 — A live-mode batch is silently destroyed when `process_messages` raises

| | |
|---|---|
| Severity | **S1** · blind verifier V1: **S3** |
| Confidence | **VERIFIED** — two independent probes |
| Affected feature | Live mode (`/live`), the flagship mention-free path |
| File:line | `src/bot/live_message_coordinator.py:118-137` — pop at `:119`, bare `except` at `:127-137`; the correct requeue idiom is in the same file at `:216-220`; `finally` at `:146-155` |
| Merged raw IDs | `B2-02` (S1); `C7-F5` is the same defect re-found by the historical sweep (register entry `DAB-208`, marked DUP) |

**Mechanism.** The worker pops the entire pending batch out of the dict *before* processing it:

    async with lock:
        pending_messages = self.pending_messages.pop(channel_id, [])
    ...
    try:
        response_sent = await self.process_messages(pending_messages)
    except Exception as exc:
        logger.error("Live worker error in channel %s ...")   # nothing else

There is no requeue, no retry counter, and no user-facing error. After `:119` the batch is
unreachable. `process_messages` covers the whole expensive path — rate limiting, RAG retrieval,
the Gemini call at `:309`, token recording, delivery at `:330` — so the loss window spans the
entire multi-second request. The asymmetry is telling: live mode *has* an error callback
(`handle_response_error`) but it is invoked only when `api_response.success` is `False`
(`:320-324`), never for an exception. And the author clearly knew the requeue idiom: it is used
correctly 80 lines later when a batch is deliberately split at an attachment (`:216-220`).

**Trigger conditions.** Any exception on the live path: a Gemini 5xx or timeout that survives the
client's own retries, a `discord.HTTPException` while sending, or a `RuntimeError` from a missing
injected callback (`:308`, `:322`, `:329` raise these by design when the bot is partially
constructed).

**Observable symptom.** In a busy live channel the bot simply does not answer, and the messages
queued behind the failure never get an answer either. To the users it looks like they were ignored.

**Evidence.**

- B2 probe: 25 messages enqueued, 1 `generate_response` call, `pending after failure: []`, context
  buffer 0 entries, zero replies — plus one log line `Live worker error in channel 9 for messages
  0-24: transient API 503`.
- V1 probe (independent): `batch lost = True`, `user notified = False`, `retried = False`,
  `worker still registered: None`.

**Verifier correction (V1, S3).** Nothing *durable* is lost. The user messages remain in Discord
and were already written to the RAG index at `discord_bot.py:766-775`, which runs **before** the
live-mode fork at `:778-780`; only the reply to that batch is lost. The channel is not left broken
either — the `finally` at `:146-155` deregisters the task and restarts a worker if anything is
pending. And the routine failure modes are already handled *inside* `process_messages` (a
non-successful `APIResponse` goes to `handle_response_error` at `:320-324`; a rate-limited user
gets a reply at `:234`), so reaching the bare `except` requires an unexpected raise. V1 also flags
a related hazard it checked and found currently unreachable: if any injected callback were `None`,
`:308/:322/:329` would raise on **every** batch and live mode would go permanently, silently dead
— the wiring at `discord_bot.py:163-188` uses `getattr(..., None)` against objects that do define
the methods.

**Recommended fix.** On exception, requeue the batch at the head under the lock with a bounded
attempt counter:

    self.pending_messages[cid] = pending_messages + self.pending_messages.get(cid, [])

back off, and after N attempts reply to `messages[-1]` through `handle_response_error` so the user
learns the turn was dropped.

**Suggested regression test.** `IsolatedAsyncioTestCase`: make `generate_response` raise on the
first call and succeed on the second; enqueue 3 messages; assert `generate_response` is called
twice, that the second call's prompt still contains all three messages, and that a reply is
eventually sent.

---

### DAB-039 — The response-text fallback returns chain-of-thought as the answer

| | |
|---|---|
| Severity | **S1** · blind verifier V1: **S4 latent** (becomes S2 the day thought summaries are enabled) |
| Confidence | **VERIFIED** mechanism; **trigger unreachable at HEAD**; the provider-behaviour half is UNPROVABLE-HERE |
| Affected feature | All generation with thinking enabled (all three complexity tiers ship with thinking on) |
| File:line | `src/services/gemini_client.py:1529-1555`, specifically the loop at `:1549-1551`; consumed at `:1105` (finish_reason STOP) and `:1116` (MAX_TOKENS) |
| Merged raw IDs | `B3-17` (S1) |

**Mechanism.** The fallback loop is:

    for part in candidate.content.parts:
        if hasattr(part, 'text') and part.text:
            return part.text          # no part.thought check

The SDK's own `.text` property deliberately skips thought parts
(`if isinstance(part.thought, bool) and part.thought: continue`). So `response.text` is `None`
exactly when the only text present is chain-of-thought — which is precisely the condition in which
this hand-rolled fallback fires. The fallback was presumably written to work around an SDK quirk;
it instead defeats the SDK's redaction.

**Trigger conditions.** Any response whose text parts are all thought parts. The realistic path is
`finish_reason = MAX_TOKENS`, where the thinking budget consumes the whole output cap before an
answer token is emitted — reachable in principle given `max_output_tokens_low: 4096` combined with
DAB-045 (every `/summarize` and live-mode request is pinned to the low tier).

**Observable symptom.** The bot posts the model's internal reasoning to the channel under its
normal model header; in the MAX_TOKENS case it is framed as a legitimate answer with the
truncation footer. Given that this bot's system prompts instruct the model to reason about the
user's intent and about refusals, the leaked text can include the model's assessment of the user.

**Evidence.**

- B3 probes with genuine `google.genai.types` objects: SDK `response.text` → `None`;
  `_get_response_text(response)` → `'The user is asking about X. I should first check whether they
  are trying to jailbreak me… internal chain of thought …'`, delivered under
  `[Model: … + Thinking:High]`. The MAX_TOKENS variant is delivered with the "may have been
  truncated" footer and `TokenUsage(input_tokens=800, output_tokens=0, total_tokens=4896)` — the
  same root cause as DAB-048.
- V1 reproduced the asymmetry independently: `SDK response.text -> None`,
  `_get_response_text -> '<thought text>'`.

**Verifier refutation (V1, S4).** The trigger does not exist at HEAD. Gemini returns thought parts
only when `ThinkingConfig.include_thoughts` is true; a repo-wide grep for
`include_thoughts|thoughts` (excluding `.venv`) returns **zero hits**, and
`_build_thinking_config_for_model` (`gemini_client.py:575-620`) only ever sets `thinking_level=` or
`thinking_budget=`, with `types.ThinkingConfig()` defaulting `include_thoughts=None`. With no
thought parts in the payload, `response.text` and the fallback agree, and when the model burns its
budget the parts list is empty and the function correctly returns `None` → `build_error_response
("max_tokens", …)` at `:1132-1135`. V1 states the honest caveat: "the API never returns thought
parts unless `include_thoughts=True`" was verified against the installed SDK's `_get_text` contract
and documented semantics, not against a live call, so that sub-point is UNPROVABLE-HERE and the
downgrade rests on it.

**Recommended fix.** Two lines:

    for part in candidate.content.parts:
        if getattr(part, "thought", False):
            continue
        if getattr(part, "text", None):
            return part.text

Better still, delete the fallback and trust `response.text`; if it is `None` that is a genuine
empty response and the caller's existing handling is correct.

**Suggested regression test.** Build a real `types.GenerateContentResponse` whose only part has
`thought=True` and non-empty `text`; assert `_get_response_text` returns `None` and that the
pipeline produces the `max_tokens` error path rather than delivering the thought text. A second
case with one thought part followed by one answer part must return the answer.

---

### DAB-065 — A transient DB error during a message edit permanently tombstones the message

| | |
|---|---|
| Severity | **S1** · blind verifier V1: **S2** ("the closest of the ten to S1") |
| Confidence | **VERIFIED** — two independent probes |
| Affected feature | Every RAG retrieval path (recent, lexical, semantic, reply anchors) and embedding |
| File:line | `src/services/message_index_service.py:449-463` (the `ON CONFLICT` list omits `deleted_at`), `:548-550` (blanket `except → return False`), `:542` (`embedding_status` forced to `skipped`), `:1023-1042` (`mark_deleted`); `src/bot/rag_event_coordinator.py:75-80` |
| Merged raw IDs | `B4-01` (S1) |

**Mechanism.** Three independent decisions compose into unrecoverable loss:

1. `upsert_message` wraps its whole body in `try/except Exception → logger.error(...); return False`
   (`:548-550`). A `sqlite3.OperationalError("database is locked")` is indistinguishable from
   "this message is not indexable".
2. `RagEventCoordinator.handle_message_edit` reads that falsy return as "no longer eligible" and
   tombstones: `if not indexed: await self.message_index_service.mark_deleted_async(after.id)`
   (`:79-80`).
3. `mark_deleted` sets **both** `deleted_at` and `hidden = 1`, and the `ON CONFLICT(message_id) DO
   UPDATE` list updates 13 columns but never `deleted_at = NULL`. No public API clears it; only
   `delete_rag_data()`, which wipes the channel or the entire index.

The result is a write-once tombstone set by a *transient* condition — and DAB-076 makes that
condition routine, because the auto-started full-history backlog holds the write lock almost
continuously at boot while DAB-095 leaves the busy timeout at CPython's implicit 5 s with no WAL.

**Trigger conditions.** `rag_enabled: true` (default) plus any Discord `MESSAGE_UPDATE` for an
indexed message while the RAG database is locked for more than 5 s — a backlog pass, a
`delete_rag_data`, a large `/rag pregenerate`, a slow disk, or a permissions blip. Also any other
exception inside `upsert_message` (disk full, corruption).

**Observable symptom.** The message disappears from the bot's memory permanently and silently. The
bot answers "I don't have that in context" about a message plainly visible in the channel. The
logs show one `Failed to index message …` line; `mark_deleted` logs nothing on success.

**Evidence.**

- B4's chain probe: after the edit under a >5 s lock, `hidden=1 deleted_at=SET lexical_hits=[]`
  (the edit handler returned after 6.55 s); after a clean re-index, still `deleted_at=SET`,
  `lexical_hits=[]`; after `mark_hidden(False)`, `hidden=0 deleted_at=SET`, still invisible;
  `search_recent`, `get_by_ids` and `pending embeds` all empty. Only `delete_rag_data()` recovers.
- V1's independent probe injected one `OperationalError` through `:548` and let the DB recover:
  `deleted_at still set after successful reindex = True`, `row in FTS index = 0`,
  `embedding_status = skipped`. V1 notes the claim is slightly *understated*: every subsequent
  successful re-index keeps `content_text` faithfully updated while the row stays excluded from
  both the lexical and the semantic candidate sets, and `/rag backfill` does **not** heal it
  because backfill goes through the same `upsert_message`.

**Verifier severity (V1, S2).** What is lost is derived index state, not user content — the
message is intact in Discord and in `message_index.content_text` — and a destructive repair path
exists. V1 holds S2 while noting the trigger is ordinary rather than exotic: SQLite is opened
per-call with no WAL and the default 5 s busy timeout while writes are fanned across threads by
`asyncio.to_thread`.

**Recommended fix.**
1. Make `upsert_message` distinguish "not indexable" from "write failed" — raise on
   `sqlite3.Error`, or return a tri-state / a dedicated `IndexWriteError`.
2. In `handle_message_edit`, tombstone only when the *content* became ineligible, never on an
   infrastructure error.
3. Add `deleted_at = NULL` to the `ON CONFLICT DO UPDATE` list (a re-index of a live Discord
   message is proof the message exists), or add an explicit `restore()`; the `hidden` carry-forward
   must then stop keying off the tombstone.
4. Stop overloading `hidden` in `mark_deleted`; use `deleted_at` alone so the two flags stay
   independent.
5. Land DAB-095 (WAL + explicit busy timeout) first — it removes most of the trigger.

**Suggested regression test.** `TombstoneRecoveryTest`. Happy path: index → `mark_deleted` →
`upsert_message` with the same id → assert `deleted_at IS NULL` and that `search_recent`,
`search_lexical`, `get_messages_by_ids` and `get_pending_embeddings` all see it again. Error path:
patch `upsert_message` to raise `sqlite3.OperationalError("database is locked")`, drive
`RagEventCoordinator.handle_message_edit`, and assert `mark_deleted_async` was **not** called and
the row is still retrievable.

---

### DAB-066 — The legacy RAG migration silently discards every row, then records success

| | |
|---|---|
| Severity | **S1** · blind verifier V1: **S3** (latent S1 for the next schema change) |
| Confidence | **VERIFIED** mechanism, reproduced verbatim twice; **precondition unreachable in this git history** |
| Affected feature | One-time migration of the shared `token_usage.db` RAG tables into `message_rag.db` |
| File:line | `src/services/message_index_service.py:642-659` (intersection + `INSERT OR IGNORE`), `:685-688` (ledger write), `:628-632` (early-return check), `:689` (the success log); NOT NULL columns at `:140` and `:144` |
| Merged raw IDs | `B4-02` (S1) |

**Mechanism.** The migration copies only the **intersection** of target and legacy columns:

    common_columns = sorted(target_columns & legacy_columns)
    conn.execute(f'INSERT OR IGNORE INTO main."{table_name}" ({columns_sql}) '
                 f'SELECT {columns_sql} FROM legacy."{table_name}"')

`INSERT OR IGNORE` extends conflict resolution to `NOT NULL` violations, so if the legacy schema
lacks `content_hash` or `indexed_at` — both `NOT NULL` with no default in the current
`message_index` — **every row is silently skipped**. The migration then unconditionally writes
`rag_migrations('legacy_shared_rag_v1')` and logs `INFO: Copied legacy message RAG data from
<path>`. The ledger is checked at `:628-632` on every later boot, so the migration is never
retried, and `delete_rag_data()` does not clear the ledger either.

**Trigger conditions.** Any upgrade from a build whose `message_index` lacked `content_hash` or
`indexed_at` (or whose `message_embeddings` lacked `content_hash`). Zero operator visibility.

**Observable symptom.** After the upgrade the bot has no memory of anything said before it.
`/rag status` reports `messages: 0`. The log says the copy succeeded.

**Evidence.**

- B4 probe: `legacy rows to migrate: 5000`, `rows in new RAG db: 0`,
  `ledger says done: [('legacy_shared_rag_v1', …)]`; three consecutive boots, no retry, no error;
  the control case with a matching schema copies correctly.
- V1 reproduced the whole matrix independently: all columns present → 5000 migrated; missing
  `content_hash` → 0 migrated, ledger written; missing `indexed_at` → 0 migrated, ledger written;
  missing a NOT NULL column that *has* a default → 5000 migrated; re-running after the ledger
  exists → 0 rows, retry blocked.

**Verifier refutation (V1, S3).** No legacy database that this codebase could have written can
trigger it. V1 diffed the `message_index` `CREATE TABLE` body at every commit that ever touched the
file (`d923856`, `88e330b`, `5e45599`, `5daa9ea`, `cbf32b6`, `f22c29a`, `784e71e`): the NOT NULL
column set — `channel_id, author_name, is_bot, created_at, indexed_at, content_text,
attachment_summary, content_hash, hidden` — is **byte-identical in all seven revisions**. The only
column ever added is `embedding_eligibility_text TEXT`, nullable, introduced by `784e71e`, which is
the very commit that added this migration. V1 also checked the value-producing sites: `author_name`
(`:341`), `content_hash` (`:419`) and `indexed_at` (`:427`) can never be NULL. So the live data
loss is hypothetical; it converts to a genuine S1 the moment anyone adds a NOT NULL column without
a default — exactly the kind of change `AGENTS.md` instructs contributors to make via
`_ensure_column`. V1's stated limit: it can only speak to what *this* history could have written;
a fork or a hand-edited schema is out of scope.

**Recommended fix.**
1. Drop `OR IGNORE` for anything except genuine primary-key collisions; use
   `ON CONFLICT(message_id) DO NOTHING` so NOT NULL violations raise.
2. Supply values for required target columns the legacy schema lacks — simplest correct approach is
   to select legacy rows into Python and route them through `upsert_message`, which computes
   `content_hash` and `indexed_at` itself.
3. Count rows before and after, compare, and refuse to write the ledger row on a mismatch; log the
   delta at WARNING or ERROR.
4. Make the ledger name include a schema fingerprint so a fixed migration can re-run. (Note
   DAB-083: `rag_migrations` is not owned by `_ensure_schema` and exists on a fresh install only as
   a side effect of `TokenTracker` construction order.)

**Suggested regression test.** `LegacyMigrationTest`. Happy path: legacy DB with the current schema
→ all rows appear, ledger written. Error path: legacy `message_index` without
`content_hash`/`indexed_at` → assert **either** the rows are migrated with a recomputed hash **or**
the migration raises/logs and the ledger row is absent so it retries. Assert `COUNT(*) > 0`; the
current behaviour of `0 rows + ledger written` must fail.

---

### DAB-115 — `split_message` is O(n²) and runs synchronously on the event loop

| | |
|---|---|
| Severity | **S1** · blind verifier V2: **S2** "trending S1 at the top of the range" |
| Confidence | **VERIFIED** — profiled independently three times |
| Affected feature | Every long bot reply |
| File:line | `src/utils/markdown_utils.py:295-320` `is_safe_split_point` (`find_code_block_boundaries(text)` at `:307`, `parse_markdown(text)` at `:313`, early return at `:310`), driven by `src/services/message_splitter.py:203-274`; called synchronously from `src/bot/response_delivery.py:281` |
| Merged raw IDs | `B6-05` (S1), `C5-03` (S1) |

**Mechanism.** `_find_next_split_point` builds candidate break points per 1,950-character window
and, for each candidate, calls `is_safe_split_point(content, pos)` with the **entire** message.
Inside a code fence every candidate returns `False`, so all four tiers exhaust their candidate
lists — roughly 260 full-text scans per window, times `n/1950` windows, giving Θ(n²). Nothing is
offloaded: `grep` for `run_in_executor|to_thread|ThreadPool` across `response_delivery.py`,
`message_splitter.py`, `content_renderer.py` and `markdown_utils.py` returns no matches, while the
codebase uses `asyncio.to_thread` in 20+ places elsewhere. The splitter was simply missed.

**Trigger conditions.** An ordinary long code answer. `config.py:114` sets
`max_output_tokens: 65536`, an envelope of roughly 262 KB of text, and `response_delivery.py:145`
only compares against `min(get_split_length(), 2000)` to *decide* to split — there is no length cap
between the model and the splitter.

**Observable symptom.** The whole single-process bot stops: no other channel, no other command, no
heartbeat, no shutdown path. Past the gateway's `heartbeat_timeout` the shard is closed and
restarted.

**Evidence — three independent measurements.**

| Input | B6 (owning lane) | V3 (reconciliation) | V2 (blind verifier) |
|---|---|---|---|
| 8 KB | — | — | 0.120 s |
| 30 KB | 1.297 s | — | 0.380 s (16 KB) / 1.305 s (32 KB) |
| 60 KB | 5.192 s | 5.16 s | 5.235 s |
| 120 KB | 20.492 s | 20.38 s | 18.714 s |
| 180 KB | 46.613 s | — | 43.100 s |
| 240 KB | — | — | 77.301 s |

Scaling exponent measured at 1.73–2.03 across three input shapes (B6) and converging on **2.03**
across six sizes (V2). `cProfile` at 60 KB: **99.1% of `tottime` in `find_code_block_boundaries`**
(8,222 calls, 5.200 s of 5.247 s). Real event-loop starvation, measured against a 50 ms heartbeat
task: 1.451 s gap at 30 KB, 5.206 s at 60 KB, **20.359 s at 120 KB**. V2 measured the stall through
the real `ResponseDeliveryCoordinator`: a 32 KB response gives a 1.433 s maximum heartbeat gap.

**Verifier corrections (V2).** Two, and both make the finding sharper rather than softer:
- The disconnect trigger is discord.py 2.7.1's `KeepAliveHandler.run` with
  `heartbeat_timeout=60.0`, not the 41.25 s heartbeat *interval*. On V2's curve
  (`t = 1.342e-3 · N²` s/KB²) 41.25 s arrives at **~175 KB** — earlier than the claim's ~240 KB —
  and 60 s at **~211 KB**. Between those you get `heartbeat blocked` warnings; past ~211 KB you get
  the forced reconnect.
- V2 rates it S2 because the *disconnect* outcome needs the model to emit ~200 KB, the extreme top
  of the envelope. At realistic long-answer sizes (30–60 KB) it still freezes the entire bot for
  1.4–5 s for every user in every guild, with no workaround — and `validate_split_integrity` then
  runs a second time at `response_delivery.py:285`.

**Attribution dispute, settled.** A subsystem map attributed the quadratic cost to the four
`^(\s*)` MULTILINE patterns at `markdown_utils.py:55,56,59,62`. That is wrong as a *causal*
attribution: for a position inside a fence `is_safe_split_point` returns at `:310` **before**
`parse_markdown`, so those patterns never run in the worst case — `_find_lists`, `_find_headers`
and `_find_text_formatting` do not appear in the hot profile at all. They are genuinely quadratic
in isolation and are 66% of `parse_markdown` on fence-free text (2.79 ms of a 4.22 ms call at
60 KB), but that is a 4 ms term next to a 5,118 ms one. Fix the boundary recomputation;
optimising the MULTILINE patterns would buy essentially nothing.

**Recommended fix.**
1. Compute code-block boundaries and the markdown block list **once** per `split_message` call and
   pass them into `is_safe_split_point`. An improvement-lane prototype of exactly this rewrite runs
   234 KB in **0.055 s** versus 59.96 s (**1088×**) with **131/131 outputs byte-identical**.
2. Bound candidate iteration: tiers 3–5 should stop after the first K (say 32) failures.
3. Offload `response_delivery.py:281` (and `:135`, DAB-129) with `run_in_executor` as defence in
   depth.
4. Cap the accepted response length before splitting.

**Suggested regression test.** `MessageSplitterPerformanceTest.test_large_fenced_block_splits_quickly`
— build a 120 KB single fenced block and assert `split_message` completes in under 2 s. Pair it
with a machine-speed-independent assertion: patch `MarkdownParser.find_code_block_boundaries` and
assert it is called O(parts), not O(candidates).

---

### DAB-141 — `/rag delete scope:all` lets any member irreversibly wipe every index and every pin

| | |
|---|---|
| Severity | **S1** · blind verifier V1: **S1** (upheld — the only one of its ten claims left at S1) |
| Confidence | **VERIFIED** — runtime introspection of the real registered command tree |
| Affected feature | `/rag delete` |
| File:line | `src/bot/command_modules/research.py:242-289` (scope choice `"all"` at `:246`, `defer` at `:266`, `channel_id = None` at `:267`, call at `:272-274`), group at `:140`; backing SQL at `src/services/message_index_service.py:883-912` |
| Merged raw IDs | `B7-06` (S1) |

**Mechanism.** Choosing `scope: All channels` sets `channel_id = None`, selecting the unscoped
branch: six unqualified `DELETE`s against `message_search_fts`, `message_embeddings`,
`message_index`, `message_retrieval_events`, `message_backfill_progress` and **`pinned_messages`**.
There is no `guild_id` predicate anywhere and `data/message_rag.db` is shared across every guild
the bot serves. The command first calls `retriever.cancel_background_work(channel_id=None)`
(`research.py:271`), cancelling in-flight indexing everywhere. There is no confirmation step — no
button, no typed confirmation, no dry run.

**Trigger conditions.** Any guild member, including in a DM (`guild_only=False` on all 35 command
nodes). One click.

**Observable symptom.** The bot stops recalling anything. Because RAG failures degrade silently to
the legacy `ContextCollector` path, this reads as "the bot got dumber", not "someone deleted the
database". Nothing logs *who* did it (`research.py:284-289` records no actor).

**Evidence.** V1 built the actual command tree via `setup_commands` and introspected it:

    rag delete     default_permissions=None                   checks=[] guild_only=False
    clear-cache    default_permissions=<Permissions value=8>  checks=[] guild_only=False
    rag GROUP      default_permissions: None | guild_only: False
      callback mentions 'permission' / 'administrator' / 'manage_' / 'confirm' /
                       'guild_id' / 'interaction.user' :  all False
    delete_rag_data(channel_id=None) -> 6 unqualified DELETEs; any guild filter?: False

`Permissions value=8` is `administrator`; `None` means Discord exposes the command to `@everyone`.
The codebase demonstrably knows the mechanism — `/clear-cache`, which clears an in-memory metrics
cache, carries `@app_commands.default_permissions(administrator=True)` at `configuration.py:322`.

**Why S1 survives the hobbyist threat model.** The message index can in principle be rebuilt from
Discord history via `/rag backfill` (re-paying for embeddings). `pinned_messages` cannot: pins are
human-authored memories created through `/pin` and stored **only** in `message_rag.db`. There is no
upstream source of truth. That is unrecoverable user-data destruction by an unprivileged caller,
one click, no undo, cross-guild.

Note also that the existing test `test_rag_delete_scopes_deletion_and_stops_background_work`
(`tests/test_command_registration.py:187-232`) pins the global-wipe *behaviour* as intended and
asserts nothing about permissions — so this is an unguarded gap, not a guarded one anyone missed.

**Recommended fix.**
1. Gate the `rag` group with an admin check plus `@app_commands.guild_only()`.
2. Scope `scope:all` to the invoking guild: pass `guild_id` down and delete `WHERE guild_id = ?`.
   A truly global wipe belongs in `scripts/`, not in a slash command.
3. Require a confirmation interaction for the destructive scope — a `discord.ui.View` with a danger
   button and an `interaction_check` pinning it to the invoker (note DAB-152: `/pins` delete
   buttons currently have no `interaction_check`).
4. Log the actor at WARNING with user id, guild id, scope and row counts.
5. Stop taking pins with the RAG data, or at minimum rename the description — "Delete stored
   message RAG data" does not tell the user their pins die too.

**Suggested regression test.** `RagDeleteAuthorizationTest`: with an interaction whose
`user.guild_permissions.manage_guild` is `False`, invoke the callback with `scope="all"` and assert
the index service's delete was not called and the user got a refusal. Second test: with an
authorized user and `scope="all"`, assert a channel in a *different* guild retains its rows. Third:
`PinSurvivalTest` — after a channel-scoped delete, pins in other channels survive.

---

### DAB-198 — A 3.6 KB PDF costs 105 s and 1.63 GB and returns nothing

| | |
|---|---|
| Severity | **S1** · blind verifier V2: **S2** on a normal host, **S1** on any host with < 2 GB RAM |
| Confidence | **VERIFIED** — two independent probes, numbers within 2% |
| Affected feature | PDF attachment handling |
| File:line | `src/bot/discord_bot.py:1178-1180` (no dimension or pixel cap before `get_pixmap`), `:1189-1191` (per-page `except … continue`), `:318-321` (`ThreadPoolExecutor(max_workers=1)`), `:1144-1150` (`run_in_executor` with **no timeout**); intake gate at `src/bot/media_extraction.py:104-113` |
| Merged raw IDs | `C4-14` (S1), `C4-15` (S2), `C4-19` (S4) |

**Mechanism.** `mat = fitz.Matrix(2.0, 2.0); pix = page.get_pixmap(matrix=mat)` on an 8000×8000 pt
MediaBox (legal PDF; the spec limit is 14400) asks for a 16000×16000 px pixmap — 256 megapixels,
about 768 MB. MuPDF allocates, then aborts with `code=5: Overly large image`; on slightly smaller
pages PIL's decompression-bomb guard fires instead, *after* the CPU and the allocation have already
been spent. The per-page `except Exception: … continue` at `:1189-1191` swallows the failure and
moves to the next page, so the cost is paid up to `max_pdf_pages: 20` times and nothing is
produced. Nothing bounds page dimensions, total megapixels, attachment size on this path
(DAB-199), or wall-clock.

**Trigger conditions.** Any guild member attaching a file. The generator is three lines of PyMuPDF;
the 20-page fixture is 3,630 bytes and the single-page one is 520 bytes — small enough to send from
a phone.

**Observable symptom.** 105+ seconds of blocking work on the **only** PDF worker; every other PDF
in the guild queues behind it with no queue bound (ten such uploads is roughly 17.5 minutes of
stalled PDF processing). 1.63 GB peak RSS. And the user is told nothing — `media_extraction.py:133`
logs `"No pages could be extracted from PDF"` at WARNING and the model answers as though no
attachment existed (DAB-006).

**Evidence.**

| Fixture | Bytes | Wall | CPU | Peak RSS | Images | Source |
|---|---|---|---|---|---|---|
| 20 pages @ 8000 pt | 3,630 | 110.3 s | 108.28 s | 1,628 MB | 0 | C4 |
| 20 pages @ 8000 pt | 1,571 | 106.97 s | 106.95 s | 1,624.7 MB | 0 | V2 (blind) |
| 1 page @ 8000 pt | 195 | 5.29 s | 5.62 s | 1,623.5 MB | 0 | V2 (blind) |
| 1 page @ 8000 pt | 520 | 5.24 s | — | 1,626 MB | 0 | C4 |
| 5 pages @ 8000 pt | 479 | 26.31 s | 27.69 s | 1,625.0 MB | 0 | V2 (blind) |

Per page: **5.24–5.35 s and a ~1.63 GB transient peak**; RSS does not accumulate across pages.
Related measurements from the same sweep: a 3,630-byte A0×20 document produces **642.7 megapixels**
across 20 *live* images that are then PNG-encoded and posted to Gemini in one request (C4-15,
DAB-131's sibling), and a 509,996-byte flate bomb costs 17.9 s (C4-19). Amplification: 1.5 KB of
attacker input to 107 CPU-seconds.

**Verifier corrections (V2).** Two, one mitigating and one aggravating, and the claim omitted both:
- **Mitigating:** the work is offloaded to `self._pdf_executor` (`discord_bot.py:318`, `:1145`), so
  unlike DAB-115 the event loop is **not** blocked and the gateway heartbeat survives. The
  single-worker choice is deliberate — the comment at `:316` cites PyMuPDF thread-safety.
- **Aggravating:** because it is a *single*-worker executor, one attacker serialises PDF conversion
  for the whole bot, and nothing bounds the queue.
V2 rates S2 because the process survives, the loop keeps turning and nothing is corrupted — and
S1 on any host with under 2 GB of memory, where the 1.63 GB spike is an OOM kill.

**Recommended fix** — three cheap, independent guards:
1. Before `get_pixmap`, clamp by area:
   `if w*h > MAX_RENDER_PIXELS: scale = sqrt(MAX_RENDER_PIXELS / (page.rect.width * page.rect.height))`.
   A 40 MP cap keeps every legitimate document. Add a total-megapixel budget across the pages of
   one attachment (closes C4-15).
2. Wrap the executor call in `asyncio.wait_for(..., timeout=config.pdf_conversion_timeout)`.
3. Break out of the page loop after N consecutive failures instead of `continue`, so a poison
   document costs one page, not twenty.
Land this together with DAB-197 (delete the pointless `pix.tobytes("png")` → `Image.open`
round-trip, measured 11.7–16.55× on the same worker) and DAB-199/DAB-200 (size gate, `%PDF-` magic
check).

**Suggested regression test.**
`PdfConversionTest.test_oversized_page_is_downscaled_not_retried_per_page` — feed the 520-byte
8000 pt fixture, assert the call returns in under 1 s and either yields a bounded-size image or
zero pages *without* attempting all 20 pages. Second: assert every returned image satisfies
`img.width * img.height <= MAX_RENDER_PIXELS` for an A0×20 fixture.

---

## 5. S2 findings (72)

S2 is "user-visible wrong output or wrong behaviour on a realistic trigger; unrecoverable-but-loud
data loss; disclosure outside the guild; meaningful unbudgeted cost; or a resource blowup that
degrades the host". Grouped by subsystem, in canonical id order. Merged raw IDs are listed because
one fix closes all of them.


### A. Orchestration and lifecycle — 3 S2

| ID | Finding | File:line | Merged raw IDs |
|---|---|---|---|
| **DAB-006** | Audio / image / PDF download failures are invisible to the user and produce a confidently wrong reply | `media_extraction.py; discord_bot.py:1129-1200` | `B1-06`, `C2-16`, `C4-18` |
| **DAB-009** | Unguarded `await change_presence()` and `_start_automatic_rag_backlog` sit outside the `setup_commands` try | `discord_bot.py:523-527,544` | `B1-09`, `C2-07`, `C2-08` |
| **DAB-016** | One image-service `start()` failure nulls `enhanced_command_handler` and collapses complexity routing for ALL text traffic | `discord_bot.py:394-420` | `C2-01` |

### B. Concurrency, async lifecycle and unbounded state — 9 S2

| ID | Finding | File:line | Merged raw IDs |
|---|---|---|---|
| **DAB-020** | Live queue silently destroyed on toggle-off, on `close()`, and after `close()` | `live_message_coordinator.py:113-116,139-142,166` | `B2-03` |
| **DAB-021** | `pending_messages` uncapped -> a single 1.5 M-character model prompt | `live_message_coordinator.py:87-105` | `B2-04` |
| **DAB-022** | Router cache key is a 200-char prefix -> cross-message, cross-user routing collisions (complexity, thinking, context, tokens, prompt) | `enhanced_command_handler.py:70-110` | `B2-05`, `C1-14`, `C4-02` |
| **DAB-024** | Router / selector / embedding Gemini calls have no timeout; the router runs in an uncancellable `to_thread` | `enhanced_command_handler.py:191; gemini_client.py:681,781` | `B2-07`, `B3-18` |
| **DAB-025** | Router failure at call time is never cached and silently downgrades every request to complexity=low | `enhanced_command_handler.py:204-226,296-322` | `B2-08`, `C2-04` |
| **DAB-027** | `_vector_lock` held across CPU-bound numpy scoring -> all semantic retrieval serialised; threads make it worse | `message_index_service.py:93,1329-1345` | `B2-10`, `C5-04` |
| **DAB-028** | `search_semantic` copies the whole matrix and recomputes norms on every call (586 MiB / 2.2x peak) | `message_index_service.py:767-780,1339-1343` | `B2-11`, `C5-05` |
| **DAB-029** | Unbounded in-memory state: 6 dicts/caches that never evict (live locks+context, vector cache, pregeneration status, completed image jobs, image rate limits, router cache) | `live_message_coordinator.py:67-85; message_index_service.py:724-780; hybrid_context_retriever.py:63,197; image_processing_service.py:86` | `B2-01`, `B2-12`, `B2-13`, `B2-19`, `B2-20`, `B4-21`, `C5-19` |
| **DAB-035** | `get_live_enabled()` = blocking `sqlite3.connect`+SELECT per message on the event loop | `channel_settings_service.py:119; discord_bot.py:710,778` | `B2-21`, `C5-07` |

### C. Gemini pipeline, model routing and accounting — 7 S2

| ID | Finding | File:line | Merged raw IDs |
|---|---|---|---|
| **DAB-040** | Retry classification is substring matching on `str(error)`: real 429s are never retried; bare '500'/'502'/'503'/'400' digits false-positive | `error_manager.py:170-241` | `B3-04`, `B8-14` |
| **DAB-041** | `error_class` computed at error_manager.py:181 and never used -> every empty-message transport exception is UNKNOWN_ERROR, should_retry=False | `error_manager.py:181,265-276` | `B3-05`, `B8-04` |
| **DAB-042** | No whole-sequence deadline: one Discord message can occupy the bot ~488 s (unbounded variant 10,800 s) | `gemini_client.py:1283-1316` | `B3-01` |
| **DAB-044** | Backoff applied only on the exception path; timeout and empty-STOP retries fire instantly | `gemini_client.py:1283-1316` | `B3-03` |
| **DAB-048** | Thinking tokens never billed: `thoughts_token_count` is not read anywhere | `token_extraction.py:48-56` | `B3-09` |
| **DAB-049** | Token usage attached only to successful responses; tokens burned on retries and failures are never recorded | `data_models.py:87-88; response_generation.py:142` | `B3-10`, `C2-10` |
| **DAB-050** | Router, context-selector and embedding calls are entirely unaccounted (5-6 Gemini call sites bypass token accounting) | `gemini_client.py:681,781; enhanced_command_handler.py:191` | `B3-11`, `C1-22` |

### D. RAG: indexing, retrieval, migration — 10 S2

| ID | Finding | File:line | Merged raw IDs |
|---|---|---|---|
| **DAB-067** | An unreadable legacy `token_usage.db` aborts `DiscordBot.__init__` -> permanent boot loop | `message_index_service.py:604` | `B4-03` |
| **DAB-069** | Lost update on `hidden`: `/hide` racing a re-index re-exposes hidden content to RAG (37% under natural interleave) | `message_index_service.py:413-437` | `B4-05` |
| **DAB-070** | `store_embedding` accepts a wrong-width vector, marks it `done`, then silently drops it at search time | `message_index_service.py:1257-1300` | `B4-06`, `B4-20` |
| **DAB-071** | No FTS rebuild path: enabling FTS5 later leaves the whole history lexically invisible; FTS drifts out of sync with `message_index` with no reconciliation | `message_index_service.py:257,1124` | `B4-07`, `B4-19` |
| **DAB-072** | Context pack is bounded by message COUNT only (4/6/8) - no token budget at all | `context_pack_builder.py:62-110` | `B4-08` |
| **DAB-073** | Pins have absolute priority and can starve retrieval to zero slots while the full pipeline still runs; a single oversized pin is injected into every prompt forever | `context_pack_builder.py:102-110; pin_service.py:100` | `B4-09`, `C4-09`, `C4-10`, `C4-11` |
| **DAB-076** | Backfill: 2 connections + 2 commits per message, unbounded full history, auto-started at boot | `message_index_service.py:552-590` | `B4-12`, `C5-11` |
| **DAB-077** | `_reconcile_embedding_eligibility` full-table scan on every construction blocks startup (2.6 s = 97.8% of `__init__`) | `message_index_service.py:265,705-722` | `B4-18`, `C5-02` |
| **DAB-078** | `search_recent` cannot use its index; every call full-scans the corpus (30.8 ms -> 0.026 ms with a partial index) | `message_index_service.py:1072-1103,152-155` | `C5-01` |
| **DAB-087** | Hybrid RAG is blind to every non-Latin script: both the lexical and semantic legs are gated on `[A-Za-z0-9]`; zero-width chars in a query also destroy lexical retrieval | `message_index_service.py:698,1109-1122` | `C4-01`, `C4-07` |

### E. Config and persistence — 9 S2

| ID | Finding | File:line | Merged raw IDs |
|---|---|---|---|
| **DAB-095** | `sqlite_utils` sets no PRAGMAs: journal_mode=delete (no WAL), implicit 5 s busy timeout, foreign_keys OFF, synchronous=FULL; contended writes are silently dropped | `sqlite_utils.py:19-30` | `B5-01`, `B4-14` |
| **DAB-096** | Synchronous SQLite runs on the asyncio event loop: a lock contention freezes the whole bot for 5 s (9 call sites) | `channel_settings_service.py, pin_service.py, message_visibility_service.py, report_service.py` | `B5-02`, `B4-13` |
| **DAB-099** | No column-upgrade path for 4 of 5 `token_usage.db` tables: an older database breaks silently or fatally | `token_tracker.py:44-56; report_service.py` | `B5-05` |
| **DAB-100** | `ReportService.create_report` commits the row and THEN raises `IndexError`, so the user is told it failed | `report_service.py:80-130` | `B5-06` |
| **DAB-101** | `expanduser()` applied by only 4 of 7 path-consuming services: one `~` in a path splits each database in two, defeats the separate-DB check, and the mkdir failure is swallowed | `channel_settings_service.py, user_preferences_service.py, message_visibility_service.py; config_helpers.py:525` | `B5-07`, `B5-08`, `B5-09`, `C3-11`, `C3-04` |
| **DAB-102** | No cost model anywhere and the model name is never persisted: spend is unreconstructable after the fact | `token_tracker.py:44-56` | `B5-10` |
| **DAB-105** | A whole usage event is discarded whenever the API's reported total is less than input+output | `data_models.py:60-61; token_extraction.py` | `B5-13` |
| **DAB-106** | 44-47 of 100 `BotConfig` fields have no validation; 34 of 42 extreme values pass `validate_config`; 4 fields are entirely dead | `config_helpers.py:491-744; config.py:22-180` | `B5-14`, `C4-34`, `B8-11`, `C4-36`, `C4-37`, `C4-38` |
| **DAB-112** | `message_split_length: 100` + `continuation_overhead: 100` -> effective_max_length 0 -> one response becomes ~20,000 Discord messages | `message_splitter.py:57-58` | `C4-35` |

### F. Rendering, media and delivery — 10 S2

| ID | Finding | File:line | Merged raw IDs |
|---|---|---|---|
| **DAB-114** | Every LaTeX response with 2+ equations fails to render: `ax.axhline(transform=...)` is rejected by matplotlib and swallowed | `content_renderer.py:489-493` | `B6-07` |
| **DAB-116** | Continuation markers cost up to 64 chars but only 50 are reserved; overflow silently discards the entire smart split | `message_splitter.py:331-370` | `B6-01` |
| **DAB-119** | `_preserve_code_blocks` is always undone: added fences fail integrity validation, so fence preservation is effectively disabled | `message_splitter.py:371-440` | `B6-04` |
| **DAB-121** | Overlapping block/inline LaTeX spans corrupt the surrounding text | `content_renderer.py:149-330` | `B6-08` |
| **DAB-122** | Currency amounts are eaten by `INLINE_LATEX_RE` | `content_renderer.py:216` | `B6-09` |
| **DAB-125** | LaTeX and table rewriting are not code-fence-aware: code blocks get mangled | `content_renderer.py:106-330` | `B6-12` |
| **DAB-128** | `fig_height` is unbounded and becomes a live DoS the moment B6-07 is fixed | `content_renderer.py:416-511` | `B6-15` |
| **DAB-129** | Rendering and splitting both run synchronously on the asyncio event loop (matplotlib + splitter) | `response_delivery.py:135,281; content_renderer.py:416-511` | `B6-16`, `C5-10` |
| **DAB-131** | `validate_image` accepts a 100+ megapixel decompression bomb from 95 KB (144 MP / 1.26 GB RSS from 449 KB) | `image_utils.py; gemini_client.py:918-921` | `B6-18`, `C4-20` |
| **DAB-138** | PIL PNG encoding runs on the event loop, up to 26 images per request (2.13 s of loop block) | `gemini_client.py:918-921` | `C5-09` |

### G. Security and authorization — 11 S2

| ID | Finding | File:line | Merged raw IDs |
|---|---|---|---|
| **DAB-140** | The command layer has effectively no authorization model: 1 of 35 commands is gated and `/dev`'s gate is commented out while its docstring claims "admin only" | `configuration.py:322,352` | `B7-05` |
| **DAB-142** | `/dev` is ungated and turns raw exception text and full stack traces into public Discord messages | `configuration.py:351-377` | `B7-09` |
| **DAB-143** | Report web UI serves every report from every guild with no authentication and no authorization | `report_web_server.py:41-102` | `B7-01` |
| **DAB-144** | `ReportWebServer.url` rewrites a `0.0.0.0`/`::` bind to `127.0.0.1`, actively misleading the operator about network exposure | `report_web_server.py` | `B7-02` |
| **DAB-147** | `/report-status` reads any report by enumerable integer id with no guild or ownership scoping | `reports_usage.py:101-149` | `B7-07` |
| **DAB-148** | Every `/config` subcommand mutates process-global state with no permission gate | `configuration.py:38-320` | `B7-08`, `C1-17` |
| **DAB-150** | `/pin` is an ungated, uncapped, unsanitised prompt-injection channel that also evicts all real retrieved context | `personalization.py:175; pin_service.py:100` | `B7-11` |
| **DAB-153** | Error handling is unsafe by construction: raw exception text is echoed to Discord by default with no secret scrubbing (the `?key=` exfiltration claim is REFUTED for this SDK) | `error_manager.py:89,435-479` | `B7-14`, `B7-16` |
| **DAB-157** | `.gitignore` covers `.env`, `data/`, `*.db`, `bot.log` but NOT rotated log files (`bot.log.1`, `logs/*.log.N`) - exactly the files holding user content | `.gitignore`:71-72,78 | `B7-19`, `C6-19` |
| **DAB-159** | Indirect prompt injection: retrieved message content can forge the RAG context fence (`--- End Context ---`, `User:`) | `gemini_client.py:format_prompt` | `C4-08` |
| **DAB-163** | `/pins` breaks permanently at 26 pins (Discord's 25-field embed cap), making a pin flood unrecoverable through the UI | `personalization.py:202-230` | `C4-12` |

### H. Observability and error handling — 6 S2

| ID | Finding | File:line | Merged raw IDs |
|---|---|---|---|
| **DAB-165** | A `%` in any extra value SILENTLY DROPS the record from the log file | `logging_config.py:99` | `B8-02` |
| **DAB-166** | `/config debug` is a no-op: handler levels are pinned at startup, and it mutates the logger but not `config.log_level` | `logging_config.py:439,456; configuration.py:154-156` | `B8-03` |
| **DAB-167** | `send_error_response` raises out of its own `except` block, defeating the last-resort safety net | `error_manager.py:452-479` | `B8-05` |
| **DAB-168** | Live-mode RAG retrieval failure is logged at DEBUG - the invisible twin of the mention path's WARNING | `live_message_coordinator.py:299-300` | `B8-07` |
| **DAB-169** | Silent-degradation inventory: 39 of 237 `except` handlers (16%) are invisible at the default log level | src/**/*.py (39 of 237 `except` handlers) | `B8-08` |
| **DAB-170** | `get_status` returns an all-zeros dict on DB failure -> `/rag status` shows a healthy-looking empty index | `message_index_service.py:1508-1522` | `B8-09` |

### I. Cold start, packaging and ops — 2 S2

| ID | Finding | File:line | Merged raw IDs |
|---|---|---|---|
| **DAB-180** | `grok-prompts/` is a dangling submodule gitlink; a fresh clone can never populate it and `git submodule` fails repo-wide, so `/deepresearch` silently uses a stub prompt with a no-op CWD-relative fallback | `research.py:77-101; .gitmodules` | `C3-01`, `C3-02`, `C3-03`, `C6-20` |
| **DAB-181** | A failed first `start.sh` leaves a poisoned `.venv` that later runs never repair | `start.sh` | `C3-05` |

### K. Performance (not covered above) — 1 S2

| ID | Finding | File:line | Merged raw IDs |
|---|---|---|---|
| **DAB-197** | PDF rendering: a needless PNG encode->decode round-trip costs 11.7-16.5x; the `max_workers=1` executor is a second-order factor (measured 1.21x from widening) | `discord_bot.py:318,1178-1184` | `C5-17` |

### L. Adversarial input handling — 2 S2

| ID | Finding | File:line | Merged raw IDs |
|---|---|---|---|
| **DAB-201** | The text-file decode ladder can never fail: binary is mojibaked into the prompt, UTF-16 is silently corrupted, BOM is left in | `media_extraction.py:473` | `C4-27` |
| **DAB-202** | 52.4 MB of text-file content is concatenated into one prompt with no truncation; the 5 MB gate trusts `attachment.size`; `content_type` overrides the extension allowlist | `media_extraction.py:417-446; response_generation.py:293-309` | `C4-28`, `C4-30`, `C4-31` |

### M. Test suite and historical claims — 2 S2

| ID | Finding | File:line | Merged raw IDs |
|---|---|---|---|
| **DAB-204** | `test_main_response_path_does_not_wrap_client_retry_timeout` blocks the correct fix for the missing whole-sequence deadline (a correct fix FAILS the suite) | `tests/test_bug_regressions.py` | `C7-F1` |
| **DAB-207** | `MessageSplitter` root cause: continuation/fence overhead is computed after split points are chosen, so `_preserve_code_blocks` can never survive | `message_splitter.py:125-166` | `C7-F4` |

#### Notes where the mechanism is not obvious

**DAB-095 + DAB-096 (SQLite).** These are one problem in two halves and should be fixed together.
`sqlite_utils.sqlite_connection` calls `sqlite3.connect(db_path)` with no arguments and sets no
PRAGMAs; measured live, that gives `journal_mode=delete` (no WAL), `foreign_keys=0`,
`synchronous=2`, and a busy timeout of 5,000 ms inherited from CPython's implicit
`sqlite3.connect(timeout=5.0)` default — an undocumented default, not a stated contract. (Two
subsystem maps said "no busy_timeout" and a lane said "there is one, 5,000 ms"; both are true of
different things.) Meanwhile five services expose only blocking methods and every caller is an
`async def` that invokes them directly; `TokenTracker` is the only service that offloads to a
thread. `get_live_enabled()` runs on **every inbound message** (`discord_bot.py:778` → `:710`). So
a contended write blocks the event loop for the full 5.01 s, freezing the gateway heartbeat. An
improvement lane measured the fix: contended read `fails after 5.01 s → succeeds in 3.3 ms`,
single-row write throughput `2,918 → 12,355/s (4.2×)`, maximum loop stall `4,997 ms → 3.1 ms`.

**DAB-041 (dead `error_class`).** `error_manager.py:181` computes
`error_class = type(error).__name__.lower()` and never reads it again — verified by reading all of
`:180-241`. Because classification is pure substring matching on `str(error)`,
`asyncio.TimeoutError()` and `ConnectionResetError()` both stringify to `""`, fall through to
`UNKNOWN_ERROR`, and `UNKNOWN_ERROR` is not in the retryable set at `:265-276`. The two commonest
transient faults in a Discord/Gemini bot are therefore classified as permanent, and the variable
that would fix it is already computed one line above. Highest payoff-per-line fix in the corpus.

**DAB-040 (substring retry classification).** The same function, the other half. Reproduced twice:
a real `google.genai` 429 (`429 RESOURCE_EXHAUSTED. {'error': …'You exceeded your current
quota'…}`) classifies as `unknown_error` with `should_retry=False`, because the required literal is
`"quota exceeded"` and the body says "your current quota"; meanwhile `Response exceeded 1500
characters` matches the bare substring `"500"` and is retried as a service outage. The owning lane
measured 12 of 19 realistic errors misclassified; V1's independently written 19-case corpus gave
13/19 wrong categories and 9/19 wrong retry decisions. The count is corpus-dependent and should not
be quoted as a constant; the direction is certain.

**DAB-116 + DAB-119 + DAB-207 (the splitter's overhead ordering).** `effective_max_length` reserves
50 characters for continuation markers, but the markers cost 56–64 characters once part numbers
reach two or three digits, so a tier that lands near 1,950 overflows 2,000, the integrity guard at
`message_splitter.py:125-133` throws away the whole formatted split, and `_hard_split_parts` emits
blind 2,000-character slices with no markdown awareness, no part markers and fences cut mid-block.
That is why fence preservation is *effectively disabled* for most long responses (DAB-119) and why
the historical BUG-0003 is only partially fixed (§8). The root cause (DAB-207) is that
continuation and fence overhead is computed *after* split points are chosen.

**DAB-073 (pins starve retrieval).** Pins have absolute priority in the context pack and the pack
is bounded by message *count* only (4/6/8 — DAB-072, no token budget at all). Eight junk pins evict
100% of retrieved context permanently; one 100 KB pin is injected verbatim into every prompt
forever; 1,000 pins materialise 6.0 MB per message and then discard it. `/pin` is ungated,
uncapped and unsanitised (DAB-150), and `/pins` breaks permanently at 26 pins because of Discord's
25-field embed cap (DAB-163) — so a pin flood cannot be undone through the UI.

**DAB-087 (RAG is blind to every non-Latin script).** Both legs are gated on `[A-Za-z0-9]`: the
embedding-eligibility gate at `message_index_service.py:698` and the FTS query builder at
`:1109-1122`. CJK, Cyrillic, Arabic, Hebrew, Greek and Thai messages are never embedded and produce
a `None` lexical query, so hybrid RAG silently returns nothing for them. XS fix (widen two regexes);
it sits low in the priority order only because the target deployment is assumed English.

**DAB-165 (a `%` deletes the log record).** `StructuredFormatter.format` at `logging_config.py:99`
`%`-formats records that carry `extra` data. A single `%` in any extra value raises inside logging
and the record is **silently dropped from the log file** — the failure mode is invisible by
construction, in the subsystem you would use to see failure modes. Fix it before relying on any
other instrumentation.

**DAB-157 (rotated logs are not gitignored).** `.gitignore` covers `.env`, `data/`, `*.db` and
`bot.log` (line 78) — but not `bot.log.1` or `logs/*.log.N`, which is exactly where
`RotatingFileHandler` puts the history containing full user message content (DAB-155), uploaded
file content, and unredacted exception text (DAB-154). A solo operator who `git add -A`s their own
bot repo and pushes it publishes their guild's conversations. This is the finding the hobbyist
threat model *raises* rather than lowers; one line of `.gitignore` fixes it.

**DAB-112 (a config combination turns one reply into ~20,000 messages).**
`message_split_length: 100` with `continuation_overhead: 100` gives `effective_max_length = 0` at
`message_splitter.py:57-58`. Nothing validates the relationship. It is one member of DAB-106's
family: 44 of 100 `BotConfig` fields have no validation rule anywhere, 47 have none inside
`validate_config`, and 34 of 42 deliberately extreme values load without complaint.

**DAB-016 (one image-service failure collapses text routing).** If `ImageProcessingService.start()`
raises, `discord_bot.py:394-420` sets `enhanced_command_handler = None`. That handler is the LLM
router for **all** text traffic, so every subsequent message silently falls back to
`complexity="low"` — wrong model tier, wrong context window, wrong token cap, no signal. An image
subsystem failure degrades the core text path.

**DAB-105 (a whole usage event is discarded).** `data_models.py:60-61` treats
`total < input + output` as invalid and rejects the record. Providers legitimately report totals
that do not equal the sum (thinking tokens, cached content). The result is that the accounting
system silently drops exactly the events it exists to record.

---

## 6. S3 and S4 findings

Complete, compact. "Area" is the subsystem letter from §5: A orchestration, B concurrency,
C Gemini/routing, D RAG, E config/persistence, F rendering/media, G security, H observability,
I cold start/ops, J dead code, K performance, L adversarial input, M tests/history.

### S3 (82)

| Area | ID | Finding | File:line |
|---|---|---|---|
| A | **DAB-003** | No `on_ready` re-entry guard -> `CommandAlreadyRegistered` on gateway re-IDENTIFY | `discord_bot.py:461` |
| A | **DAB-004** | `close()` has two unguarded awaits that abort PDF-executor drain and `super().close()` | `discord_bot.py:632-692` |
| A | **DAB-005** | "Processing..." status message orphaned on 3 of 5 failure paths | `response_generation.py:331-430` |
| A | **DAB-008** | Direct image attachments gated on `content_type` only; context images also accept by extension | `media_extraction.py:95-190` |
| A | **DAB-011** | Message edits never reschedule embeddings; uncached edits are not handled at all | `rag_event_coordinator.py:53-72` |
| A | **DAB-013** | No SIGTERM handler -> `main.py`'s `finally: await bot.close()` never runs on service stop | `main.py` |
| A | **DAB-014** | Pre-`on_ready` window: live mode, personality and user preferences silently off; no log marks the window | `personalization.py:43,142,146,380` |
| A | **DAB-015** | `/status`+`/config info` hard-code `gemini_client: available` and cannot distinguish failed vs never-configured | `discord_bot.py:582` |
| A | **DAB-018** | Locked/erroring `channel_settings` DB silently turns live mode off for every channel | `channel_settings_service.py:119` |
| B | **DAB-023** | Router cache stampede: no lock, no in-flight dedup | `enhanced_command_handler.py:93-120` |
| B | **DAB-030** | `_all_channels_task` exception swallowed; `/rag status` reports "running" forever | `hybrid_context_retriever.py:255` |
| B | **DAB-032** | Rate limiter rebuilds every active user's history on every call: O(users) on the event loop | `rate_limiter.py:24-30` |
| B | **DAB-034** | Nano-banana global image limiter is bypassed by concurrency (check-then-act race) | `nano_banana_client.py:479` |
| C | **DAB-043** | Server-suggested `retry_after` honoured verbatim and uncapped (3-hour stall) | `gemini_client.py:1823` |
| C | **DAB-045** | `set_model_by_complexity` has zero callers -> `_current_complexity_level` frozen at 'low'; every 'current' getter and 4 reporting surfaces are wrong; all 3 tiers name the same model anyway | `gemini_client.py:346,290,167,173,192` |
| C | **DAB-046** | `model_override` never validated against `valid_models` for any caller; a stored `/preferences model` is never re-validated after a config edit | `gemini_client.py; user_preferences_service.py:72` |
| C | **DAB-051** | Silent search downgrade: multimodal replies still advertise "Grounding: Online Search Enabled" with zero citations | `gemini_client.py:499-551` |
| C | **DAB-052** | Unknown or mis-cased safety threshold silently fails OPEN to `BLOCK_NONE`; safety config is never validated | `gemini_client.py:1437-1446` |
| C | **DAB-053** | System instructions prepended as user text instead of passed as `system_instruction` | `gemini_client.py:1714-1720` |
| C | **DAB-054** | Thinking level `off` maps to MINIMAL instead of disabling thinking | `gemini_client.py:553-600` |
| C | **DAB-056** | `/config model` is a one-way door: nothing can restore complexity routing once a runtime override is set | `gemini_client.py:97,248` |
| C | **DAB-057** | `/preferences model` silently outranks the router and `/config model`; the reply overpromises | `discord_bot.py:1090-1102` |
| C | **DAB-058** | `/summarize` is the only path that falls through to `_current_model_name` + the frozen `low` tier (4096 cap, "be brief" prompt) | `research.py:302-381` |
| C | **DAB-059** | Live mode answers users with the *router* model and drops every user preference | `live_message_coordinator.py:268,309` |
| C | **DAB-062** | Seven in-memory-only mutations, five with replies that imply durability (settings silently lost on restart) | `configuration.py:40-170` |
| D | **DAB-068** | Pin legacy migration fails silently and retries on every boot forever | `pin_service.py:55-70` |
| D | **DAB-074** | Audio path truncates the finished pack with `context[-5:]`, dropping pins and reply anchors first | `response_generation.py:217-239` |
| D | **DAB-075** | Rank fusion discards bm25/cosine magnitudes; recency is inert and double-counted | `hybrid_context_retriever.py:470-570` |
| D | **DAB-079** | `search_lexical` ORs every token and its cost is proportional to matching rows | `message_index_service.py:1108-1169` |
| D | **DAB-080** | `rag.cross_channel_enabled` turns semantic search into a 479 ms global mutex | `message_index_service.py:1332-1345` |
| D | **DAB-082** | Migrated messages without a `message_embeddings` row can never be embedded | `message_index_service.py:602-697` |
| D | **DAB-083** | `rag_migrations` is not owned by `_ensure_schema`; the ledger name is path-independent and its existence depends on TokenTracker construction order | `message_index_service.py:126-269,620-627; pin_service.py:63-70` |
| D | **DAB-086** | `message_retrieval_events` is a write-only, unbounded table nothing ever reads (402 MB/yr) | `message_index_service.py:1403-1454,218-230` |
| D | **DAB-092** | `format_prompt` partitions context with quadratic value-comparison (`msg not in pins`) | `gemini_client.py:format_prompt` |
| E | **DAB-097** | DDL autocommits inside `sqlite_transaction`, so a failed transaction leaves a half-built schema | `sqlite_utils.py:34-44` |
| E | **DAB-103** | Leaderboard `GROUP BY` includes denormalized `username`/`guild_name`: a rename splits one user into several rows and corrupts the ranking | `token_tracker.py:166` |
| E | **DAB-104** | `token_usage` grows without bound: no retention, no pruning, no vacuum (219 MB/yr) | `token_tracker.py` |
| E | **DAB-107** | Malformed `config.yaml` crashes with a raw traceback: `load_and_validate_config` catches only `YAMLError`/`ValueError` (6 of 8 probes) | `config.py:257-265` |
| E | **DAB-108** | `MessageContext.__post_init__` rejects a real Discord message shape and the `ValueError` escapes context collection | `data_models.py` |
| E | **DAB-109** | `BotConfig` is a mutable process-global mutated at runtime by `/dev`, whose admin gate is commented out | `configuration.py:352,357` |
| E | **DAB-110** | `datetime.utcnow()` deprecated in 3.12 at 6 call sites, and three timestamp encodings coexist in the same columns compared as strings | `4 services; message_index_service.py` |
| E | **DAB-111** | `TokenUsage`/`APIResponse` invariants make legitimate API outcomes unrepresentable | `data_models.py:60-88` |
| E | **DAB-113** | `rag.enabled: false` still builds the RAG database AND bypasses the "must be separate DB" validation | `discord_bot.py:355; config_helpers.py:525` |
| F | **DAB-118** | Whitespace-only parts survive filtering and become empty Discord messages | `message_splitter.py:276-330` |
| F | **DAB-120** | Tier 2 iterates forward while every other tier iterates reversed -> 8.7x message amplification | `message_splitter.py:203-274` |
| F | **DAB-123** | `LATEX_UNICODE_MAP` prefix shadowing: `\int`->`in-t`, `\cdots`->`.s` | `content_renderer.py:513` |
| F | **DAB-124** | `SUBSCRIPT_MAP` maps `i` to subscript j; 17 letters silently unmapped | `content_renderer.py` |
| F | **DAB-126** | `TABLE_RE` false positives and ragged-row corruption | `content_renderer.py:76-79,579` |
| F | **DAB-130** | `validate_image` is header-only and passes truncated/corrupt images that explode at save time | `image_utils.py` |
| F | **DAB-132** | `image_utils.py` has zero direct test coverage; assorted secondary defects | `image_utils.py` |
| F | **DAB-133** | `discord.File` objects are reused across the delivery fallback without `reset()` | `response_delivery.py:289-340` |
| F | **DAB-136** | The two image gates disagree on formats and minimum dimensions; declared `content_type` is fully trusted, magic bytes never checked | `image_utils.py; media_extraction.py` |
| F | **DAB-139** | matplotlib importable but broken at construction is FATAL because only `ImportError` is caught; render-time failure silently downgrades LaTeX | `content_renderer.py:96-104` |
| G | **DAB-145** | No CSRF protection: a cross-origin form POST mutates report state | `report_web_server.py:80-102` |
| G | **DAB-149** | `/live` lets any guild member make the bot answer every message in a channel (cost + data egress) | `personalization.py:99` |
| G | **DAB-151** | `/hide` and `/unhide` let any guild member rewrite the bot's past messages | `personalization.py:231,304` |
| G | **DAB-154** | The logging pipeline has no redaction filter at all; full exception messages always reach the log file | `logging_config.py` |
| G | **DAB-155** | Inconsistent content redaction: two INFO sites log user message and uploaded-file content while two adjacent sites redact (the `on_error` claim is REFUTED) | `gemini_client.py:951-979; media_extraction.py` |
| G | **DAB-158** | No cooldown or rate limit on any slash command; the mention path's rate limiter does not apply | src/bot/command_modules/*.py |
| G | **DAB-160** | Uploaded file content is fenced with ``` but never escaped - trivial prompt-injection breakout | `response_generation.py:293-309` |
| G | **DAB-161** | NUL bytes, C0/C1 control characters and bidi overrides reach SQLite and the outbound prompt verbatim | `message_index_service.py:399; gemini_client.py` |
| G | **DAB-162** | Zero-width characters bust the router cache (cost amplification) and bypass the empty-prompt guard (free LLM call) | `enhanced_command_handler.py:75; discord_bot.py:830` |
| H | **DAB-164** | `StructuredFormatter.format` mutates `record.msg`; extras are printed 2-4x per record | `logging_config.py:99` |
| H | **DAB-171** | `setup_logging` is non-idempotent for the performance logger (handlers accumulate 1->5) | `logging_config.py:430,476` |
| H | **DAB-172** | `performance_*.log` is always 0 bytes: the configured logger name is orphaned | `logging_config.py:462,109` |
| H | **DAB-174** | `sqlite_utils.py:28` bare `except sqlite3.Error: pass` hides every rollback failure | `sqlite_utils.py:25-30` |
| H | **DAB-178** | The report web server does synchronous, unindexed SQLite inside the bot's event loop | `report_web_server.py:73,91` |
| I | **DAB-182** | `start.sh` gives no remediation when Python 3.12 is absent or pip/venv fails (start.bat does); it is committed non-executable; both scripts silently ignore unknown flags | `start.sh` |
| I | **DAB-183** | `validate_service_connectivity` performs no connectivity check and always returns OK (README overstates it) | `config.py:213-223` |
| I | **DAB-184** | README's verbatim `.env` placeholders half-pass token validation; token validation never strips, so whitespace-only secrets boot the bot | `config.py:195-211; README.md` |
| I | **DAB-185** | `NANO_BANANA_API_KEY=` (present but empty) does not fall back to `GEMINI_API_KEY` | `config_helpers.py` |
| I | **DAB-186** | Read-only project dir: two services silently degrade, the third hard-fails, and the fatal message names only 'data' | `config.py` |
| I | **DAB-187** | `fitz`, `PIL` and `numpy` are hard top-level imports: a missing one is a bare `ImportError` before the startup banner | `discord_bot.py:11; message_index_service.py` |
| I | **DAB-188** | Missing-`config.yaml` message points at a nonexistent `config.yaml.example`, which a test also pins | `config_helpers.py:29; tests/test_config.py:167` |
| J | **DAB-195** | `/help` is promised on 4 reachable user-visible surfaces (13 source occurrences) but is never registered | `general.py:118; configuration.py:305; error_manager.py:103,121` |
| J | **DAB-196** | `/features` advertises two capabilities that do not work (reaction feedback, progress updates) and one command that does not exist (`/model`) | `general.py:102-114` |
| L | **DAB-199** | PDF and image attachments bypass the size gate entirely (100 MB PDF accepted, 273 MB RSS) | `media_extraction.py:417` |
| M | **DAB-203** | `test_command_registration.py` pins the DEGRADED (22-command) tree, not the production one, so `/edit-image` and `/image-queue` are invisible to reference-counting AND to the suite | `tests/test_command_registration.py:147` |
| M | **DAB-205** | BUG-0002 has no end-to-end guard: reinstating the buggy assignment leaves all 125 tests green | `tests/test_bug_regressions.py:37` |
| M | **DAB-209** | `test_live_batch_rate_limits_each_participating_user` PINS message-dropping for rate-limited users - decide whether that is intended | `tests/test_bug_regressions.py:208` |
| M | **DAB-210** | **CLOSED (round 2 Phase 6) — the file was deleted.** `docs/DEEP_BUG_HUNT_REPORT.md` was self-refuting: build `8bc80f9` never existed, the report shipped in the same commit as all five fixes, every Status field was stale on arrival | (deleted; §8.1 and §8.2 below) |
| M | **DAB-211** | `docs/tech-debt-register.md`: 6 of 8 evidence rows HOLD, 2 DRIFTED, TD-004's evidence is FALSE, and the "no cross-file duplicate blocks" scan note is FALSE (26 windows at HEAD) | `docs/tech-debt-register.md` |

### S4 (43)

| Area | ID | Finding | File:line |
|---|---|---|---|
| A | **DAB-007** | `except discord.NotFound` at response_delivery.py:215 is unreachable (shadowed by `HTTPException`) | `response_delivery.py:189,215` |
| A | **DAB-010** | `_extract_user_prompt` never strips role mentions although `is_bot_mentioned` honours them | `discord_bot.py:1299-1307` |
| A | **DAB-012** | Three `ResponseGenerationCoordinator` helpers reachable only through wrappers nothing calls | `response_generation.py:56,69,77` |
| A | **DAB-017** | `EnhancedCommandHandler` image-generation path dereferences `image_processing_service` behind the wrong flag (latent) | `enhanced_command_handler.py:297` |
| B | **DAB-026** | Router cache TTL is insertion-based and never refreshed on hit | `enhanced_command_handler.py:93` |
| B | **DAB-031** | Rate-limit retry hint truncates -> a user who obeys it is denied again | `rate_limiter.py:37-60` |
| B | **DAB-033** | `TextRateLimiter` raises `ValueError` when a limit is 0; zero test coverage | `rate_limiter.py:8` |
| B | **DAB-036** | Concurrent `show_typing_indicator` orphans forever-looping Typing tasks (entry point currently dead) | `user_experience_service.py:122` |
| B | **DAB-037** | Unreferenced `create_task` for reaction removal | `user_experience_service.py:292` |
| B | **DAB-038** | Per-channel `cancel_background_work` also kills the global backlog pass | `hybrid_context_retriever.py:359` |
| C | **DAB-047** | `/deepresearch` reads `config.model_complexity` raw, bypassing the validating accessor and all user/global settings | `research.py:47-52` |
| C | **DAB-055** | `api_timeout_buffer` parsed into `BotConfig` and never read (dead config) | `config.py:78; config_helpers.py:244` |
| C | **DAB-060** | Model and language halves of `/preferences` are gated asymmetrically | `personalization.py:389,407` |
| C | **DAB-061** | README documents no precedence order and overstates per-user preferences | `README.md` |
| C | **DAB-063** | `/config deepsearch` claims "EVERY query"; three mechanisms silently exempt requests | `gemini_client.py:499-551` |
| C | **DAB-064** | `config.yaml:192` references a nonexistent `/config personality` command | `config.yaml:192` |
| D | **DAB-081** | First boot after the RAG database split costs an extra 4.2 s (full FTS rebuild) | `message_index_service.py:602-697` |
| D | **DAB-084** | `embedding_vector` is an untagged BLOB / TEXT union in a column declared TEXT | `message_index_service.py:126-269` |
| D | **DAB-085** | Deleted messages keep their 4,113-byte embedding row forever | `message_index_service.py:1023-1042` |
| D | **DAB-088** | A lone surrogate silently drops a message from the index; hashing and storage disagree on encoding policy | `message_index_service.py:399` |
| D | **DAB-089** | Dead escape in `_build_fts_query` | `message_index_service.py:1116` |
| D | **DAB-090** | `ContextPackBuilder` silently clamps non-positive `max_messages` to 1 | `context_pack_builder.py:70,104` |
| D | **DAB-091** | `get_pins_for_prompt` is completely uncapped (currently dead code) | `pin_service.py:185-204` |
| D | **DAB-093** | FTS5 unavailable is a clean degradation, but keyword recall vanishes with no per-query signal | `message_index_service.py:257` |
| F | **DAB-117** | `split_message` returns whitespace-only input unstripped as one over-limit part | `message_splitter.py:62` |
| F | **DAB-127** | `RAW_LATEX_RE` is dead code and is quadratic on non-matching input | `content_renderer.py:90-93` |
| F | **DAB-134** | `clean_split_part_for_embed` is lossy and embed pages can contain unbalanced fences | `response_delivery.py` |
| F | **DAB-137** | Animated GIFs lose every frame but the first; EXIF orientation never applied; exotic colour modes silently coerced | `media_extraction.py:161` |
| G | **DAB-152** | `/pins` delete buttons have no `interaction_check`, so any user can press another user's button | `personalization.py:148-172` |
| G | **DAB-156** | `logging.getLogger("google")` does not cover `google_genai.*`; `httpx`/`httpcore` unpinned | `logging_config.py:479-483` |
| H | **DAB-173** | Reserved-key `extra` raises `KeyError`; latent in 3 public APIs and only at enabled levels | `logging_config.py:379,744,767,502` |
| H | **DAB-175** | Three `ErrorType` members are structurally unreachable dead code | `error_manager.py:37,38,42` |
| H | **DAB-176** | `'invalid format'` is stolen from `VALIDATION_ERROR` by `IMAGE_FORMAT_ERROR` | `error_manager.py:194,232` |
| H | **DAB-177** | Synchronous logging I/O on the event loop (RotatingFileHandler, no QueueHandler); one INFO record per context message | `logging_config.py:450-476` |
| H | **DAB-179** | `report_web_server.py:73` dereferences `report_service` without a guard (currently unreachable) | `report_web_server.py:73` |
| I | **DAB-189** | README/AGENTS doc drift: project layout names 3 nonexistent root files; verification commands are Windows-only; `.gitignore` promises a `.env.example` that was never added | `README.md:202-207` |
| I | **DAB-190** | `logs/` and `temp/` are created only by `health_check.py`; the bot never uses `temp/` | `scripts/health_check.py` |
| I | **DAB-191** | Discord login failure is not attributed to `DISCORD_BOT_TOKEN`; the shutdown banner prints twice | `main.py` |
| I | **DAB-192** | `constraints.txt` no longer matches the validated environment (colorama / 3.12.10 header drift) | `constraints.txt` |
| I | **DAB-193** | 5 declared dependencies are never imported (requests, markdown, beautifulsoup4, pdf2image, colorlog); jinja2 imported in 6 files, used in 1 | `requirements.in` |
| J | **DAB-194** | Dead code: `help_system.py` (636 lines, zero importers), `UserExperienceService` (8 of 9 public methods unreachable), 11 `DiscordBot` wrappers, 6 `ErrorManager` methods, 5 `GeminiClient` accessors, 2 logger classes + 3 methods, 9 long-tail functions, a dead attribute alias, 4 dead config fields, 87 unused import bindings | src/services/help_system.py (635 lines) + sweep-C6 SAFE list |
| L | **DAB-200** | `filetype="pdf"` is not enforced - any MuPDF-supported format is reachable from a `.pdf` filename | `discord_bot.py:1161` |
| M | **DAB-206** | `api_timeout_buffer` is dead config - remove or wire into the response budget | `config.yaml:61` |

### Informational (5)

These are recorded so they are not re-litigated: two are verified-clean negative results, two are
refutations, and one is a duplicate marker retained for traceability.

| Area | ID | Finding | File:line |
|---|---|---|---|
| D | **DAB-094** | FTS5 MATCH injection is DEFENDED (negative finding, control-verified) | `message_index_service.py:1109-1122` |
| E | **DAB-098** | REFUTED: `return` inside `with sqlite_transaction` does NOT discard the commit (2 sites, not 3) | `sqlite_utils.py:34-44` |
| F | **DAB-135** | REFUTED: `estimate_processing_time` edit-type key mismatch | `image_processing_service.py` |
| G | **DAB-146** | NEGATIVE (verified clean): no SQL injection, no path traversal, no stored XSS in the report UI | `report_web_server.py:104-201` |
| M | **DAB-208** | Live-worker batch loss on exception - requeue on failure or the pop at `:119` destroys the batch | `live_message_coordinator.py:127-137` |

---

## 7. Refuted and overstated claims

This section is what makes the rest of the document usable. Every claim below was knocked down or
cut back by someone who went looking for the evidence, and in several cases the person who knocked
it down was the same person who had been asked to confirm it.

### 7.1 Outright refutations (the claim is not true)

| Claim | Verdict and evidence | Where |
|---|---|---|
| **A SQLite write that exceeds the lock timeout returns falsy, the caller ignores it, and the write is lost silently with no exception and no error log** | **REFUTED, every clause.** V2 held a real `BEGIN EXCLUSIVE` lock from a second connection and exercised five paths. `sqlite_transaction` **raises** `OperationalError: database is locked` at 5.01 s (it does not return falsy). `ChannelSettingsService.set_live_enabled`, `set_personality` and `PinService.add_pin` each **log at ERROR** with the operation and the channel id, and their callers **do** check: `/live` replies "Failed to update live mode for this channel." (`personalization.py:114-117`), `/personality` and `/pin` likewise (`:68`, `:180`). `TokenTracker._record_usage_sync` raises and `response_generation.py:171-176` catches and logs at ERROR. The writes are lost — but loudly. What *is* real, and is filed separately, is that the 5.01 s block happens on the calling thread and the read side (`get_live_enabled`) is on the hottest path in the bot: DAB-035 / DAB-096. | V2 claim 10 |
| **`return` inside `with sqlite_transaction` discards the commit; 3 occurrences** | **REFUTED**, and the count is 2, not 3. `sqlite_transaction` is a `@contextmanager` generator; a `return` in the `with` body is a *normal* exit, so `__exit__(None, None, None)` resumes the generator and `connection.commit()` runs before the frame unwinds. Probe: `VERDICT return: committed / break: committed / raise: rolled back (correct)`. An AST scan of `src/` found exactly two `Return` nodes inside such a `with` (`pin_service.py:76`, `report_service.py:212`) and zero `Break` nodes, and neither is even reachable with a pending write. Recorded as `DAB-098` so it is not re-litigated; worth a pinning test in case someone refactors to a class-based `__exit__`. | `B5-04`, upheld by V3 §3.C |
| **`rag_migrations` is missing on a fresh install** | **REFUTED for a real boot** — and both sides of the dispute were right about different things. `_ensure_schema` does not create the table; only the two legacy-migration paths do, via a defensive `CREATE TABLE IF NOT EXISTS`. But in `DiscordBot.__init__`, `TokenTracker` creates `data/token_usage.db` at `:329` **before** `MessageIndexService` is constructed at `:355`, so the legacy path always finds a file, always runs, and always creates the ledger. Reproduced both cases: service alone → **absent**; `TokenTracker` first, as in `__init__` → **present**. The residual, real finding is `DAB-083`: the ledger's existence is an accident of construction order, and a future migration check written without the defensive `CREATE TABLE IF NOT EXISTS` will raise `no such table` from inside `__init__` — which per `DAB-067` is fatal. A fresh install also *burns* both one-shot migrations and logs "Copied legacy … data" for zero rows. | `B4-15` vs `C3`, adjudicated V3 §3.D |
| **A Google API key can reach a user-visible Discord message through a `?key=<KEY>` URL in an error** | **REFUTED for this SDK.** `google-genai` 2.11.0 authenticates with the `x-goog-api-key` **header** (`_api_client.py:798`), never a query parameter; `grep -rn "?key=" src/ main.py` finds nothing; `APIError.__str__` is `f'{code} {status}. {details}'` where `details` is the parsed response body, never the URL or headers; the SDK raises its own `APIError` subclasses and never calls `httpx.Response.raise_for_status()`, which is the one httpx API whose message embeds the URL. Probe with a real `ClientError` built from a 400 whose request URL carried a key: `KEY IN USER MSG: False`. **What survives is worse-shaped, not worse-severity:** the machinery has no scrubbing at all, `include_error_details` defaults to `True`, and the only thing that stopped an `httpx.HTTPStatusError` from leaking a key in the same probe was that its message happened to be 267 characters rather than under the 200-character gate. Filed as `DAB-153` (S2). | `B7-14` / `B7-16` |
| **`on_error` logs full event args including message content** | **REFUTED.** It does dump `args` (`discord_bot.py:558-559`), but `discord.Message.__repr__` omits `content` — it emits `id`, `channel`, `type`, `author`, `flags`. Empirically: `CONTENT LEAKED IN on_error log line: False`. The real content-logging sites are elsewhere and are filed as `DAB-155`: `enhanced_command_handler.py:252-257` logs the first 50 characters of every user message at **INFO**, and `media_extraction.py:508-512` logs uploaded-file text — in **full** for any file under 150 characters — at INFO, i.e. at the shipped default log level, while two adjacent sites in the same codebase deliberately redact. | `B7-17` |
| **`estimate_processing_time` has an edit-type key mismatch** | **REFUTED.** Every `EditType` member's `.value` is a key in `time_factors` and `image_processing_service.py:220` passes `request.edit_type.value`, so the lookup always hits. Recorded as `DAB-135`. | `B6-22` |
| **FTS5 MATCH injection is exploitable** | **REFUTED with a control experiment**, and this was the surface flagged as most likely to be exploitable. `_build_fts_query` extracts tokens with `re.findall(r"[A-Za-z0-9_@#./:-]{2,}", query)` — a class that excludes every FTS5 metacharacter — then wraps each token in double quotes. Thirteen attacks (wildcard, bare `OR`, `NEAR()`, column filter, phrase break-out, `^`, `NOT`, paren group, colon-prefixed, all-punctuation, a 100k-character query) all failed to reach either a cross-channel secret or a hidden message; the channel/hidden/deleted predicates are separate bound parameters. Recorded as `DAB-094`. | `C4-32` |
| **The report UI has SQL injection, path traversal or stored XSS** | **REFUTED, verified clean.** Every SQL parameter is bound; `status` is normalised against a five-element tuple and `report_id` is `int()`-cast twice; all seven user-controllable strings go through `html.escape` (default `quote=True`); there is no `add_static`, no `FileResponse` and no path joining, so traversal has no surface. Probes: `?status="open' OR '1'='1"` → 200 with the filter ignored, `DROP TABLE` attempt → table still queryable, `/reports/../../etc/passwd/status` → 404, `<script>` rendered escaped. Recorded as `DAB-146`. **The report UI's real problems are authentication (`DAB-143`), CSRF (`DAB-145`) and the URL that lies about the bind address (`DAB-144`)** — not injection. | `B7-04` |
| **The prior dead-code list includes the live `image_queue` command** | **REFUTED.** An improvement lane speculated that a 272-line discrepancy between two independent dead-code counts was explained by one list including `image_queue`. It does not: the SAFE list enumerates 50 methods by file and line and `image_queue` is not among them, nor does `general.py` appear in its deletion table. The underlying *warning* is nevertheless correct and important — `image_queue` has exactly one reference in the whole repo (its own definition at `general.py:264`, inside a conditional registration block at `:124`), so a naive reference-counting pass would delete a live user-facing command **and no test would fail** (`DAB-203`). Adopt the deny-filter guard before running the cleanup. | V3 §3.A |
| **`_pin_service` is attached during command registration, not in `__init__`** | **REFUTED — and the wrong statement is in `AGENTS.md`.** `discord_bot.py:364` constructs it and `:376` passes it into `HybridContextRetriever`; `personalization.py:138-142` only re-binds it with `getattr(bot, "_pin_service", None) or PinService(...)`. A cold-start probe confirms `hasattr(bot,'_pin_service') = True` before `on_ready` while the other three services are `False`. Practical consequence: pinned memories work throughout the pre-`on_ready` window; live mode, personality and user preferences do not. Two documents need correcting (`AGENTS.md`, and a subsystem map that contradicts its own detail table). | V3 §3.E |
| **The PDF `max_workers=1` executor is the bottleneck; widening it gives ~4×** | **RETRACTED.** Measured on the shipped code, widening the pool gives **1.21×** (13.59 s → 11.23 s at 8 concurrent jobs). The real cost is a pointless `pix.tobytes("png")` → `Image.open` round-trip when `pix.samples` is already an RGB buffer: deleting it gives **11.7× / 16.55×** (measured on two different fixtures) and makes one worker faster than eight were. Fix the round-trip (`DAB-197`), leave `max_workers=1`. The original observation — that the tenth concurrent uploader waits ~11 s and nothing on that path has a timeout — stands and is filed as `DAB-198`. | `C5-17` vs `I3-08`, adjudicated V3 §3.H |
| **The O(n²) splitter is caused by the four `^(\s*)` MULTILINE regexes** | **REFUTED as a causal attribution** (the measurement itself was fine). 99.1% of `tottime` is `find_code_block_boundaries`; `is_safe_split_point` returns at `markdown_utils.py:310` before `parse_markdown`, so in the worst case the MULTILINE patterns never execute. They are 66% of `parse_markdown` on fence-free text — a 4 ms term next to a 5,118 ms one. This matters because it changes the fix. | `B6-05` vs a subsystem map, adjudicated V3 §3.F |

### 7.2 Overstated claims (mechanism real, impact or precondition wrong)

| Claim | What was overstated | Corrected position |
|---|---|---|
| `B3-13` safety threshold fails open to `BLOCK_NONE` (claimed S1) | "Safety filtering silently disabled" misrepresents this codebase: `config.yaml:142-147` already ships `BLOCK_NONE` for all four categories, and the system prompts explicitly instruct the model to apply no content restrictions. A typo changes nothing relative to the shipped posture. | **S3** (`DAB-052`). V1 found two cases the claim missed: the SDK's own canonical names `BLOCK_ONLY_HIGH` and `OFF`, and any value with stray whitespace, all silently degrade to `BLOCK_NONE`. The victim is the operator who deliberately tightens safety, mistypes, and gets no warning. |
| `B3-17` chain-of-thought leak (claimed S1) | The trigger does not exist at HEAD: nothing sets `include_thoughts`, so the API returns no thought parts. | **S4 latent** per V1; carried at S1 in the register. §4 prints both. |
| `B4-02` legacy migration data loss (claimed S1) | The precondition is unreachable: the NOT NULL column set is byte-identical across all seven commits that ever touched the file. | **S3** per V1, latent S1 for the next schema change. §4 prints both. |
| `B5-05` no `_ensure_column` for 4 of 5 `token_usage.db` tables (claimed S1) | "An older database is never upgraded and queries referencing a newer column fail at runtime" is counterfactual: for those four tables no newer column exists in any revision, so no such database can exist. V1's git archaeology found that `channel_settings` is the **only** table that ever gained a column post-release — and it is precisely the one with the upgrade path. Even in the hypothetical, every accessor wraps its query in `try/except` and returns a silent default, so the failure would be a silent feature outage, not a crash. | **S4** per V1 (a maintenance hazard already documented as a rule in `AGENTS.md`); **S2** in the register (`DAB-099`) on the grounds that it bites on upgrade and fails loudly on one of the four tables. |
| `B5-07` `expanduser()` missing in some services (claimed S1) | "Resolves to two physically different files, splitting the database" only happens if a literal `~` directory already exists in the process CWD — nothing in the repo creates one, because all three `mkdir` sites run *after* `expanduser`. The real default-case symptom is different and arguably worse: the non-expanding services cannot open the file at all and, because `_ensure_table` and every accessor swallow exceptions, preferences / hide-unhide / personality silently do nothing forever. | **S3** per V1, **S2** in the register (`DAB-101`); requires a non-default `~` path either way. Count corrected twice: "3 of 7 services" → **4 of 7** (`report_service.py:42` was missed). |
| `B2-05` router cache 200-character prefix key (claimed S1) | The leaked artefact is a routing *label*, not content: one wrong `intent`/`complexity`/`needs_context` for a colliding message inside a 300 s window. Nothing is written, no privilege is crossed, and rewording the opening is a workaround. V1 measured the threshold exactly: 199 identical leading characters → no collision, 200 → collision. | **S2** (`DAB-022`); V1 says S3. |
| `B2-02` live batch loss (claimed S1) | Nothing durable is lost — the messages are already in the RAG index before the live fork, and the worker self-restarts. | **S3** per V1; S1 in the register. §4 prints both. |
| `B6-07` LaTeX with 2+ equations never renders (claimed S1) | 100% reproducible and it kills an advertised feature, but the exception is caught at `content_renderer.py:504`, `plt.close('all')` prevents figure leaks, and the renderer degrades to inline code blocks — no content is lost. | **S2** (`DAB-114`), agreed by V2. One-keyword fix: delete `transform=ax.transAxes` from `content_renderer.py:493`. Note the tests cannot see it for two independent reasons: they mock `plt.subplots` (a `Mock` accepts the illegal kwarg) and they pass a single expression, so the `if i < n - 1` separator line is never reached. |
| `B7-01` report web UI (claimed S1) | The shipped default binds loopback (`config.yaml:16`). | **S2** in the register, **S3-by-default / S1-if-rebound** per V2. Both agree the aggravating factor is `DAB-144`: `ReportWebServer.url` rewrites a `0.0.0.0` or `::` bind to `127.0.0.1` in the very log line the operator reads, so an operator who exposes the panel is told it is loopback. V2 also confirmed the CSRF half by POSTing with `Origin: https://evil.example` and getting 303. |
| `B7-05` no authorization model (claimed S1) | An umbrella over the individual authorization findings. In a guild of friends "anyone can flip a setting" is a nuisance; the destructive member of the family is `DAB-141`, rated separately at S1. | **S2** (`DAB-140`), agreed by V2, who confirmed the count exactly: **1 of 35** nodes gated (1 of 37 with the image service enabled), all 35 `dm_permission=True`, `guild_only=False`. |
| `B7-07` `/report-status` cross-tenant read (claimed S1) | "Cross-tenant" presupposes tenants; the target deployment has one guild. The disclosed data is user-authored report text, admin triage notes and a display name — not credentials — and the path is read-only. | **S2** (`DAB-147`), agreed by V2, who reproduced the cross-guild read and the id enumeration sweep and added that they "would not argue hard against someone calling it S1". |
| `B7-09` `/dev` ungated (claimed S1) | Rated S1 because it "disables output sanitisation", but the live secret-exfiltration path is refuted (see 7.1). What `/dev` actually leaks is stack traces, absolute paths and internal module layout. | **S2** (`DAB-142`). V2 adds a finding the claim *missed*: with `/dev` never touched, `error_manager.py:307-310` still appends raw exception text up to 200 characters to public replies, because `create_error_context`'s `include_error_details` defaults to `True`. |
| `C2-12` hard `fitz`/`PIL`/`numpy` imports (claimed S1) | All three are declared, pinned, hard dependencies installed by the only supported bootstrap, and the failure is a traceback naming the exact module and line — the most diagnosable failure mode available. | **S3** in the register, **S4** per V2 ("a defensible design preference, not a bug"), with the caveat that `fitz`'s only use is PDF conversion, so a lazy import there is a reasonable cleanup. |
| `C5-01`, `C5-02`, `C5-04`, `C5-05` performance findings (claimed S1) | All measured at 100k–350k indexed messages; a hobbyist single-guild install is two to three orders of magnitude smaller. | **S2** (`DAB-078`, `DAB-077`, `DAB-027`, `DAB-028`) — still S2 because the startup reconcile is 97.8% of every boot and the vector paths block the loop at *any* size. `C5-03` (the splitter) stays S1 because its trigger is a message length, not a corpus size. |
| `B8-01` formatter mutates `record.msg` (claimed S2) | Noisy logs (extras printed 2–4× per record), no loss. Its dangerous sibling — the `%` that *drops* records — is a different finding and stays S2. | **S3** (`DAB-164`); `DAB-165` stays S2. |
| `B6-05` splitter, "41.25 s crossed around 240 KB" | Both the threshold and the mechanism were wrong in the claim's favour. | Corrected upward: 41.25 s at **~175 KB**, and the actual disconnect trigger is `heartbeat_timeout=60.0`, crossed at **~211 KB**. |
| `C4-14` PDF bomb, "no timeout, blocks the bot" | The claim omitted that the work *is* offloaded to a single-worker executor, so the event loop is not blocked — and omitted that the single worker means one attacker serialises PDF handling for everyone. | Both corrections folded into `DAB-198`. |
| Feature inventory "131 features" | The body runs `F-01…F-137`; the header's ranges are stale. | **137**. Every downstream "131 features" statement inherits the error. |
| Dead-code total "−1,755 lines is the safe floor; −2,027 is unverified" | The two counts measure different scopes against different denominators. Reconciled item by item: `UserExperienceService` 8 methods vs 6 (125 lines), 4 dead `BotConfig` fields (25), unused import bindings 87 vs ~40 (47), 3 coordinator helpers (39), 3 `ErrorType` members (3), `RAW_LATEX_RE` (4), one attribute alias (1) — 244 of the 272-line gap explained, the remainder counting convention. | Publish **−2,027** as the total (Python lines across `main.py`+`src`+`scripts`+`tests`) and **−1,755** as the code-only subtotal. Both were verified green at 125/125 on independent `/tmp` copies. |

### 7.3 What the refutations tell you about the corpus

Eighteen of the twenty independently re-derived claims came back with a lower severity than
claimed, one came back refuted outright, exactly one survived at S1, and the single most
confident-sounding security claim in the whole corpus (the `?key=` exfiltration) was false. The failure mode was not fabrication — every mechanism except one
reproduced exactly — it was **severity inflation and unstated preconditions**. Two patterns
recur: rating a defect by its worst imaginable deployment rather than the shipped one, and
asserting a consequence ("the database splits in two", "an older database exists") without checking
whether the precondition is reachable in this repository's own history. When reading any finding
below S1, assume the mechanism and check the trigger.

---

## 8. Historical corrections

The repo ships two documents that describe its own defects. Both were re-validated by git
archaeology and mutation testing (the repo was copied to `/tmp`, bugs were reintroduced *in the
copy*, and the regression tests were re-run there). Both are misleading in ways that matter.

### 8.1 `docs/DEEP_BUG_HUNT_REPORT.md` is self-refuting

> The file was **deleted** in round 2 Phase 6 on the strength of this section; see the
> disposition note at the end of §8.3. Everything below was measured while it was still in the
> tree, and `git show 88e330b:DEEP_BUG_HUNT_REPORT.md` still reproduces it.

**The build identifier does not exist.** `git cat-file -t 8bc80f9` → *not a valid object name*.
No match across all 824 objects in the repository (commits, trees and blobs); 69 commits scanned
across all branches; `git fsck --lost-found` empty. The code the report describes —
`discord_bot.py:1982-2006`, `:2349-2415`, `:537-597` — matches commit **`936ab58`**. The report was
written against `936ab58` and mislabels its build.

**The report shipped in the same commit as the fixes for all five of its "Open" bugs.**
`git show 88e330b --stat`:

    DEEP_BUG_HUNT_REPORT.md               | 254 ++++    <- the report, all 5 bugs "Status: Open"
    tests/test_bug_regressions.py         | 359 ++++    <- the regression tests for those 5 bugs
    docs/tech-debt-register.md            |  40 ++++
    src/bot/discord_bot.py                | 209 +--     <- BUG-0001/0002/0003/0004/0005 fixes
    src/services/gemini_client.py         |  13 +-      <- BUG-0002 fix
    src/services/message_index_service.py |  19 +-      <- BUG-0004 fix
    src/services/message_splitter.py      |  54 +-      <- BUG-0003 fix

Every `Status: Open` field was stale on arrival. Commit `5e45599` merely moved the file into
`docs/` — `diff <(git show 88e330b:DEEP_BUG_HUNT_REPORT.md) docs/DEEP_BUG_HUNT_REPORT.md` is empty.
**The document has never been edited since it was written.** Recorded as `DAB-210`.

### 8.2 True status of BUG-0001 … BUG-0005

| Bug | Printed status | True status at `c83f740` | Evidence |
|---|---|---|---|
| **BUG-0001** nested timeout cancels retries | Open, S2/P1 | **FIXED** in `88e330b` (the outer `wait_for` was deleted) — and **superseded by `DAB-042`**: with the outer budget gone there is no whole-sequence deadline at all. Virtual-clock probe of `_run_response_attempts` with every attempt timing out: **480.0 s**; analytic ceiling with backoff **488.5 s** (`4 × 120 + 8.5`). The fix also left `api_timeout_buffer` as dead config (`config.yaml:61` → `config.py:78`, read by nothing — `DAB-206`). | `response_generation.py:405-418` has no wrapper; `gemini_client.py:1199,1283-1316` |
| **BUG-0002** routing bypasses model selections | Open, S3/P2 | **FIXED** in `88e330b`. Precedence is resolved once in `_resolve_request_preferences` (`discord_bot.py:1078-1104`): request → user preference → runtime `/config model` → complexity default; `routed_model` at `:821-826` is now computed for logging only. | `has_runtime_model_override` at `gemini_client.py:97-99` |
| **BUG-0003** long fenced code exceeds the limit | Open, S3/P2 | **PARTIALLY FIXED.** The overflow symptom is closed inside `MessageSplitter` and the three `response_delivery` send paths (five probe inputs, all `max part = 2000, over2000 = 0`). The **root cause is untouched**: the pre-guard pipeline still produces `[1981, 2030, 1153]` with `integrity=False` — the report's own probe result reproduced character-for-character — and the post-hoc guard at `message_splitter.py:125-133` then discards the formatted split. | See `DAB-119`, `DAB-207` |
| **BUG-0004** paginator edits overwrite RAG rows | Open, S3/P2 | **FIXED**, both the primary defect and the "related persistence defect". The guard is at `rag_event_coordinator.py:71-72` exactly as reported (`if before.content == after.content: return`); `upsert_message` now takes `hidden: Optional[bool] = None` and preserves existing state (`message_index_service.py:413,431-437`), and `deleted_at = NULL` was removed from the `ON CONFLICT` clause. | Mutation: deleting `:71-72` → `FAILED` |
| **BUG-0005** live attachment batches discard queued messages | Open, S3/P2 | **FIXED for the reported mechanism** — `live_message_coordinator.py:209-220` requeues `messages[i+1:]` at the head and folds all earlier messages into the prompt. Four other live-mode loss paths remain and are **not** regressions of this bug: `DAB-019` (batch lost on worker exception), `close():166` clearing pending, `:113-116`/`:139-142` popping on disable, and `:243` dropping rate-limited users' messages. | Mutation: restoring the discard → `FAILED (failures=1, errors=1)` |

An important consequence of BUG-0003's partial fix: it traded "Discord rejects an oversized
message" for "Discord renders mangled code". Because the guard fires on nearly every long response
— including ordinary prose that over-runs by **one character** (`[1976, 2001, 2001, 189]`) —
`_preserve_code_blocks` is bypassed and the output is a raw character chop. A realistic 4,600-character
code answer produces three pages with an unterminated fence on page 1, no fence on page 2, and an
orphan closing fence on page 3.

### 8.3 The two regression tests that are insufficient or actively obstructive

**`test_main_response_path_does_not_wrap_client_retry_timeout`** (`tests/test_bug_regressions.py:110`)
— **a live obstruction** (`DAB-204`, S2). Three mutations were run against it:

| Mutation | Result | Reading |
|---|---|---|
| Reintroduce the exact original bug (`wait_for(generate_response(...), timeout=t+10)`) | `FAILED` | Catches it — but the coordinator's blanket `except Exception` (`response_generation.py:486`) swallows the `AssertionError` and the surfaced failure is a misleading `TypeError: object Mock can't be used in 'await' expression`. |
| Same bug via the modern idiom (`async with asyncio.timeout(...)`) | `OK` | **Blind.** The test guards a call name, not a behaviour. |
| A **correct** whole-sequence deadline — the fix `DAB-042` needs (`timeout = per_attempt × (max_retries+1) + 30`) | `FAILED` | **The test fails a correct implementation.** Anyone fixing `DAB-042` will hit it and, reading its name, may conclude their fix is wrong. |

Rewrite it to assert the *budget* (for example, that the effective deadline is at least
`per_attempt × (max_retries + 1)`) rather than the absence of `asyncio.wait_for`, and do that
**before** attempting `DAB-042`.

**`test_request_model_precedence`** (`tests/test_bug_regressions.py:37`) — **insufficient**
(`DAB-205`, S3). It calls the helper directly with a `SimpleNamespace` bot and never exercises
`_process_message_with_context`, which is where the bug lived. Mutation A (swapping user-preference
and global order inside the helper) → `FAILED`, so the helper's table is guarded. Mutation B
(reinstating the original root cause, `model_override = routed_model` right after
`discord_bot.py:821`) → **`Ran 125 tests … OK`**. Not one test notices, and with that line back,
user preferences and `/config model` are silently bypassed again exactly as originally reported.

A third test is worth a decision rather than a fix: **`test_live_batch_rate_limits_each_participating_user`**
(`:208`) **pins message-dropping for rate-limited users** as intended behaviour (`DAB-209`, S3). Any
change that retains those messages will break it. Decide whether dropping is the contract before
touching `DAB-019`'s neighbourhood.

### 8.4 `docs/tech-debt-register.md`

Substantially sound — 6 of 8 evidence rows HOLD, 2 have DRIFTED, 0 are genuinely unresolved — with
two false statements (`DAB-211`, S3):

| Item | Verdict | Detail |
|---|---|---|
| TD-001 | DRIFTED | `setup_commands` is still exactly 35 lines; `DiscordBot` is **1,157** lines (claimed 1,131) and the module is **1,452**. |
| TD-002, TD-005, TD-006, TD-007, TD-008 | HOLD | `generate_response` still 70 lines; PDF executor as described; `from_yaml`/`validate` 7 and 3; **exactly one** `sqlite3.connect` under `src/` (`sqlite_utils.py:20`); 16 `>=` bounds and 57 `==` pins. |
| TD-003 | DRIFTED favourably | 98 tests in 12 files when written; **125 in 13** now. |
| **TD-004** | **FALSE, and false on the day it was written** | It claims the README's project layout matches tracked files. `README.md:202-207` still lists `BOT_SYSTEM_REPORT.md`, `pipeline.html` and `message-sequence-flowchart.html` at the repo root — the two HTML files were **deleted by `5e45599` itself** and the report was **moved to `docs/` by `5e45599` itself**. The identical stale lines are in `git show 5e45599:README.md:181-186`. |
| Scan note: "no cross-file duplicate blocks" | **FALSE** | An exact 8-line sliding-window scan gives **26 cross-file windows in 7 file-set groups at HEAD**, 29 at `5e45599`, and 3 already at `88e330b`. The dominant source is an identical ~15-line import header replicated across the five `command_modules/*.py` files — created by `5e45599`, the same commit that re-asserted the note. |
| Scan notes: module and function size | DRIFTED | 16 of 51 modules over 500 lines → **17**; 94 functions over 50 lines → **101**. |
| Header date | Wrong | Dated 2026-07-12; the resolution content is from `5e45599` (2026-07-13). |

**Recommended disposition — superseded by what was done, round 2 Phase 6.** This paragraph
recommended moving `docs/DEEP_BUG_HUNT_REPORT.md` to `docs/archive/` with a banner. It was
**deleted** instead. The banner was tried first and failed on its own terms: the SUPERSEDED
banner added to the file went stale in turn and ended up asserting a current gate of "140 tests
in 15 files" against a suite of 394 in 37 — a correction that needed correcting. Git history is
a complete archive; `git show 88e330b:DEEP_BUG_HUNT_REPORT.md` returns the file in full.

Both carry-forwards this paragraph names survive the deletion, which is what made it safe.
BUG-0003's root-cause analysis is in §8.2 above and in `AGENTS.md`, re-measured at `083ebea`: a
6,918-char fenced response still splits into 4 pages, every one `hard_split=True`. BUG-0001's
"outer budget that includes every attempt" is in `analysis-tickets/DAB-042.md:22`, which also
records where the budget must go — `GeminiClient._run_response_attempts`, not
`response_generation.py`, or DAB-042 and DAB-204 contradict each other.

The `docs/tech-debt-register.md` corrections listed here were applied on 2026-07-31.

---

## 9. Coverage and limits

### What was not exercised at all

- **No live Discord gateway.** Every Discord interaction was simulated with `SimpleNamespace` /
  `AsyncMock` fakes or by introspecting the real `app_commands` tree built by `setup_commands`. So
  gateway-level outcomes are inferred from discord.py's source, not observed: the forced reconnect
  in `DAB-115` (V2 read `KeepAliveHandler.run` and computed the crossing point from its own timing
  curve), the re-IDENTIFY behaviour that makes `DAB-002` "effectively" rather than strictly
  permanent, and every rate-limit and permission response from Discord itself.
- **No real Gemini credentials.** All model interaction was faked or driven through real
  `google.genai` *types* and *error* objects constructed locally. Provider behaviour claims are
  therefore verified against the installed SDK's contract (`google-genai` 2.11.0), not against the
  service — most consequentially `DAB-039`, whose demotion rests on "Gemini never returns thought
  parts unless `include_thoughts=True`".
- **No multi-guild deployment.** Cross-guild findings (`DAB-141`, `DAB-147`, `DAB-143`) were
  demonstrated with two guild ids in one database, which is faithful to the code but not to
  operational reality.
- **No load, no soak, no real disk pressure.** Memory and timing numbers come from single-process
  probes on one machine (117 GB RAM, see §10). The unbounded-growth projections
  (219 MB/yr, 402 MB/yr) are extrapolations from per-row sizes, not observations.

### Features no lane meaningfully examined

These have zero substantive coverage across all 29 reports. They are not "clean" — they are
*unlooked-at*.

| Feature | Symbol | Why the gap matters |
|---|---|---|
| Reply-anchor context | `hybrid_context_retriever.py:452` `_load_reply_anchor_context`, consumed at `:531,617-621` | The largest gap in the corpus. Anchors are excluded from the candidate pool and then re-prepended to the finished pack; nobody checked whether the exclusion and the re-prepend agree, what happens when an anchor is hidden or deleted, or how anchors compete with pins for slots — while `DAB-074` shows the audio path truncates with `context[-5:]`, which drops anchors first. Live on **every reply-to-bot message**. |
| "Image required" suggestion | `enhanced_command_handler.py:566` `_suggest_image_upload` | Reached from `:318` whenever the router says IMAGE_EDIT with no attachment. Zero tests, zero analysis. |
| `__pycache__` cleanup script | `scripts/cleanup_pycache.py:31,86` | Invoked by `start.sh --rebuild`, the documented recovery procedure for the poisoned-venv failure (`DAB-181`). Nobody checked what it deletes. |
| Paginator sender-priority window | `response_delivery.py:54-58` (2 s window), `:263` (120 s timeout) | The 2-second race over who may press the buttons was never analysed — and `DAB-152` found exactly this class of bug in `PinDeleteView`. |
| `/preferences language` injection point | `gemini_client.py:1719-1720` | The stored language string is interpolated straight into the system prompt. It is constrained by a Discord choice list today, but `valid_languages` is one of the 44 unvalidated config fields (`DAB-106`). Nobody joined those two facts. |
| API-response error mapping | `response_generation.py:97-140` `handle_response_error` | Three lanes name it; **none audited the 11-entry mapping table**. Reading it during reconciliation: `"max_tokens" → INVALID_REQUEST` tells the user their *request* was invalid when the real cause is the output cap, and `"safety_filter"`/`"recitation" → EMPTY_RESPONSE` hides a safety block behind "I couldn't generate a response". Both are user-visible wrong diagnoses on realistic triggers. **This is a real, unfiled S3** — it has no DAB id because it was found after the register was built. |
| NL image edit and generation | `enhanced_command_handler.py:333,441` | Two of the three router destinations, **0% test coverage** (332 statements, 0 hit), with a hard-coded 120 s / 3 s poll loop and their own token-usage path. |
| Audio / verbatim transcription | `media_extraction.py:311-342`; prompt rules at `config.yaml:225-231,277-280` | Touched only for the `context[-5:]` truncation. The substantial hand-written transcription prompt block in `config.yaml` was never read by any lane, and the feature is not advertised in the README. |
| Finish-reason interpretation | `gemini_client.py:1048,1557,1602` | The empty-STOP retry path was covered; `_normalize_finish_reason`'s integer mapping and `recitation` handling were not. |
| Streaming | `gemini_client.py:1389` `on_chunk` | Mentioned once, as a dead path (`on_chunk=None` always) — while `tests/test_gemini_pipeline.py:422` tests it. Inverted coverage nobody costed. |
| `scripts/health_check.py` | 5 check functions | Referenced by three lanes, audited by none — and it is the only thing in the repo that actually contacts Gemini, which matters given `DAB-183` (`validate_service_connectivity` hardcodes `all_ok = True` and never opens a socket). |
| Resumable backfill cursor | `message_index_service.py:791,808,848` | Throughput was measured; cursor **correctness** across an interrupted run was not — which is exactly what the proposed batching change would perturb. |

### Structural gaps in the corpus itself

1. **No lane owned the command callbacks.** Authorization, model precedence and advertisement were
   each audited by a different sweep, but **26 of 37 command nodes have a callback body that no
   lane read line by line and no test executes**. The unfiled `handle_response_error` finding above
   is what that gap produces.
2. **No lane audited `/summarize`'s history walk** (`research.py:313-346`), which builds
   `MessageContext` objects — the same `MessageContext` whose `__post_init__` rejects real Discord
   shapes (`DAB-108`). The two facts were never connected.
3. **Cross-feature interaction is under-covered.** `DAB-198` (PDF bomb) × `DAB-197` (single worker,
   16.5× waste) × the absence of any `wait_for` on that path is one compound failure found by three
   lanes separately and never composed. The same is true of `DAB-095` (5 s lock) × `DAB-096` (on the
   loop) × `DAB-065` (silent `False`), which is the actual mechanism of the worst data-loss bug in
   the register.
4. **The feature count in the inventory is wrong** (131 in its header, 137 in its body), so any
   coverage percentage derived from it is off by 4%.

### Findings that remain THEORETICAL or unresolved

| Finding | What is unresolved |
|---|---|
| `DAB-037` (`B2-23`) unreferenced `create_task` for reaction removal | The only entry the corpus filed as THEORETICAL outright; the entry point is currently dead. |
| `DAB-024` (`B3-18`) router/selector/embedding calls have no timeout | Mechanism VERIFIED, trigger THEORETICAL — it needs a real hung provider call. |
| `DAB-043` (`B3-02`) server-suggested `retry_after` honoured uncapped | Mechanism VERIFIED; the 3-hour stall needs the provider to actually send such a value. |
| `DAB-039` chain-of-thought | Turns on provider behaviour verified only against the SDK contract. UNPROVABLE-HERE. |
| `DAB-115` gateway disconnect | The quadratic curve and the loop stall are measured; the disconnect itself is inferred from discord.py's source. |
| `DAB-066` / `DAB-099` legacy-database preconditions | Refuted against *this* repository's history only. A fork, a hand-edited schema, or an out-of-tree build could still hold a triggering database. |
| Threat-model dependence | Roughly a dozen severities move by one level if the deployment is not the assumed one (public multi-guild bot, or a host with under 2 GB RAM). The most sensitive are `DAB-143`/`DAB-144` (S2 → S1 the moment `web_host` changes) and `DAB-198` (S2 → S1 under 2 GB). |

---

## 10. Reproducibility

Verified independently by the integrity pass, which also confirmed that the ~25 analysis agents
left the tree untouched.

### Interpreter and environment

| | |
|---|---|
| Test interpreter | **CPython 3.12.13** — `Python 3.12.13 (main, Jul 23 2026, 14:43:28) [Clang 22.1.3]`, at `.venv/bin/python` |
| System interpreter | CPython 3.13.14 `(main, Jun 10 2026, 18:10:12) [GCC 15.2.0]` — **cannot run this suite** |
| `python3.12` on `PATH`? | **No.** `start.sh` (lines 18-20, 38, 75-76) searches `python3.12`, `python3`, `python` and hard-refuses anything that is not exactly 3.12, so **`./start.sh` cannot bootstrap on this machine unaided**. |
| How `.venv` was actually built | With a **standalone `uv`** (`uv = 0.12.0` per `pyvenv.cfg`, wrapping a uv-managed CPython at `~/.local/share/uv/python/cpython-3.12.13-linux-x86_64-gnu`). This route was necessary because **the corporate pip index returns HTTP 403 for `discord-py`**, so the documented `pip install -r requirements.txt` path cannot complete here. |
| `pyvenv.cfg` | `implementation = CPython`, `version_info = 3.12`, `include-system-site-packages = false` |
| OS | Debian GNU/Linux **rodete** (gLinux), kernel `6.18.14-1rodete4-amd64`, `x86_64`, 117 GB RAM |

### Key dependency versions (56 distributions installed)

| Package | Version | Package | Version |
|---|---|---|---|
| discord.py | **2.7.1** | aiohttp | 3.14.1 |
| google-genai | **2.11.0** | pydantic | 2.13.4 |
| numpy | **2.5.1** | PyYAML | 6.0.3 |
| matplotlib | **3.11.0** | python-dotenv | 1.2.2 |
| pillow | **12.3.0** | httpx | 0.28.1 |
| pymupdf | **1.28.0** (MuPDF 1.29.0) | SQLite | 3.53.1 |

`google-generativeai` is **not** installed; this project uses the newer `google-genai` SDK. Several
declared dependencies are installed but never imported (`requests`, `markdown`, `beautifulsoup4`,
`pdf2image`, `colorlog` — 0 import sites each, `DAB-193`).

### Commands

Both run **from the repo root** — there is no `tests/__init__.py` and no `sys.path` shim, and
`pytest` is not configured and must not be used:

    .venv/bin/python -m unittest discover -s tests -p "test_*.py"
    .venv/bin/python -m compileall -q main.py src scripts tests

### Baseline: 125 tests, 7 runs, zero flakes

| Run | Tests | Result | Wall |
|---|---|---|---|
| 1 | 125 | OK | 1.007 s |
| 2 | 125 | OK | 0.998 s |
| 3 | 125 | OK | 1.006 s |
| 4 | 125 | OK | 1.005 s |
| 5 | 125 | OK | 0.999 s |
| 6 | 125 | OK | 1.040 s |
| 7 | 125 | OK | 1.042 s |
| (8, post-cleanup confirmation) | 125 | OK | 1.004 s |

Runs 3–7 were executed with `-v` and compared per test id: **125 unique ids in every run, set
equality across all five, zero failures, zero errors, zero skips, zero expected failures**.
`compileall` exits 0. Notably the matplotlib-LaTeX test that `AGENTS.md` says self-skips did *not*
skip — LaTeX rendering is genuinely exercised.

The known timing-sensitive test, `tests/test_rag_optimization.py:104`
(`EmbeddingEfficiencyTest.test_cache_mutation_waits_for_initial_load_lock`, a negative 50 ms
window), **did not flake**: 7/7 in-suite plus 50/50 in isolated stress = **57/57**. Line 104 only
fails if the lock is genuinely uncontended (a logic failure, not a scheduling one); the
load-sensitive assertion is line 108's `worker.join(timeout=1)`. `AGENTS.md`'s caveat remains good
advice for CI, but the test is not flaky on this machine.

The suite prints tracebacks and `DeprecationWarning`s during normal operation
(`discord.errors.HTTPException: 500 Server Error`, `Failed to process PDF bad.pdf`,
`RAG backfill failed for channel 20`, and `datetime.utcnow()` deprecations from four services —
`DAB-110`). These are deliberate error-path exercises and pre-existing warnings, not failures.

### Worktree integrity during the analysis

`git status --porcelain`, `git diff --stat`, `git diff --cached --stat`, `git ls-files -m`,
`git ls-files -d`, `git ls-files -o --exclude-standard` and `git stash list` were all **empty**;
all 85 tracked files match HEAD. No commits were created — the whole reflog is
`clone → checkout dev → one fast-forward pull`, and HEAD equals `origin/dev` exactly. No `data/`,
`logs/`, `temp/`, `*.db`, `.env` or `*.log` was left in the tree. The only debris found was ten
`*.cpython-313.pyc` files byte-compiled by the system Python 3.13 at 22:17, before `.venv` existed
at 22:41; they were confirmed untracked and gitignored, then deleted. Every probe ran under `/tmp`.

**Conclusion: the 125-test suite is deterministic and is a trustworthy regression gate for the fix
programme in this document.** Three of the recommended fixes deliberately change that number —
DAB-001 adds a 9-test file, DAB-114 requires the `plt.subplots` mocks to be replaced, and the §8.3
rewrites change two pinned assertions. `tests/test_command_registration.py`'s `22` and `35`
literals should be bumped only for genuine tree changes, per the three-file rule in `AGENTS.md`.
