## Technical Debt Register

Last updated: 2026-07-12

Total items: 8 | Estimated total effort: 1 XL + 2 L + 5 M

| ID | Category | Description | Files | Accepted because | Effort | Impact | Priority | Added | Sprint |
|----|----------|-------------|-------|------------------|--------|--------|----------|-------|--------|
| TD-004 | Documentation Debt | Reconcile setup documentation with the repository. The README references 11 absent configuration, Docker, and feature-guide assets, and advertises Python 3.8 despite runtime annotations that require a newer interpreter. | `README.md`, `src/config.py` | Deployment and configuration assets changed without a corresponding documentation and compatibility-baseline cleanup. | M | High | 4.50 | 2026-07-12 | Next sprint |
| TD-002 | Architecture Debt | Split the Gemini response pipeline into focused routing, retry, search, usage, and formatting components. `generate_response` is about 434 lines inside a 1,691-line client. | `src/services/gemini_client.py` | Provider capabilities were added incrementally to one API adapter. | L | High | 4.00 | 2026-07-12 | Next sprint |
| TD-003 | Test Debt | Expand dedicated coverage beyond the current 21 tests in three files, which directly target 8 of 30 production modules. Prioritize command handling, configuration validation, rendering, and persistence behavior. | `tests/`, `src/bot/commands.py`, `src/config.py`, `src/services/content_renderer.py` | Discord and Gemini integration paths require substantial mocking, so tests concentrated on isolated services and known regressions. | L | High | 4.00 | 2026-07-12 | Next sprint |
| TD-005 | Performance Debt | Move PDF rasterization and Pillow conversion off the async Discord event loop. `_convert_pdf_to_images` performs synchronous per-page rendering inside an async method. | `src/bot/discord_bot.py` | Initial PDF support used the libraries' straightforward synchronous APIs. | M | High | 3.00 | 2026-07-12 | Backlog |
| TD-006 | Code Quality Debt | Break configuration parsing and validation into smaller, testable units. `BotConfig.from_yaml` is about 252 lines and `validate` is about 180 lines. | `src/config.py` | Configuration was centralized during the migration from environment variables to YAML. | M | Medium | 3.00 | 2026-07-12 | Backlog |
| TD-007 | Code Quality Debt | Consolidate SQLite connection, transaction, cleanup, and error-handling behavior shared by seven persistence services and 22 direct `sqlite3.connect` call sites. | `src/services/channel_settings_service.py`, `src/services/message_index_service.py`, `src/services/message_visibility_service.py`, `src/services/pin_service.py`, `src/services/report_service.py`, `src/services/token_tracker.py`, `src/services/user_preferences_service.py` | Each feature introduced a small, independent SQLite repository to minimize its initial implementation scope. | M | Medium | 3.00 | 2026-07-12 | Backlog |
| TD-008 | Dependency Debt | Add a reproducible dependency strategy. All 16 runtime requirements are open-ended lower bounds, and the repository has no lock or constraints file. | `requirements.txt` | Loose lower bounds made dependency upgrades easy while compatibility control remained manual. | M | Medium | 3.00 | 2026-07-12 | Backlog |
| TD-001 | Architecture Debt | Decompose the monolithic Discord command registration and event orchestration. `setup_commands` spans about 1,873 lines, and `DiscordBot` occupies most of a 2,491-line module. | `src/bot/commands.py`, `src/bot/discord_bot.py` | Discord features accumulated around the original command-registration and event-handling entry points. | XL | High | 2.25 | 2026-07-12 | Backlog |

### Prioritization Method

Priority score = `(impact if unfixed x frequency of encounter) / fix effort`.

- Impact: Low = 1, Medium = 2, High = 3, Critical = 4.
- Frequency: Rare = 1, Occasional = 2, Common = 3, Continuous = 4.
- Effort: S = 1, M = 2, L = 3, XL = 4.
- Equal scores are ordered by impact, then by debt ID for stability.

### Next Sprint Recommendation

- TD-004: repair onboarding, deployment, and compatibility documentation.
- TD-002: separate the central Gemini response pipeline into focused components.
- TD-003: add coverage around the TD-002 refactor and other high-risk untested paths.

Together these items represent 2 L + 1 M of estimated effort. For a one-developer sprint, complete TD-004 and TD-002 first and limit TD-003 to regression coverage required by the Gemini refactor.

### Scan Notes

- No code-level `TODO`, `FIXME`, `HACK`, or deprecation markers were found. The unchecked items in `README.md` are treated as product backlog rather than technical debt.
- Fourteen production Python modules exceed 500 lines, and 80 production functions exceed 50 lines.
- An exact eight-line duplicate-block scan found no cross-file matches. TD-007 records repeated persistence structure rather than literal duplication.
- The test suite was not executed during this scan because no installed Python runtime was available in the inspection environment.
