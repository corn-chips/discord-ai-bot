## Analysis Backlog

Last updated: 2026-07-29 | Repo state: branch `dev`, HEAD `6f1dc79` | Gate: 140 tests, OK
Findings below were measured at HEAD `c83f740` against a 125-test baseline; the document state
above is current. Trust the finding, re-locate the line number, and quote the 140 gate.

Tier 0: 20 (19 open, 1 done: DAB-157) | Tier 1: 12 | Tier 2: 5 (4 open, 1 done: DAB-194) | Tier 3: 47 (compact, no tickets) | Tickets written: 37 (35 open)

### What this is

One prioritized worklist joining **defects** and **improvements**. It is derived from a 33-report
analysis corpus whose master register (`DAB-001` … `DAB-211`, 211 canonical findings after
deduplicating 311 raw lane IDs) lives in the reconciliation report, not in this repo. That register
recalibrated every severity against one threat model — a **self-hosted, single-guild, hobbyist
deployment whose operator personally pays the Gemini bill** — producing 9 S1, 72 S2, 82 S3, 43 S4
and 5 informational findings.

This document does not restate the register. It answers one question: **what should be done next,
and in what order.** Every row that is actionable has a ticket in `docs/analysis-tickets/`, written
so an implementation agent can execute it with no other context.

### How to use it

1. Read the tier you are working in. Tiers are ordered by priority score; within a tier, order is
   by score then by dependency.
2. Open the ticket file named in the last column. It carries file:line evidence, the measured
   payoff, a reproduction, acceptance criteria, the exact files to touch, and the regression test
   to add.
3. Check the **Blocked by** line in the ticket before starting. The dependency graph at the bottom
   of this document supersedes the tickets — but `docs/ANALYSIS_CORRECTIONS.md` supersedes the
   graph. Two of the edges recorded here were later refuted; read that file first.
4. Re-run `python -m unittest discover -s tests -p "test_*.py"` **from the repo root** after each
   commit. Several tickets deliberately change the pass count; each says so.
5. Adding a slash command still means three files (`AGENTS.md`, "Adding a slash command"). Adding a
   config key still means four places. Several tickets do both.

### Scoring method

Reused unchanged from `docs/tech-debt-register.md` so the two registers stay comparable:

```
priority = (impact if unfixed  x  frequency of encounter) / fix effort
```

- **Impact**: Low = 1, Medium = 2, High = 3, Critical = 4
- **Frequency**: Rare = 1, Occasional = 2, Common = 3, Continuous = 4
- **Effort**: S = 1, M = 2, L = 3, XL = 4
- Equal scores are ordered by impact, then by canonical ID for stability.

**One documented extension.** The analysis corpus uses an XS band (<= 1 hour) that the original
four-point effort scale cannot express, and the corpus's highest-value items are literally one- to
three-line changes. This backlog therefore scores **XS = 0.5**. S/M/L/XL keep their original
values, so every `TD-00x` score in the existing register remains valid and directly comparable.

Impact is judged under the register's threat model, which is why some ratings differ from a
generic reading: data-loss findings gain a level (no backup, no replica, no DBA), cost findings
gain weight (one person pays), authorization findings lose a level unless the action is
destructive or irreversible, and performance findings lose weight **unless they block the event
loop**.

### Severity, payoff and confidence columns

- **Severity** is the recalibrated value from the master register (S1 worst, S4 hygiene).
- **Payoff** for improvement rows is the *measured* before/after from the lane that prototyped it.
  Unmeasured proposals are marked "estimated".
- **Confidence** is per ticket: `VERIFIED` (a probe reproduced it), `SUSPECTED`, or `THEORETICAL`.
  Two independent verification passes re-derived nine S1 claims from source and disagreed with the
  register on several severities; where that happened, the ticket carries both verdicts. Do not
  treat any single number here as unchallenged.

---

## TIER 0 — DO FIRST

High impact, cheap, and mostly independent. This tier contains all nine recalibrated S1 defects
plus every quick win whose payoff was measured on a prototype. Fourteen of the twenty are XS or S.

