## Technical Debt Register

Last updated: 2026-07-29 (re-measured at branch `dev`, HEAD `0c9eb91`)

Open items: 10 | Resolved items: 9 | Total: 19
Original Sprint 1-4 estimate: 1 XL + 2 L + 5 M | Current open estimate: 2 S + 4 M + 4 L

Every figure in this document was re-measured on 2026-07-29 against `0c9eb91` using
`.venv/bin/python` (CPython 3.12.13). The figures were first taken at `c83f740` and re-taken
after `0bf532c` (dead-code removal) and `0c9eb91` (five dependencies dropped) landed. Prior
revisions carried the date `2026-07-12`, but the Resolution Evidence below was written at commit
`5e45599` (2026-07-13) and had never been re-measured since; nine commits landed in between. The
corrections in this revision are marked inline.

For the full 2026-07-29 finding register that produced the new items, see
[`docs/ANALYSIS_BACKLOG.md`](ANALYSIS_BACKLOG.md).

### Open Items

| ID | Category | Description | Files | Accepted because | Effort | Impact | Priority | Added | Sprint |
|----|----------|-------------|-------|------------------|--------|--------|----------|-------|--------|
| TD-012 | Security Debt | Introduce an authorization model for the command layer. Exactly one of the 35 registered commands carries an `@app_commands.default_permissions` gate, and `/dev`'s gate is commented out while its docstring still claims administrator-only access. Global state mutators such as `/config model`, `/config deepsearch`, and `/config debug` are reachable by any guild member. | `src/bot/command_modules/configuration.py`, `src/bot/command_modules/general.py`, `src/bot/command_modules/personalization.py`, `src/bot/command_modules/reports_usage.py`, `src/bot/command_modules/research.py` | Commands were added feature-first for a small trusted server, and Discord's own permission UI was assumed to be sufficient. | M | Critical | 6.00 | 2026-07-29 | Open - unscheduled |
| TD-014 | Process Debt | Add continuous integration. The repository has no `.github/` directory and no CI configuration of any kind, so the unittest suite, `compileall`, and every documented gate run only when a human remembers to run them locally. | repository root | The project has been developed by one operator on one machine, where running `./start.sh` and the test command by hand was adequate. | M | High | 6.00 | 2026-07-29 | Open - unscheduled |
| TD-017 | Performance Debt | Replace the quadratic message splitter. `MessageSplitter.split_message` re-scans for code-block boundaries on every candidate split point, and it runs inline on the Discord event loop from the delivery path. A single-parse rewrite is measured at 1088x faster with byte-identical output on every probe input. | `src/services/message_splitter.py`, `src/bot/response_delivery.py` | The splitter was written for typical two-to-three-page responses, where the quadratic term is invisible. | S | High | 6.00 | 2026-07-29 | Open - unscheduled |
| TD-009 | Data Debt | Adopt a schema-migration framework. There is no `PRAGMA user_version` anywhere under `src/` and no migration runner; each service creates its own schema at construction and new columns depend on remembering to call the idempotent `_ensure_column` helper as well as editing the `CREATE TABLE` body. One-shot data migrations are tracked ad hoc by named rows in the `rag_migrations` ledger. | `src/services/sqlite_utils.py`, `src/services/message_index_service.py`, `src/services/channel_settings_service.py`, `src/services/token_tracker.py`, `src/services/report_service.py`, `src/services/pin_service.py`, `src/services/message_visibility_service.py`, `src/services/user_preferences_service.py` | Each persistence service was introduced independently with a self-creating schema, which needed no coordination while the schemas were young. | M | High | 4.50 | 2026-07-29 | Open - unscheduled |
| TD-010 | Observability Debt | Build a cost model for AI usage. There is no price table and no cost arithmetic anywhere in the repository; `get_api_usage_info` returns two English strings. The `token_usage` table has nine columns and none records the model, `thoughts_token_count` is never read, usage is recorded only on success, the router/selector/embedding call paths are unmetered, and `/usage-report` reads an in-memory dictionary that estimates tokens arithmetically and resets on restart. | `src/services/gemini_client.py`, `src/services/token_tracker.py`, `src/utils/token_extraction.py`, `src/bot/command_modules/reports_usage.py`, `src/utils/logging_config.py`, `src/models/data_models.py` | Token counting was added first as a curiosity metric, and the free tier removed any immediate pressure to convert tokens into money. | L | High | 4.00 | 2026-07-29 | Open - unscheduled |
| TD-013 | Test Debt | Close the zero-coverage gap. Twelve modules totalling 2,862 lines are never imported, patched, or executed by any test, concentrated in cross-cutting infrastructure: the enhanced router, user-experience service, context collector, report web server, rate limiter, logging config, error manager, image utils, token extraction, `main.py`, and both scripts. A thirteenth, the help system, left the list when `0bf532c` deleted `src/services/help_system.py` outright. The 37.9 percent statement coverage of `src/` previously quoted here was measured at `c83f740` and is NOT re-measured: `coverage` is not installed and cannot be installed from this machine's pip index. Every one of the 2,111 lines `0bf532c` removed was unexecuted, so the figure at HEAD is strictly higher. | `src/bot/enhanced_command_handler.py`, `src/services/user_experience_service.py`, `src/services/context_collector.py`, `src/services/report_web_server.py`, `src/services/rate_limiter.py`, `src/utils/logging_config.py`, `src/utils/error_manager.py`, `src/utils/image_utils.py`, `src/utils/token_extraction.py`, `main.py`, `scripts/` | Sprints 1-4 targeted the modules named in TD-003; cross-cutting infrastructure was left for later because it is awkward to exercise without network or a live Discord connection. | L | High | 4.00 | 2026-07-29 | Open - unscheduled |
| TD-016 | Performance Debt | Move synchronous work off the async event loop. Thirty-four classes of blocking work are reachable from `async def` without `to_thread` or `run_in_executor`, including every SQLite read and write outside the PDF path. An inline five-second database call was measured to stall the loop for 4,997.3 ms against 3.1 ms when offloaded. TD-005 fixed only PDF rasterization, and to a single-worker executor. | `src/bot/discord_bot.py`, `src/services/sqlite_utils.py`, `src/services/message_index_service.py`, `src/services/content_renderer.py`, `src/services/rate_limiter.py`, `src/services/report_web_server.py`, `src/bot/command_modules/personalization.py` | SQLite calls are individually sub-millisecond on a small database, so the synchronous API was the obvious choice and stayed invisible until the index grew. | L | High | 4.00 | 2026-07-29 | Open - unscheduled |
| TD-011 | Configuration Debt | Validate the remaining configuration surface. Of the 96 annotated `BotConfig` fields, 43 are never mentioned by any `_validate_*` helper, so out-of-range, wrong-typed, and nonsensical values are accepted at startup and fail later in unrelated code. Four safety enums are unchecked, and values such as `pdf_render_scale: 0` load without complaint. | `src/config_helpers.py`, `src/config.py`, `config.yaml` | Validation was written as a separate pass from parsing, so every setting added since has defaulted to unvalidated unless someone remembered the fourth of AGENTS.md's four places. | M | High | 3.00 | 2026-07-29 | Open - unscheduled |
| TD-019 | Product/Code Debt | Decide the fate of the typing-indicator feature, then make the code and the config agree. `show_typing_indicator` was unreachable long before `0bf532c` removed it, so `UserExperienceService.active_typing_tasks` now has no writer: `cleanup_typing_indicators` (`user_experience_service.py:122`) iterates and clears a dict that is always empty, and `_close_typing_entry` (`:88`) and `_sweep_stale_typing_entries` (`:104`) are unreachable through it. The service is an empty shell that `discord_bot.py:392` still constructs and `:654` still awaits on shutdown, while `config.yaml:73` `show_typing_indicators: true` and `/config info` (`configuration.py:217`) continue to advertise a feature the bot has not performed for some time; the flag also still feeds the `enhanced_ux` rollup at `config.py:226`. Either restore the producer or remove the remnant and the setting -- but the setting must stop claiming something untrue. **This is a product decision, which is why it is registered rather than swept into the dead-code commit.** | `src/services/user_experience_service.py`, `src/bot/discord_bot.py`, `src/config.py`, `src/config_helpers.py`, `config.yaml`, `src/bot/command_modules/configuration.py` | The dead-code pass correctly removed an unreachable producer, which exposed rather than caused the incoherence; finishing the job means answering whether the feature is wanted. | S | Low | 2.00 | 2026-07-30 | Open - unscheduled |
| TD-018 | Performance Debt | Give the RAG index a retention and eviction policy. `MessageIndexService._vector_matrix` retains every embedded message in RAM forever at roughly 2.93 KB per message resident with no eviction and no cap; `message_retrieval_events` is a write-only unpruned table; `message_embeddings` rows survive soft deletion; `bot_reports` is append-only; and per-channel structures in `LiveMessageCoordinator` and `HybridContextRetriever` are never removed once created. A 200-channel server at 50,000 messages a day is extrapolated to about 2.23 GB RSS within a week. | `src/services/message_index_service.py`, `src/services/hybrid_context_retriever.py`, `src/bot/live_message_coordinator.py`, `src/services/report_service.py` | The hybrid RAG index was built and validated against development-scale corpora where full retention is cheap and simple. | L | High | 3.00 | 2026-07-29 | Open - unscheduled |

