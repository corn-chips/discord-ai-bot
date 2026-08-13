"""Indexed text used to keep Discord mention markup raw.

`message.content` holds `<@111222333444555666>` and nothing in the tree mapped
that snowflake to a name, so the FTS tokenizer stored the bare number: a
question naming the person matched none of the messages that mentioned them,
and a question carrying the mention matched only those. Half a channel talking
about "alice" and half mentioning her read as two unrelated people.

`ContextCollector._build_message_content` now rewrites the markup as
`@alice (111222333444555666)`, keeping the id because that is still what a
mention in a *query* tokenizes to. `scripts/rebuild_fts.py` re-derives the FTS
rows for databases indexed before the change -- it cannot recover names from
text already stored, only re-index what is there.
"""

import contextlib
import importlib.util
import io
import sqlite3
import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from src.services.context_collector import ContextCollector
from src.services.message_index_service import MessageIndexService

ALICE_ID = 111222333444555666
BASE = datetime(2026, 1, 1, 12, 0, tzinfo=timezone.utc)
SCRIPT_PATH = Path(__file__).resolve().parents[1] / "scripts" / "rebuild_fts.py"


def fake_message(content, *, message_id=1, author_id=7, channel_id=20, minutes=0.0, **extra):
    fields = dict(
        id=message_id,
        content=content,
        attachments=[],
        embeds=[],
        stickers=[],
        type=SimpleNamespace(name="default"),
        author=SimpleNamespace(id=author_id, display_name=f"user{author_id}", bot=False),
        channel=SimpleNamespace(id=channel_id),
        guild=SimpleNamespace(id=10),
        reference=None,
        created_at=BASE + timedelta(minutes=minutes),
    )
    fields.update(extra)
    return SimpleNamespace(**fields)


class MentionResolutionTest(unittest.TestCase):
    def setUp(self):
        self.alice = SimpleNamespace(id=ALICE_ID, display_name="alice", name="alice_login")
        self.mods = SimpleNamespace(id=999, name="moderators")
        self.general = SimpleNamespace(id=4242, name="general")

    def build(self, content, **extra):
        return ContextCollector._build_message_content(fake_message(content, **extra))

    def test_a_user_mention_gains_the_display_name_and_keeps_the_id(self):
        built = self.build(f"ask <@{ALICE_ID}> about it", mentions=[self.alice])

        self.assertIn("@alice", built)
        self.assertIn(str(ALICE_ID), built)
        self.assertNotIn(f"<@{ALICE_ID}>", built)

    def test_the_legacy_nickname_form_resolves_too(self):
        built = self.build(f"ask <@!{ALICE_ID}> about it", mentions=[self.alice])

        self.assertIn(f"@alice ({ALICE_ID})", built)

    def test_role_and_channel_mentions_resolve_with_their_own_sigils(self):
        built = self.build(
            "<@&999> please read <#4242>",
            role_mentions=[self.mods],
            channel_mentions=[self.general],
        )

        self.assertIn("@moderators (999)", built)
        self.assertIn("#general (4242)", built)

    def test_the_guild_answers_when_the_mention_lists_do_not(self):
        # discord.py leaves `mentions` empty for a user it could not resolve
        # from the payload but the member cache still knows.
        guild = SimpleNamespace(get_member=lambda user_id: self.alice if user_id == ALICE_ID else None)

        built = self.build(f"hi <@{ALICE_ID}>", guild=guild)

        self.assertIn(f"@alice ({ALICE_ID})", built)

    def test_an_unresolvable_mention_keeps_its_raw_markup(self):
        built = self.build("who is <@777>?")

        self.assertIn("<@777>", built)

    def test_a_fake_without_mentions_role_mentions_or_guild_does_not_crash(self):
        bare = SimpleNamespace(
            content=f"hi <@{ALICE_ID}> and <@&999> in <#4242>",
            attachments=[],
            embeds=[],
            stickers=[],
            type=SimpleNamespace(name="default"),
        )

        built = ContextCollector._build_message_content(bare)

        self.assertIn(f"<@{ALICE_ID}>", built)
        self.assertIn("<@&999>", built)
        self.assertIn("<#4242>", built)

    def test_a_resolver_that_raises_falls_back_to_the_raw_text(self):
        def explode(_user_id):
            raise RuntimeError("member cache is gone")

        built = self.build(f"hi <@{ALICE_ID}>", guild=SimpleNamespace(get_member=explode))

        self.assertIn(f"hi <@{ALICE_ID}>", built)

    def test_text_without_mentions_is_untouched(self):
        self.assertTrue(self.build("plain question about the deploy").startswith("plain question"))


class MentionSearchTest(unittest.TestCase):
    """The point of the change: search by name finds a message that only mentioned."""

    def setUp(self):
        self._dir = tempfile.TemporaryDirectory()
        self.addCleanup(self._dir.cleanup)
        self.db_path = str(Path(self._dir.name) / "rag.db")
        self.service = MessageIndexService(self.db_path, embedding_model="test-embedding")
        self.assertTrue(self.service.fts_enabled, "FTS5 is required for this test")
        self.alice = SimpleNamespace(id=ALICE_ID, display_name="alice", name="alice_login")

    def search(self, query):
        # One discriminating token on purpose: search_lexical falls back from
        # AND to OR below fts_min_and_results, so a longer question would match
        # on its other words and prove nothing about the name.
        return sorted(
            message.message_id
            for message in self.service.search_lexical(
                query, guild_id=10, channel_id=20, cross_channel=False, limit=10
            )
        )

    def index(self, content, message_id, **extra):
        self.assertTrue(
            self.service.index_discord_message(fake_message(content, message_id=message_id, **extra))
        )

    def test_a_message_that_only_mentioned_alice_is_found_by_her_name(self):
        self.index(f"<@{ALICE_ID}> owns the deployment runbook", 500, mentions=[self.alice])
        self.index("bob owns the deployment runbook", 501, minutes=1)

        self.assertEqual(self.search("alice"), [500])

    def test_the_same_message_is_still_found_by_the_raw_mention(self):
        self.index(f"<@{ALICE_ID}> owns the deployment runbook", 502, mentions=[self.alice])

        self.assertEqual(self.search(f"<@{ALICE_ID}>"), [502])

    def test_mentioned_and_named_messages_now_answer_the_same_question(self):
        self.index(f"<@{ALICE_ID}> rotated the certificates", 600, mentions=[self.alice])
        self.index("alice rotated the certificates", 601, minutes=1)

        self.assertEqual(self.search("alice"), [600, 601])