| # | ID(s) | Title | Type | Severity / payoff | Effort | Score | Depends on | Ticket |
|---|---|---|---|---|---|---|---|---|
| 1 | DAB-041 | Dead `error_class`: timeouts and connection resets classified as permanent | BUG | S2 | XS | **18.0** | — | [DAB-041](analysis-tickets/DAB-041.md) |
| 2 | DAB-001 | Hybrid-RAG fallback `try` spans delivery: duplicate reply, duplicate bill | BUG | **S1** | XS | **16.0** | — | [DAB-001](analysis-tickets/DAB-001.md) |
| 3 | DAB-078 | **DONE** (Phase 4) — `search_recent` cannot use its index. Landed by **replacing** the `(guild_id, channel_id, created_at)` composite with two partial indexes rather than adding to it: same read plans, +15% on writes instead of +35%, and no size growth. Re-measured 924x channel / 2273x guild / 549x on the pending poll | IMPROVEMENT | 1200x / 1870x measured | XS | **16.0** | — | [DAB-078](analysis-tickets/DAB-078.md) |
| 4 | DAB-212 | **REFUTED** (Phase 4) — the 1844x is measured on a query production never issues; on the real channel-scoped form it is **111x slower** than leaving the `ORDER BY` alone once DAB-078 lands, and it reintroduces the temp B-tree it exists to remove. Not applied; `M-DAB212` stops it being re-applied. See [`ANALYSIS_CORRECTIONS.md`](ANALYSIS_CORRECTIONS.md) item 13 | IMPROVEMENT | 1844x measured, two lines | XS | **16.0** | — | [DAB-212](analysis-tickets/DAB-212.md) |
| 5 | DAB-115 | `split_message` is O(n^2) on the event loop | BUG | **S1** | S | **12.0** | — | [DAB-115](analysis-tickets/DAB-115.md) |
| 6 | DAB-114 | Every LaTeX response with 2+ equations fails to render | BUG | S2 | XS | **12.0** | — | [DAB-114](analysis-tickets/DAB-114.md) |
| 7 | DAB-095 | **PARTIAL** (Phase 4) — busy timeout landed as `bot.sqlite_busy_timeout_ms`; **WAL deferred** until connections are pooled, because it costs +0.34 ms on every call here and made zero difference to the DAB-065 trigger in all four lock cells. FKs still off. See [`ANALYSIS_CORRECTIONS.md`](ANALYSIS_CORRECTIONS.md) item 14 | IMPROVEMENT | 5.01 s failure -> 3.3 ms; 4.2x writes | S | **9.0** | — | [DAB-095](analysis-tickets/DAB-095.md) |
| 8 | DAB-002 | `on_ready` swallows `setup_commands` failure | BUG | **S1** | S | **8.0** | — | [DAB-002](analysis-tickets/DAB-002.md) |
| 9 | DAB-039 | Chain-of-thought posted to Discord as the answer | BUG | **S1** | XS | **8.0** | — | [DAB-039](analysis-tickets/DAB-039.md) |
| 10 | DAB-141 | `/rag delete scope:all` is ungated, cross-guild, irreversible | BUG | **S1** | XS | **8.0** | DAB-203 | [DAB-141](analysis-tickets/DAB-141.md) |
| 11 | DAB-197 | PDF PNG encode/decode round-trip | IMPROVEMENT | 11.7x-16.5x, pixel-identical | XS | **8.0** | — | [DAB-197](analysis-tickets/DAB-197.md) |
| 12 | DAB-077 | Startup eligibility reconcile full-scans on every boot | IMPROVEMENT | 1938 ms -> 0.034 ms (57,000x) | S | **8.0** | DAB-083 | [DAB-077](analysis-tickets/DAB-077.md) |
| 13 | DAB-165 | **DONE** (Phase 3b) — A `%` in any log extra silently drops the record. The `%` trigger turned out unreachable from any current call site; the live half was DAB-164, the extras doubling, which one `RotatingFileHandler` is enough to cause. See [`ANALYSIS_CORRECTIONS.md`](ANALYSIS_CORRECTIONS.md) item 9 | BUG | S2 | XS | **8.0** | — | [DAB-165](analysis-tickets/DAB-165.md) |
| 14 | DAB-019 | **DONE** (Phase 3b) — Live batch silently destroyed when `process_messages` raises. Landed with an unanswered-messages receipt, **not** the ticket's requeue-the-popped-batch expression, which duplicates the attachment suffix, re-debits the rate limiter and re-bills Gemini. See [`ANALYSIS_CORRECTIONS.md`](ANALYSIS_CORRECTIONS.md) item 8 | BUG | **S1** | S | **6.0** | — | [DAB-019](analysis-tickets/DAB-019.md) |
| 15 | DAB-198 | PDF resource bomb: 3.6 KB upload -> 107 s CPU, 1.63 GB RSS | BUG | **S1** | S | **6.0** | DAB-197 | [DAB-198](analysis-tickets/DAB-198.md) |
| 16 | DAB-157 | **DONE** — Rotated log files are not gitignored. Landed in `b5851ab` | BUG | S2 | XS | **6.0** | — | [DAB-157](analysis-tickets/DAB-157.md) |
| 17 | DAB-009 | **DONE** (Phase 3b) — Unguarded `change_presence()` sits before `setup_commands`. Reproduced: with presence raising, `setup_commands` was awaited 0 times. `_start_automatic_rag_backlog` is guarded in the same change. The `DAB-002` edge was discharged, not exercised: DAB-002's presence-badge half never landed | BUG | S2 | XS | **6.0** | DAB-002 | [DAB-009](analysis-tickets/DAB-009.md) |
| 18 | DAB-029 | Six never-evicting in-memory containers | IMPROVEMENT | 699.5 MiB -> 1.9 MiB (373x) | S | **6.0** | — | [DAB-029](analysis-tickets/DAB-029.md) |
| 19 | DAB-083 | **DONE** (Phase 4) — `rag_migrations` is not owned by `_ensure_schema`. Both halves landed: the DDL moved into the schema, and a fresh install no longer records a migration that copied nothing. `DAB-077` is unblocked but was not in Phase 4's scope | BUG | S3 | XS | **4.0** | — | [DAB-083](analysis-tickets/DAB-083.md) |
| 20 | DAB-032 | Rate limiter rebuilds every user's history on every call | IMPROVEMENT | p95 22.1 ms -> 0.003 ms (6320x) | S | **4.0** | — | [DAB-032](analysis-tickets/DAB-032.md) |

