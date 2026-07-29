#!/usr/bin/env python3
"""Targeted mutation testing for this repo's regression tests.

Every regression test in ``tests/test_bug_regressions.py`` claims to prevent a
specific bug from coming back. That claim is untested. This script tests it:
for each entry in ``scripts/mutants.toml`` it reintroduces the original defect
into a scratch copy of the tree, runs the suite, and reports whether the suite
noticed.

    KILLED   the suite failed -> the regression test does its job
    SURVIVED the suite passed -> the regression test is decorative

Deliberately not ``mutmut`` / ``cosmic-ray``: those mutate every operator in
the codebase, take tens of minutes on a tree this size, need a new dependency
and a pytest runner this repo does not have, and drown a real finding in
hundreds of equivalent mutants. A hand-written catalogue is ~40x cheaper, is
reviewable in a diff, and answers the only question worth asking here --
"would this bug get past us a second time?".

Usage:
    python scripts/mutation_check.py                 # every mutant
    python scripts/mutation_check.py M-BUG0002       # one mutant
    python scripts/mutation_check.py --jobs 4        # parallel
    python scripts/mutation_check.py --list

Exit status is 0 only when every mutant matched its declared ``expect``.
No network, no writes outside the scratch directory, no repo mutation.
"""

from __future__ import annotations

import argparse
import concurrent.futures
import re
import shutil
import subprocess
import sys
import tempfile
import tomllib
from dataclasses import dataclass, field
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
CATALOGUE = REPO_ROOT / "scripts" / "mutants.toml"
COPY_ROOTS = ("src", "tests", "scripts", "main.py", "config.yaml")
IGNORE = shutil.ignore_patterns("__pycache__", "*.pyc", ".mypy_cache", ".pytest_cache")
FAILURE_LINE = re.compile(r"^(?:FAIL|ERROR): (\S+)", re.MULTILINE)


@dataclass
class Mutant:
    id: str
    bug: str
    summary: str
    guard: str
    file: str
    expect: str
    edits: list[dict]


@dataclass
class Result:
    mutant: Mutant
    survived: bool
    killers: list[str] = field(default_factory=list)
    error: str | None = None

    @property
    def status(self) -> str:
        return "SURVIVED" if self.survived else "KILLED"

    @property
    def ok(self) -> bool:
        if self.error:
            return False
        return self.status == self.mutant.expect.upper().replace("KNOWN-", "")


def load_catalogue(path: Path) -> list[Mutant]:
    data = tomllib.loads(path.read_text())
    return [
        Mutant(
            id=entry["id"],
            bug=entry.get("bug", "?"),
            summary=entry.get("summary", ""),
            guard=entry.get("guard", ""),
            file=entry["file"],
            expect=entry.get("expect", "killed"),
            edits=entry.get("edit", []),
        )
        for entry in data.get("mutant", [])
    ]


def stage_tree(destination: Path) -> None:
    for name in COPY_ROOTS:
        source = REPO_ROOT / name
        if not source.exists():
            continue
        target = destination / name
        if source.is_dir():
            shutil.copytree(source, target, ignore=IGNORE)
        else:
            shutil.copy2(source, target)


def apply_mutant(tree: Path, mutant: Mutant) -> None:
    target = tree / mutant.file
    text = target.read_text()
    for index, edit in enumerate(mutant.edits):
        find, replace = edit["find"], edit["replace"]
        occurrences = text.count(find)
        if occurrences != 1:
            raise ValueError(
                f"{mutant.id} edit #{index}: anchor matched {occurrences} times in "
                f"{mutant.file} (expected exactly 1). The catalogue has drifted "
                f"from the source; update the anchor."
            )
        text = text.replace(find, replace)
    target.write_text(text)


def run_suite(tree: Path) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, "-m", "unittest", "discover", "-s", "tests", "-p", "test_*.py"],
        cwd=tree,
        capture_output=True,
        text=True,
        timeout=900,
    )


