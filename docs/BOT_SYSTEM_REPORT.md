> # STALE - READ WITH CARE
>
> **Reviewed 2026-07-29** at branch `dev`, HEAD `c83f740`, on `.venv/bin/python`
> (CPython 3.12.13). This report is dated **2026-06-17** and predates the four tech-debt sprints
> that landed on 2026-07-12 and 2026-07-13. **The body below has not been rewritten.** Its
> per-module descriptions of surviving modules are largely still accurate, but its architecture
> narrative describes the pre-sprint shape of the codebase and its verification line is wrong in
> both halves. It also documents `help_system.py` (at `:112`) and `HelpSystem` (at `:745`), both
> deleted on 2026-07-29 as verified-dead code; treat every reference to them as historical.
>
> **Banner refreshed after `9894bcc` ("Remove verified dead code from the source tree") and
> `4baa29c` ("Drop five unused runtime dependencies") landed on `dev`.** Where those commits
> invalidated a row below, it is re-measured against the new HEAD and says so; every other figure
> is still as of `c83f740`. The body itself remains untouched.
>
> **Known-wrong claims, each re-verified on 2026-07-29:**
>
> | Line | Claim as printed | Reality at `c83f740` |
> |---|---|---|
> | 16 | "`python -m pytest -q` passed: **9 tests**." | Wrong on both counts. pytest is not configured anywhere (no `pyproject.toml`, `setup.cfg`, `pytest.ini`, or `tox.ini`; pytest appears in neither `requirements.in` nor `constraints.txt`), and async tests would error without `pytest-asyncio`. The suite is **401 stdlib `unittest` tests in 37 files** (125 in 13 files when this banner was written at `c83f740`), run from the repository root with `python -m unittest discover -s tests -p "test_*.py"`. |
> | 21 | "Most durable application state is stored in **one** SQLite database path from `config.token_db_path`." | There are now **two** databases, `data/token_usage.db` and `data/message_rag.db`, and configuration validation *requires* them to be distinct (`config_helpers.py:513`; the check was at `:525` when this banner was written, and `9894bcc` shifted it). RAG and pin state lives in the second. |
> | 27, 52 | "`commands.py` registers slash commands and creates several persistence services." | `src/bot/commands.py` is now a **35-line compositor** (`:46-80`; the range was `:57-91` when this banner was written, and `9894bcc` shifted it — the compositor itself is unchanged at 35 lines). Registration lives in the five registrar modules under `src/bot/command_modules/`. |
> | 71 | `user_experience_service.py` described as providing typing indicators, embeds, reaction feedback, and progress notifications. | The service is dormant, and `9894bcc` deleted the eight unreachable public methods this banner originally listed. The file is now **130 lines** (was 513) with **one** public method left, `cleanup_typing_indicators`, which cleans up a dict that nothing populates; the other three members are the private helpers `_task_error_handler`, `_close_typing_entry` and `_sweep_stale_typing_entries`. |
> | 83 | Module table lists `pipeline.html` and `message-sequence-flowchart.html` as "existing visual docs". | Neither file exists. `find . -name '*.html'` outside `.venv` returns zero results. |
> | 462 | `message_retrieval_events` described as retrieval observability. | The table exists and is written to, but **nothing ever reads it**, and it has no pruning or retention policy. |
> | 48-77 (module table) | 30 modules listed. | **15 of the 44** non-`__init__` production modules under `src/` are never mentioned anywhere in this document (45 when this banner was written; `9894bcc` deleted `services/help_system.py`, which the document *did* mention, so the unmentioned 15 are unchanged): `config_helpers.py`, `services/sqlite_utils.py`, `services/gemini_response_pipeline.py`, `bot/response_generation.py`, `bot/response_delivery.py`, `bot/live_message_coordinator.py`, `bot/rag_event_coordinator.py`, `bot/media_extraction.py`, and all seven `bot/command_modules/*.py`. All were created after this report's date. |
> | ~462 (table inventory) | 10 SQLite tables named. | **Omits `message_backfill_progress` and `rag_migrations`**, both of which exist today. |
> | 586 | "If over Discord's 2000-character limit, calls `_send_split_response()`." | `DiscordBot._send_split_response` (and its sibling `_send_simple_split_response`) had decayed into unreferenced back-compat wrappers and were **deleted in `9894bcc`**; `grep -rn _send_split_response src/` returns nothing. `_send_response_safely` at `:582` does survive, but only as a thin delegate (`discord_bot.py:1338`) to `ResponseDeliveryCoordinator.send_response_safely` (`response_delivery.py:119`), and the split path is now that coordinator's `send_split_response` (`:273`). Read step 3 as naming a behaviour that still exists on a different object. |
> | 614 | README "references `.env.example`, `config.yaml.example`, `Dockerfile`, `docker-compose.yml` ... that are not present". | Historical. The README has since been cleaned of all of these, and its project-layout section — which for a time still linked this report at the repository root alongside two deleted HTML files — has been repaired too: `README.md:234` now points at `docs/` and records that neither diagram exists. |
>
> The report contains **zero `file:line` citations**, so unlike `AGENTS.md` it carries no stale
> line-number debt. The only positional claims to maintain are the module and table inventories
> above.
>
> **Still accurate and worth reading:** the executive summary's list of remaining risks (in
> particular the ungated configuration commands and the unauthenticated report web UI), the
> per-module descriptions of modules that still exist, the slash-command inventory, and the
> `compileall` verification on line 17.
>
> **Current documents:** [`docs/BUG_ANALYSIS_2026-07-29.md`](BUG_ANALYSIS_2026-07-29.md),
> [`docs/IMPROVEMENT_ANALYSIS_2026-07-29.md`](IMPROVEMENT_ANALYSIS_2026-07-29.md),
> [`docs/ANALYSIS_BACKLOG.md`](ANALYSIS_BACKLOG.md), and
> [`docs/tech-debt-register.md`](tech-debt-register.md). Start with
> [`docs/README.md`](README.md).
>
> --- end of staleness banner; original document follows unchanged ---