class RebuildFtsScriptTest(unittest.TestCase):
    """scripts/rebuild_fts.py, run in-process against a real database."""

    def setUp(self):
        self._dir = tempfile.TemporaryDirectory()
        self.addCleanup(self._dir.cleanup)
        self.db_path = str(Path(self._dir.name) / "rag.db")
        self.config_path = Path(self._dir.name) / "config.yaml"
        self.config_path.write_text("rag:\n  embedding_model: test-embedding\n", encoding="utf-8")
        self.service = MessageIndexService(self.db_path, embedding_model="test-embedding")
        for offset in range(3):
            self.service.upsert_message(
                message_id=100 + offset,
                guild_id=10,
                channel_id=20,
                author_id=1,
                author_name="user1",
                is_bot=False,
                reply_to_message_id=None,
                created_at=BASE + timedelta(minutes=offset),
                content_text=f"channel twenty message {offset} about the deploy",
            )
        self.service.upsert_message(
            message_id=300,
            guild_id=10,
            channel_id=21,
            author_id=2,
            author_name="user2",
            is_bot=False,
            reply_to_message_id=None,
            created_at=BASE,
            content_text="channel twentyone message about the deploy",
        )

        spec = importlib.util.spec_from_file_location("rebuild_fts", SCRIPT_PATH)
        self.script = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(self.script)

    def run_script(self, *args):
        argv = ["rebuild_fts.py", "--db", self.db_path, "--config", str(self.config_path), *args]
        output = io.StringIO()
        with patch.object(sys, "argv", argv), contextlib.redirect_stdout(output):
            code = self.script.main()
        return code, output.getvalue()

    def fts_rowids(self):
        with sqlite3.connect(self.db_path) as conn:
            return sorted(row[0] for row in conn.execute("SELECT rowid FROM message_search_fts"))

    def empty_fts(self):
        with sqlite3.connect(self.db_path) as conn:
            conn.execute("DELETE FROM message_search_fts")
            conn.commit()

    def test_a_dry_run_reports_the_rows_without_writing_them(self):
        self.empty_fts()

        code, output = self.run_script("--dry-run")

        self.assertEqual(code, 0)
        self.assertIn("nothing written", output)
        self.assertIn("4 FTS rows", output)
        self.assertEqual(self.fts_rowids(), [])

    def test_a_real_run_restores_every_row(self):
        self.empty_fts()

        code, _output = self.run_script()

        self.assertEqual(code, 0)
        self.assertEqual(self.fts_rowids(), [100, 101, 102, 300])

    def test_one_channel_can_be_rebuilt_alone(self):
        self.empty_fts()

        self.run_script("--channel", "21")

        self.assertEqual(self.fts_rowids(), [300])

    def test_the_rebuilt_text_is_the_text_the_index_now_holds(self):
        with sqlite3.connect(self.db_path) as conn:
            conn.execute("UPDATE message_index SET content_text = ? WHERE message_id = 100", ("@alice (1) rewrote it",))
            conn.commit()
        self.empty_fts()

        self.run_script()

        with sqlite3.connect(self.db_path) as conn:
            hits = conn.execute("SELECT rowid FROM message_search_fts WHERE message_search_fts MATCH 'alice'").fetchall()
        self.assertEqual([row[0] for row in hits], [100])

    def test_hidden_and_deleted_messages_leave_the_index(self):
        with sqlite3.connect(self.db_path) as conn:
            conn.execute("UPDATE message_index SET hidden = 1 WHERE message_id = 101")
            conn.execute("UPDATE message_index SET deleted_at = ? WHERE message_id = 102", (BASE.isoformat(),))
            conn.commit()

        self.run_script()

        self.assertEqual(self.fts_rowids(), [100, 300])

    def test_small_batches_cover_the_same_rows_and_resume_from_an_id(self):
        self.empty_fts()

        code, output = self.run_script("--batch-size", "1")

        self.assertEqual(code, 0)
        self.assertIn("through message 100", output)
        self.assertEqual(self.fts_rowids(), [100, 101, 102, 300])

        self.empty_fts()
        self.run_script("--resume-from", "101")
        self.assertEqual(self.fts_rowids(), [102, 300])

    def test_a_missing_database_is_reported_rather_than_created(self):
        missing = str(Path(self._dir.name) / "absent.db")
        argv = ["rebuild_fts.py", "--db", missing, "--config", str(self.config_path)]
        with patch.object(sys, "argv", argv), contextlib.redirect_stdout(io.StringIO()):
            with contextlib.redirect_stderr(io.StringIO()):
                code = self.script.main()

        self.assertEqual(code, 2)
        self.assertFalse(Path(missing).exists())

    def test_the_help_states_that_stored_text_is_all_it_can_rebuild(self):
        self.assertIn("LIMITATION", self.script.__doc__)
        self.assertIn("re-indexing", self.script.__doc__)


if __name__ == "__main__":
    unittest.main()