### Resolved Items

| ID | Category | Description | Files | Accepted because | Effort | Impact | Priority | Added | Sprint |
|----|----------|-------------|-------|------------------|--------|--------|----------|-------|--------|
| TD-004 | Documentation Debt | Reconcile setup documentation with the repository. The README references 11 absent configuration, Docker, and feature-guide assets, and advertises Python 3.8 despite runtime annotations that require a newer interpreter. | `README.md`, `src/config.py` | Deployment and configuration assets changed without a corresponding documentation and compatibility-baseline cleanup. | M | High | 4.50 | 2026-07-12 | Resolved (partially) - Sprint 1 |
| TD-002 | Architecture Debt | Split the Gemini response pipeline into focused routing, retry, search, usage, and formatting components. `generate_response` is about 434 lines inside a 1,691-line client. | `src/services/gemini_client.py` | Provider capabilities were added incrementally to one API adapter. | L | High | 4.00 | 2026-07-12 | Resolved - Sprint 3 |
| TD-003 | Test Debt | Expand dedicated coverage beyond the current 21 tests in three files, which directly target 8 of 30 production modules. Prioritize command handling, configuration validation, rendering, and persistence behavior. | `tests/`, `src/bot/commands.py`, `src/config.py`, `src/services/content_renderer.py` | Discord and Gemini integration paths require substantial mocking, so tests concentrated on isolated services and known regressions. | L | High | 4.00 | 2026-07-12 | Resolved - Sprints 1-4 |
| TD-005 | Performance Debt | Move PDF rasterization and Pillow conversion off the async Discord event loop. `_convert_pdf_to_images` performs synchronous per-page rendering inside an async method. | `src/bot/discord_bot.py` | Initial PDF support used the libraries' straightforward synchronous APIs. | M | High | 3.00 | 2026-07-12 | Resolved - Sprint 1 |
| TD-006 | Code Quality Debt | Break configuration parsing and validation into smaller, testable units. `BotConfig.from_yaml` is about 252 lines and `validate` is about 180 lines. | `src/config.py` | Configuration was centralized during the migration from environment variables to YAML. | M | Medium | 3.00 | 2026-07-12 | Resolved - Sprint 2 |
| TD-007 | Code Quality Debt | Consolidate SQLite connection, transaction, cleanup, and error-handling behavior shared by seven persistence services and 22 direct `sqlite3.connect` call sites. | `src/services/channel_settings_service.py`, `src/services/message_index_service.py`, `src/services/message_visibility_service.py`, `src/services/pin_service.py`, `src/services/report_service.py`, `src/services/token_tracker.py`, `src/services/user_preferences_service.py` | Each feature introduced a small, independent SQLite repository to minimize its initial implementation scope. | M | Medium | 3.00 | 2026-07-12 | Resolved - Sprint 2 |
| TD-008 | Dependency Debt | Add a reproducible dependency strategy. All 16 runtime requirements are open-ended lower bounds, and the repository has no lock or constraints file. | `requirements.txt` | Loose lower bounds made dependency upgrades easy while compatibility control remained manual. | M | Medium | 3.00 | 2026-07-12 | Resolved - Sprint 1 |
| TD-015 | Code Quality Debt | Remove dead code. A verified-safe removal list covers roughly 2,027 lines of Python, 8.5 percent of the 23,973-line tree: one orphan module with zero importers, 11 unreferenced `DiscordBot` compatibility wrappers, eight of nine public `UserExperienceService` methods, two dead classes, four dead `BotConfig` fields, three dead `ErrorType` members, one dead constant, and 87 unused import bindings. Applying the whole list to a scratch copy left `compileall` clean and 125/125 tests passing. | `src/services/help_system.py`, `src/services/user_experience_service.py`, `src/bot/discord_bot.py`, `src/utils/logging_config.py`, `src/utils/error_manager.py`, `src/config.py`, `src/config_helpers.py`, `config.yaml` | The Sprint 2-4 extraction into coordinators and command modules deliberately left back-compat shims in place, and nothing has since audited whether they acquired callers. | M | Medium | 3.00 | 2026-07-29 | Resolved - `0bf532c` |
| TD-001 | Architecture Debt | Decompose the monolithic Discord command registration and event orchestration. `setup_commands` spans about 1,873 lines, and `DiscordBot` occupies most of a 2,491-line module. | `src/bot/commands.py`, `src/bot/discord_bot.py` | Discord features accumulated around the original command-registration and event-handling entry points. | XL | High | 2.25 | 2026-07-12 | Resolved - Sprint 4 |

