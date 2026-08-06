## Analysis Backlog

Last updated: 2026-08-05 | Repo state: branch `remediation/2026-07-30`, HEAD `083ebea` | Gate:
394 tests, OK; mutation harness 130/130. `dev` is frozen at `c83f740` on purpose and is **not**
where this work lives
Findings below were measured at HEAD `c83f740` against a 125-test baseline; the status column is
current as of 2026-08-05. Trust the finding, re-locate the line number, and quote the 394 gate.

**Ticket status at `083ebea`: 24 LANDED, 6 PARTIAL (DAB-029, DAB-042, DAB-073, DAB-087, DAB-095,
DAB-198), 1 REFUTED (DAB-212), 6 OPEN.**

| Tier | Rows | Landed | Partial | Refuted | Open |
|---|---|---|---|---|---|
| 0 | 20 | 15 | 3 | 1 | 1 |
| 1 | 12 | 8 | 2 | — | 2 |
| 2 | 5 | 1 | 1 | — | 3 |
| **Tickets** | **37** | **24** | **6** | **1** | **6** |
| 3 (compact, no tickets) | 49 | 13 | 1 | — | 35 |

The six open tickets are DAB-027, DAB-028, DAB-032, DAB-096, DAB-106 and DAB-180. Six more are
partial — DAB-029, DAB-042, DAB-073, DAB-087, DAB-095 and DAB-198 — and DAB-212 is refuted.
Everything else in Tiers 0-2 is closed, deferred with a measurement, or refuted. See [`REMEDIATION_2026-07-30.md`](REMEDIATION_2026-07-30.md) for
the programme that closed them, and the "Post-programme findings" section at the foot of this
document for the nineteen post-programme findings, `PPR-01` to `PPR-19`.

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
| 1 | DAB-041 | **DONE** (Phase 1, `a51da49`) — Dead `error_class`: timeouts and connection resets classified as permanent. The computed class is now dispatched; `a0782cf` then replaced the whole substring chain for DAB-040 | BUG | S2 | XS | **18.0** | — | [DAB-041](analysis-tickets/DAB-041.md) |
| 2 | DAB-001 | **DONE** (Phase 1, `1d7ed94`) — Hybrid-RAG fallback `try` spanned delivery: duplicate reply, duplicate bill. Delivery now sits outside the `try`, behind `if rag_context is not None`; `M-DAB001` and `M-DAB001B` pin both the scope and the `is not None` gate | BUG | **S1** | XS | **16.0** | — | [DAB-001](analysis-tickets/DAB-001.md) |
| 3 | DAB-078 | **DONE** (Phase 4) — `search_recent` cannot use its index. Landed by **replacing** the `(guild_id, channel_id, created_at)` composite with two partial indexes rather than adding to it: same read plans, +15% on writes instead of +35%, and no size growth. Re-measured 924x channel / 2273x guild / 549x on the pending poll | IMPROVEMENT | 1200x / 1870x measured | XS | **16.0** | — | [DAB-078](analysis-tickets/DAB-078.md) |
| 4 | DAB-212 | **REFUTED** (Phase 4) — the 1844x is measured on a query production never issues; on the real channel-scoped form it is **111x slower** than leaving the `ORDER BY` alone once DAB-078 lands, and it reintroduces the temp B-tree it exists to remove. Not applied; `M-DAB212` stops it being re-applied. See [`ANALYSIS_CORRECTIONS.md`](ANALYSIS_CORRECTIONS.md) item 13 | IMPROVEMENT | 1844x measured, two lines | XS | **16.0** | — | [DAB-212](analysis-tickets/DAB-212.md) |
| 5 | DAB-115 | **DONE** (Phase 1, `b0d8308`) — `split_message` was O(n^2) on the event loop. `MarkdownParser.build_split_index` parses once and each query is a bisect: 30.08 s -> 0.0375 s (802x) on a 139 KB fenced block, 192/192 outputs byte-identical. Re-measured 2026-07-31 on prose: x2.00 per doubling, i.e. linear. **It is still called synchronously on the loop** (`response_delivery.py:281`) — see TD-017 | BUG | **S1** | S | **12.0** | — | [DAB-115](analysis-tickets/DAB-115.md) |
| 6 | DAB-114 | **DONE** (Phase 1, `dce7510`) — Every LaTeX response with 2+ equations failed to render. DAB-128's unbounded `fig_height` was capped in the same commit, as [`ANALYSIS_CORRECTIONS.md`](ANALYSIS_CORRECTIONS.md) item 2 required | BUG | S2 | XS | **12.0** | — | [DAB-114](analysis-tickets/DAB-114.md) |
| 7 | DAB-095 | **PARTIAL** (Phase 4) — busy timeout landed as `bot.sqlite_busy_timeout_ms`; **WAL deferred** until connections are pooled, because it costs +0.34 ms on every call here and made zero difference to the DAB-065 trigger in all four lock cells. FKs still off. See [`ANALYSIS_CORRECTIONS.md`](ANALYSIS_CORRECTIONS.md) item 14 | IMPROVEMENT | 5.01 s failure -> 3.3 ms; 4.2x writes | S | **9.0** | — | [DAB-095](analysis-tickets/DAB-095.md) |
| 8 | DAB-002 | **DONE** (Phase 3b, `82b38a3`) — `on_ready` swallowed a `setup_commands` failure. The three personalization services are now built eagerly in `DiscordBot.__init__` (`discord_bot.py:394-405`), so a registrar raising can no longer decide whether they exist; the registrars reuse the eager instances | BUG | **S1** | S | **8.0** | — | [DAB-002](analysis-tickets/DAB-002.md) |
| 9 | DAB-039 | **DONE** (Phase 1, `7955652`) — Chain-of-thought could be posted to Discord as the answer. The `_get_response_text` fallback now honours `part.thought` | BUG | **S1** | XS | **8.0** | — | [DAB-039](analysis-tickets/DAB-039.md) |
| 10 | DAB-141 | **DONE** (Phase 2/3, `eb9daaf`) — `/rag delete scope:all` was ungated, cross-guild, irreversible. Gated at the **group** level (`Permissions(manage_guild=True)` plus `guild_only`), which is the only placement discord.py 2.7.1 serialises, with a runtime `_has_rag_admin` check on `/rag delete`. The ticket's prescribed subcommand-level gate does not work; see [`ANALYSIS_CORRECTIONS.md`](ANALYSIS_CORRECTIONS.md) item 3 | BUG | **S1** | XS | **8.0** | DAB-203 | [DAB-141](analysis-tickets/DAB-141.md) |
| 11 | DAB-197 | **DONE** (Phase 1, `4fb091c`) — PDF PNG encode/decode round-trip deleted, in the same commit as DAB-198 and in the corrected order — **after** DAB-198's clamp, not before (`ANALYSIS_CORRECTIONS.md` item 6) | IMPROVEMENT | 11.7x-16.5x, pixel-identical | XS | **8.0** | DAB-198 | [DAB-197](analysis-tickets/DAB-197.md) |
| 12 | DAB-077 | **DONE** (round 2 Phase 4) — the startup eligibility reconcile is gated on a fingerprint of everything that decides its answer, in a one-row `rag_state` table. Two things the ticket does not say: it ran **twice** per boot, because `_migrate_legacy_database` re-entered on every normal install and called it again, and the ticket's own acceptance criterion is unreachable by the gate alone — PPR-02's early return is the other half. Measured together on a fresh install with a populated target, whole constructor, median of 7, ~132-char bodies: **100,000 rows 4,426.5 ms -> 9.2 ms**, 20,000 rows 833.1 ms -> 3.3 ms | IMPROVEMENT | 1938 ms -> 0.034 ms (57,000x) | S | **8.0** | DAB-083 | [DAB-077](analysis-tickets/DAB-077.md) |
| 13 | DAB-165 | **DONE** (Phase 3b) — A `%` in any log extra silently drops the record. The `%` trigger turned out unreachable from any current call site; the live half was DAB-164, the extras doubling, which one `RotatingFileHandler` is enough to cause. See [`ANALYSIS_CORRECTIONS.md`](ANALYSIS_CORRECTIONS.md) item 9 | BUG | S2 | XS | **8.0** | — | [DAB-165](analysis-tickets/DAB-165.md) |
| 14 | DAB-019 | **DONE** (Phase 3b) — Live batch silently destroyed when `process_messages` raises. Landed with an unanswered-messages receipt, **not** the ticket's requeue-the-popped-batch expression, which duplicates the attachment suffix, re-debits the rate limiter and re-bills Gemini. See [`ANALYSIS_CORRECTIONS.md`](ANALYSIS_CORRECTIONS.md) item 8 | BUG | **S1** | S | **6.0** | — | [DAB-019](analysis-tickets/DAB-019.md) |
| 15 | DAB-198 | **DONE** (Phase 1, `4fb091c`) — PDF resource bomb: a 3.6 KB upload could cost 107 s of CPU and 1.63 GB RSS. Page rasterisation is clamped. **This lands first**, and DAB-197's PNG round-trip deletion follows it in the same commit — the reverse order removes the only guard against a 256 MP allocation ([`ANALYSIS_CORRECTIONS.md`](ANALYSIS_CORRECTIONS.md) item 6) | BUG | **S1** | S | **6.0** | — (blocks DAB-197) | [DAB-198](analysis-tickets/DAB-198.md) |
| 16 | DAB-157 | **DONE** — Rotated log files are not gitignored. Landed in `f0938a8` | BUG | S2 | XS | **6.0** | — | [DAB-157](analysis-tickets/DAB-157.md) |
| 17 | DAB-009 | **DONE** (Phase 3b) — Unguarded `change_presence()` sits before `setup_commands`. Reproduced: with presence raising, `setup_commands` was awaited 0 times. `_start_automatic_rag_backlog` is guarded in the same change. The `DAB-002` edge was discharged, not exercised: DAB-002's presence-badge half never landed | BUG | S2 | XS | **6.0** | DAB-002 | [DAB-009](analysis-tickets/DAB-009.md) |
| 18 | DAB-029 | **PARTIAL — item 5a only, one of six.** The completed-image-job archive no longer keeps the uploaded payload: `_process_job` clears `request.image_data` before archiving. Note the ring is capped at 100 and swept hourly, so it was never "retained forever" — the defect was what each entry weighed. **Five containers are untouched:** per-channel live state (1-2), the vector cache (3), `_pregeneration_status` (4), this archive's byte cap (5b), and the per-user rate-limit map (6). `result.edited_image` is deliberately still retained, because `/edit-image` reads it from the archive after completion. The ticket's prescribed `dataclasses.replace(..., image_data=b"")` **raises** — `__post_init__` forbids empty image data — and took a worker down in its own `finally` on first attempt | IMPROVEMENT | 699.5 MiB -> 1.9 MiB (373x) | S | **6.0** | — | [DAB-029](analysis-tickets/DAB-029.md) |
| 19 | DAB-083 | **DONE** (Phase 4) — `rag_migrations` is not owned by `_ensure_schema`. Both halves landed: the DDL moved into the schema, and a fresh install no longer records a migration that copied nothing. `DAB-077` is unblocked but was not in Phase 4's scope | BUG | S3 | XS | **4.0** | — | [DAB-083](analysis-tickets/DAB-083.md) |
| 20 | DAB-032 | **OPEN**, and now with a second caller: `b9a4514` added `expensive_command_limiter`, a second `TextRateLimiter` instance with the same O(n) rebuild | IMPROVEMENT | p95 22.1 ms -> 0.003 ms (6320x) | S | **4.0** | — | [DAB-032](analysis-tickets/DAB-032.md) |

