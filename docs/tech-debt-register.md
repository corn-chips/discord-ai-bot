## Technical Debt Register

Last updated: 2026-07-12

Open items: 0 | Resolved items: 8 | Original estimate: 1 XL + 2 L + 5 M

| ID | Category | Description | Files | Accepted because | Effort | Impact | Priority | Added | Sprint |
|----|----------|-------------|-------|------------------|--------|--------|----------|-------|--------|
| TD-004 | Documentation Debt | Reconcile setup documentation with the repository. The README references 11 absent configuration, Docker, and feature-guide assets, and advertises Python 3.8 despite runtime annotations that require a newer interpreter. | `README.md`, `src/config.py` | Deployment and configuration assets changed without a corresponding documentation and compatibility-baseline cleanup. | M | High | 4.50 | 2026-07-12 | Resolved - Sprint 1 |
| TD-002 | Architecture Debt | Split the Gemini response pipeline into focused routing, retry, search, usage, and formatting components. `generate_response` is about 434 lines inside a 1,691-line client. | `src/services/gemini_client.py` | Provider capabilities were added incrementally to one API adapter. | L | High | 4.00 | 2026-07-12 | Resolved - Sprint 3 |
| TD-003 | Test Debt | Expand dedicated coverage beyond the current 21 tests in three files, which directly target 8 of 30 production modules. Prioritize command handling, configuration validation, rendering, and persistence behavior. | `tests/`, `src/bot/commands.py`, `src/config.py`, `src/services/content_renderer.py` | Discord and Gemini integration paths require substantial mocking, so tests concentrated on isolated services and known regressions. | L | High | 4.00 | 2026-07-12 | Resolved - Sprints 1-4 |
| TD-005 | Performance Debt | Move PDF rasterization and Pillow conversion off the async Discord event loop. `_convert_pdf_to_images` performs synchronous per-page rendering inside an async method. | `src/bot/discord_bot.py` | Initial PDF support used the libraries' straightforward synchronous APIs. | M | High | 3.00 | 2026-07-12 | Resolved - Sprint 1 |
| TD-006 | Code Quality Debt | Break configuration parsing and validation into smaller, testable units. `BotConfig.from_yaml` is about 252 lines and `validate` is about 180 lines. | `src/config.py` | Configuration was centralized during the migration from environment variables to YAML. | M | Medium | 3.00 | 2026-07-12 | Resolved - Sprint 2 |
| TD-007 | Code Quality Debt | Consolidate SQLite connection, transaction, cleanup, and error-handling behavior shared by seven persistence services and 22 direct `sqlite3.connect` call sites. | `src/services/channel_settings_service.py`, `src/services/message_index_service.py`, `src/services/message_visibility_service.py`, `src/services/pin_service.py`, `src/services/report_service.py`, `src/services/token_tracker.py`, `src/services/user_preferences_service.py` | Each feature introduced a small, independent SQLite repository to minimize its initial implementation scope. | M | Medium | 3.00 | 2026-07-12 | Resolved - Sprint 2 |
| TD-008 | Dependency Debt | Add a reproducible dependency strategy. All 16 runtime requirements are open-ended lower bounds, and the repository has no lock or constraints file. | `requirements.txt` | Loose lower bounds made dependency upgrades easy while compatibility control remained manual. | M | Medium | 3.00 | 2026-07-12 | Resolved - Sprint 1 |
| TD-001 | Architecture Debt | Decompose the monolithic Discord command registration and event orchestration. `setup_commands` spans about 1,873 lines, and `DiscordBot` occupies most of a 2,491-line module. | `src/bot/commands.py`, `src/bot/discord_bot.py` | Discord features accumulated around the original command-registration and event-handling entry points. | XL | High | 2.25 | 2026-07-12 | Resolved - Sprint 4 |

### Resolution Evidence

| ID | Resolution evidence |
|----|---------------------|
| TD-001 | `setup_commands` is a 35-line compositor over five command registrars; `DiscordBot` is 1,131 lines and delegates live batching, RAG events, response delivery, media extraction, and response generation to focused collaborators. |
| TD-002 | `GeminiClient.generate_response` is 70 lines and delegates request planning, content assembly, retry/timeout execution, response interpretation, grounding, and usage construction to focused helpers. |
| TD-003 | The suite expanded from 21 tests in 3 files to 98 tests in 12 files, including commands, configuration, rendering, persistence, Gemini, PDF, media, response delivery, and orchestration success/error/cancellation paths. |
| TD-004 | README setup/configuration/command claims now match tracked files; unsupported Docker/example-file instructions were removed and the supported Python 3.12 workflow is explicit. |
| TD-005 | PDF rasterization uses a dedicated single-worker executor, preserving event-loop responsiveness without unsafe concurrent PyMuPDF calls; cancellation and shutdown are covered. |
| TD-006 | `BotConfig.from_yaml` and `validate` are 7 and 3 lines and delegate to domain-focused parsing/validation helpers with differential compatibility tests. |
| TD-007 | All 22 direct connection sites in the seven services now use shared connection/transaction helpers; the helper is the only `sqlite3.connect` site under `src/`. |
| TD-008 | `requirements.in` holds direct bounds, `constraints.txt` pins the complete validated Python 3.12 environment, and `requirements.txt` composes both with a documented refresh workflow. |

### Prioritization Method

Priority score = `(impact if unfixed x frequency of encounter) / fix effort`.

- Impact: Low = 1, Medium = 2, High = 3, Critical = 4.
- Frequency: Rare = 1, Occasional = 2, Common = 3, Continuous = 4.
- Effort: S = 1, M = 2, L = 3, XL = 4.
- Equal scores are ordered by impact, then by debt ID for stability.

### Execution Outcome

All eight registered items were completed through the four phased plans in
`docs/tech-debt-sprints/`. The register trend is shrinking: 8 open items to 0.
Any newly discovered debt should receive a new ID rather than reopening these
entries without evidence that their acceptance criteria regressed.

### Scan Notes

- No code-level `TODO`, `FIXME`, `HACK`, or deprecation markers were found. The unchecked items in `README.md` are treated as product backlog rather than technical debt.
- Sixteen of 51 production Python modules exceed 500 lines, and 94 production
  functions exceed 50 lines. These are size heuristics, not automatically open
  debt; the registered monoliths were closed against their behavior and
  delegation acceptance criteria above.
- An exact eight-line duplicate-block scan found no cross-file matches. TD-007 records repeated persistence structure rather than literal duplication.
- The final implementation gate executed 98 offline unit tests under the repository's Python 3.12 virtual environment.