# Discord AI Bot Codebase Report

Date: 2026-06-17

This report began as a full read-only review of the Discord AI bot repository. It has been updated with the implemented hybrid message RAG architecture added on 2026-06-17.

Subagent coverage:

- Startup, configuration, Discord lifecycle, and command routing.
- Core message processing, Gemini pipeline, media handling, prompts, rendering, and splitting.
- Persistence, reports, settings, preferences, visibility, pins, rate limiting, token tracking, help, and tests.
- Operations, dependencies, logging, errors, security/privacy, scripts, README, and existing diagrams.

Local verification performed after the RAG implementation:

- `python -m pytest -q` passed: 9 tests.
- `python -m compileall -q src tests scripts main.py` passed.

## Executive Summary

This is a Python `discord.py` bot centered on `src/bot/discord_bot.py`. It responds to DMs, direct mentions, replies to bot messages, role mentions for roles the bot has, and optional per-channel live mode. The bot uses Google Gemini through `google-genai` for text, context selection, multimodal inputs, Google Search grounding, and image generation/editing. Most durable application state is stored in one SQLite database path from `config.token_db_path`, defaulting to `data/token_usage.db`.

The high-level architecture is service-oriented:

- `main.py` loads `.env`, validates `config.yaml`, creates `DiscordBot`, and connects to Discord.
- `DiscordBot` owns the Discord lifecycle, message event handling, live mode, message indexing, hybrid context retrieval, media extraction, Gemini calls, token tracking, response rendering, and safe sending.
- `commands.py` registers slash commands and creates several persistence services that are attached back to the bot instance.
- `GeminiClient` handles text generation, routing support, context reranking, Gemini embeddings, search grounding, safety settings, thinking config, retry handling, and token extraction.
- `MessageIndexService`, `HybridContextRetriever`, and `ContextPackBuilder` implement the local SQLite hybrid RAG system.
- `ImageProcessingService` and `NanoBananaClient` handle queued image edits and direct text-to-image generation.
- Support services handle reports, local report web UI, user preferences, channel settings, pins, message hide/unhide, token leaderboard, rate limiting, UX helpers, and help content.

Main remaining risks found:

1. Several global configuration/dev commands are not permission-gated, including `/config model`, `/config deepsearch`, `/config debug`, `/config image-generation`, and `/dev`.
2. Some logs still include raw exception text and operational metadata; prompt text, API key fragments, model output previews, message text, and uploaded file previews have been redacted in the main message path.
3. The report web UI has no authentication or CSRF protection. It is safe only while bound to localhost.
4. `scripts/health_check.py` imports the removed `google.generativeai` SDK even though `requirements.txt` now installs `google-genai`.
5. README/deployment docs reference files not present in the repo and contain stale behavior descriptions.

## Repository Map

| Path | Role |
| --- | --- |
| `main.py` | Process entrypoint. Loads env/config, validates startup connectivity, creates and starts `DiscordBot`. |
| `config.yaml` | Non-secret runtime configuration: bot settings, report web UI, logging, context windows, models, generation parameters, safety thresholds, image settings, rate limits, prompts, validation, misc limits. |
| `requirements.txt` | Python dependencies for Discord, Gemini, config loading, web UI, media/PDF processing, rendering, templates, logging, and health checks. |
| `src/config.py` | `BotConfig` dataclass, YAML/env loading, validation, token checks, logging setup, feature availability, startup connectivity checks. |
| `src/constants.py` | Fixed API limits and supported file/media constants. |
| `src/models/data_models.py` | Shared dataclasses and enums: `MessageContext`, `TokenUsage`, `APIResponse`, `EditType`, image edit request/result, validation result. |
| `src/bot/discord_bot.py` | Main `discord.Client` subclass. Owns service setup, Discord events, live mode, context handling, media extraction, AI calls, output sending, token usage recording. |
| `src/bot/commands.py` | Slash command registration and command handlers. Also creates channel settings, pin, visibility, and user preference services. |
| `src/bot/enhanced_command_handler.py` | Gemini router-based natural language intent/complexity classification and image edit/generation command handling. |
| `src/services/context_collector.py` | Fetches and formats recent channel/reply context as `MessageContext` objects. |
| `src/services/message_index_service.py` | SQLite message index for hybrid RAG: message metadata, FTS5 lexical index, embedding storage, retrieval metrics, backfill, and visibility state. |
| `src/services/hybrid_context_retriever.py` | Hybrid RAG coordinator for reply anchors, pinned memories, recent messages, FTS hits, semantic hits, fusion scoring, reranking, and fallback signaling. |
| `src/services/context_pack_builder.py` | Builds prompt-ready `MessageContext` packs with retrieval provenance and pinned-memory ordering. |
| `src/services/gemini_client.py` | Main Gemini text/multimodal client with context selector, prompt construction, search grounding, thinking config, safety settings, retries, and response parsing. |
| `src/services/nano_banana_client.py` | Gemini image model client for text-to-image and image editing. |
| `src/services/image_processing_service.py` | Queue, worker pool, image validation, image edit rate limiting, progress, job state, and image service health. |
| `src/services/message_splitter.py` | Smart long-message splitting with markdown/code-block preservation. |
| `src/services/content_renderer.py` | Post-processes AI output: LaTeX image rendering, inline math conversion, markdown table formatting. |
| `src/services/report_service.py` | SQLite-backed report tracking. |
| `src/services/report_web_server.py` | Local aiohttp report web UI and report status/admin note mutation. |
| `src/services/channel_settings_service.py` | SQLite-backed per-channel personality and live-mode settings. |
| `src/services/user_preferences_service.py` | SQLite-backed per-user preferred model and language. |
| `src/services/message_visibility_service.py` | Stores original bot message content for `/hide` and `/unhide`. |
| `src/services/pin_service.py` | Stores/list/deletes per-channel pinned memories. Pins are loaded by the hybrid RAG packer for prompt context. |
| `src/services/rate_limiter.py` | In-memory per-user text rate limiter for mention/live message paths. |
| `src/services/token_tracker.py` | SQLite-backed token usage event storage and leaderboard aggregation. |
| `src/services/user_experience_service.py` | Typing indicators, embeds, reaction feedback, progress/completion notifications, cleanup. |
| `src/services/help_system.py` | Static help sections and command suggestions. Appears dormant; no `/help` command is registered. |
| `src/utils/error_manager.py` | Central error categorization, user/technical messages, API/Discord/image/formatting error helpers, error reply fallback. |
| `src/utils/logging_config.py` | Structured logging, performance/image/message formatting loggers, timing context. |
| `src/utils/markdown_utils.py` | Markdown block parsing and safe split point helpers. |
| `src/utils/image_utils.py` | Image validation and supported format helpers. |
| `src/utils/token_extraction.py` | Shared Gemini usage metadata parser. |
| `scripts/health_check.py` | Operational health check for config, filesystem, memory, Gemini chat, and image service. Currently uses stale Gemini SDK import. |
| `scripts/cleanup_pycache.py` | Recursive cleanup tool for `__pycache__` and Python bytecode files. |
| `tests/test_report_service.py` | Unit tests for basic report creation/status update and invalid status rejection. |
| `grok-prompts/*.j2` | Prompt assets. Normal chat uses `config.yaml` prompts, not most files here. `/deepresearch` loads `default_deepsearch_final_summarizer_prompt.j2`. |
| `README.md` | User/operator documentation. Several sections are stale or reference missing artifacts. |
| `pipeline.html`, `message-sequence-flowchart.html` | Existing visual docs. They reflect some newer behavior than README but still do not replace source-of-truth code review. |

