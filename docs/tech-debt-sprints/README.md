# Technical Debt Sprint Roadmap

This roadmap schedules every open item in `docs/tech-debt-register.md` while
keeping high-risk refactors behind regression-test and review gates.

| Sprint | Theme | Debt items | Exit condition |
|--------|-------|------------|----------------|
| 1 | Stabilize the foundation | TD-003 (part), TD-004, TD-005, TD-008 | Setup docs match the checkout, installs are reproducible, PDF conversion is non-blocking, and focused tests pass. |
| 2 | Make shared infrastructure testable | TD-003 (part), TD-006, TD-007 | Configuration and SQLite behavior use smaller shared units without changing public behavior. |
| 3 | Decompose the Gemini pipeline | TD-002, TD-003 (part) | Response routing, request construction, retries, extraction, and accounting have explicit boundaries and regression coverage. |
| 4 | Decompose Discord orchestration | TD-001, TD-003 (completion) | Command registration and event orchestration are split by responsibility with unchanged command/event behavior. |

## Execution Rules

Every implementation batch follows the same closed loop:

1. Capture a passing baseline for the affected tests.
2. Make one bounded change and add or update regression tests.
3. Run the focused tests, the full `unittest` suite, Python syntax compilation,
   and `git diff --check`.
4. Re-read the changed code and review the diff for correctness, compatibility,
   async lifecycle, persistence, and error-path regressions.
5. If the review finds a bug, fix it and repeat steps 3-4 until the batch has no
   blocking findings.

The repository's pre-existing `production/review-mode.txt` deletion is outside
this roadmap and must remain untouched.

## Execution Result - 2026-07-12

All four phases completed. The final gate passed 98 unit tests on 2026-07-12 (the gate is 394 in 37 files today), Python source
compilation, dependency consistency checks, and diff hygiene. Every production
batch received an implementation review plus an independent regression review;
all blocking findings were repaired and re-reviewed before the next batch.
