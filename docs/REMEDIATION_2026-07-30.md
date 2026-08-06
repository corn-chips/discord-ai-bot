# Remediation programme, 2026-07-29 to 2026-08-05 — outcome

Last updated: 2026-08-05 | Repo state: branch `remediation/2026-07-30`, HEAD at the tip of the
Phase 6 documentation commits | Gate: **394 tests, OK**; mutation harness **130/130** (220 s at
`--jobs 5`)

**Read this before any other document in `docs/`.** The analysis corpus describes the repository
as it stood at `c83f740`. **58 implementation commits have landed since** (`c83f740..083ebea`),
across two rounds, plus this documentation phase, and they closed
most of what that corpus reports as open. Working from the analysis alone, you would re-fix work
that is already done.

`dev` is frozen at `c83f740` on purpose — it is the pre-programme state, kept as the analysis
baseline. It is not behind by accident and must not be "restored".

## What the programme was

The 2026-07-29 repository-wide analysis produced a register of 211 canonical findings
(9 S1, 72 S2, 82 S3, 43 S4, 5 informational), of which 37 were written up as executable tickets in
[`analysis-tickets/`](analysis-tickets/). Two rounds worked that ticket set, plus the findings the
rounds' own reviews produced.

Every commit states its measured before/after, the mutants it added, and the suite delta. Where
measurement contradicted a ticket, the ticket was **not** followed; the contradiction is recorded
in [`ANALYSIS_CORRECTIONS.md`](ANALYSIS_CORRECTIONS.md) and inline at the top of the ticket.

Each phase was followed by a review of its own output. Six of those reviews found defects in the
phase that had just landed, and each is a commit of its own — `4fd2409`, `05d4912`, `01f75ba`,
`8168151`, `083ebea`, and the DAB-198 correction in Phase 6. That is the programme's single most
productive habit. Counting only what is attributable: the **nine** post-programme findings now
marked CLOSED (`PPR-02`, `04`, `05`, `06`, `07`, `08`, `09`, `11`, `19`) were all introduced or
missed by the programme itself, against 24 tickets closed from the original register. Prefer the
absolute figure to a ratio — the denominators are not comparable.

## Round 1 — 34 commits, `9894bcc` to `922e899`

| Phase | Commits | Theme |
|---|---|---|
| 0 — instruments | `9894bcc` `4baa29c` `e278a08` `f0938a8` `4fc5063` `8168151` `693350d` `2d7c761` | Remove dead code, publish the analysis, add the mutation harness, close the BUG-0002 coverage gap. Nothing else could be trusted until a reintroduced bug was guaranteed to fail something. |
| 1 — stop the bleeding | `1d7ed94` `a51da49` `7955652` `b0d8308` `dce7510` `4fb091c` `4fd2409` `11357e7` | Duplicate reply and duplicate bill, unretryable transport faults, chain-of-thought posted as the answer, the quadratic splitter, LaTeX rendering, the PDF resource bomb. |
| 2/3 — authorization and spend | `eb9daaf` `d4b91a5` `b9a4514` `417e468` `5f884fa` `36bf261` | Gate `/rag` and the `/config` mutations, stop leaking internals to unprivileged users, cap the two commands that fan out, cap pinned memory and stop it starving retrieval. |
| 3b — startup and observability | `82b38a3` `161ff5d` `3a16e82` `c6cdcc3` `c756f48` `a0782cf` `05d4912` | Eager service construction, live-batch retry, an honest log file and a real debug lever, two failures that looked exactly like health, the last bare awaits in `on_ready`, error classification by code rather than prose. |
| 4 — the data layer | `15fc69a` `f2e8dd4` `7d2d8a2` `2ba9a82` `922e899` | Retrieval indexes, the migration ledger, the SQLite lock-wait budget, the tombstone-on-transient-error defect, the short migration that recorded itself as a success. |

## Round 2 — 24 commits, `a2ef986` to `083ebea`, plus Phase 6