### Resolution Evidence

Re-measured 2026-07-29 at `0c9eb91`. Where the original wording has gone out of date, the
current figure follows in a bracketed correction; where the original claim was never true, the
row is marked RETRACTED.

| ID | Resolution evidence | 2026-07-29 verification |
|----|---------------------|-------------------------|
| TD-001 | `setup_commands` is a 35-line compositor over five command registrars; `DiscordBot` is 1,131 lines and delegates live batching, RAG events, response delivery, media extraction, and response generation to focused collaborators. | HOLDS, evidence DRIFTED. `setup_commands` is still exactly 35 lines (`commands.py:46-80`) over the five registrars, and now occupies almost all of an **80-line** file. The `DiscordBot` **class** now spans **1,067 lines** (`discord_bot.py:296-1362`), not 1,131, inside a **1,362-line** module. The original wording did not say the figure was the class rather than the file; it is the class. The delegation to coordinators is intact. |
| TD-002 | `GeminiClient.generate_response` is 70 lines and delegates request planning, content assembly, retry/timeout execution, response interpretation, grounding, and usage construction to focused helpers. | HOLDS. Exactly **70 lines** (`gemini_client.py:1255-1324`), unchanged since `5e45599`. Delegation to `_resolve_response_request_routing`, `_build_response_request_content`, `_log_response_request_context`, `_complete_response_request_plan`, and `_run_response_attempts` confirmed. The enclosing client has grown to 1,773 lines. |
| TD-003 | The suite expanded from 21 tests in 3 files to 98 tests in 12 files, including commands, configuration, rendering, persistence, Gemini, PDF, media, response delivery, and orchestration success/error/cancellation paths. | HOLDS, evidence DRIFTED favourably. 98 in 12 was exact when written; the suite is now **140 tests in 15 files** (`Ran 140 tests ... OK`, 1.25 s), having passed through 125 in 13 at `c83f740`. The added files are `tests/test_rag_optimization.py` (9 tests, pre-`c83f740`), then `tests/test_repo_hygiene.py` (2, added by `02b91a7`) and `tests/test_on_message_flow.py` (13, added by `28409e4`). Note the separate coverage gap now registered as TD-013: breadth of files is not breadth of modules. |
| TD-004 | README setup/configuration/command claims now match tracked files; unsupported Docker/example-file instructions were removed and the supported Python 3.12 workflow is explicit. | **RETRACTED IN PART, SINCE REPAIRED.** The Python 3.12 and Docker halves always held (`README.md:19,23,25,60,116`). The project-layout half was FALSE on the day it was written: the README listed and linked `BOT_SYSTEM_REPORT.md` at the repository root alongside `pipeline.html` and `message-sequence-flowchart.html`, even though `5e45599` had itself moved the report to `docs/` and deleted both HTML files. That has since been corrected — `README.md:234` now states the report lives in `docs/` and that neither diagram exists, and `find . -name '*.html'` outside `.venv` returns zero results. Sprint 1's acceptance criterion, that every local link and path resolves in this checkout, is now met. |
| TD-005 | PDF rasterization uses a dedicated single-worker executor, preserving event-loop responsiveness without unsafe concurrent PyMuPDF calls; cancellation and shutdown are covered. | HOLDS. `ThreadPoolExecutor` imported at `discord_bot.py:11`, created at `:320`, handed over on shutdown at `:682-683`, and used via `loop.run_in_executor` at `:1117`. Covered by `tests/test_pdf_processing.py` (6 tests). Scope caveat: this closed the PDF path only; the general case is now TD-016. |
| TD-006 | `BotConfig.from_yaml` and `validate` are 7 and 3 lines and delegate to domain-focused parsing/validation helpers with differential compatibility tests. | HOLDS. Exactly **7 and 3 lines** (`config.py:179-185` and `:187-189`), delegating to the 732-line `src/config_helpers.py`. Caveat: decomposition succeeded, coverage did not follow; 43 of the 96 `BotConfig` fields have no validation rule at all, now TD-011. |
| TD-007 | All 22 direct connection sites in the seven services now use shared connection/transaction helpers; the helper is the only `sqlite3.connect` site under `src/`. | HOLDS. `grep -rn "sqlite3.connect" --include=*.py src/` returns exactly one hit, `src/services/sqlite_utils.py:20`. All seven services import `sqlite_connection` or `sqlite_transaction`. |
| TD-008 | `requirements.in` holds direct bounds, `constraints.txt` pins the complete validated Python 3.12 environment, and `requirements.txt` composes both with a documented refresh workflow. | HOLDS. `requirements.txt` is three content lines, `-c constraints.txt` plus `-r requirements.in`. `requirements.in` declares **11** direct dependencies, all `>=`, zero `==`. `constraints.txt` carries **51** exact `==` pins. Refresh workflow at `README.md:116`. Caveat RESOLVED by `0c9eb91`: the five declared-but-never-imported dependencies (`requests`, `markdown`, `beautifulsoup4`, `pdf2image`, `colorlog`) were dropped, taking the direct list from 16 to 11 and the pin set from 57 to 51 as `colorama` and `soupsieve` were orphaned. All 11 surviving declarations are imported under `src/`, `scripts/`, or `main.py`; `requests` stays pinned as a transitive of `google-genai`. |
| TD-015 | `0bf532c` removed **2,111 lines** across 25 files: `src/services/help_system.py` in full, 47 unreferenced symbols across 24 modules (including `ImageProcessingService.cancel_job`), three unreachable `ErrorType` members, four `BotConfig` fields with their parsers and their `config.yaml` keys, and 96 unused import bindings. No test file was touched. | HOLDS. `git show --numstat 0bf532c` totals 18 insertions against **2,111** deletions, above the 2,027-line estimate the item was registered with. `src/services/help_system.py` is gone, `grep -rn cancel_job` over `src/`, `tests/`, and `main.py` returns 0, `system_prompts.thinking_mode_addon` is absent from `config.yaml`, and none of `api_timeout_buffer`, `edit_detection_max_output_tokens`, `system_prompt_thinking_addon`, or `progress_update_threshold` survives anywhere. `python -m compileall -q main.py src scripts tests` is clean and the suite is **125 tests, OK**. |

