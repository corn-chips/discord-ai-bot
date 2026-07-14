#!/bin/sh
# Start script for Discord Bot (Linux/macOS)
# Usage: sh start.sh
#   Flags:
#     --rebuild    Force recreate the venv from scratch

set -e

# Resolve project root (same directory as this script)
PROJECT_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$PROJECT_DIR"

REBUILD=0
if [ "${1:-}" = "--rebuild" ]; then
    REBUILD=1
fi

# Find a working Python 3.12 binary
find_python() {
    for cmd in python3.12 python3 python; do
        if command -v "$cmd" >/dev/null 2>&1; then
            # Match the version used to validate constraints.txt.
            if "$cmd" -c "import sys; sys.exit(0 if sys.version_info[:2] == (3, 12) else 1)" 2>/dev/null; then
                echo "$cmd"
                return 0
            fi
        fi
    done
    echo ""
    return 1
}

VENV_PYTHON=".venv/bin/python"
PYTHON=""
if [ "$REBUILD" = "1" ] || [ ! -f "$VENV_PYTHON" ]; then
    PYTHON="$(find_python)" || true
    if [ -z "$PYTHON" ]; then
        echo "Error: Python 3.12 not found through python3.12, python3, or python."
        exit 1
    fi
fi

# If --rebuild, remove the venv only after a replacement interpreter is ready.
if [ "$REBUILD" = "1" ]; then
    echo "Removing existing venv..."
    rm -rf .venv
    if [ -e .venv ]; then
        echo "Error: Failed to remove the existing virtual environment."
        exit 1
    fi
fi

# Create venv if it doesn't exist
if [ ! -f "$VENV_PYTHON" ]; then
    echo "Creating virtual environment..."

    # Clean up stale Python caches
    echo "Cleaning Python caches..."
    find . -type d -name "__pycache__" -exec rm -rf {} + 2>/dev/null || true
    find . -type f -name "*.pyc" -delete 2>/dev/null || true
    rm -rf .mypy_cache .pytest_cache

    "$PYTHON" -m venv .venv

    echo "Installing dependencies..."
    "$VENV_PYTHON" -m pip install --upgrade pip
    .venv/bin/pip install -r requirements.txt

    echo "Virtual environment ready."
else
    echo "Using existing virtual environment."
fi

if ! "$VENV_PYTHON" -c "import sys; sys.exit(0 if sys.version_info[:2] == (3, 12) else 1)"; then
    echo "Error: The existing virtual environment does not use Python 3.12."
    echo "Run sh start.sh --rebuild after installing Python 3.12."
    exit 1
fi

echo "Starting bot..."
exec .venv/bin/python main.py
