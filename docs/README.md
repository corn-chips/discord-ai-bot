# Documentation Index

An honest front door to `docs/`. Several documents here are historical snapshots that were
never re-measured after the code moved on; the Status column says which is which, so nothing in
this directory has to be taken on trust.

> **Start with [`REMEDIATION_2026-07-30.md`](REMEDIATION_2026-07-30.md).** A four-phase
> remediation programme landed **34 commits** over `c83f740` and closed all nine S1 defects.
> Every other document in this directory was written before or during that programme, so a
> finding described as open in one of them has a good chance of being closed. The outcome summary
> says which.

Status key:

- **CURRENT** - the findings are measured and still worth acting on, but they were measured
  against branch `dev`, HEAD `c83f740`, on 2026-07-29. **34 commits have landed since**, so
  (a) many findings are now closed — check the outcome summary or the ticket's own `Status:`
  header before starting one — and (b) essentially every `file:line` reference has shifted.
  Trust the finding, re-locate the symbol, do not trust the line number.
- **HISTORICAL** - accurate for the state it describes and useful as a record. Do not treat its
  figures as current.
- **SUPERSEDED** - kept for provenance only. Every status field in it is wrong. Read the banner
  at the top before the body.

| Document | What it is | Status | Date |
|---|---|---|---|
| [`REMEDIATION_2026-07-30.md`](REMEDIATION_2026-07-30.md) | The outcome of the four-phase remediation programme: the phases, all nine S1 defects with the commit that closed each, the suite going 125 -> 273 and the mutation harness 0 -> 56 mutants, what was refuted rather than applied, and what remains open. Read before any other document here. | CURRENT | 2026-07-31 |
| [`ANALYSIS_CORRECTIONS.md`](ANALYSIS_CORRECTIONS.md) | The record of claims made by the 2026-07-29 analysis that later verification refuted, with the evidence that overturned each one, plus the corrections the 2026-07-31 pre-push review added (items 17-19) and round 2's own (items 20-21). Read it before acting on any ticket, so a corrected claim is not re-implemented. | CURRENT | 2026-07-31 |
| [`BUG_ANALYSIS_2026-07-29.md`](BUG_ANALYSIS_2026-07-29.md) | Correctness findings from the 2026-07-29 repository-wide bug sweep: reproduction, evidence, severity, and current status for each defect. Replaces `DEEP_BUG_HUNT_REPORT.md`. | CURRENT | 2026-07-29 |
| [`IMPROVEMENT_ANALYSIS_2026-07-29.md`](IMPROVEMENT_ANALYSIS_2026-07-29.md) | Improvement lanes from the same sweep: architecture, data layer, performance, cost accounting, configuration, and testing/DX, with measured before-and-after figures rather than estimates. | CURRENT | 2026-07-29 |
| [`ANALYSIS_BACKLOG.md`](ANALYSIS_BACKLOG.md) | The reconciled finding register behind both analyses: one row per finding, deduplicated across lanes, with severity and source, each row reconciled against the landed commits. Also carries the post-programme findings: ten from the 2026-07-31 pre-push review, PPR-11 to PPR-13 from round 2 Phase 2.3. The index to read first when tracing where a number came from. | CURRENT | 2026-07-31 |
| [`analysis-tickets/`](analysis-tickets/) | One file per actionable finding, sized for a single change: problem, evidence, proposed fix, and verification. Derived from `ANALYSIS_BACKLOG.md`. All 37 carry a `Status:` header as of 2026-07-31: 23 LANDED, 1 PARTIAL, 1 REFUTED, 12 OPEN. On 12 of the 37 the fix landed by a route the body does not describe, or was refuted; the Status line says so and the body was not rewritten. | CURRENT | 2026-07-31 |
| [`tech-debt-register.md`](tech-debt-register.md) | The technical debt register. Nine resolved items with re-measured evidence, plus TD-009 to TD-019, re-assessed at `922e899`: TD-017 is now partially resolved and TD-012 substantially so, and the rest are unchanged. Uses a `(impact x frequency) / effort` priority score. | CURRENT | 2026-07-31 |
| [`tech-debt-sprints/`](tech-debt-sprints/) | The four phased plans that closed TD-001 to TD-008, plus the roadmap README. A record of how those items were retired, not a live plan. Some figures inside are stale: the gate count of 98 tests is now 273 in 26 files, the `DiscordBot` class is 1,258 lines not 1,131, and `BotConfig` has 99 fields not 93. Sprint 1's TD-004 project-layout claim was false when written; the README has since been repaired and that criterion now holds. | HISTORICAL | 2026-07-12 / 2026-07-13 |
| [`BOT_SYSTEM_REPORT.md`](BOT_SYSTEM_REPORT.md) | Full read-only architecture and message-pipeline review, including the hybrid RAG design. Predates the four tech-debt sprints, so its architecture narrative describes the pre-sprint shape. Carries a staleness banner listing every known-wrong claim; the body is unedited. | HISTORICAL | 2026-06-17 |
| [`DEEP_BUG_HUNT_REPORT.md`](DEEP_BUG_HUNT_REPORT.md) | Five-bug audit written against commit `936ab58` but labelled with a build id that does not exist in this repository. It shipped in the same commit as the fixes for all five bugs, so every "Status: Open" was stale on arrival. Carries a superseding banner with the true current status of each bug; the body is unedited. | SUPERSEDED | 2026-07-12 |
| `README.md` (this file) | Index of `docs/`. | CURRENT | 2026-07-31 |

## Reading order

If you are new to the repository, read `AGENTS.md` at the root first, then
`REMEDIATION_2026-07-30.md` for what the code has just been through, then
`BOT_SYSTEM_REPORT.md` for the architecture narrative (with its banner in mind), then
`ANALYSIS_BACKLOG.md` for what is still wrong, then `tech-debt-register.md` for what is
scheduled.

If you are picking up work, start at `analysis-tickets/` — every ticket now says at the top
whether it has LANDED, is PARTIAL, was REFUTED, or is OPEN — and use `ANALYSIS_BACKLOG.md` to see
how a ticket relates to everything else. Read `ANALYSIS_CORRECTIONS.md` before acting on any
ticket: later verification refuted several of the analysis's claims, including two dependency
edges and four prescribed fixes, and the ticket *bodies* were not rewritten.

## Verified baseline

Every CURRENT document in this directory has been re-checked on 2026-07-31 against branch `dev`,
HEAD `922e899`, using `.venv/bin/python` (CPython 3.12.13). The findings themselves were measured
on 2026-07-29 against `c83f740`, and **34 commits have landed since**, so `file:line` evidence in
the analysis documents and the ticket bodies no longer resolves; re-locate the symbol rather than
trusting the offset. `AGENTS.md`'s citations, by contrast, were re-measured at `922e899` and are
current.

The test baseline is `python -m unittest discover -s tests -p "test_*.py"` run from the repository
root: **125 tests, OK** in 13 files when the analysis was written, **273 tests, OK** in 26 files at
HEAD. Ticket baselines quote the 125 figure; the gate is 273. There is a second gate the analysis
documents predate: `python scripts/mutation_check.py --jobs 5`, **56/56 mutants**, ~80 s.
pytest is not configured in this repository.
