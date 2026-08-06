# Repository Guidelines


Discord bot on Google Gemini. Entry point `main.py`; Discord orchestration in `src/bot/`,
business logic in `src/services/`, dataclasses in `src/models/`, helpers in `src/utils/`.

**Branches.** Work on `remediation/2026-07-30`. It carries the 2026-07-29/30 remediation programme
and everything since. Do **not** commit to `dev`: it sits at `c83f740`, the pre-programme state,
**deliberately** - it is not behind by accident, and it must not be "restored". `main` is off
limits, and a local `pre-push` hook enforces that. Never push; pushing is on the owner's explicit
request only.

> Because `dev` is frozen, the copy of this file on `dev` still says "work on the `dev` branch".
> That cannot be corrected from here without committing to `dev`. If you arrived via `dev`, this
> paragraph is the authority.


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
 scope. Without deps installed, collection fails outright — an environment problem, not
 a code failure.
- No formatter or linter is configured. Do not run `black`/`ruff` across the repo; match
 adjacent code.


## Commands


- `sh start.sh` (`start.bat` on Windows) — create `.venv` if absent, install, run.
 `--rebuild` recreates it and clears `__pycache__`, `.mypy_cache`, `.pytest_cache`.
 It installs deps only when `.venv/bin/python` is missing, so a half-failed install leaves a
 `.venv` that later runs accept and launch with packages missing; only `--rebuild` repairs it,
 and on this box `--rebuild` cannot finish (see Environment).
- `python -m unittest discover -s tests -p "test_*.py"` — **must run from the repo root**;
 there is no `tests/__init__.py` and no `sys.path` shim.
- Focused run: `python -m unittest tests.test_message_rag_services` or
 `python -m unittest tests.test_config.BotConfigTest` (there is no `ConfigParsingTest`;
 the 26 test files hold 65 `TestCase` classes, 63 of them named `<Subject>Test`; the two
 exceptions are the shared harnesses `LoggingHarness` and `OnReadyHarness`).
- `python -m compileall -q main.py src scripts tests` — syntax check without booting.
- `python scripts/mutation_check.py` — reintroduces each fixed bug in a scratch copy and
 reports whether the suite notices. **56 mutants, ~80 s with `--jobs 5`** (it was 5 mutants
 and ~7 s when the harness landed); exit 0 only when every mutant matches its declared
 expectation in `scripts/mutants.toml`. Use it to prove a new regression test actually fails
 on reintroduction, rather than assuming it does.
- `python scripts/health_check.py` — needs real credentials and network; not an offline check.
- **pytest is not configured** (no pyproject/setup.cfg/pytest.ini, not in requirements) and
 async tests would error without `pytest-asyncio`. Use `unittest`.


## Dependencies


`requirements.txt` is only `-c constraints.txt` + `-r requirements.in`. Add direct deps as a
lower bound in `requirements.in`. `constraints.txt` is a validated exact snapshot regenerated
with `pip freeze --exclude pip` from a clean 3.12 env (see README "Dependency workflow") —
never hand-edit a version in it.


## Adding a slash command (three files, all required)


1. Add or extend a `register_*` function in `src/bot/command_modules/` (`general`,
  `configuration`, `research`, `reports_usage`, `personalization`). Registrars take a frozen
  `CommandContext` (`command_modules/context.py`) and declare commands via
  `@bot.tree.command` closures.
2. Call it from `setup_commands` in `src/bot/commands.py:46-80`. **Registration order is
  load-bearing** and deliberately preserved; groups are built then added via
  `bot.tree.add_command`.
3. Update `tests/test_command_registration.py`, which pins the entire tree. Three ordered
  lists, and a new command touches at least two of them:
  - `EXPECTED_SIGNATURE` — ordered `(path, description, callback_name)` tuples, subcommands
    included; the description must match the decorator byte-for-byte. Asserted against the
    image-**disabled** tree only.
  - `EXPECTED_TOP_LEVEL_ORDER` and `EXPECTED_TOP_LEVEL_ORDER_WITH_IMAGES` — the top-level
    names in registration order, one list per tree `setup_commands` can build. The second is
    the only ordering assertion the image-enabled tree gets.
  - `len(EXPECTED_SIGNATURE) == 35` — bump for any addition, subcommands included.

  These replaced a positional `get_commands()[5:10]` slice (DAB-203). Read the change for
  what it is: **de-brittling, not strengthening.** The slice broke whenever anything was
  inserted at or before index 9, which five landed commits had to reason about, and no defect
  could be constructed that it misses and the full lists catch — `EXPECTED_SIGNATURE`
  backstops the disabled tree and the image test carries its own metadata assertions. What
  the lists buy is a failure that names what moved, and 24 of 24 top-level names pinned in
  the image tree instead of 5.


