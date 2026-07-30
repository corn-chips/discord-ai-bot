# Repository Guidelines


Discord bot on Google Gemini. Entry point `main.py`; Discord orchestration in `src/bot/`,
business logic in `src/services/`, dataclasses in `src/models/`, helpers in `src/utils/`.
Work on the `dev` branch â€” do not target `main`.


## Environment


- **Python 3.12 exactly.** `start.sh`/`start.bat` refuse any other version and
 `constraints.txt` is pinned from a clean 3.12.13 environment. A newer system Python will
 not do.
- A fresh checkout has no `.venv`; **this working copy has one and it works**
 (`.venv/bin/python` is 3.12.13, and the suite runs green against it). The system `python3`
 on this box is 3.13.14, but `python3.12` resolves to 3.12.13, and that is the first name
 `start.sh` tries. There is no Docker path.
- `start.sh` is committed non-executable (git mode `100644`), so `./start.sh` fails with
 "Permission denied". Use `sh start.sh`.
- **Do not run `sh start.sh --rebuild` here.** It deletes `.venv` before reinstalling, and
 the reinstall cannot succeed: this machine's default pip index is an authenticated
 corporate mirror (`/etc/pip.conf`), not PyPI, and `pip install discord.py` against it
 reports "from versions: none". You would destroy the only working environment in the tree.
- The `.venv` in this working copy was **not** built by `start.sh`. Per `.venv/pyvenv.cfg` it
 was created by a standalone `uv` 0.12.0 from a uv-managed CPython 3.12.13; `uv` itself is no
 longer installed on this box. Consequence: **the venv has no `pip`**, so
 `.venv/bin/python -m pip ...` dies with "No module named pip". Drive pip from outside
 instead: `python3.12 -m pip --python .venv/bin/python check` works and currently reports
 "No broken requirements found."
- Tests import `discord`, `google.genai`, `numpy`, `yaml`, `PIL`, and `fitz` at module
 scope. Without deps installed, collection fails outright â€” an environment problem, not
 a code failure.
- No formatter or linter is configured. Do not run `black`/`ruff` across the repo; match
 adjacent code.


## Commands


- `sh start.sh` (`start.bat` on Windows) â€” create `.venv` if absent, install, run.
 `--rebuild` recreates it and clears `__pycache__`, `.mypy_cache`, `.pytest_cache`.
 It installs deps only when `.venv/bin/python` is missing, so a half-failed install leaves a
 `.venv` that later runs accept and launch with packages missing; only `--rebuild` repairs it,
 and on this box `--rebuild` cannot finish (see Environment).
- `python -m unittest discover -s tests -p "test_*.py"` â€” **must run from the repo root**;
 there is no `tests/__init__.py` and no `sys.path` shim.
- Focused run: `python -m unittest tests.test_message_rag_services` or
 `python -m unittest tests.test_config.BotConfigTest` (there is no `ConfigParsingTest`;
 the 18 test files hold 43 `TestCase` classes, all named `<Subject>Test`).
- `python -m compileall -q main.py src scripts tests` â€” syntax check without booting.
- `python scripts/mutation_check.py` â€” reintroduces each fixed bug in a scratch copy and
 reports whether the suite notices. ~7 s with `--jobs 5`; exit 0 only when every mutant
 matches its declared expectation in `scripts/mutants.toml`. Use it to prove a new
 regression test actually fails on reintroduction, rather than assuming it does.
- `python scripts/health_check.py` â€” needs real credentials and network; not an offline check.
- **pytest is not configured** (no pyproject/setup.cfg/pytest.ini, not in requirements) and
 async tests would error without `pytest-asyncio`. Use `unittest`.


## Dependencies


`requirements.txt` is only `-c constraints.txt` + `-r requirements.in`. Add direct deps as a
lower bound in `requirements.in`. `constraints.txt` is a validated exact snapshot regenerated
with `pip freeze --exclude pip` from a clean 3.12 env (see README "Dependency workflow") â€”
never hand-edit a version in it.


## Adding a slash command (three files, all required)