## System Flowchart

```mermaid
flowchart TD
  Start["Process start: start.bat/start.sh or python main.py"] --> Env["Load .env"]
  Env --> Config["Load config.yaml and env secrets into BotConfig"]
  Config --> Validate["Validate required tokens, models, prompts, limits"]
  Validate --> Logging["Configure logging"]
  Logging --> BotInit["Create DiscordBot"]

  BotInit --> Services["Initialize services: ErrorManager, TokenTracker, ReportService, ContextCollector, GeminiClient, MessageIndexService, ContextPackBuilder, HybridContextRetriever, MessageSplitter, ContentRenderer, UX, rate limiter"]
  Services --> ImageConfigured{"Nano Banana / Gemini image key configured?"}
  ImageConfigured -->|Yes| ImageService["Create ImageProcessingService and EnhancedCommandHandler"]
  ImageConfigured -->|No| NoImage["Image features disabled"]
  ImageService --> Ready
  NoImage --> Ready

  Ready["Discord on_ready"] --> StartOptional["Start image workers and report web UI if enabled"]
  StartOptional --> Presence["Set Discord presence"]
  Presence --> Commands["Register slash commands via setup_commands"]
  Commands --> Sync["Global app command sync"]
  Sync --> Listen["Listen for Discord messages and interactions"]

  Listen --> Msg["on_message"]
  Msg --> IgnoreBots{"Author is bot?"}
  IgnoreBots -->|Yes| Drop["Ignore"]
  IgnoreBots -->|No| IndexIncoming["Index readable incoming message into SQLite message_index and FTS5"]
  IndexIncoming --> LiveCheck{"Guild channel live mode enabled?"}

  LiveCheck -->|Yes| LiveQueue["Enqueue message in per-channel live queue"]
  LiveQueue --> LiveWorker["Live worker batches pending messages"]
  LiveWorker --> LiveRate{"Text rate limit ok?"}
  LiveRate -->|No| RateReply["Reply with rate-limit message"]
  LiveRate -->|Yes| LiveAttach{"Any attachments?"}
  LiveAttach -->|Yes| FullPipeline["Use full context/media pipeline with live overrides"]
  LiveAttach -->|No| LiveGemini["Generate short live response with rolling in-memory context"]
  LiveGemini --> TokenUsage["Record token usage if available"]
  TokenUsage --> SendSafe["Render, split, and send response safely"]

  LiveCheck -->|No| MentionCheck{"DM, direct mention, reply to bot, or bot role mention?"}
  MentionCheck -->|No| Drop
  MentionCheck -->|Yes| TextRate{"Text rate limit ok?"}
  TextRate -->|No| RateReply
  TextRate -->|Yes| Enhanced{"EnhancedCommandHandler available?"}

  Enhanced -->|Yes| Router["Gemini router classifies intent and complexity"]
  Enhanced -->|No| ExtractPrompt["Extract user prompt"]
  Router --> Intent{"Intent"}
  Intent -->|image_generate| ImageGen["Direct Gemini image generation via NanoBananaClient"]
  Intent -->|image_edit| ImageEdit["Queue image edit job via ImageProcessingService"]
  Intent -->|text/unknown| ExtractPrompt

  ImageGen --> ImageResult{"Image result ok?"}
  ImageEdit --> ImageWorkers["Image workers validate, rate-limit, call Gemini image model"]
  ImageWorkers --> ImageResult
  ImageResult -->|Yes| ImageReply["Reply with generated/edited image and token usage"]
  ImageResult -->|No| ErrorReply["Send user-friendly error"]

  ExtractPrompt --> EmptyPrompt{"Prompt or media present?"}
  EmptyPrompt -->|No| AskForPrompt["Ask user to include prompt/media"]
  EmptyPrompt -->|Yes| RAGEnabled{"Hybrid message RAG enabled?"}
  RAGEnabled -->|Yes| RAGBackfill["One-time recent channel backfill into message index"]
  RAGBackfill --> RAGRetrieve["Retrieve reply anchors, pins, recent messages, FTS5 hits, and embedding hits"]
  RAGRetrieve --> RAGFuse["Fuse scores with recency/reply/pin boosts"]
  RAGFuse --> RAGRerank["Optional Gemini router rerank of top fused candidates"]
  RAGRerank --> PackContext["Pack provenance-bearing context with pins first and strongest hits near user prompt"]
  RAGEnabled -->|No| Collect["Legacy: collect channel and reply context"]
  RAGRetrieve -->|Failure| Collect
  Collect --> SelectContext["Legacy: Gemini router selects relevant recent context by complexity limit"]
  SelectContext --> PackContext
  PackContext --> Media["Extract current/replied/context images, PDFs, audio, and text files"]
  Media --> Prefs["Apply channel personality and user language/model preferences"]
  Prefs --> Search{"Search enabled by forced DeepSearch or prompt heuristics?"}
  Search --> GeminiText["Gemini generate_response with prompt, selected context, media parts, safety settings, thinking config"]
  GeminiText --> Finish{"Gemini finish reason"}
  Finish -->|STOP or MAX_TOKENS with text| SuccessText["Add model/search headers, sources, token usage"]
  Finish -->|Safety, recitation, empty, timeout, exception| ErrorReply
  SuccessText --> TokenUsage

  SendSafe --> Renderer["ContentRenderer: LaTeX images, inline math, tables"]
  Renderer --> Long{"Over Discord limit?"}
  Long -->|Yes| Split["MessageSplitter and paginated embed"]
  Long -->|No| Reply["Discord reply"]
  Split --> IndexBotReply["Index bot response text for future RAG recall"]
  Reply --> IndexBotReply
  IndexBotReply --> Sources["Send grounding sources if present"]

  Listen --> Slash["Slash command interaction"]
  Slash --> CommandGroup["setup_commands callbacks"]
  CommandGroup --> ConfigCmds["Config/stats/dev/API usage/deepresearch/summarize"]
  CommandGroup --> PersistenceCmds["Reports, personality, live mode, pins, hide/unhide, preferences"]
  CommandGroup --> ImageSlash["edit-image and image-queue if image service exists"]
  PersistenceCmds --> SQLite["SQLite token_usage.db tables"]
  TokenUsage --> SQLite
  ConfigCmds --> RuntimeState["Mutable runtime state: model, thinking, search, debug, image_generation_enabled"]
```

