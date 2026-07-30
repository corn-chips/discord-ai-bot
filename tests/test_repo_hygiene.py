"""Guards for DAB-157: secrets, databases and rotated logs must stay untracked.

`RotatingFileHandler` names its backups `bot.log.1`, `bot.log.2` and so on, and
the glob `*.log` does not match them. Those backups hold the *older* history --
full user message content, uploaded-file text and unredacted exception text --
so a solo operator running `git add -A` on their own bot repo would publish
their guild's conversations.

These are repository invariants rather than behaviour, but the test suite is the
only thing in this repository that runs automatically, so they live here. Both
tests skip cleanly outside a git checkout (a source tarball, say).
"""

import shutil
import subprocess
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent

# Paths that must never be committable. None of these exists on disk; git only
# needs the name to answer.
MUST_BE_IGNORED = (
    ".env",
    ".env.local",
    "bot.log",
    "bot.log.1",
    "logs/bot.log",
    "logs/bot.log.3",
    "logs/performance_bot.log.2",
    "data/token_usage.db",
    "data/message_rag.db",
    "data/x.db",
)

# Representative tracked files. If a broadened rule starts swallowing these, the
# ignore file has gone too far.
MUST_STAY_TRACKABLE = (
    "README.md",
    "config.yaml",
    "src/config.py",
    "tests/test_repo_hygiene.py",
)


def _git(*args):
    return subprocess.run(
        ("git", *args),
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
    )


class RepoHygieneTest(unittest.TestCase):
    def setUp(self):
        if shutil.which("git") is None:
            self.skipTest("git is not available")
        if _git("rev-parse", "--git-dir").returncode != 0:
            self.skipTest("not a git checkout")

    def test_secrets_databases_and_rotated_logs_are_ignored(self):
        for path in MUST_BE_IGNORED:
            with self.subTest(path=path):
                self.assertEqual(
                    _git("check-ignore", "-q", path).returncode,
                    0,
                    f"{path} is not gitignored; `git add -A` would commit it",
                )

        # --no-index is load-bearing. `git check-ignore` normally short-circuits
        # on tracked paths and reports them un-ignored whatever the rules say, so
        # without it this loop passes even for a `.gitignore` that swallows the
        # whole repository.
        for path in MUST_STAY_TRACKABLE:
            with self.subTest(path=path):
                self.assertNotEqual(
                    _git("check-ignore", "--no-index", "-q", path).returncode,
                    0,
                    f"{path} became ignored; an ignore rule is too broad",
                )

    def test_no_secret_database_or_log_file_is_tracked(self):
        tracked = _git("ls-files").stdout.splitlines()
        self.assertTrue(tracked, "git ls-files returned nothing")

        offenders = [
            path
            for path in tracked
            if path == ".env"
            or path.startswith((".env.", "data/"))
            or path.endswith((".db", ".sqlite", ".sqlite3", ".log"))
            or ".log." in path
        ]
        self.assertEqual(offenders, [], f"sensitive files are tracked: {offenders}")


if __name__ == "__main__":
    unittest.main()
