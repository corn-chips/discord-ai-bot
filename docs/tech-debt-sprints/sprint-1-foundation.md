# Sprint 1 - Stabilize the Foundation

## Sprint Goal

Remove low-coupling onboarding, dependency, and event-loop risks while building
the first layer of regression coverage.

## Capacity

- Window: 2026-07-13 to 2026-07-26
- Available engineering capacity: 8 days
- Reserved verification and unplanned-work buffer: 2 days

## Tasks

| Priority | ID | Task | Estimate | Dependencies | Acceptance criteria |
|----------|----|------|----------|--------------|---------------------|
| Must | TD-004 | Reconcile README setup, configuration, feature, Docker, and Python-version claims with files that actually exist. | 2 days | None | Every local link/path and setup command resolves in this checkout; documented Python baseline matches runtime syntax. |
| Must | TD-005 | Move PDF rasterization and Pillow conversion off the Discord event loop. | 2 days | None | Synchronous rendering runs through a worker thread; success and failure behavior have async regression tests. |
| Must | TD-008 | Add a reproducible dependency workflow and document how to refresh it. | 2 days | Baseline tests | A clean environment has an exact, reviewable install input; normal contributor install remains documented. |
| Should | TD-003A | Add focused tests for PDF processing, dependency/docs validation, and existing high-risk response regressions. | 1 day | TD-004, TD-005, TD-008 | Tests do not require network access or real tokens and fail against the old behavior. |

## Risks

| Risk | Probability | Impact | Mitigation |
|------|-------------|--------|------------|
| Thread offload changes exception propagation or image lifetime | Medium | High | Preserve the existing return/error contract and test corrupt PDFs plus multi-page success. |
| Dependency pins are generated from an unrepresentative environment | Medium | High | Record the generation command and validate a fresh install/test run when network access is available. |
| README cleanup accidentally removes a supported workflow | Low | Medium | Verify claims against tracked files and startup scripts before editing. |

## Definition of Done

- All focused and full tests pass.
- All Python files compile.
- Changed code receives a fresh bug review after tests pass.
- No blocking findings remain after the repair/re-review loop.
- The debt register records the resolved or remaining scope accurately.

## Completion Record - 2026-07-12

- TD-004, TD-005, TD-008, and the Sprint 1 TD-003 coverage are complete.
- PDF work is serialized through a dedicated worker and covered for concurrency,
  cancellation, resource lifetime, and shutdown.
- Dependency, startup-script, README-path, and full-suite checks passed.