Commands do not exist until `on_ready` (`discord_bot.py:516`) runs `setup_commands` +
`tree.sync()`. `on_ready` re-fires on every gateway reconnect, so registration is guarded by
`bot._slash_commands_registered`, claimed before the await and released again on any exit that is
not a completed registration — including a `CancelledError`, which `except Exception` cannot see
(DAB-003). Do not replace that flag with
`bool(self.tree.get_commands())`: `register_ping_command` runs first, so a registrar failing
after it leaves a non-empty but genuinely incomplete tree, and ten of the eleven registrars
produce exactly that. `M-DAB003C` reintroduces it. Do not clear the tree and rebuild either —
`M-DAB003E` — because `tree.sync()` is a full-replace PUT, so a transient second-run fault
deletes the surviving commands from Discord globally.


## Service wiring


- No DI container. `DiscordBot.__init__` is hand-wired and **construction order matters** —
 module-level `_get_or_create_*` factories read attributes set earlier in the constructor.
- `bot._channel_settings_service`, `_message_visibility_service` and `_user_prefs_service` are
 built in `DiscordBot.__init__` (`discord_bot.py:394-405`), alongside `_pin_service`. They used
 to be attached as a side effect of `register_personalization_commands`, which meant any
 registrar raising left them absent — and since every reader reaches them through
 `getattr`/`hasattr`, `_is_live_mode_enabled` then returned `False` for every channel and user
 preferences were skipped, silently, for the life of the process (DAB-002). The registrars now
 reuse the eager instances via `getattr(bot, ...) or ...`; keep it that way, and do not move
 construction back into a registrar.
- `_pin_service` is **not** in that group, despite sitting next to them in
 `personalization.py`. `DiscordBot.__init__` builds it at `discord_bot.py:377` and hands it
 to `HybridContextRetriever` at `:409`; `personalization.py:129` only reuses it
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


`DiscordBot.on_message` (`discord_bot.py:827`) → live-mode channels fork to
`LiveMessageCoordinator.enqueue` and **return early, skipping the mention gate and the
router** → `is_bot_mentioned` (`:1417`) → rate limit → `EnhancedCommandHandler.handle_message`
(an LLM router with an LRU/TTL cache that may fully handle image requests) → context →
`ResponseGenerationCoordinator` → `ResponseDeliveryCoordinator` → the bot's own reply is
re-indexed into RAG.


Silent degradation is the house style: any exception during hybrid RAG **retrieval** falls back to
the legacy `ContextCollector` path with only a warning (`discord_bot.py:1006-1011`), and indexing
failures log at `debug`. A broken RAG change looks like "nothing happened" — check the logs.

The word *retrieval* is load-bearing. That `try` used to span generation and delivery as well, so
anything that failed after the model had already answered fell through to the legacy path and
answered again — a duplicate reply and a duplicate Gemini charge (DAB-001). Delivery now sits
outside it, behind `if rag_context is not None` (`:1022`). Keep it that way: widening the `try`, or
weakening that gate to a truthiness test, each reintroduce paid duplicate work, and
`scripts/mutation_check.py` carries `M-DAB001` and `M-DAB001B` for exactly those two mistakes.

