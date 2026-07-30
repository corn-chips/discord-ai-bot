# Documentation Index

An honest front door to `docs/`. Several documents here are historical snapshots that were
never re-measured after the code moved on; the Status column says which is which, so nothing in
this directory has to be taken on trust.

Status key:

- **CURRENT** - measured against branch `dev`, HEAD `c83f740`, on 2026-07-29. Act on it. Four
  commits have landed since (`0bf532c` dead-code removal, `0c9eb91` dependency drop, then
  `02b91a7` and `28409e4`, both test-only). The findings themselves remain valid, but every
  `file:line` reference into a file `0bf532c` touched has shifted. Trust the finding and
  re-locate the symbol; do not trust the line number.
- **HISTORICAL** - accurate for the state it describes and useful as a record. Do not treat its
  figures as current.
- **SUPERSEDED** - kept for provenance only. Every status field in it is wrong. Read the banner
  at the top before the body.

| Document | What it is | Status | Date |
|---|---|---|---|
| [`ANALYSIS_CORRECTIONS.md`](ANALYSIS_CORRECTIONS.md) | The record of claims made by the 2026-07-29 analysis that later verification refuted, with the evidence that overturned each one. Read it before acting on any ticket, so a corrected claim is not re-implemented. | CURRENT | 2026-07-29 |
| [`BUG_ANALYSIS_2026-07-29.md`](BUG_ANALYSIS_2026-07-29.md) | Correctness findings from the 2026-07-29 repository-wide bug sweep: reproduction, evidence, severity, and current status for each defect. Replaces `DEEP_BUG_HUNT_REPORT.md`. | CURRENT | 2026-07-29 |
| [`IMPROVEMENT_ANALYSIS_2026-07-29.md`](IMPROVEMENT_ANALYSIS_2026-07-29.md) | Improvement lanes from the same sweep: architecture, data layer, performance, cost accounting, configuration, and testing/DX, with measured before-and-after figures rather than estimates. | CURRENT | 2026-07-29 |
| [`ANALYSIS_BACKLOG.md`](ANALYSIS_BACKLOG.md) | The reconciled finding register behind both analyses: one row per finding, deduplicated across lanes, with severity and source. The index to read first when tracing where a number came from. | CURRENT | 2026-07-29 |
| [`analysis-tickets/`](analysis-tickets/) | One file per actionable finding, sized for a single change: problem, evidence, proposed fix, and verification. Derived from `ANALYSIS_BACKLOG.md`. | CURRENT | 2026-07-29 |
| [`tech-debt-register.md`](tech-debt-register.md) | The technical debt register. Eight resolved items with re-measured evidence, plus ten items (TD-009 to TD-018) added on 2026-07-29, of which TD-015 (dead code) was closed by `0bf532c`/`0c9eb91`; TD-019 was then registered for the incoherence `0bf532c` exposed, leaving ten open. Uses a `(impact x frequency) / effort` priority score. | CURRENT | 2026-07-29 |
| [`tech-debt-sprints/`](tech-debt-sprints/) | The four phased plans that closed TD-001 to TD-008, plus the roadmap README. A record of how those items were retired, not a live plan. Some figures inside are stale: the gate count of 98 tests is now 140 in 15 files, and after `0bf532c` the `DiscordBot` class is 1,067 lines not 1,131, with `BotConfig` at 96 fields not 93. Sprint 1's TD-004 project-layout claim was false when written; the README has since been repaired and that criterion now holds. | HISTORICAL | 2026-07-12 / 2026-07-13 |
| [`BOT_SYSTEM_REPORT.md`](BOT_SYSTEM_REPORT.md) | Full read-only architecture and message-pipeline review, including the hybrid RAG design. Predates the four tech-debt sprints, so its architecture narrative describes the pre-sprint shape. Carries a staleness banner listing every known-wrong claim; the body is unedited. | HISTORICAL | 2026-06-17 |
| [`DEEP_BUG_HUNT_REPORT.md`](DEEP_BUG_HUNT_REPORT.md) | Five-bug audit written against commit `936ab58` but labelled with a build id that does not exist in this repository. It shipped in the same commit as the fixes for all five bugs, so every "Status: Open" was stale on arrival. Carries a superseding banner with the true current status of each bug; the body is unedited. | SUPERSEDED | 2026-07-12 |
| `README.md` (this file) | Index of `docs/`. | CURRENT | 2026-07-29 |

## Reading order

If you are new to the repository, read `AGENTS.md` at the root first, then
`BOT_SYSTEM_REPORT.md` for the architecture narrative (with its banner in mind), then
`ANALYSIS_BACKLOG.md` for what is currently wrong, then `tech-debt-register.md` for what is
scheduled.

If you are picking up work, start at `analysis-tickets/` and use `ANALYSIS_BACKLOG.md` to see
how a ticket relates to everything else. Read `ANALYSIS_CORRECTIONS.md` before acting on any
ticket: later verification refuted several of the analysis's claims, including two dependency
edges and two prescribed fixes, and the ticket files were not rewritten.

## Verified baseline

Every CURRENT document in this directory was measured on 2026-07-29 against branch `dev`,
HEAD `c83f740`, using `.venv/bin/python` (CPython 3.12.13). Four commits have landed since:
`0bf532c` (dead-code removal, -2,111 lines across 25 files), `0c9eb91` (`constraints.txt`
57 -> 51 pins), and `02b91a7` / `28409e4`, which are test-only. Their findings still hold, but
`file:line` evidence pointing into a file `0bf532c` touched no longer resolves; re-locate the
symbol rather than trusting the offset. The test
baseline is `python -m unittest discover -s tests -p "test_*.py"` run from the repository root:
it was **125 tests, OK** in 13 files when the analysis was written, and is **140 tests, OK** in
15 files at HEAD, after `02b91a7` added `tests/test_repo_hygiene.py` (2) and `28409e4` added
`tests/test_on_message_flow.py` (13). Ticket baselines quote the 125 figure; the gate is 140.
pytest is not configured in this repository.
