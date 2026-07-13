# Repository Guidelines

## Project Structure & Module Organization

`main.py` is the application entry point. Bot event handling and commands live in `src/bot/`; reusable application logic belongs in `src/services/`; shared dataclasses are in `src/models/`; and cross-cutting helpers are in `src/utils/`. Configuration loading is centralized in `src/config.py`, with non-secret runtime settings in `config.yaml`. Tests live in `tests/` and currently focus on service behavior. Operational utilities are under `scripts/`. Root-level HTML files and `BOT_SYSTEM_REPORT.md` document the message pipeline and architecture.

## Build, Test, and Development Commands

- `start.bat` (Windows) or `./start.sh` (Linux/macOS): create `.venv`, install dependencies when needed, and start the bot.
- `start.bat --rebuild` or `./start.sh --rebuild`: recreate the virtual environment before running.
- `python -m pip install -r requirements.txt`: install dependencies into an already activated environment.
- `python main.py`: run the configured bot directly.
- `python -m unittest discover -s tests -p "test_*.py"`: run the full test suite.
- `python scripts/health_check.py`: check a running instance and its configured services.

## Coding Style & Naming Conventions

Use Python 3.8+ and four-space indentation. Follow PEP 8: `snake_case` for modules, functions, and variables; `PascalCase` for classes; and `UPPER_SNAKE_CASE` for constants. Keep Discord-specific orchestration in `src/bot/` and move testable business logic into focused service classes. Prefer type hints, short docstrings for public behavior, and `logging` over `print` outside startup diagnostics. No formatter or linter is currently configured, so keep imports grouped and changes consistent with adjacent code.

## Testing Guidelines

Tests use the standard-library `unittest` framework. Name files `test_<feature>.py`, classes `<Subject>Test`, and methods `test_<expected_behavior>`. Use temporary directories or mocked clients for SQLite, Discord, and Gemini interactions; tests must not require real tokens or network access. Add regression tests for bug fixes and cover both success and validation/error paths. There is no enforced coverage threshold.

## Commit & Pull Request Guidelines

Recent commits use short, imperative summaries such as `Wire hybrid RAG into Discord bot flow` and `Document hybrid RAG architecture`. Keep each commit focused and describe the user-visible or architectural outcome. Pull requests should explain the change, configuration impact, and test command/results; link relevant issues and include screenshots for Discord UI, generated reports, or HTML diagram changes.

## Security & Configuration

Keep Discord and Gemini credentials only in `.env`; never commit tokens, generated databases, logs, or user message data. Put non-secret defaults in `config.yaml` and document new settings in `README.md`.