Round 2 began from a pre-push review of round 1's combined diff, which produced ten findings
(`PPR-01` to `PPR-10`) that had not existed in the 211-finding register.

| Phase | Commits | Theme |
|---|---|---|
| 0 — correct the record | `a2ef986` `91db99d` `5552724` `8ffef67` `c0ffe1c` `9e9db61` | Say what actually landed in the documents a new reader opens first; delete a dead local; retract three comments claiming a rate limit that never applied; **stop two documents pointing an implementer at a 256 MP allocation**; replace the test that blocked DAB-042 with one that asserts the budget instead of banning the call. |
| 1 — the round 1 leftovers | `0283c5b` `6d35c71` | Back off on every retry decision, not one of three. Take the LLM router off the thread pool every SQLite call shares. |
| 2 — authorization and disclosure | `7da959d` `7e2dcf1` `2107aa5` `071f442` `8266d6b` | Gate `/live` and close the DM bypass around `/clear-cache` (`PPR-07`, `PPR-08`); scope `/report-status` to the caller; make the report web UI reachable only from this machine and only from itself; stop the safety thresholds failing open; stop three replies handing out the filesystem. |
| 3 — pins and startup | `0fb7016` `56bd52c` `a55c601` `01f75ba` | The pin migration burning its one shot; `/pins` refusing to render the channels that need it most; a routine reconnect reported as a lost command tree — then four defects and seven figures an independent review found in all three. |
| 4 — the data layer, again | `6591022` `f96717a` | A legacy migration with nothing to do costing every boot (`PPR-02`); the startup eligibility reconcile, gated on a fingerprint (`DAB-077`). |
| 5 — the remainder | `4558643` `6b735fa` `5b3b58a` `a621038` `083ebea` | Non-Latin search; the command tree pinned by name and order; a live attachment turn that died after the bill telling nobody; the image-job archive holding every upload; five live findings the pre-push review had left in the documentation pile. |
| 6 — documentation | this phase | Delete the report that had to be corrected by its own header; reconcile all 37 ticket `Status:` headers against their bodies; make every count say what the tree measures; record the review findings that lived only in scratch files. |

## All nine S1 defects are closed

| ID | Defect | Closed in |
|---|---|---|
| DAB-001 | Hybrid-RAG fallback `try` spanned delivery: duplicate reply, duplicate Gemini charge | `1d7ed94` |
| DAB-002 | `on_ready` swallowed a `setup_commands` failure, permanently disabling live mode and preferences | `82b38a3` |
| DAB-019 | A live batch was silently destroyed when processing raised | `161ff5d` (corrected in `05d4912`) |
| DAB-039 | The model's private reasoning could be posted to Discord as the answer | `7955652` |
| DAB-065 | A transient DB error during an edit permanently tombstoned the message | `2ba9a82` |
| DAB-066 | A legacy migration that copied nothing recorded itself as complete | `922e899` (idempotency in `6591022`) |
| DAB-115 | `split_message` was O(n^2) on the event loop | `b0d8308` |
| DAB-141 | `/rag delete scope:all` was ungated, cross-guild and irreversible | `eb9daaf` |
| DAB-198 | PDF resource bomb: a 3.6 KB upload could cost 107 s of CPU and returned nothing | `4fb091c` |

Four of the nine landed by a route the ticket did not prescribe, because the prescribed route was
measured and found wrong: DAB-019, DAB-065, DAB-066 and DAB-141. Each ticket says so at the top.

**Two of the nine close the S1 without closing the ticket, and the tickets say which is which.**
DAB-002's S1 is closed, but three observability criteria were not taken. DAB-198's S1 is closed —
the fixture that cost 107 s and produced nothing now returns 20 pages in 5.8 s — but **seven of
its eight acceptance criteria are untaken**. The clamp is per page, so nothing bounds the
attachment as a whole: that fixture still rasterises to 792 MP across 20 pages at 3.3 GB peak —
criterion 6, "no 1.6 GB spike", is unmet — and all 792 MP reach a paid request with no total
budget, no timeout and no size gate (`PPR-21`). Both tickets are marked PARTIAL.

