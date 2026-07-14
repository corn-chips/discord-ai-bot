# Sprint 3 - Decompose the Gemini Response Pipeline

## Sprint Goal

Turn the central Gemini response path into independently testable routing,
request, retry, response-extraction, and usage components while preserving API
behavior.

## Capacity

- Window: 2026-08-10 to 2026-08-23
- Available engineering capacity: 8 days
- Reserved verification and unplanned-work buffer: 2 days

## Tasks

| Priority | ID | Task | Estimate | Dependencies | Acceptance criteria |
|----------|----|------|----------|--------------|---------------------|
| Must | TD-002A | Extract immutable request planning and model/search/thinking routing. | 2 days | Sprint 2 config boundaries | Precedence, search, thinking, safety, and token-limit decisions match regression fixtures. |
| Must | TD-002B | Extract attempt execution and retry/backoff policy. | 2 days | TD-002A | Attempt count, timeout ownership, retryable failures, and cancellation behavior are explicit and tested. |
| Must | TD-002C | Extract response parsing, grounding, token usage, and APIResponse construction. | 2 days | TD-002B | Success, empty response, safety stop, and provider-error outputs remain compatible. |
| Should | TD-003C | Add Gemini pipeline unit tests with a fake provider client. | 2 days | TD-002A-C | Tests cover routing, retries, cancellation, usage, and error paths without network access. |

## Risks

| Risk | Probability | Impact | Mitigation |
|------|-------------|--------|------------|
| Provider SDK objects are difficult to fake faithfully | Medium | High | Define narrow internal inputs/outputs and keep SDK access at one boundary. |
| Refactor changes prompt ordering or optional-tool behavior | Medium | High | Compare request plans and formatted prompts before and after extraction. |
| Retry extraction swallows cancellation | Low | Critical | Test `CancelledError` propagation explicitly. |

## Definition of Done

- `GeminiClient.generate_response` is orchestration rather than a monolithic implementation.
- All request and response contracts remain backward compatible.
- Focused and full tests, compilation, and diff hygiene pass.
- The final pipeline diff has no unresolved blocking bug-review findings.

## Completion Record - 2026-07-12

- TD-002 and its TD-003 coverage are complete.
- `generate_response` fell from approximately 433 to 70 lines.
- Routing, ordered media, retries, timeout ownership, finish reasons, usage,
  grounding, provider errors, streaming, and cancellation are covered offline.