### Prioritization Method

Priority score = `(impact if unfixed x frequency of encounter) / fix effort`.

- Impact: Low = 1, Medium = 2, High = 3, Critical = 4.
- Frequency: Rare = 1, Occasional = 2, Common = 3, Continuous = 4.
- Effort: S = 1, M = 2, L = 3, XL = 4.
- Equal scores are ordered by impact, then by debt ID for stability.

### Execution Outcome

All eight originally registered items were completed through the four phased plans in
`docs/tech-debt-sprints/`. Six of the eight hold on re-measurement, TD-001 and TD-003 hold with
drifted evidence, and TD-004's project-layout claim is retracted for the state it was written
against, though the README has since been repaired.

The register is no longer at zero. A repository-wide analysis on 2026-07-29 registered ten new
items, TD-009 through TD-018, drawn from the improvement lanes summarized in
[`docs/ANALYSIS_BACKLOG.md`](ANALYSIS_BACKLOG.md). The trend is therefore 8 open, to 0, to 10
open, to 9 after `0bf532c` closed TD-015, and back to 10 with TD-019, which that same commit
exposed. None of the new items is a regression of a closed one: they are debt the Sprint 1-4
scope never covered. Three of them (TD-013 coverage, TD-016 event-loop blocking, TD-011
configuration validation) are the unaddressed remainder of areas the closed items touched only
in part, which is recorded in the Resolution Evidence caveats above rather than by reopening
those IDs.

