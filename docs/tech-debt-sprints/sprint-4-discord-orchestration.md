# Sprint 4 - Decompose Discord Orchestration

## Sprint Goal

Split command registration and Discord event orchestration into cohesive modules
without changing command names, permissions, response semantics, or live/RAG
lifecycle behavior.

## Capacity

- Window: 2026-08-24 to 2026-09-13
- Available engineering capacity: 12 days
- Reserved verification and unplanned-work buffer: 3 days

## Tasks

| Priority | ID | Task | Estimate | Dependencies | Acceptance criteria |
|----------|----|------|----------|--------------|---------------------|
| Must | TD-001A | Split command groups into registration modules with shared command dependencies. | 4 days | Sprint 3 stable client | The command tree exposes the same names, choices, checks, and callbacks; no duplicate registration occurs. |
| Must | TD-001B | Extract live batching, attachment extraction, response delivery, and RAG event collaborators from `DiscordBot`. | 5 days | TD-001A | `DiscordBot` delegates focused behavior; queue ownership, cancellation, edit/delete events, and response ordering remain unchanged. |
| Must | TD-003D | Complete command, rendering, and Discord orchestration regression coverage. | 3 days | TD-001A-B | Fake Discord objects cover registration and critical callbacks; rendering and persistence integration paths have success/error tests. |

## Risks

| Risk | Probability | Impact | Mitigation |
|------|-------------|--------|------------|
| Decorator-based command registration changes tree identity or callback binding | High | High | Extract one command group at a time and assert registered command metadata. |
| Event collaborators lose client state or async lifecycle ownership | Medium | Critical | Make ownership explicit and test close, cancellation, live batching, and paginator edit flows. |
| Broad file movement obscures review | High | Medium | Use small moves with behavior-preserving tests and review each move independently. |

## Definition of Done

- `setup_commands` and `DiscordBot` are reduced to clear composition/orchestration roles.
- Public Discord behavior and command metadata remain compatible.
- All focused and full tests, compilation, and diff hygiene pass.
- Every extraction has completed the test, bug-review, repair, and re-review loop.
- All eight register items are either resolved or explicitly retained with evidence and a new owner/sprint.

## Completion Record - 2026-07-12

- TD-001 and the remaining TD-003 coverage are complete.
- `setup_commands` fell from 1,871 to 35 lines and preserves the exact command
  tree; `DiscordBot` fell from 2,367 to 1,131 lines through five collaborators.
- Callback-level import, live queue, RAG mutation, response delivery, media,
  generation, error, cancellation, and renderer regressions passed.