Two S1 defects are deliberately **not** in Tier 0: `DAB-065` (transient DB error tombstones a
message) and `DAB-066` (legacy migration discards all rows). Both are real S1 data loss, but
`DAB-065`'s trigger largely disappears once `DAB-095` lands and its correct fix is a write-semantics
change, and `DAB-066`'s precondition is not reachable from any schema this repository's history has
ever produced. They sit at the head of Tier 1 with their prerequisites.

Row 16 is retained rather than deleted so that the `#` numbering stays stable across the whole
document. `DAB-157` shipped as `b5851ab` (`.gitignore` now ignores `logs/` wholesale plus
`*.log.[0-9]*`, and the dead `!data/.gitkeep` negation is gone), guarded by
`tests/test_repo_hygiene.py`.

---

## TIER 1 — HIGH VALUE

Real defects with a realistic trigger, or improvements with a measured payoff, that cost more than
an afternoon or that need a Tier-0 item first.

| # | ID(s) | Title | Type | Severity / payoff | Effort | Score | Depends on | Ticket |
|---|---|---|---|---|---|---|---|---|
| 21 | DAB-040 | **DONE** (Phase 3b) — Retry classification is substring matching on `str(error)`. Measured 11 flips, all corrections, zero regressions over a 29-case corpus. The ticket's placement (structured pass first) and its anchored regex are both wrong; see the inline correction | BUG | S2 | S | **9.0** | DAB-041 | [DAB-040](analysis-tickets/DAB-040.md) |
| 22 | DAB-180 | `grok-prompts` is a dangling gitlink; `/deepresearch` silently degrades | BUG | S2 | S | **8.0** | DAB-203 | [DAB-180](analysis-tickets/DAB-180.md) |
| 23 | DAB-213 | No cooldown on any slash command | IMPROVEMENT | worst case $3,247/h -> ~$0.90/h | S | **8.0** | DAB-203 | [DAB-213](analysis-tickets/DAB-213.md) |
| 24 | DAB-096 | Synchronous SQLite on the asyncio event loop (13 call sites) | IMPROVEMENT | 4997 ms -> 3.1 ms max loop stall | M | **6.0** | DAB-095 | [DAB-096](analysis-tickets/DAB-096.md) |
| 25 | DAB-166 | **DONE** (Phase 3b) — `/config debug` is a no-op; handler levels pinned at startup. Landed as option 1 plus `perf_logger.propagate = False`, which option 1 needs to be safe. See [`ANALYSIS_CORRECTIONS.md`](ANALYSIS_CORRECTIONS.md) item 10 | BUG | S2 | S | **6.0** | — | [DAB-166](analysis-tickets/DAB-166.md) |
| 26 | DAB-073 | Pins have absolute priority and starve retrieval to zero slots | BUG | S2 | S | **6.0** | — | [DAB-073](analysis-tickets/DAB-073.md) |
| 27 | DAB-150 | `/pin` is uncapped, unsanitised, and evicts all retrieved context | BUG | S2 | S | **6.0** | DAB-073 | [DAB-150](analysis-tickets/DAB-150.md) |
| 28 | DAB-065 | **DONE** (Phase 4) — A transient DB error during an edit permanently tombstones the message. Closed by separating a write failure from a business decision, NOT by adding `deleted_at = NULL` to the `ON CONFLICT` list (that reintroduces BUG-0004 and does not work anyway — `mark_deleted` also sets `hidden = 1`). See [`ANALYSIS_CORRECTIONS.md`](ANALYSIS_CORRECTIONS.md) item 15 | BUG | **S1** | M | **4.0** | DAB-095 | [DAB-065](analysis-tickets/DAB-065.md) |
| 29 | DAB-066 | **DONE** (Phase 4) — Legacy migration silently discards all rows, then records success. Landed as a count assertion that logs and returns, NOT the prescribed raise, which would abort DiscordBot.__init__ on every boot forever. Latent-trap guard: the precondition is unreachable across all nine revisions of the schema. See [`ANALYSIS_CORRECTIONS.md`](ANALYSIS_CORRECTIONS.md) item 16 | BUG | **S1** | S | **4.0** | — | [DAB-066](analysis-tickets/DAB-066.md) |
| 30 | DAB-203 | `test_command_registration.py` pins the degraded 22-command tree | BUG | S3 | S | **4.0** | — | [DAB-203](analysis-tickets/DAB-203.md) |
| 31 | DAB-042 | No whole-sequence deadline: one message can occupy the bot ~488 s | BUG | S2 | M | **3.0** | DAB-204 | [DAB-042](analysis-tickets/DAB-042.md) |
| 32 | DAB-204 | A regression test actively blocks the whole-sequence-deadline fix | BUG | S2 | S | **3.0** | — | [DAB-204](analysis-tickets/DAB-204.md) |