Two S1 defects are deliberately **not** in Tier 0: `DAB-065` (transient DB error tombstones a
message) and `DAB-066` (legacy migration discards all rows). Both are real S1 data loss, but
`DAB-065`'s trigger largely disappears once `DAB-095` lands and its correct fix is a write-semantics
change, and `DAB-066`'s precondition is not reachable from any schema this repository's history has
ever produced. They sit at the head of Tier 1 with their prerequisites.

Row 16 is retained rather than deleted so that the `#` numbering stays stable across the whole
document. `DAB-157` shipped as `f0938a8` (`.gitignore` now ignores `logs/` wholesale plus
`*.log.[0-9]*`, and the dead `!data/.gitkeep` negation is gone), guarded by
`tests/test_repo_hygiene.py`.

---

## TIER 1 — HIGH VALUE

Real defects with a realistic trigger, or improvements with a measured payoff, that cost more than
an afternoon or that need a Tier-0 item first.

| # | ID(s) | Title | Type | Severity / payoff | Effort | Score | Depends on | Ticket |
|---|---|---|---|---|---|---|---|---|
| 21 | DAB-040 | **DONE** (Phase 3b) — Retry classification is substring matching on `str(error)`. Measured 11 flips, all corrections, zero regressions over a 29-case corpus. The ticket's placement (structured pass first) and its anchored regex are both wrong; see the inline correction | BUG | S2 | S | **9.0** | DAB-041 | [DAB-040](analysis-tickets/DAB-040.md) |
| 22 | DAB-180 | **OPEN.** `grok-prompts` is a dangling gitlink; `/deepresearch` silently degrades | BUG | S2 | S | **8.0** | DAB-203 | [DAB-180](analysis-tickets/DAB-180.md) |
| 23 | DAB-213 | **DONE** (Phase 2/3, `b9a4514`) — landed as a dedicated `expensive_command_limiter` on `/deepresearch` and `/summarize` (1/min, 4/h), measured 5.13e9 -> 2.42e7 tokens/h worst case for one unprivileged user. Two corrections: `/summarize` is ~2.5x worse than `/deepresearch`, not the reverse, and the title's premise is false — those commands were governed by **no** rate limiter at all, not by the 60/h text limit. See [`ANALYSIS_CORRECTIONS.md`](ANALYSIS_CORRECTIONS.md) item 17 | IMPROVEMENT | worst case $3,247/h -> ~$0.90/h | S | **8.0** | DAB-203 | [DAB-213](analysis-tickets/DAB-213.md) |
| 24 | DAB-096 | **OPEN.** Synchronous SQLite on the asyncio event loop. The per-call payoff is refuted — re-measured like-for-like, `to_thread` is 0.144 -> 0.225 ms, i.e. *slower*; the loop-stall figure stands. See [`ANALYSIS_CORRECTIONS.md`](ANALYSIS_CORRECTIONS.md) item 14 | IMPROVEMENT | 4997 ms -> 3.1 ms max loop stall | M | **6.0** | DAB-095 | [DAB-096](analysis-tickets/DAB-096.md) |
| 25 | DAB-166 | **DONE** (Phase 3b) — `/config debug` is a no-op; handler levels pinned at startup. Landed as option 1 plus `perf_logger.propagate = False`, which option 1 needs to be safe. See [`ANALYSIS_CORRECTIONS.md`](ANALYSIS_CORRECTIONS.md) item 10 | BUG | S2 | S | **6.0** | — | [DAB-166](analysis-tickets/DAB-166.md) |
| 26 | DAB-073 | **DONE** (Phase 2/3, `36bf261`) — pins no longer have absolute priority; a retrieval floor is reserved before pins are allocated | BUG | S2 | S | **6.0** | — | [DAB-073](analysis-tickets/DAB-073.md) |
| 27 | DAB-150 | **DONE** (Phase 2/3, `417e468`) — `/pin` is capped at 25 per channel and 40,000 chars per channel, 4,000 per pin, with prompt-delimiter defusal. Landed with DAB-073's floor, as [`ANALYSIS_CORRECTIONS.md`](ANALYSIS_CORRECTIONS.md) item 7 required. **Note:** the 25-pin cap did not by itself make `/pins` safe — at 25 pins the embed exceeds Discord's 6,000-character cap once display names reach 9 characters, and the cap was bypassed entirely by the legacy migration. Both closed in round 2 Phase 3; see PPR-06 and DAB-068 below | BUG | S2 | S | **6.0** | DAB-073 | [DAB-150](analysis-tickets/DAB-150.md) |
| 28 | DAB-065 | **DONE** (Phase 4) — A transient DB error during an edit permanently tombstones the message. Closed by separating a write failure from a business decision, NOT by adding `deleted_at = NULL` to the `ON CONFLICT` list (that reintroduces BUG-0004 and does not work anyway — `mark_deleted` also sets `hidden = 1`). See [`ANALYSIS_CORRECTIONS.md`](ANALYSIS_CORRECTIONS.md) item 15 | BUG | **S1** | M | **4.0** | DAB-095 | [DAB-065](analysis-tickets/DAB-065.md) |
| 29 | DAB-066 | **DONE** (Phase 4) — Legacy migration silently discards all rows, then records success. Landed as a count assertion that logs and returns, NOT the prescribed raise, which would abort DiscordBot.__init__ on every boot forever. Latent-trap guard: the precondition is unreachable across all nine revisions of the schema. See [`ANALYSIS_CORRECTIONS.md`](ANALYSIS_CORRECTIONS.md) item 16 | BUG | **S1** | S | **4.0** | — | [DAB-066](analysis-tickets/DAB-066.md) |
| 30 | DAB-203 | **DONE** (round 2 Phase 5) — the positional `get_commands()[5:10]` slice is replaced by two full ordered name lists, one per tree `setup_commands` can build. **De-brittling, not strengthening, and the distinction is measured:** no defect could be constructed that the slice misses and the list catches — `EXPECTED_SIGNATURE` is itself ordered and backstops the image-disabled tree, and the image test carries its own metadata assertions. What changed is that a failure now names what moved instead of reporting an opaque window mismatch, and 24 of 24 top-level names are pinned in the image-enabled tree instead of 5 | BUG | S3 | S | **4.0** | — | [DAB-203](analysis-tickets/DAB-203.md) |
| 31 | DAB-042 | **OPEN, now UNBLOCKED** (round 2, Phase 0). No whole-sequence deadline yet, but DAB-204 has landed and a correct 480 s deadline now leaves the suite green. Read the ticket header first: the budget must go **inside** `GeminiClient`, and this ticket's acceptance criterion contradicts DAB-204's unless it does. The 488 s applies only to `medium`/`high`; two adjacent findings outrank it (an untimed router `to_thread`, and four billed calls in 0.0001 s on empty-STOP) | BUG | S2 | M | **3.0** | — (DAB-204 discharged) | [DAB-042](analysis-tickets/DAB-042.md) |
| 32 | DAB-204 | **DONE** (round 2, Phase 0) — there were **two** obstructing tests, not the one this ticket names; the second was `tests/test_response_generation.py:107-110`. Replaced by `test_main_response_path_preserves_the_full_client_retry_budget`, which records deadlines through a module-scoped `asyncio` shim and asserts the budget instead of banning `wait_for`. `M-BUG0001` was retargeted in the same commit (deleting the test alone takes the harness to 55/56) and `M-BUG0001B` added for the `asyncio.timeout` spelling the old test was blind to | BUG | S2 | S | **3.0** | — | [DAB-204](analysis-tickets/DAB-204.md) |

