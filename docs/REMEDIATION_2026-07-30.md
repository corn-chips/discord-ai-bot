# Remediation programme, 2026-07-29 / 2026-07-30 — outcome

Last updated: 2026-07-31 | Repo state: branch `dev`, HEAD `922e899` | Gate: **273 tests, OK**;
mutation harness **56/56**

**Read this before any other document in `docs/`.** The analysis corpus describes the repository
as it stood at `c83f740`. **34 commits have landed since**, and they closed most of what that
corpus reports as open. Working from the analysis alone, you would re-fix work that is already
done.

## What the programme was

The 2026-07-29 repository-wide analysis produced a register of 211 canonical findings
(9 S1, 72 S2, 82 S3, 43 S4, 5 informational), of which 37 were written up as executable tickets in
[`analysis-tickets/`](analysis-tickets/). The remediation programme worked that ticket set in
phases, each phase followed by a review of its own output — two of those reviews found defects in
the phase that had just landed, and both are recorded as commits of their own (`4fd2409`,
`05d4912`).

Every commit states its measured before/after, the mutants it added, and the suite delta. Where
measurement contradicted a ticket, the ticket was **not** followed; the contradiction is recorded
in [`ANALYSIS_CORRECTIONS.md`](ANALYSIS_CORRECTIONS.md) and, since Phase 4, inline at the top of
the ticket itself.

## The phases

| Phase | Commits | Theme |
|---|---|---|
| 0 — instruments | `9894bcc` `4baa29c` `e278a08` `f0938a8` `4fc5063` `8168151` `693350d` `2d7c761` | Remove dead code, publish the analysis, add the mutation harness, close the BUG-0002 coverage gap. Nothing else could be trusted until a reintroduced bug was guaranteed to fail something. |
| 1 — stop the bleeding | `1d7ed94` `a51da49` `7955652` `b0d8308` `dce7510` `4fb091c` `4fd2409` `11357e7` | Duplicate reply and duplicate bill, unretryable transport faults, chain-of-thought posted as the answer, the quadratic splitter, LaTeX rendering, the PDF resource bomb. |
| 2/3 — authorization and spend | `eb9daaf` `d4b91a5` `b9a4514` `417e468` `5f884fa` `36bf261` | Gate `/rag` and the `/config` mutations, stop leaking internals to unprivileged users, cap the two commands that fan out, cap pinned memory and stop it starving retrieval. |
| 3b — startup and observability | `82b38a3` `161ff5d` `3a16e82` `c6cdcc3` `c756f48` `a0782cf` `05d4912` | Eager service construction, live-batch retry, an honest log file and a real debug lever, two failures that looked exactly like health, the last bare awaits in `on_ready`, error classification by code rather than prose. |
| 4 — the data layer | `15fc69a` `f2e8dd4` `7d2d8a2` `2ba9a82` `922e899` | Retrieval indexes, the migration ledger, the SQLite lock-wait budget, the tombstone-on-transient-error defect, the short migration that recorded itself as a success. |

## All nine S1 defects are closed

| ID | Defect | Closed in |
|---|---|---|
| DAB-001 | Hybrid-RAG fallback `try` spanned delivery: duplicate reply, duplicate Gemini charge | `1d7ed94` |
| DAB-002 | `on_ready` swallowed a `setup_commands` failure, permanently disabling live mode and preferences | `82b38a3` |
| DAB-019 | A live batch was silently destroyed when processing raised | `161ff5d` (corrected in `05d4912`) |
| DAB-039 | The model's private reasoning could be posted to Discord as the answer | `7955652` |
| DAB-065 | A transient DB error during an edit permanently tombstoned the message | `2ba9a82` |
| DAB-066 | A legacy migration that copied nothing recorded itself as complete | `922e899` |
| DAB-115 | `split_message` was O(n^2) on the event loop | `b0d8308` |
| DAB-141 | `/rag delete scope:all` was ungated, cross-guild and irreversible | `eb9daaf` |
| DAB-198 | PDF resource bomb: a 3.6 KB upload could cost 107 s of CPU and 1.63 GB RSS | `4fb091c` |

Four of the nine landed by a route the ticket did not prescribe, because the prescribed route was
measured and found wrong: DAB-019, DAB-065, DAB-066 and DAB-141. Each ticket now says so at the
top.

## The verification instruments