---

## TIER 2 — WORTH DOING

Correct, measured, and worth landing — but the payoff scales with corpus size or guild language,
neither of which the target deployment maximises, or the change is broad enough to need its own
review window.

| # | ID(s) | Title | Type | Severity / payoff | Effort | Score | Depends on | Ticket |
|---|---|---|---|---|---|---|---|---|
| 33 | DAB-028 | `search_semantic` copies the whole matrix and recomputes norms | IMPROVEMENT | 2225 ms -> 62 ms (35.8x); 2056 -> 8.4 MiB | M | **3.0** | DAB-027 | [DAB-028](analysis-tickets/DAB-028.md) |
| 34 | DAB-087 | Hybrid RAG is blind to every non-Latin script | BUG | S2 | S | **3.0** | DAB-077 | [DAB-087](analysis-tickets/DAB-087.md) |
| 35 | DAB-194 | **DONE** — Dead code: 2,027 removable lines, verified twice. Landed in `9894bcc` / `4baa29c` | IMPROVEMENT | delivered **-2,111 lines** across 25 files; `constraints.txt` 57 -> 51 pins | S | **3.0** | — (landed ahead of DAB-203) | [DAB-194](analysis-tickets/DAB-194.md) |
| 36 | DAB-027 | `_vector_lock` held across CPU-bound numpy scoring | IMPROVEMENT | 244x concurrent throughput | M | **2.0** | — | [DAB-027](analysis-tickets/DAB-027.md) |
| 37 | DAB-106 | 40-43 of 96 config fields have no validation | BUG | S2 | M | **1.5** | — | [DAB-106](analysis-tickets/DAB-106.md) |