---

## TIER 2 — WORTH DOING

Correct, measured, and worth landing — but the payoff scales with corpus size or guild language,
neither of which the target deployment maximises, or the change is broad enough to need its own
review window.

| # | ID(s) | Title | Type | Severity / payoff | Effort | Score | Depends on | Ticket |
|---|---|---|---|---|---|---|---|---|
| 33 | DAB-028 | **OPEN.** `search_semantic` still copies the whole matrix and recomputes norms | IMPROVEMENT | 2225 ms -> 62 ms (35.8x); 2056 -> 8.4 MiB | M | **3.0** | DAB-027 | [DAB-028](analysis-tickets/DAB-028.md) |
| 34 | DAB-087 | **PARTIAL** (round 2 Phase 5) — **the lexical half landed and cost nothing.** `_build_fts_query` tokenised on `[A-Za-z0-9_@#./:-]`, so a query in any other script produced no tokens and the function returned `None`: the lexical leg was not degraded, it was dead, while `message_search_fts` already held the text correctly tokenised by `unicode61`. Widening the class to `\w` is retroactive over all history with no re-indexing, no FTS rebuild and no embedding — it also stops accented Latin being shredded. **The semantic half is deliberately deferred, and not over the money.** See the reopen condition below | BUG | S2 | S | **3.0** | DAB-077 (discharged) | [DAB-087](analysis-tickets/DAB-087.md) |
| 35 | DAB-194 | **DONE** — Dead code: 2,027 removable lines, verified twice. Landed in `9894bcc` / `4baa29c` | IMPROVEMENT | delivered **-2,111 lines** across 25 files; `constraints.txt` 57 -> 51 pins | S | **3.0** | — (landed ahead of DAB-203) | [DAB-194](analysis-tickets/DAB-194.md) |
| 36 | DAB-027 | **OPEN.** `_vector_lock` is still held across CPU-bound numpy scoring | IMPROVEMENT | 244x concurrent throughput | M | **2.0** | — | [DAB-027](analysis-tickets/DAB-027.md) |
| 37 | DAB-106 | **OPEN**, re-measured as **39 of 99**. The programme added three fields (`expensive_command_limit_per_minute`, `expensive_command_limit_per_hour`, `sqlite_busy_timeout_ms`) and all three arrived with a validation rule, so the count did not move for those; round 2 Phase 2.4 then took it from 43 to 39 by validating the four `safety_*` enums, which is this ticket's first acceptance box. The remaining 39 are untouched | BUG | S2 | M | **1.5** | — | [DAB-106](analysis-tickets/DAB-106.md) |

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
| DAB-024 | Router / selector / embedding Gemini calls have no timeout — **router half DONE** (round 2, Phase 1): the router was the severe one and it was worse than "no timeout". It called the *synchronous* SDK through `asyncio.to_thread`, i.e. on the loop's shared default executor, so a hung provider starved every SQLite `to_thread` in the process (measured: unrelated `to_thread` unscheduled after 1.5 s with the pool saturated). Moved to `client.aio` + a `response_timeout` deadline. **`asyncio.wait_for` around a `to_thread` would NOT have fixed it** — measured, 32/32 worker threads still alive after cancelling every await. Selector (`gemini_client.py:680`) and embeddings (`:768`) are already on `aio`, so they cost no thread; they remain deadline-less and are the open remainder | gemini |
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
| DAB-128 | **DONE** (Phase 1, `dce7510`) — `fig_height` was unbounded: an already-live DoS, **not** contingent on DAB-114. Capped in the same commit as DAB-114, which corrections item 2 said it must not simply ride on; it got its own cap and its own tests. The published 2,558 MB peak RSS did not reproduce — **270 MB** — and the mechanism was real | rendering |
| DAB-129 | matplotlib rendering and message splitting both run on the event loop | rendering |
| DAB-131 | `validate_image` accepts a 144 MP decompression bomb from 449 KB | media |
| DAB-138 | PIL PNG encoding on the event loop, up to 26 images per request | media |
| DAB-140 | **PARTIAL.** The headline count is out of date: measured at `922e899` by building the tree and reading `to_dict()`, **11 of the 35 registered entries** are behind at least one gate, not 1, and 3 of the 22 top-level entities carry a payload permission (`/clear-cache` 8, `/dev` 8, `/rag` 32). There is still no *model* — the gates are per-command decisions. Three holes are open and listed under "Post-programme findings": `/live`, `/clear-cache` in DMs, and the two commands that still echo `str(exc)`. Tracked as TD-012 | security |
| DAB-142 | **DONE** (Phase 2/3, `d4b91a5`) — `/dev` is gated three ways: payload `default_permissions(administrator=True)`, `guild_only()` (Discord does not evaluate payload permissions in a DM), and a runtime administrator check, because `default_member_permissions` is a default a guild admin can re-grant | security |
| DAB-052 | **DONE** (round 2 Phase 2.4) — the safety threshold lookup was a four-entry map whose `.get` defaulted to `BLOCK_NONE`, and one of the four keys was a name the SDK does not define. Measured: `BLOCK_ONLY_HIGH`, `OFF` and `HARM_BLOCK_THRESHOLD_UNSPECIFIED` — all real — plus every typo arrived as `BLOCK_NONE`. Now resolved against the real enum, with `BLOCK_HIGH_AND_ABOVE` kept as an alias because `config.yaml`'s own comment advertised it and listed neither of the two real names the map ate, validated at boot, and failing to the strictest threshold rather than the most permissive if it is ever reached. Like DAB-145, this row did not exist until the fix landed: the finding was in `BUG_ANALYSIS_2026-07-29.md:1224` and never reached the register | correctness |
| DAB-145 | **DONE** (round 2 Phase 2.3) — cross-origin form POST against the report web UI. Named once in `BUG_ANALYSIS_2026-07-29.md:1260` and never carried into this register, which is why no earlier pass saw it; written up as PPR-11 below, where the three shapes beyond the headline one are recorded | security |
| DAB-143 | **DONE** (round 2 Phase 2.3) — the off-box surface is closed at the socket, not in `validate_config`: `ReportWebServer.start` reads the addresses actually bound back off the runner and refuses anything that is not all-loopback, logging and returning rather than raising so the bot still boots. No opt-in flag; a port forward is the supported remote path. Residual: a local process still reads everything — see PPR-11 | security |
| DAB-144 | **DONE** (round 2 Phase 2.3) — the display rewrite is gone. A running server reports the addresses it actually bound (both of them, when a name binds two), IPv6 bracketed; `on_ready`'s status line now distinguishes "Unavailable" from "Disabled" so a refusal no longer reads as an operator choice | security |
| DAB-147 | **DONE** (round 2, `7e2dcf1`) — `get_report` takes a `visible_to_guild` / `visible_to_reporter` scope enforced in the SQL, so the web UI and any future caller inherit it. The `OR reporter_id` half keeps DM-filed reports readable by their author. This row said OPEN for one commit after it was fixed; corrected in Phase 2.3 | security |
| DAB-148 | **DONE** — Every `/config` subcommand mutates process-global state, ungated. Promoted out of Tier 3 and landed in Phase 3: runtime Manage Server guards on the five mutating subcommands, `/config info` deliberately left open | security |
| DAB-153 | **DONE** (round 2 Phase 3) — raw exception text no longer reaches unprivileged users through `error_manager` (`d4b91a5`), and the three call sites outside that scope are now closed too. `/deepresearch` and `/summarize` reply with a fixed string, ephemeral (PPR-09). `/rag status`'s DEGRADED embed **keeps** its `str(exc)` deliberately — ephemeral, Manage Server only, and no path in any realistic failure — while the resolved database path beside it, which was the actual disclosure and was in the healthy embed as well, is gone (PPR-05) | security |
| DAB-159 | **OPEN.** Indirect prompt injection: retrieved content can forge the RAG context fence. Partially mitigated for *pins* only — `417e468` defuses the prompt delimiters in pinned text (`pin_service.py:45-48`) — but retrieved messages are untouched | security |
| DAB-163 | **DONE** (round 2 Phase 3) — it read as closed by the 25-pin cap and was not: the binding ceiling is the embed's 6,000-character *total*, reached at 6,046 with nine-character display names. `/pins` now stops at whichever of five ceilings binds first, and the delete view covers exactly what it listed. Four things the finding missed changed the fix; see PPR-06 below | security |
| DAB-167 | `send_error_response` raises out of its own `except` block | observability |
| DAB-168 | **DONE** (Phase 3b) — Live-mode RAG retrieval failure is logged at DEBUG. Now WARNING with `exc_info`, matching the mention path | observability |
| DAB-169 | 39 of 237 `except` handlers are invisible at the default log level | observability |
| DAB-170 | **DONE** (Phase 3b) — `get_status` returns all zeros on DB failure; `/rag status` looks healthy. The degraded payload now carries an `error` key; `/rag status` renders a red DEGRADED embed and pre-generation no longer reports `complete` over an unreadable index | observability |
| DAB-181 | A failed first `start.sh` leaves a poisoned `.venv` | ops |
| DAB-201 | The text-file decode ladder can never fail; binary is mojibaked into the prompt | media |
| DAB-202 | 52.4 MB of text-file content concatenated into one prompt, no truncation | media |