1. Add or extend a `register_*` function in `src/bot/command_modules/` (`general`,
  `configuration`, `research`, `reports_usage`, `personalization`). Registrars take a frozen
  `CommandContext` (`command_modules/context.py`) and declare commands via
  `@bot.tree.command` closures.
2. Call it from `setup_commands` in `src/bot/commands.py:46-80`. **Registration order is
  load-bearing** and deliberately preserved; groups are built then added via
  `bot.tree.add_command`.
3. Update `tests/test_command_registration.py`, which pins the entire tree:
  - `EXPECTED_SIGNATURE` (line 26) â€” ordered `(path, description, callback_name)` tuples;
    the description must match the decorator byte-for-byte.
  - `len(bot.tree.get_commands()) == 22` (line 147) â€” bump only for a new *top-level*
    command or group.
  - `len(EXPECTED_SIGNATURE) == 35` (line 148) â€” bump for any addition, subcommands included.
  - the positional slice guard at line 239, which pins `get_commands()[5:10]` to
    `["features", "edit-image", "image-queue", "clear-cache", "dev"]` in the
    image-enabled tree. Anything inserted at or before index 9 shifts that window and
    breaks it independently of `EXPECTED_SIGNATURE`.


Commands do not exist until `on_ready` (`discord_bot.py:462`) runs `setup_commands` +
`tree.sync()`.


## Service wiring


- No DI container. `DiscordBot.__init__` is hand-wired and **construction order matters** â€”
 module-level `_get_or_create_*` factories read attributes set earlier in the constructor.
- `bot._channel_settings_service` (`command_modules/personalization.py:31`),
 `_message_visibility_service` (`:134`), and `_user_prefs_service` (`:368`) are attached
 during *command registration*, not in `__init__`. Nothing in `discord_bot.py` assigns them;
 it only reads them through `getattr`/`hasattr`. Before `on_ready` they are absent, so
 `_is_live_mode_enabled` (`discord_bot.py:702-705`) returns `False` and user preferences are
 skipped.
- `_pin_service` is **not** in that group, despite sitting next to them in
 `personalization.py`. `DiscordBot.__init__` builds it at `discord_bot.py:366` and hands it
 to `HybridContextRetriever` at `:378`; `personalization.py:126` only reuses it
 (`getattr(bot, "_pin_service", None) or PinService(...)`), so the fallback construction
 fires only for bots that never ran `DiscordBot.__init__`, i.e. test doubles. Pinned
 memories are available before `on_ready`.
- Frequently `None`: `token_tracker`, `report_service`, `image_processing_service`,
 `enhanced_command_handler` (nulled at runtime if the image service fails to start), and
 `gemini_client.client` when no API key. Guard, don't assume.
- Most `DiscordBot._foo` methods are thin delegates to coordinators (`response_generation.py`,
 `response_delivery.py`, `live_message_coordinator.py`, `rag_event_coordinator.py`,
 `media_extraction.py`). Change behavior in the coordinator; tests patch the wrapper.


## Message flow (read these first)


`DiscordBot.on_message` (`discord_bot.py:725`) â†’ live-mode channels fork to
`LiveMessageCoordinator.enqueue` and **return early, skipping the mention gate and the
router** â†’ `is_bot_mentioned` (`:1218`) â†’ rate limit â†’ `EnhancedCommandHandler.handle_message`
(an LLM router with an LRU/TTL cache that may fully handle image requests) â†’ context â†’
`ResponseGenerationCoordinator` â†’ `ResponseDeliveryCoordinator` â†’ the bot's own reply is
re-indexed into RAG.


Silent degradation is the house style: any exception during hybrid RAG **retrieval** falls back to
the legacy `ContextCollector` path with only a warning (`discord_bot.py:894-899`), and indexing
failures log at `debug`. A broken RAG change looks like "nothing happened" â€” check the logs.