def measure_baseline() -> tuple[bool, set[str]]:
    """Run the suite unmutated. Mutation scores are meaningless on a red suite."""
    with tempfile.TemporaryDirectory(prefix="mutate-baseline-") as scratch:
        tree = Path(scratch)
        stage_tree(tree)
        completed = run_suite(tree)
    return completed.returncode == 0, set(FAILURE_LINE.findall(completed.stderr))


def evaluate(mutant: Mutant, baseline_failures: set[str] = frozenset()) -> Result:
    with tempfile.TemporaryDirectory(prefix=f"mutate-{mutant.id}-") as scratch:
        tree = Path(scratch)
        try:
            stage_tree(tree)
            apply_mutant(tree, mutant)
            completed = run_suite(tree)
        except Exception as exc:  # catalogue drift, timeout, staging failure
            return Result(mutant, survived=False, error=str(exc))

    failures = set(FAILURE_LINE.findall(completed.stderr))
    # A test that already fails without the mutant proves nothing about it.
    killers = sorted(failures - set(baseline_failures))
    survived = not killers
    return Result(mutant, survived=survived, killers=killers)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("ids", nargs="*", help="mutant ids to run (default: all)")
    parser.add_argument("--jobs", type=int, default=1)
    parser.add_argument("--list", action="store_true")
    args = parser.parse_args()

    mutants = load_catalogue(CATALOGUE)
    if args.ids:
        wanted = set(args.ids)
        mutants = [mutant for mutant in mutants if mutant.id in wanted]
        missing = wanted - {mutant.id for mutant in mutants}
        if missing:
            print(f"unknown mutant id(s): {sorted(missing)}", file=sys.stderr)
            return 2

    if args.list:
        for mutant in mutants:
            print(f"{mutant.id:<12} {mutant.bug:<9} {mutant.summary}")
        return 0

    green, baseline_failures = measure_baseline()
    if not green:
        print(
            "baseline suite is RED; mutation scores would be meaningless.\n"
            "  pre-existing failures: "
            + ", ".join(sorted(baseline_failures))
            + "\n  fix the suite (or the production defect it found) first.",
            file=sys.stderr,
        )
        return 2

    if args.jobs > 1:
        with concurrent.futures.ThreadPoolExecutor(max_workers=args.jobs) as pool:
            results = list(pool.map(lambda m: evaluate(m, baseline_failures), mutants))
    else:
        results = [evaluate(mutant, baseline_failures) for mutant in mutants]

    print()
    print(f"{'MUTANT':<12} {'BUG':<9} {'RESULT':<9} {'EXPECT':<15} VERDICT")
    print("-" * 92)
    for result in results:
        verdict = "ok" if result.ok else ">>> MISMATCH <<<"
        print(
            f"{result.mutant.id:<12} {result.mutant.bug:<9} "
            f"{result.status:<9} {result.mutant.expect:<15} {verdict}"
        )
        print(f"{'':<12} {result.mutant.summary}")
        if result.error:
            print(f"{'':<12} error: {result.error}")
        elif result.survived:
            print(
                f"{'':<12} nothing failed. Declared guard "
                f"'{result.mutant.guard}' does not observe this defect."
            )
        else:
            shown = ", ".join(name.split(".")[-1] for name in result.killers[:4])
            guarded = any(result.mutant.guard in name for name in result.killers)
            print(
                f"{'':<12} killed by: {shown}"
                f"{'' if guarded else '   (NOTE: not the declared guard)'}"
            )
        print()

    mismatches = [result for result in results if not result.ok]
    print(f"{len(results) - len(mismatches)}/{len(results)} mutants matched expectations.")
    for result in mismatches:
        print(f"  MISMATCH {result.mutant.id}: {result.status}, expected {result.mutant.expect}")
    return 1 if mismatches else 0


if __name__ == "__main__":
    sys.exit(main())
