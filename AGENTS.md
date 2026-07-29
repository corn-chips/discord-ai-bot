# Repository Guidelines


Discord bot on Google Gemini. Entry point `main.py`; Discord orchestration in `src/bot/`,
business logic in `src/services/`, dataclasses in `src/models/`, helpers in `src/utils/`.
Work on the `dev` branch â€” do not target `main`.


## Environment


- **Python 3.12 exactly.** `start.sh`/`start.bat` refuse any other version and
 `constraints.txt` is pinned from a 3.12.10 venv. A newer system Python will not do.
- A fresh checkout has no `.venv`, and `python3.12` may not be on PATH. Verify before
 promising to run anything; `./start.sh --rebuild` builds it. There is no Docker path.
- Tests import `discord`, `google.genai`, `numpy`, `yaml`, `PIL`, and `fitz` at module
 scope. Without deps installed, collection fails outright â€” an environment problem, not
 a code failure.
- No formatter or linter is configured. Do not run `black`/`ruff` across the repo; match
 adjacent code.


## Commands


- `./start.sh` (`start.bat` on Windows) â€” create `.venv` if absent, install, run.
 `--rebuild` recreates it and clears `__pycache__`, `.mypy_cache`, `.pytest_cache`.
- `python -m unittest discover -s tests -p "test_*.py"` â€” **must run from the repo root**;
 there is no `tests/__init__.py` and no `sys.path` shim.
- Focused run: `python -m unittest tests.test_message_rag_services` or
 `python -m unittest tests.test_config.ConfigParsingTest.test_x`.
- `python -m compileall -q main.py src scripts tests` â€” syntax check without booting.
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
2. Call it from `setup_commands` in `src/bot/commands.py:73-91`. **Registration order is
  load-bearing** and deliberately preserved; groups are built then added via
  `bot.tree.add_command`.
3. Update `tests/test_command_registration.py`, which pins the entire tree:
  - `EXPECTED_SIGNATURE` (line 26) â€” ordered `(path, description, callback_name)` tuples;
    the description must match the decorator byte-for-byte.
  - `len(bot.tree.get_commands()) == 22` (line 147) â€” bump only for a new *top-level*
    command or group.
  - `len(EXPECTED_SIGNATURE) == 35` (line 148) â€” bump for any addition, subcommands included.
  - the positional slice guard at line 239, which breaks independently if anything is
    inserted before index 10.


Commands do not exist until `on_ready` (`discord_bot.py:461`) runs `setup_commands` +
`tree.sync()`.


## Service wiring


- No DI container. `DiscordBot.__init__` is hand-wired and **construction order matters** â€”
 module-level `_get_or_create_*` factories read attributes set earlier in the constructor.
- `bot._channel_settings_service`, `_user_prefs_service`, `_message_visibility_service`, and
 `_pin_service` are attached during *command registration*
 (`command_modules/personalization.py:43,142,146,380`), not in `__init__`. Before `on_ready`
 they are absent, so live mode reads as off and user preferences are skipped.
- Frequently `None`: `token_tracker`, `report_service`, `image_processing_service`,
 `enhanced_command_handler` (nulled at runtime if the image service fails to start), and
 `gemini_client.client` when no API key. Guard, don't assume.
- Most `DiscordBot._foo` methods are thin delegates to coordinators (`response_generation.py`,
 `response_delivery.py`, `live_message_coordinator.py`, `rag_event_coordinator.py`,
 `media_extraction.py`). Change behavior in the coordinator; tests patch the wrapper.


## Message flow (read these first)


`DiscordBot.on_message` (`discord_bot.py:753`) â†’ live-mode channels fork to
`LiveMessageCoordinator.enqueue` and **return early, skipping the mention gate and the
router** â†’ `is_bot_mentioned` (`:1263`) â†’ rate limit â†’ `EnhancedCommandHandler.handle_message`
(an LLM router with an LRU/TTL cache that may fully handle image requests) â†’ context â†’
`ResponseGenerationCoordinator` â†’ `ResponseDeliveryCoordinator` â†’ the bot's own reply is
re-indexed into RAG.


Silent degradation is the house style: any exception during hybrid RAG retrieval falls back to
the legacy `ContextCollector` path with only a warning (`discord_bot.py:935-940`), and indexing
failures log at `debug`. A broken RAG change looks like "nothing happened" â€” check the logs.


## SQLite


- Two databases, and config validation **requires them to be distinct**
 (`config_helpers.py:525`): `data/token_usage.db` (`token_usage`, `bot_reports`,
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
 matching `validate_*` helper. `tests/test_config.py` asserts parsed defaults.
- Fixed protocol limits go in `src/constants.py`; anything tunable goes in `config.yaml`.
- Config loading and startup diagnostics use `print` on purpose (visible before logging is
 configured). Everywhere else, use `logging`.


## Tests


- stdlib `unittest`. Classes are `<Subject>Test` (suffix, 27/27) â€” not `Test<Subject>`. Async
 tests use `IsolatedAsyncioTestCase` with `asyncSetUp`/`asyncTearDown`.
- No `conftest.py`, no `tests/__init__.py`, no shared helpers â€” every file is self-contained.
 The idiom is `types.SimpleNamespace` fakes plus `unittest.mock.AsyncMock` (`MagicMock` is
 never used), and `object.__new__(Cls)` to bypass heavy constructors before assigning attrs.
- No network anywhere; SQLite tests write real databases into `tempfile.TemporaryDirectory`.
 Some tests do real work â€” PyMuPDF rendering, matplotlib LaTeX (self-skips if absent).
- `tests/test_rag_optimization.py:94` asserts a negative 50 ms timing window against a real
 thread; it can flake on a loaded machine.
- Add regression tests for bug fixes, covering the error path as well as the happy path.


## Stale docs â€” do not trust at face value


- `docs/DEEP_BUG_HUNT_REPORT.md` marks five bugs "Open" against build `8bc80f9`, which is not
 in this history. All five now have regression tests in `tests/test_bug_regressions.py`.
 Historical snapshot only.
- `docs/BOT_SYSTEM_REPORT.md` cites `pytest -q` and 9 tests; both runner and count are wrong.
- `README.md`'s project-layout section points at `BOT_SYSTEM_REPORT.md`, `pipeline.html`, and
 `message-sequence-flowchart.html` in the repo root. The HTML files do not exist; the report
 lives in `docs/`.


## Security


Never commit `.env`, `data/`, `*.db`, or logs (all gitignored). Do not write user message
content into the repo tree.