The word *retrieval* is load-bearing. That `try` used to span generation and delivery as well, so
anything that failed after the model had already answered fell through to the legacy path and
answered again â€” a duplicate reply and a duplicate Gemini charge (DAB-001). Delivery now sits
outside it, behind `if rag_context is not None` (`:908`). Keep it that way: widening the `try`, or
weakening that gate to a truthiness test, each reintroduce paid duplicate work, and
`scripts/mutation_check.py` carries `M-DAB001` and `M-DAB001B` for exactly those two mistakes.


## SQLite


- Two databases, and config validation **requires them to be distinct**
 (`config_helpers.py:513`): `data/token_usage.db` (`token_usage`, `bot_reports`,
 `channel_settings`, `user_preferences`, `hidden_messages`) and `data/message_rag.db`
 (`message_index`, `message_embeddings`, `message_search_fts`, `message_retrieval_events`,
 `message_backfill_progress`, `pinned_messages`, `rag_migrations`).
- All access goes through `sqlite_connection` / `sqlite_transaction` in
 `src/services/sqlite_utils.py` â€” the only `sqlite3.connect` site under `src/`. Don't add
 another.
- **No migration framework and no `PRAGMA user_version`.** Each service creates its own schema
 in `_ensure_table` / `_ensure_schema` at construction.
 - New table: add `CREATE TABLE IF NOT EXISTS` to the owning service.
 - New column: you must *also* call the idempotent `_ensure_column` helper
   (`message_index_service.py:113`, `channel_settings_service.py:49`). Editing the
   `CREATE TABLE` body alone silently leaves existing databases without the column.
 - One-shot data migrations are gated by named rows in the `rag_migrations` ledger.
- FTS5 degrades gracefully to `fts_enabled = False` when unavailable.


## Configuration


- Secrets live only in `.env`: `DISCORD_BOT_TOKEN`, `GEMINI_API_KEY`, optional
 `NANO_BANANA_API_KEY` (falls back to `GEMINI_API_KEY`).
- Everything else is `config.yaml`. Three env vars override it when set: `TOKEN_DB_PATH`,
 `RAG_DATABASE_PATH`, `LOG_FILE`.
- Adding a setting touches four places: the `config.yaml` key, a parser in
 `src/config_helpers.py`, a field on `BotConfig` in `src/config.py`, and a rule in the
 matching `_validate_*` helper (the four are aggregated by `validate_config`,
 `config_helpers.py:722`). `tests/test_config.py` asserts parsed defaults.
- Fixed protocol limits go in `src/constants.py`; anything tunable goes in `config.yaml`.
- Config loading and startup diagnostics use `print` on purpose (visible before logging is
 configured). Everywhere else, use `logging`.


## Tests


- stdlib `unittest`. Classes are `<Subject>Test` (suffix, 31/31) â€” not `Test<Subject>`. Async
 tests use `IsolatedAsyncioTestCase` with `asyncSetUp`/`asyncTearDown`.
- No `conftest.py`, no `tests/__init__.py`, no shared helpers â€” every file is self-contained.
 The idiom is `types.SimpleNamespace` fakes plus `unittest.mock.AsyncMock` (`MagicMock` is
 never used), and `object.__new__(Cls)` to bypass heavy constructors before assigning attrs.
- No network anywhere; SQLite tests write real databases into `tempfile.TemporaryDirectory`.
 Some tests do real work â€” PyMuPDF rendering, matplotlib LaTeX (self-skips if absent).
- `tests/test_rag_optimization.py:94` (assertion at `:104`) asserts a negative 50 ms timing
 window against a real thread; it can flake on a loaded machine.
- Add regression tests for bug fixes, covering the error path as well as the happy path.


## Known defects (do not re-derive these)


A repository-wide analysis was completed on 2026-07-29 against this HEAD (`c83f740`), against a
`125 tests, OK` baseline. Read it before starting a bug hunt or a refactor; it already covers
most of what a fresh sweep would rediscover.

- `docs/ANALYSIS_CORRECTIONS.md`: claims from the analysis that later verification refuted.
 Read it before acting on any ticket.
- `docs/ANALYSIS_BACKLOG.md`: the prioritized worklist, tiers 0-3, derived from a 211-finding
 register. Start here.