The live-mode fork has the same rule, enforced differently. `LiveMessageCoordinator.run_channel_worker`
retries a batch whose processing raised (up to `max_retry_attempts`, default 3, with a linear
backoff), so a transient fault no longer destroys the queue silently (DAB-019). What makes that
safe is the `owed` receipt threaded through `_answer_batch`: it holds the messages still awaiting
an answer, and it is **emptied the moment a model call has been made** — after `generate_response`
returns, and before `process_message_with_context` is awaited. A call that *raises* produced
nothing and is retryable; a call that *returns* has been billed and must never be repeated. `owed`
is also narrowed when the batch is (attachment split, rate-limit refusal), and `charged` records
which users the limiter already debited so a retry costs each participant one token per turn, not
per attempt. A cancellation cannot see `except Exception` (`CancelledError` is a
`BaseException`), so the worker's `finally` checks the receipt too, and a delivery failure
*after* the model answered notifies the user without regenerating. `M-DAB019` through
`M-DAB019G` pin all seven properties. Do not requeue the popped
batch instead of `owed` — that is what the ticket prescribes and it duplicates the attachment
suffix, re-debits the limiter and re-bills Gemini.


## SQLite


- Two databases, and config validation **requires them to be distinct**
 (`config_helpers.py:525`): `data/token_usage.db` (`token_usage`, `bot_reports`,
 `channel_settings`, `user_preferences`, `hidden_messages`) and `data/message_rag.db`
 (`message_index`, `message_embeddings`, `message_search_fts`, `message_retrieval_events`,
 `message_backfill_progress`, `pinned_messages`, `rag_migrations`).
- All access goes through `sqlite_connection` / `sqlite_transaction` in
 `src/services/sqlite_utils.py` — the only `sqlite3.connect` site under `src/`. Don't add
 another.
 It sets the lock-wait budget from `bot.sqlite_busy_timeout_ms` (default 5000 ms, applied once by
 `DiscordBot.__init__` via `configure_busy_timeout`). It deliberately does **not** set
 `journal_mode=WAL`: on a per-call-connection architecture WAL costs +0.34 ms on every call and
 made no difference to the DAB-065 trigger in any journal mode. Reopen that when connections are
 pooled — see `docs/ANALYSIS_CORRECTIONS.md` item 14.
- **No migration framework and no `PRAGMA user_version`.** Each service creates its own schema
 in `_ensure_table` / `_ensure_schema` at construction.
 - New table: add `CREATE TABLE IF NOT EXISTS` to the owning service.
  - New column: you must *also* call the idempotent `_ensure_column` helper
   (`message_index_service.py:125`, `channel_settings_service.py:49`). Editing the
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
 `config_helpers.py:754`). `tests/test_config.py` asserts parsed defaults. 39 of the 99
 annotated `BotConfig` fields are still mentioned by no `_validate_*` helper (TD-011).
- Fixed protocol limits go in `src/constants.py`; anything tunable goes in `config.yaml`.
- Config loading and startup diagnostics use `print` on purpose (visible before logging is
 configured). Everywhere else, use `logging`.


## Tests


- stdlib `unittest`. Classes are `<Subject>Test` (suffix, 63 of 65; the two exceptions are shared harnesses) — not `Test<Subject>`. Async
 tests use `IsolatedAsyncioTestCase` with `asyncSetUp`/`asyncTearDown`.
- No `conftest.py`, no `tests/__init__.py`, no shared helpers — every file is self-contained.
 The idiom is `types.SimpleNamespace` fakes plus `unittest.mock.AsyncMock` (`MagicMock` is
 never used), and `object.__new__(Cls)` to bypass heavy constructors before assigning attrs.
- No network anywhere; SQLite tests write real databases into `tempfile.TemporaryDirectory`.
 Some tests do real work — PyMuPDF rendering, matplotlib LaTeX (self-skips if absent).
- `tests/test_rag_optimization.py:94` (assertion at `:104`) asserts a negative 50 ms timing
 window against a real thread; it can flake on a loaded machine.
- Add regression tests for bug fixes, covering the error path as well as the happy path.


## Known defects (do not re-derive these)


A repository-wide analysis was completed on 2026-07-29 against `c83f740`, on a `125 tests, OK`
baseline. A four-phase remediation programme then landed **34 commits** on top of it, closing
every S1. Read the analysis before starting a bug hunt or a refactor; it already covers most of
what a fresh sweep would rediscover — but read the outcome summary first, or you will re-fix
something that is already fixed.

- `docs/REMEDIATION_2026-07-30.md`: **start here.** What the programme was, which findings are
 closed and in which commit, what was refuted rather than applied, and what is still open.
- `docs/ANALYSIS_CORRECTIONS.md`: claims from the analysis that later verification refuted.
 Read it before acting on any ticket.