## The verification instruments

| | At `c83f740` | At HEAD |
|---|---|---|
| Test suite | 125 tests in 13 files | **394 tests in 37 files** |
| Mutation harness | did not exist | **130 mutants, 130/130 matching expectation** (220 s with `--jobs 5`) |

The suite grew monotonically, commit by commit — every implementation commit records its own
`Suite: X -> Y` delta, and no commit reduced it. The mutation harness
(`scripts/mutation_check.py`, `scripts/mutants.toml`) reintroduces each fixed defect in a scratch
copy and fails if the suite does not notice; it went 5 mutants at `f0938a8` to 130 at HEAD. It is
the instrument that caught the defects review missed — three tests in Phase 5 alone passed for the
wrong reason and mutation, not reading, found all three.

## Refuted rather than applied

Measurement overturned published claims and prescribed fixes across 22 numbered items in
[`ANALYSIS_CORRECTIONS.md`](ANALYSIS_CORRECTIONS.md). The ones that change what a future
implementer should do:

- **DAB-212 is refuted, not deferred** (§13). The published 1844x was measured on a query
  production never issues. On the real channel-scoped form, once DAB-078 has landed, the rewrite
  is **111x slower** and reintroduces the temp B-tree it exists to remove. `M-DAB212` exists to
  stop it being re-applied. **Do not apply it.** It is still published unrefuted five times in
  `IMPROVEMENT_ANALYSIS_2026-07-29.md`; that file's banner now says so.
- **DAB-019's prescribed requeue expression is wrong three ways** (§8) — it duplicates the
  attachment suffix, re-debits the rate limiter and re-bills Gemini.
- **DAB-065's prescribed `deleted_at = NULL` repair does not repair anything** (§15) and
  reintroduces BUG-0004.
- **DAB-066's prescribed refusal would have been worse than the bug** (§16): it aborts
  `DiscordBot.__init__` on every boot.
- **DAB-141's prescribed subcommand-level gate does not serialise on discord.py 2.7.1** (§3); the
  gate has to sit on the group.
- **DAB-197 -> DAB-198 was published in the wrong direction** (§6), and the published direction
  was hazardous. The pixel clamp lands first.
- **DAB-203 never gated DAB-194** (§1), and DAB-194 landed first without incident.
- **`b9a4514`'s central premise is false** (§17): slash commands hit no rate limiter at all, not
  the 60/h text limit the commit message claims.

## What is still open

Six of the 37 tickets are untouched, six are partial, one is refuted.

| Ticket | State |
|---|---|
| DAB-027 / DAB-028 | **Open.** `_vector_lock` still spans CPU-bound numpy scoring; `search_semantic` still copies the matrix. Land the two together. |
| DAB-032 | **Open.** The rate limiter still rebuilds every tracked user's history on every call — now at two call sites, because `b9a4514` added a second limiter. |
| DAB-096 | **Open.** Synchronous SQLite still runs on the event loop; re-inventory before quoting "13 sites". |
| DAB-106 | **Open.** 39 of 99 `BotConfig` fields have no validation rule. |
| DAB-180 | **Open.** `grok-prompts` is a dangling gitlink; `/deepresearch` degrades log-loud and user-silent. |
| DAB-029 | **Partial.** One of six containers (the image-job archive payload). |
| DAB-042 | **Partial.** Backoff on every retry decision landed; the whole-sequence deadline did not. Unblocked. |
| DAB-073 | **Partial.** The retrieval floor landed; acceptance criterion 2 still fails and criteria 3-6 are untaken. |
| DAB-087 | **Partial.** Lexical half landed; semantic half deferred, see below. |
| DAB-095 | **Partial.** Busy timeout landed; WAL deferred with the measurement. |
| DAB-198 | **Partial.** Per-page clamp only; see the S1 note above. |
| DAB-212 | **Refuted.** Not work; a trap. |