## Startup And Runtime Lifecycle

Startup begins in `start.bat`, `start.sh`, or directly through `python main.py`.

1. Startup scripts enter the project root, create or reuse `.venv`, optionally remove/recreate `.venv` with `--rebuild`, install `requirements.txt`, then run `main.py`.
2. `main.py` calls `load_dotenv()`, prints early startup banners, loads and validates config, then calls `validate_startup_connectivity(config)`.
3. `load_and_validate_config()` in `src/config.py` loads `config.yaml`, reads secrets from env, validates required values, validates token lengths, sets up logging, and prints feature availability.
4. `DiscordBot(config)` initializes Discord intents, services, optional image processing, enhanced routing, a command tree, live-mode state, and the per-user text rate limiter.
5. `bot.start(config.discord_token)` connects to Discord.
6. `on_ready()` starts optional image workers, starts the report web UI if enabled, sets bot presence, calls `setup_commands()`, and globally syncs commands.
7. `close()` stops report web UI, image processing workers, and UX typing indicators before closing the Discord client.

Important lifecycle concern: `on_ready()` calls `setup_commands()` every time Discord fires ready. On reconnect/resume patterns, this can attempt to re-add commands and services to the same command tree again. A guard such as `self._commands_registered` would make startup idempotent.

## Configuration Model

Secrets come from `.env` / process environment:

- Required: `DISCORD_BOT_TOKEN`, `GEMINI_API_KEY`.
- Optional: `NANO_BANANA_API_KEY`, defaulting to `GEMINI_API_KEY`; `TOKEN_DB_PATH`; `LOG_FILE`.

Everything else is expected in repo-root `config.yaml`. There is no runtime config-path override in `main.py` or `BotConfig.from_yaml()` beyond calling the classmethod manually.

Key config sections:

- `bot`: dev mode, token DB path.
- `reports`: local report web UI toggle, host, port.
- `logging`: level, file, performance logging.
- `context`: channel/reply context windows and max context images.
- `rag`: local hybrid message retrieval, embedding model, scope, backfill, candidate counts, reranking, recency decay, and context caps.
- `response`: timeouts and retries.
- `messages`: Discord split lengths and code-block preservation.
- `ux`: typing indicators, rich embeds, reactions, suggestions.
- `models`: default/router/valid models, display names, thinking backend, router cache.
- `model_complexity`: low/medium/high model and thinking-level mapping.
- `generation`: Gemini sampling and max output tokens.
- `safety`: Gemini safety thresholds.
- `image_processing`, `rate_limiting`, `nano_banana`, `languages`, `personalities`, `system_prompts`, `validation`, `misc`.

Configuration risks:

- `NANO_BANANA_API_KEY` defaults to `GEMINI_API_KEY`, so image services appear configured whenever text Gemini is configured.
- `validate_startup_connectivity()` always returns `True`, even if checks fail; startup never blocks on actual external service connectivity.
- The README says Python 3.8+, but code uses newer type syntax that is safer to document as Python 3.10+.

## Message Processing Flow

### Trigger Filtering

`DiscordBot.on_message()` handles normal Discord messages:

1. Ignore messages from bot users.
2. If live mode is enabled for the channel, enqueue the message for channel-specific live processing.
3. Otherwise require one of:
   - private DM,
   - direct bot mention,
   - reply to a bot message,
   - role mention where the bot has that non-default role.
4. Apply in-memory text rate limiting.
5. If enhanced command handling exists, route message through the Gemini router for intent and complexity.
6. If not handled as image generation/editing, extract prompt, collect context, and generate a text response.

