# Discord AI Bot

A Discord bot backed by Google's Gemini API. It responds to mentions and replies in guild channels, can run mention-free in channels where live mode is enabled, and automatically responds in private DMs.

## Capabilities

- Gemini model routing, configurable thinking levels, and optional Google Search grounding
- Image understanding, image generation/editing, PDF conversion, and text-file input
- Markdown-aware message splitting, table formatting, and LaTeX rendering
- Local hybrid message retrieval with lexical and embedding search
- Per-channel personalities, pinned memories, live mode, and message hide/restore
- Per-user model and language preferences
- Token accounting, usage reports, issue reports, and a local report viewer

The implementation entry point is `main.py`. Discord orchestration lives in `src/bot/`, reusable logic in `src/services/`, shared dataclasses in `src/models/`, and common helpers in `src/utils/`.

## Requirements

- Python 3.12
- A Discord bot token with Message Content Intent enabled
- A Google Gemini API key

Python 3.12 is the supported baseline for the checked-in dependency snapshot. The current `constraints.txt` was generated from the repository's Python 3.12.10 virtual environment and includes packages whose pinned versions require Python 3.12.

This repository does not currently include a `Dockerfile` or Compose configuration. Use the local Python workflow below.

## Setup

Clone the repository, then create a `.env` file in the project root:

```dotenv
DISCORD_BOT_TOKEN=replace_with_your_discord_token
GEMINI_API_KEY=replace_with_your_gemini_api_key
# Optional; defaults to GEMINI_API_KEY when omitted
NANO_BANANA_API_KEY=replace_with_a_separate_image_key
```

Do not commit `.env`; it is ignored by Git. Non-secret settings already live in the tracked `config.yaml`. Review that file before starting the bot, especially the model IDs, context limits, RAG settings, report-server settings, and logging paths.

### Start scripts

When `.venv` is absent, the start scripts create it and install the constrained dependencies. They then launch `main.py`:

```powershell
# Windows
start.bat

# Recreate .venv before starting
start.bat --rebuild
```

```sh
# Linux or macOS
sh start.sh

# Recreate .venv before starting
sh start.sh --rebuild
```

For a new or rebuilt environment, Python 3.12 must be available through `py -3.12` or `python` on Windows, or through `python3.12`, `python3`, or `python` on Linux and macOS. An existing Python 3.12 `.venv` can be used without a separate system interpreter. The rebuild option removes `.venv`; environment creation also clears repository `__pycache__` directories, `.pyc` files, `.mypy_cache`, and `.pytest_cache` before installing dependencies.

### Manual installation

Windows:

```powershell
py -3.12 -m venv .venv
.venv\Scripts\python.exe -m pip install --upgrade pip
.venv\Scripts\python.exe -m pip install -r requirements.txt
.venv\Scripts\python.exe main.py
```

Linux or macOS:

```sh
python3.12 -m venv .venv
.venv/bin/python -m pip install --upgrade pip
.venv/bin/python -m pip install -r requirements.txt
.venv/bin/python main.py
```

Press `Ctrl+C` to stop the bot gracefully.

## Configuration

Secrets are read from `.env`; operational settings are read from `config.yaml`.

Important sections in `config.yaml` include:

- `models` and `model_complexity`: available models, routing defaults, and thinking levels
- `context` and `rag`: recent-message collection and local hybrid retrieval
- `response` and `messages`: timeouts, retries, and Discord message splitting
- `image_processing` and `nano_banana`: image validation, concurrency, model, and retry behavior
- `reports`: local report viewer host and port
- `logging`: console/file logging and performance logging

The checked-in default text model is `gemini-3-flash-preview`; the router model is `gemini-2.5-flash-lite`. Restart the bot after editing `config.yaml`.

Runtime SQLite databases are written beneath `data/` by default. Console logging is the default; if `logging.file` is configured, keep the chosen log path under the ignored `logs/` directory. Runtime data and logs must not be committed.

## Dependency workflow

Dependency inputs are intentionally split by purpose:

- `requirements.in` contains the human-maintained direct dependency lower bounds.
- `constraints.txt` pins the complete environment that passed validation.
- `requirements.txt` combines both, so existing `pip install -r requirements.txt` and start-script installs use the pinned snapshot automatically.

Install or validate the current snapshot with:

```powershell
.venv\Scripts\python.exe -m pip install -r requirements.txt
.venv\Scripts\python.exe -m pip check
```