Any newly discovered debt should receive a new ID rather than reopening these entries without
evidence that their acceptance criteria regressed.

### Scan Notes

All four notes were re-measured on 2026-07-29 at `0c9eb91`.

- No code-level `TODO`, `FIXME`, `HACK`, or `XXX` markers were found:
  `grep -rn "TODO\|FIXME\|HACK\|XXX" --include=*.py src scripts main.py` returns **0**. HOLDS,
  with one qualification the original overstated: six `datetime.utcnow()` call sites emit a
  `DeprecationWarning` on every test run, so the repository is not free of deprecation signals,
  only of source-comment markers. The original note's second sentence, about unchecked items in
  `README.md`, is dropped: the README contains no checklist items.
- **Eleven** of 50 production Python modules under `src/` exceed 500 lines (was seventeen of 51
  at `c83f740`, and sixteen when the note was first written), and **90** production functions
  exceed 50 lines (was 101 at `c83f740`, 94 when first written). `0bf532c` accounts for the whole
  drop: it deleted `src/services/help_system.py` outright and pulled `response_generation.py`,
  `message_splitter.py`, `user_experience_service.py`, `error_manager.py`, and `logging_config.py`
  back under 500 lines. The largest functions are unchanged:
  `register_personalization_commands` (423), `register_usage_commands` (319),
  `create_config_group` (281), `register_feature_commands` (261), and
  `_process_message_with_context` (185). These remain size heuristics, not automatically open
  debt; the registered monoliths were closed against their behavior and delegation acceptance
  criteria above.
