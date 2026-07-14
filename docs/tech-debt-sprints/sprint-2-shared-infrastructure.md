# Sprint 2 - Make Shared Infrastructure Testable

## Sprint Goal

Reduce configuration and persistence duplication without changing their public
interfaces, stored schemas, or error behavior.

## Capacity

- Window: 2026-07-27 to 2026-08-09
- Available engineering capacity: 8 days
- Reserved verification and unplanned-work buffer: 2 days

## Tasks

| Priority | ID | Task | Estimate | Dependencies | Acceptance criteria |
|----------|----|------|----------|--------------|---------------------|
| Must | TD-006 | Extract configuration parsing and validation helpers from `BotConfig`. | 3 days | Sprint 1 baseline | `BotConfig.from_yaml` and `validate` delegate to focused units; existing config keys, defaults, and validation messages stay compatible. |
| Must | TD-007 | Consolidate SQLite connection setup, transaction handling, cleanup, and common error behavior. | 3 days | Persistence regression tests | Seven services use one connection helper; schemas and async boundaries are unchanged; rollback and cleanup paths are tested. |
| Should | TD-003B | Expand tests for configuration validation and persistence services. | 2 days | TD-006, TD-007 | Representative valid, defaulted, invalid, commit, rollback, and isolation cases pass without external services. |

## Risks

| Risk | Probability | Impact | Mitigation |
|------|-------------|--------|------------|
| Shared SQLite policy changes row factories, timeouts, or commit semantics | Medium | High | Inventory every call site first and preserve service-specific behavior explicitly. |
| Config extraction changes defaults or validation ordering | Medium | High | Snapshot public defaults/messages with tests before extraction. |
| Large mechanical edits hide behavioral changes | Medium | Medium | Review per service, not only as one aggregate diff. |

## Definition of Done

- Public constructors and service methods remain compatible.
- Existing SQLite data remains readable without migration.
- Focused and full tests, compilation, and diff hygiene pass.
- Each bounded extraction is re-read and bug-reviewed, with any finding fixed and re-reviewed.

## Completion Record - 2026-07-12

- TD-006 and TD-007 are complete.
- Configuration parsing/validation behavior was differentially checked across
  all 93 fields; SQLite connection sites fell from 22 to one shared helper.
- Focused configuration and cross-service persistence tests passed.