- `docs/analysis-tickets/`: 37 files, one per actionable finding, each with file:line evidence,
 a reproduction, acceptance criteria, and the regression test to add.
- `docs/BUG_ANALYSIS_2026-07-29.md`: correctness findings with reproductions and current status.
 Supersedes `DEEP_BUG_HUNT_REPORT.md`.
- `docs/IMPROVEMENT_ANALYSIS_2026-07-29.md`: architecture, data layer, performance, cost
 accounting, config, and testing/DX, with measured before/after figures.

Ticket baselines are quoted against the 125-test suite that existed at `c83f740`. **The gate is
now 177 in 18 files** (`b5851ab` added `tests/test_repo_hygiene.py`, `6f1dc79` added
`tests/test_on_message_flow.py`, and Phase 1 added `tests/test_context_fallback.py`,
`tests/test_error_classification.py` and `tests/test_message_splitter_scaling.py`).
Some tickets deliberately change the count on top of that; each says so.

The `file:line` evidence in those four documents was measured at `c83f740`. Several commits have
since landed â€” `9894bcc` (dead-code removal) and `4baa29c` (five dependencies dropped) â€”
shifting many of those offsets (for example `src/bot/commands.py:91`, cited in several tickets,
is past the end of an 80-line file). Trust the finding and re-locate the symbol; do not trust
the line number.


## Stale docs â€” do not trust at face value


- `docs/README.md` is the index for `docs/`; it marks every file CURRENT, HISTORICAL, or
 SUPERSEDED. Check it before quoting any figure out of that directory.
- `docs/DEEP_BUG_HUNT_REPORT.md` marks BUG-0001 to BUG-0005 "Open" against build `8bc80f9`,
 which is not in this history (`git cat-file -t 8bc80f9` fails). It now carries a SUPERSEDED
 banner; the body is unedited. All five do have regression tests in
 `tests/test_bug_regressions.py`, but "has a regression test" is not "fixed and guarded":
 - **BUG-0003 is only partially fixed.** Overflow is closed: the guard at
   `message_splitter.py:125-133` falls back to `_hard_split_parts` (`:138-166`), so no page
   exceeds the limit. The root cause is untouched and fence preservation is now effectively
   dead. A 6,076-char fenced-code response splits into 4 pages, every one `hard_split=True`,
   cutting mid-token, page 1 left with an unterminated fence and the last page an orphan
   close.
 - **BUG-0002's original regression test did not reach the buggy site — now closed.**
   `test_request_model_precedence` (`test_bug_regressions.py:37`) unit-tests
   `_resolve_request_preferences` in isolation, so reinstating `model_override = routed_model`
   after `discord_bot.py:798` left all 125 tests green. `6f1dc79` added
   `tests/test_on_message_flow.py`, whose
   `test_router_complexity_never_hardens_into_a_model_override` asserts the contract at the
   caller. `python scripts/mutation_check.py M-BUG0002` now reports KILLED; the full run is
   5/5.
 - **`test_main_response_path_does_not_wrap_client_retry_timeout`
   (`test_bug_regressions.py:110`) obstructs a repair the code still needs.** It patches
   `src.bot.discord_bot.asyncio.wait_for` with an `AssertionError` side effect; since that
   attribute is the singleton `asyncio` module, the patch is global. Any correct
   whole-sequence deadline fails it: wrapping the Gemini call in `asyncio.wait_for` turns the
   test into an ERROR, surfacing as a misleading `TypeError: object Mock can't be used in
   'await' expression` because `except Exception` swallows the assertion. It must assert the
   budget, not the absence of `wait_for`.

 Current status of all five: `docs/BUG_ANALYSIS_2026-07-29.md`.

- `docs/BOT_SYSTEM_REPORT.md` cites `pytest -q` and 9 tests; both runner and count are wrong
 (177 stdlib `unittest` tests across 18 files). It now carries a staleness banner listing its
 known-wrong claims; the body is unedited.


## Security


Never commit `.env`, `data/`, `*.db`, or logs (all gitignored). Do not write user message
content into the repo tree.