- `docs/ANALYSIS_BACKLOG.md`: the prioritized worklist, tiers 0-3, derived from a 211-finding
 register, each row reconciled against the landed commits. Also carries the ten post-programme
 findings the 2026-07-31 pre-push review added, which are recorded but **not** fixed.
- `docs/analysis-tickets/`: 37 files, one per actionable finding, each with file:line evidence,
 a reproduction, acceptance criteria, and the regression test to add. Every one now opens with a
 `**Status:**` line — 23 LANDED, 1 PARTIAL, 1 REFUTED, 12 OPEN. Read the Status line before the
 body: the bodies were never rewritten, and on **12** of the 37 the fix landed by a route the
 body does not describe, or was refuted outright.
- `docs/BUG_ANALYSIS_2026-07-29.md`: correctness findings with reproductions and current status.
 Supersedes `DEEP_BUG_HUNT_REPORT.md`.
- `docs/IMPROVEMENT_ANALYSIS_2026-07-29.md`: architecture, data layer, performance, cost
 accounting, config, and testing/DX, with measured before/after figures.

Ticket baselines are quoted against the 125-test suite that existed at `c83f740`. **The gate is
now 273 in 26 files** (`f0938a8` added `tests/test_repo_hygiene.py`, `4fc5063` added
`tests/test_on_message_flow.py`, Phase 1 added `tests/test_context_fallback.py`,
`tests/test_error_classification.py` and `tests/test_message_splitter_scaling.py`, Phases 2-3
added `tests/test_command_cooldowns.py`, `tests/test_pin_limits.py`,
`tests/test_config_command_gate.py` and `tests/test_startup_integrity.py`, and Phase 3b added
`tests/test_live_message_coordinator.py` and `tests/test_logging_config.py`, and Phase 4
added `tests/test_rag_query_plans.py` and `tests/test_sqlite_utils.py`).
Some tickets deliberately change the count on top of that; each says so.

The `file:line` evidence in those documents was measured at `c83f740`. **34 commits have landed
since**, so most of those offsets have moved (for example `src/bot/commands.py:91`, cited in
several tickets, is past the end of an 80-line file, and `on_message` has moved from `:725` to
`:827`). Trust the finding and re-locate the symbol; do not trust the line number. The `file:line`
citations in *this* file were re-measured at `922e899` and are current.


## Stale docs — do not trust at face value


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
   `test_request_model_precedence` (`test_bug_regressions.py:45`) unit-tests
   `_resolve_request_preferences` in isolation, so reinstating `model_override = routed_model`
   after `discord_bot.py:904` left all 125 tests green. `4fc5063` added
   `tests/test_on_message_flow.py`, whose
   `test_router_complexity_never_hardens_into_a_model_override` asserts the contract at the
   caller. `python scripts/mutation_check.py M-BUG0002` now reports KILLED; the full run is
   56/56.
  - **BUG-0005's obstructing test is fixed (DAB-204, round 2 Phase 0).** It was
   `test_main_response_path_does_not_wrap_client_retry_timeout`, which patched
   `src.bot.discord_bot.asyncio.wait_for` with an `AssertionError` side effect. That attribute
   is the singleton `asyncio` module, so the patch was global and failed *any* whole-sequence
   deadline, correct ones included. There was a **second** copy of the same patch at
   `tests/test_response_generation.py:107-110` that neither DAB-204 nor DAB-042 mentioned.
   Both are gone, replaced by `test_main_response_path_preserves_the_full_client_retry_budget`,
   which patches the module's `asyncio` *binding* with a recorder and asserts that no deadline
   is shorter than `per_attempt x (max_retries + 1)`. DAB-042 is unblocked; put the budget
   inside `GeminiClient._run_response_attempts`, not in `response_generation.py`, or the two
   tickets' acceptance criteria contradict each other.

 Current status of all five: `docs/BUG_ANALYSIS_2026-07-29.md`.

- `docs/BOT_SYSTEM_REPORT.md` cites `pytest -q` and 9 tests; both runner and count are wrong
 (273 stdlib `unittest` tests across 26 files). It now carries a staleness banner listing its
 known-wrong claims; the body is unedited.


## Security


Never commit `.env`, `data/`, `*.db`, or logs (all gitignored). Do not write user message
content into the repo tree.