### Live Mode

Live mode is a per-channel setting stored in SQLite by `ChannelSettingsService`. When enabled, guild messages in that channel bypass mention requirements.

Live mode behavior:

- Uses a per-channel queue and task to batch pending messages.
- Applies the same text rate limiter to the last message author.
- Keeps rolling in-memory live context with up to `self._live_turn_window * 2` entries.
- Text-only live responses now merge hybrid RAG context with the rolling in-memory buffer when RAG is enabled.
- Uses `config.router_model_name` as `_live_model_name`.
- Forces short prompt mode and disables search for live responses.
- If attachments are present, falls back into the full `_process_message_with_context()` media path with live-specific overrides.

### Enhanced Router

`EnhancedCommandHandler` is only created when image processing is available. It uses Gemini to classify:

- `image_generate`
- `image_edit`
- `text`

It also classifies complexity:

- `low`
- `medium`
- `high`

Router decisions are cached in an LRU-like in-memory cache keyed by normalized message content plus a boolean for image attachments. On router failure, the fallback is unknown intent and low complexity.

Behavioral implication: if image processing fails or is not configured, the enhanced router is unavailable, so text complexity routing also disappears and normal messages default to low complexity.

## Text AI Pipeline

The normal text path is:

1. `_extract_user_prompt()` removes bot mentions, `@everyone`, and `@here`.
2. `_process_message_with_context()` chooses context size from low/medium/high complexity.
3. `ContextCollector.get_channel_context()` fetches recent channel messages, skips bots, applies the cutoff window, adds attachment/embed/sticker/forwarded metadata as text, and returns chronological `MessageContext` records.
4. If the user message is a reply, `get_reply_context()` fetches the replied-to message plus before/after windows, then de-duplicates against standard channel context.
5. `GeminiClient.select_relevant_context()` asks the router model to return JSON `selected_message_ids`, then finalizes the selected set with anchor messages and a bounded max count.
6. `_generate_and_send_response()` extracts media/files:
   - current and replied images,
   - PDFs rendered to images with PyMuPDF,
   - image attachments from selected context messages,
   - audio/voice files as bytes plus MIME type,
   - non-image text files decoded into prompt text.
7. Channel personality and user preferences are applied.
8. `GeminiClient.generate_response()` formats the prompt with system instructions, selected context, image mapping, language/personality instructions, and user prompt.
9. Gemini receives text plus image/audio `Part`s, optional Google Search tool, safety settings, thinking config, and max output tokens selected by complexity.
10. Response finish reason is parsed. Successful text gets a model header and optional search-grounding header/sources.
11. Token usage is extracted and persisted.
12. `ContentRenderer` post-processes LaTeX and tables.
13. Long content is split through `MessageSplitter` and sent as a paginated embed; shorter content is sent as a direct Discord reply.

Search and multimodal behavior: if search is requested for a multimodal request, `_generate_response_async()` disables search for that single request so images/audio/files are preserved instead of being silently dropped.

## Message RAG / Context System

The response path now uses a local hybrid message RAG system before falling back to the previous recent-history selector. The old `ContextCollector` remains available for reply-context fetches, legacy fallback, and context media extraction, but it is no longer the only source of conversation memory.

### Persistent Message Index

`MessageIndexService` stores readable Discord messages in the existing SQLite database path from `config.token_db_path`.

- Incoming non-bot messages are indexed in `DiscordBot.on_message()` before live-mode and mention filtering, so unmentioned channel history can later be retrieved.
- Bot responses are indexed after a successful safe send when `rag.index_bot_responses` is enabled.
- `/hide` marks hidden bot messages as hidden in the index; `/unhide` restores them to the FTS index.
- `/rag backfill` scans recent channel history and stores messages in the same index.
- Embeddings are generated opportunistically for pending indexed messages during retrieval. Until embeddings exist, recent and FTS retrieval still work.

### Hybrid Retrieval Flow

```mermaid
flowchart TD
  Incoming["Discord message received"] --> Index["Index incoming non-bot message"]
  Index --> Trigger{"Live mode or bot mention/reply?"}
  Trigger -->|No| Idle["No response; message remains indexed"]
  Trigger -->|Yes| Query["Extract user prompt and request complexity"]
  Query --> Backfill["Backfill recent channel history once per channel"]
  Backfill --> Pins["Load pinned memories"]
  Backfill --> Reply["Fetch direct reply/thread anchors"]
  Backfill --> Recent["Load recent indexed messages"]
  Backfill --> FTS["Run SQLite FTS5 lexical search"]
  Backfill --> Embeddings["Embed pending docs and current query, then run semantic search"]
  Pins --> Fuse["Merge candidate pool"]
  Reply --> Fuse
  Recent --> Fuse
  FTS --> Fuse
  Embeddings --> Fuse
  Fuse --> Score["Apply source weights, recency decay, reply boost, and pin priority"]
  Score --> Rerank["Optional Gemini router rerank"]
  Rerank --> Pack["ContextPackBuilder creates prompt pack with provenance"]
  Pack --> Prompt["GeminiClient.format_prompt renders RAG context"]
  Prompt --> Generate["Gemini response generation"]
  Generate --> Send["Send response safely"]
  Send --> IndexReply["Index bot response for future recall"]
```

### Prompt Assembly

`ContextPackBuilder` and `GeminiClient.format_prompt()` now produce a provenance-bearing RAG section:

- pinned memories first,
- direct reply/thread anchors next,
- retrieved recent/lexical/semantic hits afterward,
- lower-confidence retrieved items before higher-confidence items so the strongest evidence is closest to the final user prompt,
- each item includes `message_id`, timestamp, retrieval source, score, and reason where available.

The response model receives compact context rather than a raw channel dump. Media extraction still uses the selected context message IDs to pull image attachments from Discord history.

### Failure And Fallback Behavior

Hybrid retrieval is fail-open:

- If SQLite retrieval, embedding generation, or reranking raises unexpectedly, `_process_message_with_context()` logs the failure and falls through to the legacy `ContextCollector` plus `GeminiClient.select_relevant_context()` path.
- If Gemini embeddings are unavailable, lexical/recent retrieval still runs.
- Transient embedding failures stay retryable with bounded backoff before a row is marked failed.
- If FTS5 is unavailable in SQLite, semantic/recent/pin/reply retrieval still runs.
- If the router reranker returns no useful selection, fused ranking remains the retrieval source.

### Privacy, Scope, And Logging

- Retrieval is scoped to the current channel by default. Same-guild cross-channel retrieval is disabled unless `rag.cross_channel_enabled` is set.
- Retrieval metrics store selected IDs, query length, fallback reason, and latency, not raw prompts or raw message text.
- The main message path no longer logs raw mention content, raw prompt previews, model output previews, uploaded file previews, or API key fragments.
- Pinned memories are explicit per-channel persistent context and are included only for the current channel.

### Architecture Change Log

- Added `MessageIndexService` with additive SQLite tables: `message_index`, `message_search_fts`, `message_embeddings`, and `message_retrieval_events`.
- Added `HybridContextRetriever` for reply anchors, pins, recent messages, lexical search, semantic search, score fusion, optional reranking, and retrieval metrics.
- Added `ContextPackBuilder` and extended `MessageContext` with retrieval metadata.
- Added `rag` configuration defaults in `config.yaml` and `BotConfig`.
- Added admin-only `/rag status` and `/rag backfill`.
- Replaced shared per-user model mutation with request-scoped model overrides.
- Preserved multimodal parts by disabling web search for search-triggered multimodal requests instead of dropping images/audio.
- Wired bot response indexing and pin prompt usage into the main and live response paths.
- Added edit/delete synchronization so deleted messages are removed from retrieval and edited messages refresh the index.
- Added bounded embedding retry metadata and channel-scoped RAG status counts.

## Image Generation And Editing

There are two image entry points:

- Natural language mention path through `EnhancedCommandHandler`.
- Slash command `/edit-image` when image processing service is available.

Image editing flow:

1. User attaches an image and asks for an edit.
2. Router classifies `image_edit` and optional `EditType`.
3. `ImageEditRequest` is created with user, bytes, instruction, edit type, timestamp, channel.
4. `ImageProcessingService.process_image_edit()` requires the service to be running, validates request, checks hourly per-user image limit, creates a job, stores it, and enqueues it.
5. Worker loops pull jobs, update progress, validate image bytes, call `NanoBananaClient.edit_image()`, convert response into `ImageEditResult`, update stats, set completion event, and retain recent completed jobs.
6. Discord handler waits on job completion and replies with the edited image or error.

Image generation flow:

1. Router classifies `image_generate`.
2. If no attached image and `bot.image_generation_enabled` is true, `handle_image_generation_command()` calls `image_processing_service.client.edit_image(image_data=None, ...)` directly.
3. `NanoBananaClient` builds `Generate an image: ...`, requests `response_modalities=['Image']`, extracts `inline_data` or `blob`, and returns image bytes.

Image risks:

- Text-to-image bypasses the queue, worker concurrency, and per-user hourly image edit limit.
- `NanoBananaClient.timeout` is stored but not applied around `generate_content`, so calls plus retries can exceed expected command/job timeouts.
- `scripts/health_check.py` constructs `NanoBananaClient(api_key=api_key, timeout=10)` without the required `model_name` argument in the reviewed code, which is another health-check compatibility issue.

## Slash Commands

Commands are registered inside `setup_commands()` and synced globally in `on_ready()`.

Current command surface:

- `/ping`: latency/status.
- `/report`, `/report-status`: report creation/status lookup.
- `/config model`: set global Gemini model.
- `/config thinking`: set global thinking-level override.
- `/config deepsearch`: force Google Search globally.
- `/config image-generation`: toggle runtime image generation.
- `/config debug`: set root logger level.
- `/config info`: current configuration and health.
- `/stats`: bot uptime/performance/API/token summary.
- `/token-leaderboard`: guild token leaderboard.
- `/features`: static feature discovery embed.
- `/edit-image`: slash image editing, only if image service exists.
- `/image-queue`: image queue and rate-limit snapshot, only if image service exists.
- `/clear-cache`: admin-only performance cache clear.
- `/dev`: toggle developer mode. Admin decorator is commented out.
- `/api-usage`: API/rate-limit/performance summary.
- `/usage-report`: attaches CSV and Markdown usage reports since startup.
- `/deepresearch`: search research phase plus template-based synthesis into a Markdown file.
- `/rag status`, `/rag backfill`: admin-only local message RAG status and channel-history indexing.
- `/summarize`: summarize recent channel conversation.
- `/personality`, `/personality-info`: per-channel personality.
- `/live`: per-channel mention-free mode.
- `/pin`, `/pins`: per-channel pinned memories and deletion buttons.
- `/hide`, `/unhide`: replace recent bot messages with `.` and restore original content.
- `/preferences model`, `/preferences language`, `/preferences show`, `/preferences clear`: per-user preferences.

Permission concern: only `/clear-cache` is explicitly admin-gated. Several commands mutate global runtime behavior and should likely be restricted to administrators or bot managers.

## Persistence And State

Most persistent services share `data/token_usage.db` unless `TOKEN_DB_PATH` or `bot.token_db_path` overrides it.