- **RETRACTED:** the claim that "an exact eight-line duplicate-block scan found no cross-file
  matches" is false now, and was already false on the day it was first written: the same scan
  run against `88e330b` finds **3** cross-file windows, all between
  `message_visibility_service.py` and `pin_service.py`. An exact eight-line sliding-window hash
  over `src/` plus `main.py` finds **3 cross-file windows across 3 file-set groups** at HEAD,
  down from **26 across 7 groups** at `c83f740`. The dominant source there was an identical
  import header replicated across the five `src/bot/command_modules/*.py` files and
  `commands.py`, created by `5e45599` (the same commit that re-asserted the note), which
  accounted for 22 of the 26 windows; `0bf532c` stripped 96 unused import bindings and
  desynchronized those headers, and it also closed the `discord_bot.py` to
  `live_message_coordinator.py` window. What survives is the coordinator delegation signatures
  `discord_bot.py` to `response_generation.py` and to `response_delivery.py`, plus a shared
  header between `message_index_service.py` and `pin_service.py`. TD-007 still
  records repeated persistence structure rather than literal duplication.
- The implementation gate now executes **140** offline unit tests in 15 files under the
  repository's Python 3.12 virtual environment (was 98 in 12 files, exact at the time, and 125
  in 13 files at `c83f740`, where the 2026-07-29 analysis was measured).