Row 35 is retained rather than deleted so that the `#` numbering stays stable across the whole
document. `DAB-194` shipped as `9894bcc` (dead code: `src/services/help_system.py` deleted, four
dead `BotConfig` fields removed, `system_prompts.thinking_mode_addon` dropped from `config.yaml`)
and `4baa29c` (five unused runtime dependencies). Both left the suite at 125 tests, OK. It also
closes `TD-015` in `docs/tech-debt-register.md`.

---

## TIER 3 — BACKLOG

Remaining S2 findings from the master register that do not yet have a ticket. Each is real and
recalibrated; none is cheap enough or urgent enough to schedule ahead of Tiers 0-2. Open a ticket
when one is scheduled. The register's 82 S3, 43 S4 and 5 informational entries are not reproduced
here.

| ID | Title | Area |
|---|---|---|
| DAB-006 | Audio / image / PDF download failures are invisible; the model answers as if blank | media |
| DAB-016 | One image-service `start()` failure collapses complexity routing for all text traffic | orchestration |
| DAB-020 | Live queue silently destroyed on toggle-off, on `close()`, and after `close()` | concurrency |
| DAB-021 | `pending_messages` uncapped -> a single 1.5 M-character prompt | concurrency |
| DAB-022 | Router cache key is a 200-char prefix -> cross-user routing collisions | routing |
| DAB-024 | Router / selector / embedding Gemini calls have no timeout | gemini |
| DAB-025 | Router failure silently downgrades every request to complexity=low | gemini |
| DAB-035 | `get_live_enabled()` is a blocking SQLite read per message on the loop | persistence |
| DAB-048 | Thinking tokens never billed: `thoughts_token_count` never read | cost |
| DAB-049 | Token usage attached only to successful responses | cost |
| DAB-050 | Router / selector / embedding call sites entirely unaccounted | cost |
| DAB-069 | Lost update on `hidden`: `/hide` racing a re-index re-exposes hidden content | rag |
| DAB-070 | `store_embedding` accepts a wrong-width vector, marks it `done`, drops it at search | rag |
| DAB-071 | No FTS rebuild path; FTS drifts out of sync with `message_index` | rag |
| DAB-072 | Context pack bounded by message count only, no token budget | rag |
| DAB-076 | Backfill: 2 connections + 2 commits per message, unbounded, auto-started at boot | rag |
| DAB-099 | No column-upgrade path for 4 of 5 `token_usage.db` tables | persistence |
| DAB-100 | `ReportService.create_report` commits the row and then raises `IndexError` | persistence |
| DAB-101 | `expanduser()` applied by only 4 of 7 path-consuming services | persistence |
| DAB-102 | No cost model and no model column: spend is unreconstructable | cost |
| DAB-105 | A whole usage event is discarded when reported total < input+output | cost |
| DAB-112 | `split_length` + `continuation_overhead` -> one reply becomes ~20,000 messages | config |
| DAB-116 | Continuation markers overflow the 50-char reserve and discard the smart split | rendering |
| DAB-119 | `_preserve_code_blocks` is always undone; fence preservation is effectively off | rendering |
| DAB-121 | Overlapping block/inline LaTeX spans corrupt the surrounding text | rendering |
| DAB-122 | Currency amounts are eaten by `INLINE_LATEX_RE` | rendering |
| DAB-125 | LaTeX and table rewriting are not code-fence-aware | rendering |
| DAB-128 | `fig_height` is unbounded: an already-live DoS, **not** contingent on DAB-114 — one expression with 300 `\\` row separators measures 2,558 MB peak RSS on the unfixed tree. Needs its own ticket and commit, not a rider on DAB-114. See [`ANALYSIS_CORRECTIONS.md`](ANALYSIS_CORRECTIONS.md) item 2 | rendering |
| DAB-129 | matplotlib rendering and message splitting both run on the event loop | rendering |
| DAB-131 | `validate_image` accepts a 144 MP decompression bomb from 449 KB | media |
| DAB-138 | PIL PNG encoding on the event loop, up to 26 images per request | media |
| DAB-140 | The command layer has effectively no authorization model (1 of 35 gated) | security |
| DAB-142 | `/dev` is ungated and turns stack traces into public Discord messages | security |
| DAB-143 | Report web UI serves every guild's reports with no authentication | security |
| DAB-144 | `ReportWebServer.url` rewrites a `0.0.0.0` bind to `127.0.0.1` | security |
| DAB-147 | `/report-status` reads any report by enumerable id, unscoped | security |
| DAB-148 | **DONE** — Every `/config` subcommand mutates process-global state, ungated. Promoted out of Tier 3 and landed in Phase 3: runtime Manage Server guards on the five mutating subcommands, `/config info` deliberately left open | security |
| DAB-153 | Raw exception text echoed to Discord by default, no secret scrubbing | security |
| DAB-159 | Indirect prompt injection: retrieved content can forge the RAG context fence | security |
| DAB-163 | `/pins` breaks permanently at 26 pins (Discord's 25-field embed cap) | security |
| DAB-167 | `send_error_response` raises out of its own `except` block | observability |
| DAB-168 | **DONE** (Phase 3b) — Live-mode RAG retrieval failure is logged at DEBUG. Now WARNING with `exc_info`, matching the mention path | observability |
| DAB-169 | 39 of 237 `except` handlers are invisible at the default log level | observability |
| DAB-170 | **DONE** (Phase 3b) — `get_status` returns all zeros on DB failure; `/rag status` looks healthy. The degraded payload now carries an `error` key; `/rag status` renders a red DEGRADED embed and pre-generation no longer reports `complete` over an unreadable index | observability |
| DAB-181 | A failed first `start.sh` leaves a poisoned `.venv` | ops |
| DAB-201 | The text-file decode ladder can never fail; binary is mojibaked into the prompt | media |
| DAB-202 | 52.4 MB of text-file content concatenated into one prompt, no truncation | media |

---

## SUGGESTED SPRINT PLAN

Four sprints, each with one causally coherent theme. The ordering is not arbitrary: you cannot
usefully measure the effect of a performance fix while a duplicate-response bug is doubling your
call count, you cannot safely run a dead-code pass while the test suite pins the degraded command
tree, and you cannot land a whole-sequence deadline while a regression test fails correct
implementations of it.

### Sprint 1 — Stop the bleeding

**Theme:** every defect that is currently losing data, losing money, or freezing the process, plus
the one-line changes that share a file with them. Seven of the nine S1 findings close here.

`DAB-041` · `DAB-001` · `DAB-115` · `DAB-114` · `DAB-039` · `DAB-141` · `DAB-157` (**DONE**,
`b5851ab`) · `DAB-019` · `DAB-197` · `DAB-198` · `DAB-002` · `DAB-009`

- 11 tickets remaining of 12: 6 XS, 5 S. **Rough total: 3-4 days.** `DAB-157` has already landed.
- Five are independent one-file diffs and can land in the first hour: `DAB-041`, `DAB-001`,
  `DAB-114`, `DAB-039`, `DAB-197`.
- `DAB-141` needs `DAB-203` (Sprint 2) if the command description changes; land the permission
  gate alone this sprint and defer the copy change, or pull `DAB-203` forward.
- **Suite count changes here.** `DAB-001` adds `tests/test_context_fallback.py`; `DAB-114` requires
  an unmocked matplotlib test. `DAB-141` does **not** change the count: gating at the *group* level
  (the only placement discord.py 2.7.1 actually serialises) leaves `.checks` empty, so the
  `/rag delete` assertion at `tests/test_command_registration.py:176` still passes and the ticket
  needs no test churn. See [`ANALYSIS_CORRECTIONS.md`](ANALYSIS_CORRECTIONS.md) item 3.

### Sprint 2 — Make it observable and safe to change

**Theme:** you cannot verify Sprint 1's work, or safely attempt Sprints 3-4, while the logging
lever is a no-op, the error taxonomy is string matching, and a regression test actively obstructs
a correct fix. This theme originally claimed **two** obstructing regression tests; only `DAB-204`'s
is one. `tests/test_command_registration.py` was cast as the second, and it has since twice been
shown to be doing its job rather than obstructing — it correctly protected `/image-queue` during
the `DAB-194` dead-code pass, and it does not break under `DAB-141`'s group-level gate. See
[`ANALYSIS_CORRECTIONS.md`](ANALYSIS_CORRECTIONS.md) items 1 and 3.

`DAB-203` · `DAB-204` · `DAB-165` · `DAB-166` · `DAB-040` · `DAB-042` · `DAB-213` · `DAB-180`

- 8 tickets: 2 XS, 4 S, 2 M. **Rough total: 1 week.**
- `DAB-204` is the one real unblocker and should land first in the sprint. `DAB-203` is
  de-brittling work, not a gate: nothing in this programme is currently blocked on it.
- `DAB-040` lands in the same function as Sprint 1's `DAB-041`; do it as a follow-up commit, not a
  merge.
- `DAB-213` (command cooldowns) belongs here rather than in Sprint 1 only because it wants
  `DAB-203`'s de-brittled tree test to prove it did not change the command surface.

### Sprint 3 — Make it durable and fast

**Theme:** the data layer. Every remaining data-loss defect and every measured performance win
lives in `sqlite_utils.py` and `message_index_service.py`, and they share prerequisites: the
pragmas remove the contention that causes the tombstone, and the migration ledger must exist before
anything can gate on it.

`DAB-212` · `DAB-078` · `DAB-083` · `DAB-077` · `DAB-095` · `DAB-096` · `DAB-065` · `DAB-066` ·
`DAB-029` · `DAB-032` · `DAB-027` · `DAB-028`

- 12 tickets: 4 XS, 5 S, 3 M. **Rough total: 1.5 weeks.**
- Strict internal order: `DAB-212` and `DAB-078` (independent, ship day one) -> `DAB-083` ->
  `DAB-077` -> `DAB-095` -> `DAB-065` -> `DAB-096`. `DAB-029`/`DAB-032`/`DAB-027`/`DAB-028` are
  independent of that chain and can run in parallel.
- The two remaining S1s (`DAB-065`, `DAB-066`) close here.
- `DAB-078` costs **+46% write throughput** (52.3 -> 76.7 us/row). That is the one deliberate
  regression in the whole plan; it is worth 1200x on the read path.

### Sprint 4 — Make it maintainable

**Theme:** with the bleeding stopped, the system observable, and the data layer sound, remove what
should not exist and bound what is unbounded.

`DAB-106` · `DAB-073` · `DAB-150` · `DAB-087`

- 4 tickets: 3 S, 1 M. **Rough total: 1 week.**
- **`DAB-194` has already landed, and it landed ahead of `DAB-203`, not after it.** It shipped as
  `9894bcc` / `4baa29c` (delivered -2,111 lines) while `DAB-203` is still open. The prediction
  recorded here — that the pass would need `DAB-203` first because a naive scan deletes the live
  `/image-queue` command and no test fails — did not hold: `/edit-image` and `/image-queue` both
  survive, and the `[5:10]` slice guard in `tests/test_command_registration.py` fails if
  `/image-queue` is removed. `DAB-203` is still wanted, to de-brittle that tree test and guard
  future removal passes, but it was never a gate on `DAB-194`. See
  `docs/ANALYSIS_CORRECTIONS.md`.
- `DAB-073` and `DAB-150` share `context_pack_builder.py` and `pin_service.py`; land them as one
  reviewed change with one decision on the cap values.
- `DAB-087` must bump the reconcile fingerprint that `DAB-077` introduces in Sprint 3, or every
  existing non-Latin row stays `skipped` forever.

**Programme total: 35 remaining tickets of the 37 written, roughly 4.5 weeks of focused work.**

---

## DEPENDENCY GRAPH

Read `A --> B` as "A must land before B". Items with no inbound edge are independent and can start
immediately.

```
INDEPENDENT ROOTS (start any of these today; DAB-157 is DONE -- landed as b5851ab,
                   guarded by tests/test_repo_hygiene.py)
  DAB-041   DAB-001   DAB-114   DAB-039   DAB-157   DAB-197   DAB-115
  DAB-019   DAB-212   DAB-078   DAB-165   DAB-166   DAB-203   DAB-204
  DAB-083   DAB-095   DAB-029   DAB-032   DAB-027   DAB-066   DAB-073
  DAB-106   DAB-002

TEST-INFRASTRUCTURE CHAIN  (one live obstruction, not two -- see below)
  DAB-203 -?> DAB-141        CONDITIONAL, and only on the copy change (:48). The permission
                             gate itself does NOT break :176: discord.py serialises
                             default_permissions only at the top level, so the fix must be a
                             GROUP-level gate, and a group-level gate leaves .checks empty.
                             DAB-141 needs no test churn. Refuted; see
                             docs/ANALYSIS_CORRECTIONS.md item 3
      +--> DAB-180           refusing to register /deepresearch changes the tree
      \--> DAB-213           proves the cooldown decorators did not change the tree
      (the DAB-203 --> DAB-194 edge is gone: refuted, and moot now that DAB-194 has
       landed first. See docs/ANALYSIS_CORRECTIONS.md)

  DAB-204 --> DAB-042        test_main_response_path_does_not_wrap_client_retry_timeout
                             FAILS a correct whole-sequence deadline; rewrite it first

DATA-LAYER CHAIN
  DAB-083 --> DAB-077        rag_migrations must be created by _ensure_schema before
                             any ledger read is added there, or __init__ raises (fatal)

  DAB-095 --> DAB-065        WAL + explicit busy_timeout removes the contention that
      |                      turns a lock timeout into a permanent tombstone
      \--> DAB-096           pragmas first, then move the 13 sync call sites off the loop

  DAB-077 --> DAB-087        widening the eligibility regex must bump the reconcile
                             fingerprint or non-Latin history stays skipped forever

ERROR-HANDLING CHAIN
  DAB-041 --> DAB-040        same function (categorize_error); land the one-line class
                             dispatch first, then replace the substring chain

PDF CHAIN
  DAB-197 --> DAB-198        delete the 11.7x round-trip before setting a timeout budget,
                             or the budget is sized against wasted work

STARTUP CHAIN
  DAB-002 --> DAB-009        eager service construction makes the presence-guard fix a
                             two-line change instead of a restructure

CONTEXT-PACK CHAIN
  DAB-073 --> DAB-150        agree the retrieval floor before capping pins at ingest;
                             both edit context_pack_builder.py and pin_service.py

VECTOR CHAIN
  DAB-027 --> DAB-028        the lock-free snapshot and the pre-normalised matvec were
                             prototyped together and share the write-path atomicity risk
```

### Cross-cutting hazards recorded on the graph

- **`tests/test_command_registration.py` is a chokepoint.** Its `EXPECTED_SIGNATURE` (`:26`), the
  `== 22` (`:147`) and `== 35` (`:148`) literals, the `[5:10]` slice (`:239`) and the
  "`/rag delete` has no checks" assertion (`:175-176`) each break independently. Five tickets touch
  it. `DAB-203` exists to make that stop.
- **`docs/DEEP_BUG_HUNT_REPORT.md` and `docs/tech-debt-register.md` are stale.** The former cites a
  build (`8bc80f9`) that is not in this history; the latter has two drifted evidence rows and one
  false claim. Neither is a dependency, but do not treat either as ground truth while working these
  tickets.
- **`AGENTS.md` contained one verified error, since corrected.** It stated `_pin_service` is
  attached during command registration; in fact `discord_bot.py:366` constructs it in `__init__`
  and `personalization.py:126` only reuses it. The current `AGENTS.md` ("Service wiring") now says
  exactly that, so nothing is outstanding here. Several tickets depend on knowing which services
  are eagerly constructed, so keep reading that section as authoritative.