| Table | Service | Purpose |
| --- | --- | --- |
| `token_usage` | `TokenTracker` | Per-request token usage, user/guild names, leaderboard aggregation. |
| `bot_reports` | `ReportService` | User-submitted issues/features, status, admin notes. |
| `channel_settings` | `ChannelSettingsService` | Channel personality and live-mode flag. |
| `user_preferences` | `UserPreferencesService` | Per-user preferred model/language. |
| `hidden_messages` | `MessageVisibilityService` | Original content for bot messages hidden by `/hide`. |
| `pinned_messages` | `PinService` | Per-channel pinned memory text. |
| `message_index` | `MessageIndexService` | Indexed Discord message metadata and normalized searchable text for local RAG. |
| `message_search_fts` | `MessageIndexService` | SQLite FTS5 lexical index over message text, author names, and attachment summaries. |
| `message_embeddings` | `MessageIndexService` | Stored Gemini embedding vectors and embedding status for semantic retrieval. |
| `message_retrieval_events` | `MessageIndexService` | Retrieval observability: selected IDs, query length, fallback reason, and latency without raw prompt text. |

In-memory state:

- Text rate limiter request windows.
- Gemini current model, complexity level, thinking override, force-search flag.
- Router decision cache.
- Image jobs, queue, active/completed jobs, image stats.
- Live-mode per-channel locks, tasks, pending messages, rolling context.
- Performance logger counters.
- Active UX typing entries.

Persistence concerns:

- SQLite writes are synchronous in several async command paths.
- Most DB services swallow exceptions and return defaults/false, which keeps the bot alive but can hide persistence failures.
- Token rows, reports, pins, hidden message originals, and admin notes have no retention policy.
- Token leaderboard groups by `user_id`, `username`, `guild_id`, and `guild_name`; renamed users/guilds can split leaderboard entries.
- `/hide` stores only message content. Embeds, attachments, stickers, and other message fields are not restored.
- `/pin` stores memory and the hybrid RAG path injects current-channel pins into prompt context.

## External API Use

Text/multimodal Gemini:

- SDK: `google-genai`.
- Client: `google.genai.Client(api_key=...)`.
- Used for:
  - final text/multimodal responses,
  - relevance-based context selection,
  - enhanced intent/complexity routing,
  - Google Search grounding,
  - deep research search/synthesis.

Gemini image model:

- Same SDK family.
- Used for:
  - image generation,
  - image editing,
  - health/status based on client availability and consecutive failures.

Discord:

- `discord.py` with `message_content` and `messages` intents.
- Uses message history, attachments, replies, slash commands, embeds, files, typing, reactions, and global app command sync.

Local web:

- `aiohttp` report web UI on `reports.web_host` and `reports.web_port`.

## Prompting And Templates

Normal chat prompt construction uses `config.yaml` system prompts:

- `system_prompt_low_complexity`
- `system_prompt_medium_complexity`
- `system_prompt_high_complexity`

`GeminiClient.format_prompt()` adds:

- system instruction by complexity,
- optional channel personality,
- optional language instruction,
- selected conversation context,
- image context mapping,
- current user message.

`grok-prompts/default_deepsearch_final_summarizer_prompt.j2` is used by `/deepresearch`. The other `grok-prompts/*.j2` files appear to be prompt assets not currently used by the normal message path.

Behavioral mismatch: README/config text still describes visible chain-of-thought style thinking mode in places, but the current code uses native Gemini thinking config and explicitly does no custom `<thinking>` parsing.

## Rendering, Splitting, And Discord Output

`_send_response_safely()` protects the Discord send path:

1. Rejects empty output.
2. Runs `ContentRenderer.process_response()`.
3. If over Discord's 2000-character limit, calls `_send_split_response()`.
4. Otherwise replies directly with optional attachments.
5. Sends grounding sources separately if present.
6. Falls back through detailed Discord error handling.

`ContentRenderer`:

- wraps raw LaTeX,
- renders block/complex LaTeX into one attached image,
- converts simple inline math to Unicode approximations,
- formats markdown tables as code blocks.

`MessageSplitter`:

- finds natural boundaries,
- preserves markdown/code blocks,
- adds continuation indicators,
- validates split integrity.

Current long responses are sent as paginated embeds, not flat sequential messages. README still describes older flat splitting behavior.

## Error Handling And Logging

`ErrorManager` centralizes:

- exception categorization,
- user-friendly messages,
- technical messages,
- API error handling,
- Discord error handling,
- image/formatting error helpers,
- error response fallback: reply, channel send, reaction.

Logging features:

- structured console/file logging,
- rotating file handlers,
- performance logging,
- API/message/image/splitting stats,
- contextual logger adapters with IDs.

Main logging/privacy risks:

- Partial Gemini API key values are logged in `GeminiClient`.
- Prompt, context, output snippets, file previews, and user IDs are logged.
- Developer mode can send stack traces to Discord.
- `create_error_context(... include_error_details=True)` can append raw exception text to user messages in normal mode.
- `setup_logging()` clears root handlers and can duplicate performance handlers on repeated setup.

## Operational Scripts And Deployment

`start.bat` and `start.sh`:

- create/reuse `.venv`,
- install requirements,
- optionally rebuild the venv,
- run `main.py`.

`scripts/cleanup_pycache.py`:

- recursively removes `__pycache__` directories and `.pyc/.pyo` files,
- skips common generated directories by default,
- can target arbitrary roots with `--root` and has no dry-run/confirmation mode.

`scripts/health_check.py`:

- checks config, filesystem, memory, Gemini chat, and image service,
- creates `logs/` and `temp/` if missing,
- performs a real external Gemini model-list call,
- currently imports stale `google.generativeai` while requirements use `google-genai`,
- reports raw exception strings.

README deployment drift:

- References `.env.example`, `config.yaml.example`, `Dockerfile`, `docker-compose.yml`, docker scripts, and extra docs that are not present in the repository listing.
- Recommends `env | grep -E 'GEMINI|DISCORD'`, which can print secrets.
- Documents `/config thinking <true/false>`, but code uses choices `default/minimal/low/medium/high`.
- Documents model names differently from `config.yaml`.
- Omits newer commands/data behavior such as `/pin`, `/hide`, `/unhide`, `/config image-generation`, and hidden/pinned tables.

## Tests And Coverage

Existing tests:

- `tests/test_report_service.py`
  - create/get/update report,
  - invalid status rejection.
- `tests/test_message_rag_services.py`
  - FTS lexical search,
  - semantic vector scoring,
  - hidden/deleted message exclusion,
  - retryable embedding failure state,
  - channel-scoped RAG status counts,
  - context-pack pin ordering and limits.

Local result:

- `9 passed in 5.37s`.
- Compile check succeeded.

Coverage gaps:

- Discord message routing and trigger filtering.
- Live-mode queue/rolling context behavior.
- Context selection fallbacks.
- Gemini response finish-reason handling.
- Hybrid retriever integration against mocked Discord/Gemini failures.
- Media extraction for images, PDFs, audio, and text files.
- Message splitting and renderer behavior.
- Error redaction and dev-mode behavior.
- Command permissions.
- Global runtime control permissions.
- Channel settings and live mode persistence.
- Token tracker and rate limiter edge cases.
- Report web UI routes/auth assumptions.
- Health-check compatibility.
- README/docs consistency.

## Risk Register

### High

1. Ungated global runtime controls
   - `/config model`, `/config thinking`, `/config deepsearch`, `/config debug`, `/config image-generation`, and `/dev` can change bot-wide behavior.
   - `/dev` has the admin permission decorator commented out.
   - Impact: any user can degrade privacy, change cost/performance, or expose stack traces.

2. Shared mutable Gemini runtime controls
   - Per-user preferences now use request-scoped model overrides, but global `/config model`, `/config thinking`, and `/config deepsearch` still mutate shared bot-wide runtime state.
   - Impact: authorized-or-unauthorized command use can change behavior for all users until changed again.
   - Preferred design: permission-gate global controls and consider persisting bot defaults separately from per-request overrides.

3. Sensitive logging
   - The main message path now redacts API key fragments, prompt/output previews, raw mention content, and uploaded file previews.
   - Raw exceptions and some operational metadata can still be logged.
   - Impact: logs remain operationally sensitive and should be treated as restricted data.

4. Unauthenticated report web UI
   - `ReportWebServer` allows status/admin-note mutation through POST.
   - Default host is localhost, but config can bind externally.
   - Impact: if exposed, anyone with network access can alter reports.

5. Broken health-check dependency path
   - Requirements use `google-genai`; health check uses removed `google.generativeai`.
   - Impact: clean installs may report unhealthy even when the actual bot dependencies are correct.

### Medium

1. Command registration is not idempotent
   - `setup_commands()` runs inside `on_ready()`.
   - Impact: reconnects can cause duplicate command registration or setup failures.

2. Image generation bypasses image edit queue/limit path
   - Direct `NanoBananaClient.edit_image(None, ...)` call skips queue and per-user image edit limit.
   - Impact: inconsistent rate/concurrency behavior.

3. Image client timeout is not enforced
   - `NanoBananaClient.timeout` is stored but not wrapped around SDK calls.
   - Impact: image calls plus retries can exceed expected Discord command/job time.

4. Synchronous SQLite in async paths
   - The new RAG service uses async wrappers around thread offloading, but several older services still use direct sqlite calls inside command handlers.
   - Impact: slow disk or lock contention can block the event loop.

5. Help system is dormant
   - `HelpSystem` exists and content references `/help`, but no `/help` command is registered.
   - Impact: user-facing help text is stale/inaccessible.

6. Data retention is undefined
   - Hidden message originals, pins, token usage, reports, admin notes, and indexed RAG messages are retained indefinitely.
   - Impact: privacy and storage growth risk.

### Low

1. `.gitignore` rotated logs and `.gitkeep`
   - Existing ignore patterns may not cover rotated logs such as `bot.log.1`.
   - `data/` ignore plus `!data/.gitkeep` may need `!data/` to reliably unignore `.gitkeep`.

2. Unused dependencies
   - Some packages in `requirements.txt` were not found in import scans.
   - Impact: slower installs and larger dependency surface.

3. README and diagrams drift
   - Existing docs disagree with code and config in command behavior, model names, setup artifacts, and data tables.

4. Eager package imports
   - Package `__init__.py` files import concrete runtime classes/services.
   - Impact: importing a package can require heavy optional dependencies.

## Recommended Follow-Up Plan

1. Lock down command permissions.
   - Add admin/default permission checks for global configuration, debug/dev, image-generation toggle, and destructive visibility controls.

2. Finish runtime-state hardening.
   - Per-user model preferences are request-scoped now.
   - Permission-gate or persist bot-wide `/config model`, `/config thinking`, and `/config deepsearch` changes intentionally.

3. Redact sensitive logs.
   - Remove API key fragments.
   - Stop logging prompt/context/output/file previews at info level.
   - Gate detailed traces to local logs only, not Discord replies.

4. Add retention and pruning.
   - Define TTLs or admin cleanup commands for RAG-indexed messages, hidden message originals, pins, reports, and token usage.

5. Fix health check.
   - Use `google-genai` consistently.
   - Add explicit timeouts.
   - Pass configured image model to `NanoBananaClient`.

6. Make `on_ready()` command setup idempotent.
   - Track whether commands and attached services were already initialized.

7. Add tests around high-risk logic.
   - Command permission checks.
   - global runtime permission checks.
   - RAG fallback behavior with mocked Discord/Gemini failures.
   - error redaction.
   - report web UI routes.
   - token tracker aggregation.
   - rate limiter windows.
   - message splitting/rendering.

8. Update README and diagrams.
   - Remove missing Docker/setup references or add the files.
   - Document current model names, thinking choices, live mode, pins, hide/unhide, report web UI, and data retention.

## Bottom Line

The bot now has a persistent local hybrid RAG memory with channel-scoped retrieval, pins, bot-response recall, edit/delete synchronization, and legacy fallback. Remaining production concerns are mostly around permission-gating global runtime controls, retention policy, report-web hardening, stale health/deployment docs, and broader integration coverage.
