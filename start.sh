#!/bin/sh
# Start script for Discord Bot (Linux/macOS)
# Usage: ./start.sh
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

# Find a working Python 3 binary
find_python() {
    for cmd in python3 python; do
        if command -v "$cmd" >/dev/null 2>&1; then
            # Verify it's Python 3
            if "$cmd" -c "import sys; sys.exit(0 if sys.version_info[0] >= 3 else 1)" 2>/dev/null; then
                echo "$cmd"
                return 0
            fi
        fi
    done
    echo ""
    return 1
}

PYTHON="$(find_python)" || true
if [ -z "$PYTHON" ]; then
    echo "Error: Python 3 not found. Please install Python 3.8+ and try again."
    exit 1
fi

# If --rebuild, remove existing venv
if [ "$REBUILD" = "1" ]; then
    echo "Removing existing venv..."
    rm -rf .venv
fi

# Create venv if it doesn't exist
if [ ! -f ".venv/bin/python" ]; then
    echo "Creating virtual environment..."

    # Clean up stale Python caches
    echo "Cleaning Python caches..."
    find . -type d -name "__pycache__" -exec rm -rf {} + 2>/dev/null || true
    find . -type f -name "*.pyc" -delete 2>/dev/null || true
    rm -rf .mypy_cache .pytest_cache

    "$PYTHON" -m venv .venv

    echo "Installing dependencies..."
    .venv/bin/python -m pip install --upgrade pip
    .venv/bin/pip install -r requirements.txt

    echo "Virtual environment ready."
else
    echo "Using existing virtual environment."
fi

echo "Starting bot..."
exec .venv/bin/python main.py
