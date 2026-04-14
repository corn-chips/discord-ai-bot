#!/usr/bin/env python3
"""
Clean Python bytecode caches from the repository.

By default, this script removes:
- __pycache__ directories
- *.pyc and *.pyo files

It skips common large/generated folders such as .git and .venv unless
explicitly requested.
"""

from __future__ import annotations

import argparse
import os
import shutil
import sys
from pathlib import Path

DEFAULT_EXCLUDED_DIRS = {
    ".git",
    ".venv",
    ".mypy_cache",
    ".pytest_cache",
    ".ruff_cache",
    "node_modules",
}


def clean_pycache(root: Path, include_venv: bool) -> tuple[int, int, list[str]]:
    removed_cache_dirs = 0
    removed_bytecode_files = 0
    errors: list[str] = []

    excluded_dirs = set(DEFAULT_EXCLUDED_DIRS)
    if include_venv:
        excluded_dirs.discard(".venv")

    for current_root, dirnames, filenames in os.walk(root, topdown=True):
        current_path = Path(current_root)

        # Prevent descending into excluded folders.
        dirnames[:] = [name for name in dirnames if name not in excluded_dirs]

        if "__pycache__" in dirnames:
            cache_path = current_path / "__pycache__"
            try:
                shutil.rmtree(cache_path)
                removed_cache_dirs += 1
            except OSError as exc:
                errors.append(f"{cache_path}: {exc}")
            dirnames.remove("__pycache__")

        for filename in filenames:
            if not filename.endswith((".pyc", ".pyo")):
                continue
            file_path = current_path / filename
            try:
                file_path.unlink()
                removed_bytecode_files += 1
            except OSError as exc:
                errors.append(f"{file_path}: {exc}")

    return removed_cache_dirs, removed_bytecode_files, errors


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Remove __pycache__ folders and Python bytecode files."
    )
    parser.add_argument(
        "--root",
        type=Path,
        default=Path(__file__).resolve().parents[1],
        help="Repository root to clean (default: project root).",
    )
    parser.add_argument(
        "--include-venv",
        action="store_true",
        help="Also clean caches inside .venv.",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    root = args.root.resolve()

    if not root.exists() or not root.is_dir():
        print(f"Error: invalid root directory: {root}", file=sys.stderr)
        return 1

    cache_dirs, bytecode_files, errors = clean_pycache(
        root=root, include_venv=args.include_venv
    )

    print(f"Cleaned root: {root}")
    print(f"Removed __pycache__ directories: {cache_dirs}")
    print(f"Removed bytecode files: {bytecode_files}")

    if errors:
        print("\nEncountered errors:")
        for error in errors:
            print(f"- {error}")
        return 1

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