Plus the twenty-one post-programme findings (`PPR-01` to `PPR-21`) and the still-open code
findings from the round 2 reviews, all at the foot of
[`ANALYSIS_BACKLOG.md`](ANALYSIS_BACKLOG.md).

## Deliberate deferrals, and what would reopen each

These are decisions, not omissions. Each was measured. A tidy-up that converts one of these into a
to-do is a regression.

| Deferral | Why | Reopen when |
|---|---|---|
| **DAB-095's WAL half** | WAL costs +0.34 ms on every call on a per-call-connection architecture, and made no difference to the DAB-065 trigger in any journal mode. | Connections are pooled. (`ANALYSIS_CORRECTIONS.md` §14) |
| **DAB-096** | `to_thread` re-measured like-for-like is 0.144 -> 0.225 ms, i.e. **slower** per call. Only the loop-stall figure argues for it. | A measured event-loop stall matters more than per-call throughput. |
| **DAB-106 as a schema** | The full pydantic migration is L effort against 39 fields nobody has misconfigured. The six-field stop-gap is the scoped version. | Someone is actually bitten, or the field count grows materially. |
| **DAB-027 / DAB-028** | M effort each, and they must land together — DAB-028 alone regresses the narrow-channel query. | Concurrency at 350k vectors is a real workload here; today it is one operator. |
| **DAB-032** | S3, and the p95 only bites at 20,000 tracked users. | The user population approaches that, or the second limiter makes it hotter. |
| **DAB-180 beyond loud degradation** | Refusing to register `/deepresearch` when the submodule is absent is now unblocked, but it changes the command tree. | The submodule is vendored, or the silent user-facing half is judged worse than a missing command. |
| **DAB-087's semantic half** | **Has a named prerequisite: backfill pacing plus a recoverable `failed` state.** Without both, widening eligibility drains ~37,500 rows / ~4.4M tokens in one uncapped run. | Both exist. The prerequisite is recorded in `_embedding_is_trivial`'s docstring — **and `_eligibility_fingerprint` hashes that function's source text, so editing it at all, including reflowing the docstring, starts a 16-40 minute paid re-embedding run.** Do not touch it. |
| **PPR-03** | **Unassessed, not unreachable.** Cancellation during the DAB-019 retry backoff may double-debit the limiter; nobody has measured whether it can happen in practice. | Someone measures it. Do not record it as closed on the strength of it being small. |

## Where to read next

- [`ANALYSIS_BACKLOG.md`](ANALYSIS_BACKLOG.md) — the worklist, row by row, with landed status, the
  21 post-programme findings, and the still-open code findings the round 2 reviews produced.
- [`ANALYSIS_CORRECTIONS.md`](ANALYSIS_CORRECTIONS.md) — 22 items. Read before acting on any ticket.
- [`analysis-tickets/`](analysis-tickets/) — every ticket carries a `Status:` header, reconciled
  against its body in Phase 6: **24 LANDED, 6 PARTIAL, 1 REFUTED, 6 OPEN**. On 16 of the 37 the
  Status line records a route correction, a refutation, or an acceptance criterion the body still
  ticks. **Read the Status line before the body; the bodies were never rewritten.**
- [`tech-debt-register.md`](tech-debt-register.md) — the longer-lived debt, TD-001 to TD-019.

## What a reader should not trust

- **Any `file:line` in the analysis corpus or a ticket body.** Measured at `c83f740`; 58 implementation commits
  have moved most of them. Trust the finding, re-locate the symbol.
- **Any present-tense figure in `BUG_ANALYSIS_2026-07-29.md` or
  `IMPROVEMENT_ANALYSIS_2026-07-29.md`.** Both are `c83f740` snapshots. Their banners name the
  classes that moved and the three figures that are wrong to act on rather than merely old.
- **A ticket body's acceptance criteria over its Status line.** The Status line is the reconciled
  one.
- **`BOT_SYSTEM_REPORT.md`'s architecture narrative.** It predates the four tech-debt sprints and
  carries a banner listing its known-wrong claims.