---

## SUGGESTED SPRINT PLAN

> **Historical as of 2026-07-31.** This plan was executed. The phases that ran did not map
> one-to-one onto the four sprints below — see
> [`REMEDIATION_2026-07-30.md`](REMEDIATION_2026-07-30.md) for what actually happened, in which
> order, and in which commit. The reasoning below is kept because it explains *why* the ordering
> mattered, and the "Suite count changes here" notes are kept because they are what the
> programme's per-commit accounting was checked against. Every estimate ("3-4 days", "1 week",
> "Programme total: 35 remaining tickets ... 4.5 weeks") is spent: 24 of the 37 tickets are
> closed and 12 remain.

Four sprints, each with one causally coherent theme. The ordering is not arbitrary: you cannot
usefully measure the effect of a performance fix while a duplicate-response bug is doubling your
call count, you cannot safely run a dead-code pass while the test suite pins the degraded command
tree, and you cannot land a whole-sequence deadline while a regression test fails correct
implementations of it.

### Sprint 1 — Stop the bleeding

**Theme:** every defect that is currently losing data, losing money, or freezing the process, plus
the one-line changes that share a file with them. Seven of the nine S1 findings close here.

`DAB-041` · `DAB-001` · `DAB-115` · `DAB-114` · `DAB-039` · `DAB-141` · `DAB-157` (**DONE**,
`f0938a8`) · `DAB-019` · `DAB-197` · `DAB-198` · `DAB-002` · `DAB-009`

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

> **Mostly discharged as of 2026-07-31.** Of the roots and edges below, only these still bind:
> `DAB-204 --> DAB-042` (the obstructing test is still in place), `DAB-077 --> DAB-087` (the
> fingerprint DAB-087 must bump does not exist yet, because DAB-077 was not taken), and
> `DAB-027 --> DAB-028`. `DAB-083 --> DAB-077` and `DAB-095 --> DAB-065` are discharged — both
> prerequisites landed. `DAB-203 -?> DAB-141` was refuted and DAB-141 landed without it.
> The remaining open tickets — DAB-029, DAB-032, DAB-096, DAB-106, DAB-180, DAB-203 — have no
> live inbound edge and can start today.

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

  DAB-204 --> DAB-042        DISCHARGED, round 2 Phase 0. Both obstructing tests (there
                             were two; the graph and both tickets each named one) are
                             replaced by an assertion on the retry BUDGET. A correct
                             480 s deadline now leaves the suite at 394 OK. DAB-042 can
                             start today -- but put the budget INSIDE GeminiClient, or
                             DAB-042's acceptance criterion and DAB-204's contradict

DATA-LAYER CHAIN
  DAB-083 --> DAB-077        rag_migrations must be created by _ensure_schema before
                             any ledger read is added there, or __init__ raises (fatal)

  DAB-095 --> DAB-065        WAL + explicit busy_timeout removes the contention that
      |                      turns a lock timeout into a permanent tombstone
      \--> DAB-096           pragmas first, then move the 13 sync call sites off the loop

  DAB-077 --> DAB-087        DISCHARGED, round 2 Phase 4. The fingerprint exists and
                             hashes the rule's own SOURCE, so widening the regex bumps
                             it with no separate action -- which turns DAB-087 from
                             "blocked" into "one commit away from an unattended
                             re-embedding run". That is now the reason to hold it, not
                             the reason it was held

ERROR-HANDLING CHAIN
  DAB-041 --> DAB-040        same function (categorize_error); land the one-line class
                             dispatch first, then replace the substring chain