| | At `c83f740` | At `922e899` |
|---|---|---|
| Test suite | 125 tests in 13 files | **273 tests in 26 files** |
| Mutation harness | did not exist | **56 mutants, 56/56 matching expectation** (~80 s with `--jobs 5`) |

The suite grew monotonically, commit by commit — every commit in Phases 1-4 records its own
`Suite: X -> Y` delta, and no commit reduced it. The mutation harness (`scripts/mutation_check.py`,
`scripts/mutants.toml`) reintroduces each fixed defect in a scratch copy and fails if the suite
does not notice; it went 5 mutants at `f0938a8` to 56 at HEAD.

## Refuted rather than applied

Measurement overturned eight published claims and four prescribed fixes. Full evidence in
[`ANALYSIS_CORRECTIONS.md`](ANALYSIS_CORRECTIONS.md); the ones that change what a future
implementer should do:

- **DAB-212 is refuted, not deferred** (corrections §13). The published 1844x was measured on a
  query production never issues. On the real channel-scoped form, once DAB-078 has landed, the
  rewrite is **111x slower** and reintroduces the temp B-tree it exists to remove. `M-DAB212`
  exists to stop it being re-applied. Do not apply it.
- **DAB-095's WAL half is deferred with the measurement** (§14). WAL costs +0.34 ms on every call
  on a per-call-connection architecture and made no difference to the DAB-065 trigger in any
  journal mode. Reopen it when connections are pooled.
- **DAB-019's prescribed requeue expression is wrong three ways** (§8) — it duplicates the
  attachment suffix, re-debits the rate limiter and re-bills Gemini.
- **DAB-065's prescribed `deleted_at = NULL` repair does not repair anything** (§15) and
  reintroduces BUG-0004.
- **DAB-066's prescribed refusal would have been worse than the bug** (§16): it aborts
  `DiscordBot.__init__` on every boot, forever, with no operator escape.
- **DAB-141's prescribed subcommand-level gate does not serialise on discord.py 2.7.1** (§3); the
  gate has to sit on the group.
- **DAB-197 -> DAB-198 was published in the wrong direction** (§6), and the published direction was
  hazardous.
- **DAB-203 never gated DAB-194** (§1), and DAB-194 landed first without incident.

## What is still open

Twelve of the 37 tickets are untouched, one is partial, one is refuted. Ordered by the backlog's
priority score:

| Ticket | Why it is still open |
|---|---|
| DAB-077 | Startup eligibility reconcile still full-scans on every boot; DAB-083 unblocked it but Phase 4 did not take it. |
| DAB-029 | Six in-memory containers still never evict. |
| DAB-032 | The rate limiter still rebuilds every tracked user's history on every call. |
| DAB-095 | **Partial** — busy timeout landed, WAL and foreign keys deliberately deferred. |
| DAB-096 | Synchronous SQLite still runs on the event loop at the remaining call sites. |
| DAB-042 / DAB-204 | No whole-sequence deadline, and the regression test that blocks a correct one is still in place. |
| DAB-027 / DAB-028 | `_vector_lock` still spans CPU-bound numpy scoring; `search_semantic` still copies the matrix. |
| DAB-087 | Hybrid RAG is still blind to non-Latin scripts. |
| DAB-106 | 43 of 99 `BotConfig` fields still have no validation rule. |
| DAB-180 | `grok-prompts` is still a dangling gitlink. |
| DAB-203 | `tests/test_command_registration.py` still pins the tree positionally. |
| DAB-212 | **Refuted.** Not work; a trap. |

Plus the residual risks and the ten findings the 2026-07-31 pre-push review added, all recorded in
[`ANALYSIS_BACKLOG.md`](ANALYSIS_BACKLOG.md) under "Post-programme findings". The largest of those:
`/live` is still completely ungated, and the report web server is still unauthenticated.

## Where to read next

- [`ANALYSIS_BACKLOG.md`](ANALYSIS_BACKLOG.md) — the worklist, row by row, with landed status.
- [`ANALYSIS_CORRECTIONS.md`](ANALYSIS_CORRECTIONS.md) — read before acting on any ticket.
- [`analysis-tickets/`](analysis-tickets/) — every ticket now carries a `Status:` header:
  23 LANDED, 1 PARTIAL, 1 REFUTED, 12 OPEN. On 12 of the 37 the Status line contradicts the body.
- [`tech-debt-register.md`](tech-debt-register.md) — the longer-lived debt, TD-001 to TD-019.
