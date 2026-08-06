# Documentation Index

An honest front door to `docs/`. Several documents here are historical snapshots that were
never re-measured after the code moved on; the Status column says which is which, so nothing in
this directory has to be taken on trust.

> **Start with [`REMEDIATION_2026-07-30.md`](REMEDIATION_2026-07-30.md).** Two
> remediation programmes landed **58 implementation commits** (`c83f740..083ebea`) plus a
> documentation phase over `c83f740`, and closed all nine S1 defects.
> Every other document in this directory was written before or during that programme, so a
> finding described as open in one of them has a good chance of being closed. The outcome summary
> says which.

Status key:

- **CURRENT** - the findings are measured and still worth acting on, but they were measured
  against branch `dev`, HEAD `c83f740`, on 2026-07-29. **58 implementation commits have landed
  since** (`c83f740..083ebea`), so
  (a) many findings are now closed — check the outcome summary or the ticket's own `Status:`
  header before starting one — and (b) essentially every `file:line` reference has shifted.
  Trust the finding, re-locate the symbol, do not trust the line number.
- **HISTORICAL** - accurate for the state it describes and useful as a record. Do not treat its
  figures as current.
- **SUPERSEDED** - kept for provenance only. Every status field in it is wrong. Read the banner
  at the top before the body.

| Document | What it is | Status | Date |
|---|---|---|---|
| [`REMEDIATION_2026-07-30.md`](REMEDIATION_2026-07-30.md) | The outcome of **both** remediation rounds: the phases of each, all nine S1 defects with the commit that closed each (and the two whose ticket is only partly discharged), the suite going 125 -> 405 and the mutation harness 0 -> 144 mutants, what was refuted rather than applied, what remains open, the eight deliberate deferrals with the condition that would reopen each, and what a reader should not trust. Read before any other document here. | CURRENT | 2026-08-06 |
| [`ANALYSIS_CORRECTIONS.md`](ANALYSIS_CORRECTIONS.md) | The record of claims made by the 2026-07-29 analysis that later verification refuted, with the evidence that overturned each one, plus the corrections the 2026-07-31 pre-push review added (items 17-19) and round 2's own (items 20-22). Read it before acting on any ticket, so a corrected claim is not re-implemented. | CURRENT | 2026-08-06 |
| [`BUG_ANALYSIS_2026-07-29.md`](BUG_ANALYSIS_2026-07-29.md) | Correctness findings from the 2026-07-29 repository-wide bug sweep: reproduction, evidence, severity, and current status for each defect. Replaces the deleted `DEEP_BUG_HUNT_REPORT.md`; §8.1 records why that report was self-refuting and §8.2 carries the true status of BUG-0001 to BUG-0005. | CURRENT | 2026-07-29 |
| [`IMPROVEMENT_ANALYSIS_2026-07-29.md`](IMPROVEMENT_ANALYSIS_2026-07-29.md) | Improvement lanes from the same sweep: architecture, data layer, performance, cost accounting, configuration, and testing/DX, with measured before-and-after figures rather than estimates. | CURRENT | 2026-07-29 |
| [`ANALYSIS_BACKLOG.md`](ANALYSIS_BACKLOG.md) | The reconciled finding register behind both analyses: one row per finding, deduplicated across lanes, with severity and source, each row reconciled against the landed commits. Also carries the post-programme findings: twenty-one in all, PPR-01 to PPR-21 — ten from the 2026-07-31 pre-push review, the rest from round 2 and its Phase 6 reconciliation. Its foot also records the still-open code findings from the round 2 reviews, whose own working notes live outside the checkout. The index to read first when tracing where a number came from. | CURRENT | 2026-08-06 |
| [`analysis-tickets/`](analysis-tickets/) | One file per actionable finding, sized for a single change: problem, evidence, proposed fix, and verification. Derived from `ANALYSIS_BACKLOG.md`. All 37 carry a `Status:` header, reconciled against their bodies on 2026-08-05: 24 LANDED, 6 PARTIAL, 1 REFUTED, 6 OPEN. On 16 of the 37 the fix landed by a route the body does not describe, was refuted, or left an acceptance criterion the body still ticks; the Status line says so and the body was not rewritten. | CURRENT | 2026-07-31 |
| [`tech-debt-register.md`](tech-debt-register.md) | The technical debt register. Nine resolved items with re-measured evidence, plus TD-009 to TD-019, re-assessed at `922e899`: TD-017 is now partially resolved and TD-012 substantially so, and the rest are unchanged. Uses a `(impact x frequency) / effort` priority score. | CURRENT | 2026-07-31 |
| [`tech-debt-sprints/`](tech-debt-sprints/) | The four phased plans that closed TD-001 to TD-008, plus the roadmap README. A record of how those items were retired, not a live plan. Some figures inside are stale: the gate count of 98 tests is now 401 in 37 files, the `DiscordBot` class is 1,258 lines not 1,131, and `BotConfig` has 99 fields not 93. Sprint 1's TD-004 project-layout claim was false when written; the README has since been repaired and that criterion now holds. | HISTORICAL | 2026-07-12 / 2026-07-13 |
| [`BOT_SYSTEM_REPORT.md`](BOT_SYSTEM_REPORT.md) | Full read-only architecture and message-pipeline review, including the hybrid RAG design. Predates the four tech-debt sprints, so its architecture narrative describes the pre-sprint shape. Carries a staleness banner listing every known-wrong claim; the body is unedited. | HISTORICAL | 2026-06-17 |
| `README.md` (this file) | Index of `docs/`. | CURRENT | 2026-08-06 |

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

Every CURRENT document in this directory has been re-checked on 2026-08-05 against branch
`remediation/2026-07-30`, HEAD `083ebea`, using `.venv/bin/python` (CPython 3.12.13). The findings themselves were measured
on 2026-07-29 against `c83f740`, and **58 implementation commits have landed since**
(`c83f740..083ebea`), so `file:line` evidence in
the analysis documents and the ticket bodies no longer resolves; re-locate the symbol rather than
trusting the offset. `AGENTS.md`'s citations, by contrast, were re-measured at `083ebea` and are
current.

The test baseline is `python -m unittest discover -s tests -p "test_*.py"` run from the repository
root: **125 tests, OK** in 13 files when the analysis was written, **405 tests, OK** in 37 files at
HEAD. Ticket baselines quote the 125 figure; the gate is 405. There is a second gate the analysis
documents predate: `python scripts/mutation_check.py --jobs 5`, **144/144 mutants**, 250 s measured at `--jobs 5`.
pytest is not configured in this repository.