PDF CHAIN
  DAB-198 --> DAB-197        REVERSED 2026-07-31; the direction printed here until then was
                             hazardous. Clamp page rasterisation FIRST. Pillow's
                             DecompressionBombError -- raised by the very Image.open that
                             DAB-197 deletes -- is the only thing stopping a 256 MP
                             allocation, so deleting the round-trip first makes the bomb
                             worse. The old note ("delete the round-trip before setting a
                             timeout budget, or the budget is sized against wasted work")
                             is about the budget's value, not safety, and is subordinate.
                             See docs/ANALYSIS_CORRECTIONS.md item 6

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
  `== 22` (`:147`) and `== 35` (`:148`) literals, the `[5:10]` slice (**`:323`**, was `:239` when
  this was written) and the "`/rag delete` has no checks" assertion each break independently. Five
  tickets touch it. `DAB-203` exists to make that stop, and is still open.
- **`docs/DEEP_BUG_HUNT_REPORT.md` has been deleted** (`DAB-210`); it cited a build (`8bc80f9`)
  that is not in this history, and `BUG_ANALYSIS_2026-07-29.md` §8.1/§8.2 carry both the reason
  and the true status of its five bugs. `docs/tech-debt-register.md` was re-assessed on 2026-07-31 and its TD-009 to TD-019
  rows now carry measured verdicts, so it is usable again — but read the bracketed 2026-07-31
  note on a row, not just its description, because two descriptions (TD-012, TD-017) are
  substantially false at HEAD and are corrected only in the note.
- **`AGENTS.md` contained one verified error, since corrected.** It stated `_pin_service` is
  attached during command registration; in fact `DiscordBot.__init__` constructs it
  (`discord_bot.py:377` at `922e899`) and `personalization.py:129` only reuses it. The current
  `AGENTS.md` ("Service wiring") says exactly that. Its `file:line` citations were re-measured at
  `083ebea`; the ones in *this* document and in the ticket bodies were not, and 58 commits have
  moved most of them.

---

## POST-PROGRAMME FINDINGS (2026-07-31)

`PPR-01` to `PPR-10` were found by reviewing the combined diff of round 1's 34 commits against
`c83f740`, before the
repository was pushed. **None of these existed in the 211-finding register**, so they carry a
`PPR-` prefix rather than a `DAB-` number: the master register lives outside this repository and
cannot be extended from here.

Every figure below was measured on 2026-07-31 at `922e899` with `.venv/bin/python` (CPython
3.12.13), on this machine. **Nothing here has been fixed** — this section is a record.

### PPR-01 (S3) — DAB-078's stated justification is false; three call paths lost their index

`15fc69a` replaced the composite `idx_message_index_scope_time (guild_id, channel_id, created_at
DESC)` with two **partial** indexes predicated on `WHERE hidden = 0 AND deleted_at IS NULL`
(`message_index_service.py:180-194`), on the stated grounds that the composite "serves no query
the two new ones do not". That is not true. Three call paths deliberately touch tombstoned and
hidden rows and therefore cannot use a partial index:

- `get_status` filters on `WHERE channel_id = ?` alone (`message_index_service.py:1599`) —
  `/rag status` reports how many messages the index holds, which must include hidden ones.
- `delete_rag_data(channel_id=...)` deletes `WHERE channel_id = ?` (`:1082`) — `/rag delete
  scope:channel` must remove tombstones too, or they are orphaned forever.
- Any bare channel-scoped `COUNT(*)`, which both of the above issue first.

Measured on a synthetic 100k-row index (20 channels, 5% hidden, 5% tombstoned), median of 7,
comparing the dropped composite against the two partial indexes on the same file:

| Path | With composite | With partial indexes only | |
|---|---|---|---|
| channel `COUNT(*)` | 4.97 ms | **11.69 ms** | 2.35x slower; plan drops from `SCAN ... USING COVERING INDEX` to a bare `SCAN` |
| `/rag status`, channel-scoped (5 queries) | 26.43 ms | **32.88 ms** | 1.24x slower |
| `/rag delete scope:channel` (median of 5, fresh copy each) | 113.9 ms | **139.4 ms** | 1.22x slower |
| `search_recent`, channel-scoped — the hot path DAB-078 targets | 21.36 ms | **0.032 ms** | **674x faster**; plan `SCAN` -> `SEARCH ... USING INDEX idx_message_index_channel_time` |

**The swap is still the right trade** — three admin paths a few milliseconds slower against a
674x win on a per-message path — and the commit's own read-plan and write-cost numbers reproduce.
What is wrong is the *reason given*, and it matters: a future reader who believes "the composite
serves no query the two new ones do not" will not think to check the tombstone paths before
touching these indexes again. This is a correction to a landed commit's rationale, not a request
to revert it. Recorded as [`ANALYSIS_CORRECTIONS.md`](ANALYSIS_CORRECTIONS.md) item 18.

### PPR-02 (S3) — one DAB-066 shortfall makes every later boot re-run the whole migration — **CLOSED, round 2 Phase 4**

The post-condition is now "no legacy row's key is absent from the target", tested per table
against `_MIGRATION_KEYS`, and idempotent by construction: a retry finds every key present and
records success.

**Two of the four keys cannot witness a lost row, and the fix does not change that.**
`message_index` and `message_embeddings` are keyed on the Discord snowflake, so the check is
exact. `message_retrieval_events.id` is `INTEGER PRIMARY KEY AUTOINCREMENT` with no UNIQUE
constraint of any kind, and both files start it at 1 — so `INSERT OR IGNORE` keeps main's row,
drops the legacy row carrying entirely different content, and the check still passes because the
*id* is present. Demonstrated: 1,000 rows in main against 1,500 in legacy loses 1,000 with the
post-condition green. `message_backfill_progress.channel_id` is natural but names a mutable
*cursor*, so it has the same blind spot. Both need an operator merging two installations to
trigger, `message_retrieval_events` is written but never read anywhere under `src/`, and
reconciling by content is a much larger change than the defect warrants. Recorded so that nobody
reads a green post-condition over those two tables as "the data is all there" — it means "the ids
are all there".

**The larger cost was the early return, not the retry.** `if not legacy_tables_found: return` sat
*after* the embedding-model reset, a second full-table eligibility reconcile and a complete FTS
teardown-and-rebuild, so every boot of every normal install paid for all three in order to
discover there was nothing to migrate. Measured here, fresh install with the target already
populated, whole constructor, median of 7, ~132-character message bodies:

| Indexed rows | Before | After |
|---|---|---|
| 0 | 1.8 ms | 1.7 ms |
| 20,000 | 833.1 ms | **326.8 ms** |
| 100,000 | 4,426.5 ms | **1,727.9 ms** |

Two independent re-measurements put the 100k saving at 3,568 ms and 2,245 ms against the 2,699 ms
above. The ratio (2.5-3.9x) reproduces and the absolute scales with message length, so quote it
with a fixture. The residual is `_ensure_schema`'s own ungated reconcile — that is DAB-077.

**Only two of the three skipped statements were redundant.** The embedding-model reset is
byte-identical to the one in `_ensure_schema` and reports rowcount 0 at every size, and the
reconcile is `_ensure_schema`'s last statement. The FTS rebuild is the only full rebuild in the
repository and was quietly repairing an *empty* FTS table on every boot; removing it without a
replacement took a database from 5 lexical hits to 0 with no boot able to recover.
`_repair_empty_fts` now does that job in `_ensure_schema`, for the empty case only — 0.33 ms flat
at 100,000 rows against 2,547 ms for the unconditional rebuild. Partial FTS drift is still
unhandled; that remains DAB-071. `M-PPR02` through `M-PPR02D` pin all four decisions.

<details><summary>The finding as originally filed</summary>


`922e899` correctly stopped a short legacy copy from recording itself as complete: on a shortfall
it logs at ERROR, leaves the ledger unwritten and returns, so the migration is retried next boot.
The retry is not idempotent with respect to its own success test.

`copied` is computed as `after_count - before` (`message_index_service.py:755`) and compared
against `expected`, the legacy row count. On the *second* boot the rows are already in `main`, so
`INSERT OR IGNORE` copies nothing: `copied = 0`, `expected = N`, shortfall. A table holding 100%
of its legacy rows reports itself short, forever. The ledger is never written, so every boot
re-runs the ATTACH, the copy attempt, `_reconcile_embedding_eligibility` and — because the
shortfall check sits *after* it (`:772-784`, the `if shortfalls:` gate is at `:785`) — the full `DELETE FROM message_search_fts` plus
reindex, before returning.

Reproduced by driving the service into the state a shortfall leaves behind (ledger absent, target
already holding every legacy row), 20,000 legacy rows in `message_index` and 20,000 in
`message_embeddings`, median of 7 boots:

| | Per boot |
|---|---|
| Control — ledger present, early return | **134.1 ms** |
| Stuck — ledger absent, 100% of rows already copied | **519.8 ms** |
| Attributable overhead | **+385.8 ms per boot (3.9x)** |

After 7 boots the ledger row was still absent and `message_index` still held exactly 20,000 rows.
Every boot logged, falsely: `message_index: expected 20000 rows, copied 0; message_embeddings:
expected 20000 rows, copied 0 ... This usually means the legacy schema lacks a column this version
declares NOT NULL.` The operator is told to fix a schema that is already fine.

Severity is S3, not higher, because the precondition — a real shortfall — is
[recorded as unreachable](analysis-tickets/DAB-066.md) across every schema this repository's
history has produced. It is a latent trap behind a latent trap. The fix is to compare
`after_count` against `expected + before`, or to count only rows genuinely absent from `main`.

</details>

### PPR-03 (S4) — cancellation during the DAB-019 retry backoff double-debits the rate limiter

`run_channel_worker` requeues `owed` into `pending_messages` and clears the receipt *before*
sleeping (`live_message_coordinator.py:170-179`), which is correct — it stops the `finally` block
requeuing the same batch twice. But `charged`, the set of user ids the limiter has already
debited for this turn, is worker-local (`:134`) and is not requeued with the batch. A
cancellation during `await asyncio.sleep(self.retry_backoff_seconds * attempts)` destroys the
worker and its `charged` set; the next worker picks the batch out of `pending_messages` with an
empty `charged` and debits every participant a second time for the same turn.

Gemini is **not** double-billed: the retry only happens because the model call raised, and a call
that raised produced nothing. The cost is one spurious rate-limit token per participant per
cancelled retry, which is exactly the property `charged` was introduced to guarantee
(`M-DAB019F`). Latent: it needs a cancellation inside a 1-3 second window that only opens after a
failure. Fixing it means moving `charged` somewhere that outlives the worker — which conflicts
with the deliberate decision in `161ff5d` not to add a seventh never-evicting per-channel
container (DAB-029). Land those two together.

### PPR-04 (S4) — a live attachment turn that raises notifies nobody — **CLOSED, round 2 Phase 5**

Closed with a second receipt, `answered`, and **not** by keeping `owed` populated across the
payment. That is what the finding reads like, and measured it delegates the attachment turn three
times for one message — `ANALYSIS_CORRECTIONS.md` item 8's hazard, in the one branch item 8 did
not cover. `M-PPR04D` is that reading.

An empty `owed` was ambiguous: it means both "there was nothing to answer" and "a paid call has
been made", and only the second obliges the worker to say something. `answered` holds the messages
a paid call was made for **and whose failure the user has not yet been told about** — so a path
that notifies on its own way out clears it, and the worker notifies only when it is still set.
That distinction is load-bearing in both directions and each has its own mutant: `M-PPR04` for the
silence, `M-PPR04C` for telling the user twice.

`test_an_attachment_turn_is_not_regenerated_either` was rewritten rather than joined, because its
stated premise was backwards: it asserted no second notification on the grounds that
`process_message_with_context` "has already told the user itself", when the case under test is an
exception that *escaped* that call and therefore its handler. It is now
`test_an_attachment_turn_is_not_regenerated_but_is_reported` and asserts both halves.

One consequence worth recording: `M-DAB019G` stopped discriminating. It deleted the post-payment
notification inside `_answer_batch`, which the new worker arm now covers, so the mutant survived.
It was retargeted to remove both routes and pin the property — a billed, undelivered turn must
reach the user by some path — rather than one implementation of it.

<details><summary>The finding as originally filed</summary>


`_answer_batch` clears `owed` before delegating an attachment turn
(`live_message_coordinator.py:466`, immediately before `await
self.process_message_with_context(...)`). The comment is right about why: that call owns its own
generation and billing, so it must not be retried. But the worker's abandonment path is gated on
the same receipt — `if owed:` at `:181` — so when the delegated call raises, `attempts` is
incremented, `_abandon_batch` is skipped and `_tell_user_the_turn_failed` is never reached. The
failure is logged at ERROR and the user who posted the attachment is told nothing at all.

The non-attachment path does not have this shape: it clears `owed` after `generate_response`
returns and still runs delivery inside the same call, so a delivery failure is notified without
regeneration. Only the attachment branch loses the receipt and the notification together. S4
because it needs an exception to escape `process_message_with_context`'s own broad handler.

</details>

### PPR-05 (S4) — `/rag status` discloses the resolved database path — **CLOSED, Phase 3**

**Closed, but not at the target the finding named, and the difference is the point.** PPR-05 was
filed against the `str(exc)` in the `Error` field. Executing the real command against a real
corrupt database showed that text carries no path in any of the three realistic failure modes
(`DatabaseError: file is not a database`, `OperationalError: unable to open database file`,
`OperationalError: no such table: message_index`), and the embed is `ephemeral=True` behind a
Manage Server gate — so it reaches exactly the administrator it is for. Stripping it would have
cost real diagnostic value and closed nothing, and `M-PPR05B` now reintroduces that over-correction.

The disclosure was the **next field**: `status["database_path"]`, a resolved absolute path
carrying the OS username. It was rendered in the *healthy* embed too, where there is no exception
at all, so every successful `/rag status` leaked it. Both fields are gone, along with the same
path in `/rag backfill`'s reply. `get_status` still returns it, for logs and tests.

<details><summary>The finding as originally filed</summary>



DAB-170's degraded payload sets `"error": f"{type(exc).__name__}: {exc}"`
(`message_index_service.py:1666`) and `/rag status` renders 900 characters of it into an embed
field (`research.py:232`). For a SQLite failure that string carries the resolved absolute database
path. Two mitigations keep this at S4 rather than S3: the embed is `ephemeral=True`, and `/rag` is
gated behind Manage Server, so only an administrator sees it. It is listed because it is the same
class as DAB-153 and was outside that fix's scope.

</details>

### PPR-06 (S3) — `/pins` exceeds Discord's 6,000-character embed cap at the permitted 25 pins — **CLOSED, round 2 Phase 3**

**Closed, and wider than the finding.** `/pins` now adds fields one at a time and stops when the
next would breach *any* ceiling: the 6,000-character total, the 25-field count, 256 per field
name, 1,024 per field value, and 25 view children. The delete view is built from exactly the pins
listed, replacing a flat `[:20]` that stranded five listed pins with no button in any channel that
rendered at all.

Four things the finding does not say, each of which changed the fix, and each found by executing
a prototype of the obvious repair rather than reading it:

- **The footer is counted.** `Embed.__len__` includes `footer.text`, so "say so in the footer"
  implemented literally puts the canonical failing case back over: 5,996 → **6,010**. The reserve
  is taken *before* the fit loop, and a complete listing carries **no footer at all** — at
  eight-character names 25 pins measure exactly 5,996, so any footer is the difference between 25
  pins and 24, and a footer on a complete listing says nothing the description does not. The
  listing is therefore fitted twice: once with no reserve, and again with the truncated-footer
  reserve only if the first pass could not show everything. This is subtle enough that a single
  hand-picked input cannot see it — each field costs ~240 characters, so the loop usually stops a
  whole field short and a 47-character footer disappears into the slack. The guard sweeps 122
  shapes near the ceiling; **18** of them go over without the reserve, up to 6,046.
- **A character budget alone is not enough, because >25 rows in a channel is reachable.** Every
  pin migrated before the DAB-068 fix bypassed `add_pin` entirely — measured, 60 rows in one
  channel — and 60 *short* pins never reach 6,000 characters at all. A length-only gate emits 60
  fields and then raises `ValueError: maximum number of children exceeded` building the view,
  which is worse than the 400: it is raised while composing the reply, so the interaction is never
  acknowledged, and this bot registers no `on_app_command_error` handler.
- **discord.py validates nothing.** `add_field` accepts 30 fields with 300-character names and
  2,000-character values and `to_dict()` passes every one through. `author_name` and `pinned_by`
  are unconstrained TEXT and never see `_sanitise_pin_content`, so name and value are clamped
  independently of the total.
- **`len(embed)` counts code points, and an astral character is two UTF-16 units** — measured
  1.84x on an all-emoji listing (5,971 against 10,971). Which unit Discord's 6,000 is denominated
  in cannot be settled without calling the API, so the budget counts the larger of the two:
  identical to `len(embed)` for any ordinary listing, conservative for the rest.

Guarded by `tests/test_pins_embed_bounds.py`, which asserts on the `discord.Embed` and
`discord.ui.View` the real callback hands to `send_message`. Before it existed, a `/pins` showing
one pin and handing out zero delete buttons passed the entire suite — and the first draft of the
guard had the same hole in miniature: every ceiling it asserted was satisfied by a listing that
showed nothing, so halving the budget left the suite green while the command showed 12 of 25. It
now asserts a lower bound first. `M-PPR06` through `M-PPR06G` pin the seven decisions.

Three residuals recorded rather than fixed, all pre-existing and none of them the overflow:
`PinDeleteView` has no `interaction_check` and `/pins` is not ephemeral, so any channel member can
click any pin's delete button; `get_pins` orders by a TEXT `pinned_at` carrying two formats, so
*which* pins a truncated listing hides is decided by a sort in which a `CURRENT_TIMESTAMP` row
precedes every ISO one; and a channel holding 60 pins needs 14 rounds of delete-and-rerun to reach
them all, which the footer now names but does not shorten.

<details><summary>The finding as originally filed</summary>



DAB-163 reads as closed by construction: `417e468` set `MAX_PINS_PER_CHANNEL = 25`
(`pin_service.py:26`), and Discord's per-embed limit is 25 *fields*. But the field count is not
the binding constraint. `/pins` builds one field per pin, name `#{i} — {author_name}` and value a
200-character preview plus `\n*Pinned by {pinned_by}*` (`personalization.py:211-217`), and
Discord also caps an embed's **total** length at 6,000 characters.

Measured with `discord.py` 2.7.1's own `len(embed)`, 25 pins, content longer than the 200-char
preview, both names the same length:

| Display-name length | `len(embed)` |
|---|---|
| 8 | 5,996 |
| **9** | **6,046 — over** |
| 32 (Discord's maximum) | **7,196** |

Each additional name character costs 50 (two occurrences x 25 fields). So `/pins` raises
`HTTPException: In embeds.0: Embed size exceeds maximum size of 6000` for any channel at the
permitted cap whose participants have names of 9 characters or more — which is most of them — and
it stays broken until someone deletes a pin, except that the delete buttons live on the view
attached to the message that will not send. The pin cap made this reachable at exactly the value
it permits. Fixes: paginate, shorten the preview, or cap on cumulative embed length rather than
count.

</details>

### PPR-07 (S3) — `/clear-cache` is administrator-gated in guilds and ungated in DMs

Measured from the serialized payload: `/clear-cache` has `default_member_permissions = 8`
(administrator) and `dm_permission = true`, no `guild_only()`, no `checks`, and no runtime
permission check in its body (`configuration.py:389-416`). Discord does not evaluate
`default_member_permissions` in a DM, so any user who shares a guild with the bot can DM
`/clear-cache` and clear the performance-logger cache regardless of their permissions.

This is precisely the hole `d4b91a5` closed for `/dev`, whose comment spells the mechanism out
("`guild_only` matters as much as the permission ... without guild_only anyone sharing a guild
with the bot could DM /dev and flip the flag"). `/dev` got `guild_only()` **and** a runtime check;
`/clear-cache`, two commands above it in the same file, got neither. Impact is low — a cache
clear, not a state mutation — which is why it is S3 and not S2.

### PPR-08 (S3) — `/live` is completely ungated, and is the largest remaining spend lever

`/live` (`personalization.py:90-125`) has no payload permission, no `guild_only`, no runtime
check and no cooldown. Any member can switch a channel into mention-free live mode, after which
**every message in that channel** is enqueued and answered — `on_message` forks to
`LiveMessageCoordinator.enqueue` and returns before the mention gate
(`discord_bot.py:827`). One `/live` turns a channel's entire traffic into Gemini calls billed to
the operator's personal key, and it persists in `channel_settings` until someone runs `/live`
again.

Set against the rest of the programme's spend work this is the outstanding gap: `b9a4514` capped
`/deepresearch` and `/summarize` at 4/hour each on the grounds that they are unbounded spend
levers reachable without permission, and `/live` is a strictly larger one that was not in scope.
The per-user text rate limit does apply to the messages the live channel then generates, which is
the only thing bounding it.

### PPR-09 (S3) — `/deepresearch` and `/summarize` echoed `str(exc)` publicly — **CLOSED, Phase 3**

Both now reply with a fixed string and `ephemeral=True`; `logger.error(..., exc_info=True)` was
already on both lines, so the operator loses nothing. Worse than the finding states, measured by
raising real exceptions through the real callbacks: the interpolation was unconditional *and*
uncapped, where `error_manager` suppresses raw text by default and appends it only when shorter
than 200 characters. A 343-character provider error was posted in full. So these two sites were
strictly more permissive than the path DAB-153 hardened, not merely outside it.

Two mutants, because there are two decisions here and only the first is obvious: `M-PPR09`
restores the echo, `M-PPR09B` keeps the sanitised text but leaves the reply public.

<details><summary>The finding as originally filed</summary>


`research.py:120` and `:493` both do `await interaction.followup.send(f"... {str(e)}")` with no
`ephemeral=True`, so an arbitrary exception string is posted into the channel for everyone.
DAB-153's fix (`d4b91a5`) routed unprivileged error reporting through `error_manager`; these two
call sites format their own message and never reach it. Recorded against DAB-153 above as the
unfixed remainder rather than as a new defect.

</details>

### PPR-10 — residual risks confirmed still open

Verified at `922e899` and unchanged by the programme. Listed together because a public push is
the moment they matter.

- ~~**DAB-143 / DAB-144 — the report web server.**~~ **Closed in round 2 Phase 2.3.** The socket
  refuses any bind whose *actually bound* addresses are not all loopback, the URL reports the
  address in use, and a state-changing request must carry a matching `Origin` from an allowlisted
  loopback `Host`. Residual, recorded rather than fixed: any process on the same machine can still
  read every guild's reports and change their status. Closing that needs a token, and a token is
  only worth its operator cost for a deployment reachable off-box — which is now impossible by
  construction.
- ~~**DAB-147 — `/report-status` is unscoped by guild.**~~ **Closed in round 2 by `7e2dcf1`**, with
  the scope in the SQL (`guild_id = ? OR reporter_id = ?`) rather than in the callback. This bullet
  and the Tier row above it both said OPEN for one commit longer than they were true; corrected in
  Phase 2.3.
- ~~**DAB-068 — the pin migration burns its one shot and logs a false success.**~~ **Closed in
  round 2 Phase 3**, together with a second, independent defect in the same method that the
  finding does not mention and that turned out to be the upstream cause of PPR-06. The ledger
  guard landed as described — an absent legacy `pinned_messages` returns without writing
  `legacy_shared_pins_v1`, so the migration stays armed. The second half: the copy was a bulk
  `INSERT OR IGNORE ... SELECT`, the **only** path in the tree that writes `pinned_messages`
  without going through `add_pin`, and it enforced none of `add_pin`'s ceilings. Measured before
  the fix on 61 legacy pins in one channel: **61 rows** against a 25-pin cap, **300,871
  characters** against a 40,000 cap, a **5,014-character** pin against a 4,000 cap, and a
  `--- End Context ---` delimiter copied in undefused. It now admits rows one at a time under the
  same three ceilings plus `_sanitise_pin_content`, so the table is left in a state `add_pin`
  itself could have produced.

  Three decisions worth recording, each from an adversarial review that ran the proposed fix
  rather than reading it. The legacy `id` is **not** preserved: carrying it across with
  `INSERT OR IGNORE` silently swallows a legacy pin whenever the target already holds that id, and
  keeping the migration armed across boots is exactly what makes "the target already has rows"
  reachable — measured, 4 legacy pins in, 0 out, `Copied 4` logged. The ledger name was **not**
  bumped to `_v2`: it would re-run the copy on deployments that already migrated legitimately, and
  without a preserved id that duplicates every pin, to recover a population (fresh install, then a
  legacy database restored on top of it) that does not otherwise exist. And the per-channel
  character cap is applied at its full 40,000 rather than at a fraction: a channel left at
  39,946/40,000 is a state `add_pin` permits, and inventing a migration-only sub-budget would be an
  undocumented second rule.
- ~~**DAB-003 — a routine gateway reconnect emits two CRITICAL records whose text is false.**~~
  **Closed in round 2 Phase 3.** Reproduced first: `setup_commands` called twice on one tree
  raises `CommandAlreadyRegistered: Command 'ping' already registered.` with the tree left intact
  at 22, after which `on_ready` logged CRITICAL "registration FAILED ... running with an
  incomplete command tree" *and* CRITICAL "Synced only 22 ... the command tree is incomplete",
  both false. Registration is now skipped outright on a reconnect, guarded by
  `bot._slash_commands_registered`.

  **The obvious fix — seed `registered` from `bool(self.tree.get_commands())` — was rejected on
  measurement, and it is worth recording why, because it reads as equivalent.** It silences only
  the *second* record: `registered` is consumed in the sync branch alone, while the `except` arm
  is unconditional, so a reconnect still logs "registration FAILED" with a traceback at CRITICAL.
  Worse, it mutes a **true** warning in ten of the eleven places a registrar can fail.
  `register_ping_command` is unconditionally first, so a fault anywhere after it leaves a
  non-empty, genuinely incomplete tree — swept across every registrar, ten of them leave 1-16
  commands and none self-heals, because the retry stops at `ping` every time. Seeding from the
  tree reports those as `Synced 6 slash command(s) globally` at INFO from the first reconnect on.
  `M-DAB003B` and `M-DAB003C` reintroduce both spellings.

  Clearing the tree and rebuilding it was rejected too, and it is the dangerous option:
  measured, a registrar raising on the second run takes the tree from 22 commands to 5, and
  `tree.sync()` is a full-replace `PUT /applications/{id}/commands`, so the other 17 are deleted
  from Discord globally by a transient fault the shipped code survives untouched. A degraded
  image backend on reconnect does the same thing at 24 → 22, silently un-publishing
  `/edit-image` and `/image-queue`. `M-DAB003E`.

  Two residuals, recorded not fixed. `tree.sync()` still runs on every reconnect — one
  full-replace PUT of an identical 8,048-byte payload — which the guard neither adds nor removes;
  moving registration and sync into `Client.setup_hook` would remove it, and discord.py's own
  docstring recommends exactly that ("only called once, in `login()` ... a better solution than
  doing such setup in the `on_ready` event"), with `application_id` assigned before `setup_hook`
  runs so `tree.sync()` works there. That is a startup restructure rather than a log-correctness
  fix, so it is a separate change. `_start_automatic_rag_backlog` re-runs on every reconnect too
  (measured, 3 of 3) — it resumes from `message_backfill_progress`, so the cost is one extra
  channel-history walk per reconnect rather than a re-index, and `setup_hook` would not move it.
  And every variant, including the shipped one, is racy if any registrar ever awaits: `on_ready`
  is dispatched as its own task and two can overlap. The flag is claimed *before* the await for
  that reason, and released on a `CancelledError` as well as on an `Exception`, but nothing
  enforces the no-await property.

### PPR-11 (S3) — the report web UI accepted a cross-site form POST — **CLOSED, Phase 2.3**

**This is `DAB-145`**, which `BUG_ANALYSIS_2026-07-29.md:1260` names in one line — "No CSRF
protection: a cross-origin form POST mutates report state" — and which never reached this
register, so it was invisible to every pass that worked from the backlog. It is written up here
because it was the one part of the web-server surface that was live on the **shipped loopback
default** rather than on a misconfiguration, and because three of its four exploitable shapes
are not in that line. `_update_status` reads `await request.post()`, an
`application/x-www-form-urlencoded` body. That is a *simple* content type: a browser submits it
cross-origin with no CORS preflight, and there was no CSRF token, no `Origin` check and no session
cookie to make `SameSite`. Any page the operator visited while the bot was running could silently
reclassify or annotate any report by id. Measured before the fix, against `127.0.0.1`:
`Origin: https://evil.example` → `303`, row `('open', None)` → `('done', 'drive-by from
evil.example')`.

Three further shapes, all found by executing the guard rather than by reading it, all now refused
and each pinned by its own mutant:

- **DNS rebinding defeats an Origin-equals-Host check.** Point `evil.example` at `127.0.0.1` and
  the victim's form POST carries `Host: evil.example` *and* `Origin: http://evil.example`. They
  agree, so the check passes: measured 303, row mutated. Closed by allowlisting the `Host` to
  loopback names and answering `421` otherwise.
- **A missing `Origin` is not a browser.** Per Fetch Standard §3.2 the `Origin` append is
  unconditional for any method that is not `GET`/`HEAD`, and a suppressing referrer policy sets it
  to the literal `null` rather than omitting it. So refusing the absent case cannot reject a
  browser form, and allowing it made the guard optional for everything else.
- **A parsed comparison is not an exact one.** A trailing slash, a path, `HTTP://`, and an embedded
  tab all yield the right netloc and all mutated a row. The comparison is now an exact string
  match against `http://{Host}`.

### PPR-12 (S4) — a partial POST to the report UI silently wipes the admin notes

`_update_status` reads `admin_notes = str(data.get("admin_notes", ""))`
(`report_web_server.py`), so a request that omits the field is indistinguishable from one that
sends it empty, and `update_status` writes the empty string over whatever was there. Measured:
a body of `status=done` alone turned `('open', 'IMPORTANT OPERATOR NOTES')` into `('done', '')`
with a 303. The served form always sends both fields, so this needs a hand-made request; it is
recorded rather than fixed because it is data loss, not a security boundary, and folding it into
the Phase 2.3 security commit would have muddied both. Two riders in the same handler: an
`admin_notes` arriving as a multipart file field stores `str(FileField(...))` — a repr containing a
file descriptor number — and the field length is unbounded (900 KB accepted and stored).

### PPR-13 (S4) — `GET /` renders 200 reports for a `HEAD`, on the event loop

`app.router.add_get` defaults to `allow_head=True`, so a `HEAD /` runs `list_reports(limit=200)`
and the full HTML render synchronously on the bot's event loop and then discards the body.
Measured directly, driving the real handler against a database of 200 reports: **2.2 ms of
uninterrupted event-loop time per request** (median of 21, min 2.1, max 2.9). Quoted that way on
purpose — an earlier review reported 232 requests/s for this shape and a re-run of it reported
397/s, so the rate is not a number worth carrying; the per-request cost is.

**The TCP client must be local; the party driving it need not be.** A remote page cannot read the
response — there are no CORS headers, so the browser withholds it — but it does not need to:
`new Image().src = "http://127.0.0.1:8080/"` is a no-cors GET, carries no `Origin`, and makes the
bot do the work anyway. The `Host` allowlist does not see it, because the `Host` genuinely is
`127.0.0.1`. Recorded rather than fixed: `Sec-Fetch-Site: cross-site` would identify it, but that
header can only ever be used to reject and never to allow, and a second, weaker guard beside the
one that already protects every state change is not a trade worth making for a request that
changes nothing. Same event-loop-blocking class as DAB-178, which already covers the synchronous
SQLite in these two handlers.

### PPR-14 (S4) — three of the four request configs send no safety settings at all

`config.yaml`'s `safety:` block reads as though it governs the bot. It governs one request path.
Measured by locating every `types.GenerateContentConfig(` under `src/` and checking each for a
`safety_settings` argument:

| Site | `safety_settings` |
|---|---|
| `gemini_client.py:1474` — the main response path | **yes** |
| `gemini_client.py:723` — the intent/complexity router | no |
| `enhanced_command_handler.py:222` — the LLM command router | no |
| `nano_banana_client.py:297` — image generation | no |

The three without it take the provider's defaults, so an operator who tightens `safety:` tightens
the answer the bot writes and nothing else — and an operator who reads the config believes
otherwise. Embeddings are correctly absent from the table: there is no safety surface on an
embedding call.

Not fixed in Phase 2.4, which was about the four thresholds resolving to what they say. Whether
the router and the image path *should* carry the same thresholds is a product question -- the
router sees the user's text and the image path sees a prompt, and the right answer for each may
not be the one configured for responses. Recorded so the question gets asked rather than
inherited. The `safety:` comment in `config.yaml` now states the scope, so the config no longer
overstates its own reach.

### PPR-15 (S4) — `/pins` hands anyone a delete button for anyone's pin

`PinDeleteView` (`personalization.py`) overrides no `interaction_check`, so discord.py's default
`return True` applies, and `/pins` replies without `ephemeral=True`. The delete buttons therefore
sit on a public message that any channel member can click, and `PinService.delete_pin` has no
permission check of its own. Pre-existing; the PPR-06 fix widens the button count from 20 to 25
but does not create it. Deleting a pin is recoverable only by re-pinning from memory, and `/pin`
itself is ungated, so the severity is bounded by the same reasoning as `/live` (PPR-08): the
action is cheap and the deployment is a single guild. It is listed because "the delete buttons are
the recovery path" is now load-bearing in two findings.

### PPR-16 (S4) — `get_pins` sorts on a TEXT column carrying two timestamp formats

`get_pins` does `ORDER BY pinned_at ASC` (`pin_service.py`). `add_pin` writes
`datetime.utcnow().isoformat()` with a `T` separator; the column default is `CURRENT_TIMESTAMP`,
which uses a space, and `0x20 < 0x54` — so a same-day default row sorts before *every* ISO row
regardless of its time. An offset suffix sorts lexically too. Measured: two rows inserted 1.1 s
apart come back newest-first.

Mostly cosmetic until PPR-06: now that `/pins` shows a prefix of the list, this sort decides
*which* pins a truncated listing hides. The legacy migration avoids it by ordering on `id`
instead, which is `INTEGER PRIMARY KEY AUTOINCREMENT` and therefore is insertion order with no
format variants; `get_pins` should do the same, but it feeds the retrieval path
(`hybrid_context_retriever.py:452`) as well as the command, so it is a wider change than it looks.

### PPR-17 (S4) — the mutation harness reports a wrong guard without failing

`scripts/mutation_check.py:253-256` computes `guarded = any(mutant.guard in name for name in
result.killers)` and appends `(NOTE: not the declared guard)` to the output — but the verdict
column still reads `ok` and the exit status is unaffected, so a mutant whose declared guard has
stopped observing it is a line of prose nobody has to act on. Two mutants are in that state today
(`M-DAB019`, `M-DAB078B`), both pre-existing. The same block prints `result.killers[:4]`, so on a
mutant killed by five or more tests the declared guard can be absent from the printed list while
present in the check — which reads as a failure and is not. Both are one-line fixes; neither is
urgent, because the wrong-guard case is still reported, just not enforced.

### PPR-18 (S4) — deliberate-failure tests write tracebacks to the suite's stderr

Round 2 Phase 3 captured the four `on_ready` cases, which turned the noise into assertions that a
real failure stays loud. The same shape remains in the RAG write-failure tests, which emit
`Failed to index message 4242: database is locked` and `Could not re-index edited message 4242`
with full tracebacks on every run. They are correct records from tests that provoke the failure on
purpose; capturing them with `assertLogs` would both quieten the run and assert the degradation is
announced, which is a property those tests do not currently check.

### DAB-087 semantic half — deferred with a named prerequisite (round 2 Phase 5)

Widening `_embedding_is_trivial`'s Latin-only classes is **not** blocked on cost. At 100k indexed
messages with half the corpus non-Latin the estimate is ~37,500 rows reclassified and ~4.4M
embedding tokens, of the order of a dollar; the price could not be verified offline, so trust the
token count rather than the figure. It is blocked on a data-integrity hazard sitting underneath it:

- `_eligibility_fingerprint` hashes `_embedding_is_trivial`'s own **source**, so any edit to that
  function — including a comment or a reformat — bumps the fingerprint and the next boot re-runs
  the full reconcile. There is no separate step to withhold.
- The reconcile flips every non-Latin row from `skipped` to `pending`, and
  `_start_automatic_rag_backlog` drains them with **no inter-batch delay, no cap and no rate
  limiter** — roughly 2,400 back-to-back embedding calls over 16-40 minutes of boot.
- `mark_embedding_failed` is terminal at three attempts, and the reconcile only ever rescues rows
  in `skipped`, never in `failed`. A rate-limit storm part-way through that run therefore leaves
  rows **permanently unembeddable**, recoverable only by hand-editing SQLite. That would be unsafe
  to run at zero cost.

**Reopen condition, both halves required:** the backfill drain needs inter-batch pacing and a cap,
and `failed` must become recoverable by the reconcile. Then widen the classes, in a commit that
expects the re-embedding and says so.

Because the deferral would otherwise depend on nobody tidying a function, the prerequisite is
written into `_embedding_is_trivial`'s own docstring, where the next person to touch it will read
it. "Widen for new content only" is not an escape: measured, one reboot sets the row back to
`skipped` **and** NULLs the vector just paid for.

### PPR-19 (S3/S4) — five live findings promoted out of the pre-push review pile — **CLOSED, round 2 Phase 5**

The 2026-07-31 phase 2.3 and 2.4 reviews produced 34 findings that no document recorded. Most are
documentation; these five were code, and are fixed here. None is a security boundary — the loopback
refusal and the `Origin` guard both hold — they are the machinery around them being wrong in ways
an operator or a gateway reconnect can reach.

- **A concurrent `start()` leaked a listening socket.** `ReportWebServer.start` awaits twice
  between checking `self._runner` and setting it, so two callers both cleared the guard and both
  bound. Measured with `ss -ltnp`: **two** listening sockets, and `stop()` closed only the one that
  won the assignment — the other stayed open for the life of the process. `on_ready` re-fires on
  every gateway reconnect, which is exactly where a second call comes from. Serialised with a lock.
- **`self.port` was never updated from the bind.** With `web_port: 0` the kernel picks the port and
  `self.port` stayed `0`, so the refusal message's remedy read `ssh -L 0:127.0.0.1:0`. `.urls`
  already read the real value and was right.
- **`::ffff:127.0.0.1` cleared the pre-check and then could not bind.** `ipaddress` calls it
  loopback, and the kernel rejects it: `OSError: [Errno 22] invalid argument`, propagating out of
  `start()` to `on_ready` as *"Failed to start report web UI"* plus a traceback — a bug report
  about this server rather than an address the operator can fix. Now a refusal, along with any
  other unbindable address.
- **The pre-check refusal claimed a socket had been bound.** It said the host *"binds 0.0.0.0"* on
  the path where nothing was ever opened, sending an operator to look for a listener that does not
  exist.
- **An unrecognised safety threshold logged once per category per call** — four identical ERROR
  records per message. Now reported once per distinct value. The volume was the only defect: the
  strict fallback is correct, and it does not fire for an operator who has booted normally, because
  the shipped config canonicalises cleanly on all four categories. Reaching it means a `BotConfig`
  got to the client without passing `validate_config`, which the message says. `M-RWS07` pins the
  hazard of touching it at all — quieter must not become more permissive.

One finding from the same pile is **corrected rather than fixed**: the loopback refusal is *not*
atomic for a host *name*. Only address literals are judged before the bind, so a name resolving
off-box does open a socket for the microseconds between `site.start()` and `runner.cleanup()`.
Closing that window means resolving the name here and handing the same string to aiohttp — two
lookups that can disagree, which is the failure the post-bind check exists to avoid. The class
docstring described the refusal as absolute; it now states the window.

`M-RWS01` through `M-RWS07` pin all seven decisions. The remaining 29 findings from that pile are
documentation and are Phase 6's.