To refresh dependencies, use a clean Python 3.12 virtual environment rather than an environment containing unrelated packages:

1. Install and upgrade from `requirements.in` without applying the old constraints.
2. Run the full test suite and `python -m pip check` in that environment.
3. Run `python -m pip freeze --exclude pip` and replace the package lines in `constraints.txt` with that exact output. Keep the explanatory header.
4. Recreate `.venv`, install through `requirements.txt`, and repeat the tests and `pip check`.
5. Review and commit `requirements.in` and `constraints.txt` together.

This process makes upgrades explicit: version changes come from a tested refresh, not an unreviewed install of whatever happens to be newest.

## Usage

In guild or group channels, mention the bot or reply to one of its messages. In private DMs, send a message directly. `/live` can enable or disable mention-free responses for the current channel.

The registered slash commands include:

- General: `/ping`, `/features`, `/summarize`, `/deepresearch`, `/stats`, `/token-leaderboard`, `/api-usage`, and `/usage-report`
- Configuration: `/config model`, `/config thinking`, `/config deepsearch`, `/config image-generation`, `/config debug`, and `/config info`
- Image tools: `/edit-image` and `/image-queue` when image processing is configured
- Local memory: `/rag status`, `/rag backfill`, `/rag delete`, `/pin`, `/pins`, `/hide`, and `/unhide`. On startup, the bot works through every guild message channel where it can view and read history, including active threads. RAG messages, pending embeddings, pinned memories, and per-channel resume cursors persist in the configured local SQLite database, so an interrupted backlog continues after the last scanned message. `/rag backfill` can start the resumable job for the current channel manually, while `/rag delete` clears either the current channel's RAG data or all channels' RAG data.

Hybrid RAG uses a cost-aware router gate. Self-contained requests can skip recent,
FTS, semantic, and reranker work while still receiving pinned memories; direct
Discord replies, live mode, and router failures always use full retrieval. Very
short messages remain stored and FTS-searchable but are marked as skipped for
embedding according to `embedding_min_words` and
`embedding_min_alphanumeric_chars`. The reranker runs only when the local fused
ranking has an ambiguous selection boundary.

Completed 768-dimensional embeddings are lazily cached as float32 vectors in
process memory. Vector payload RAM is approximately 3 KiB per cached message
(about 29 MiB per 10,000 messages), plus small NumPy metadata arrays. `/rag
status` reports skipped embeddings, cached vector count, and estimated vector
bytes. Set `vector_cache_enabled: false` to disable the cache.
- Personalization: `/personality`, `/personality-info`, and the `/preferences` subcommands
- Reports and administration: `/report`, `/report-status`, `/dev`, and `/clear-cache`

Some commands require Discord permissions or an optional service. Discord exposes the required arguments and choices when the command is selected.

## Development and verification

Run the test suite:

```powershell
.venv\Scripts\python.exe -m unittest discover -s tests -p "test_*.py"
```

Compile all Python sources without starting the bot:

```powershell
.venv\Scripts\python.exe -m compileall -q main.py src scripts tests
```

Check a configured running environment:

```powershell
.venv\Scripts\python.exe scripts/health_check.py
```

The health check loads `.env` and `config.yaml`, checks local filesystem access and memory use, and calls configured Gemini services. It therefore requires valid credentials and network access for a fully successful result.

## Project layout

```text
.
|-- main.py                    Application entry point
|-- config.yaml               Non-secret runtime settings
|-- requirements.in           Direct dependency declarations
|-- requirements.txt          Constrained installation entry point
|-- constraints.txt           Validated exact dependency snapshot
|-- start.bat / start.sh       Local environment setup and launch
|-- src/
|   |-- bot/                   Discord events and slash commands
|   |-- services/              Gemini, retrieval, rendering, reports, and persistence
|   |-- models/                Shared dataclasses
|   `-- utils/                 Logging, errors, markdown, images, and token helpers
|-- tests/                     Standard-library unittest suite
|-- scripts/                   Health check and cache cleanup utilities
|-- docs/                      Technical-debt planning and register
|-- BOT_SYSTEM_REPORT.md       Architecture and message-pipeline report
|-- pipeline.html              Pipeline diagram
`-- message-sequence-flowchart.html
```

For architecture detail, see `BOT_SYSTEM_REPORT.md`, `pipeline.html`, and `message-sequence-flowchart.html`.
