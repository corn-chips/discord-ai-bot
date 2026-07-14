import sqlite3
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

from src.services.channel_settings_service import ChannelSettingsService
from src.services.message_index_service import MessageIndexService
from src.services.message_visibility_service import MessageVisibilityService
from src.services.pin_service import PinService
from src.services.report_service import ReportService
from src.services.sqlite_utils import sqlite_connection, sqlite_transaction
from src.services.token_tracker import TokenTracker
from src.services.user_preferences_service import UserPreferencesService


class SQLiteLifecycleTest(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)
        self.db_path = Path(self.temp_dir.name) / "services.db"

    def test_transaction_commits_and_connection_closes(self):
        with sqlite_transaction(self.db_path) as connection:
            connection.execute("CREATE TABLE records (value TEXT NOT NULL)")
            connection.execute("INSERT INTO records (value) VALUES ('committed')")
            captured_connection = connection

        with sqlite_connection(self.db_path, row_factory=sqlite3.Row) as connection:
            row = connection.execute("SELECT value FROM records").fetchone()

        self.assertEqual(row["value"], "committed")
        with self.assertRaises(sqlite3.ProgrammingError):
            captured_connection.execute("SELECT 1")

    def test_transaction_rolls_back_and_connection_closes(self):
        with sqlite_transaction(self.db_path) as connection:
            connection.execute("CREATE TABLE records (value TEXT NOT NULL)")

        captured_connection = None
        with self.assertRaisesRegex(RuntimeError, "force rollback"):
            with sqlite_transaction(self.db_path) as connection:
                captured_connection = connection
                connection.execute("INSERT INTO records (value) VALUES ('rolled back')")
                raise RuntimeError("force rollback")

        with sqlite_connection(self.db_path) as connection:
            count = connection.execute("SELECT COUNT(*) FROM records").fetchone()[0]

        self.assertEqual(count, 0)
        with self.assertRaises(sqlite3.ProgrammingError):
            captured_connection.execute("SELECT 1")


class SQLiteServiceBehaviorTest(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)
        self.db_path = Path(self.temp_dir.name) / "services.db"

    def test_channel_settings_preserve_channel_isolation(self):
        service = ChannelSettingsService(
            str(self.db_path),
            personalities={"default": "Default", "focused": "Focused"},
        )

        self.assertTrue(service.set_personality(10, "focused"))
        self.assertTrue(service.set_live_enabled(10, True))

        self.assertEqual(service.get_personality(10), "focused")
        self.assertTrue(service.get_live_enabled(10))
        self.assertEqual(service.get_personality(20), "default")
        self.assertFalse(service.get_live_enabled(20))

    def test_message_visibility_preserves_channel_isolation_and_removal(self):
        service = MessageVisibilityService(str(self.db_path))

        self.assertTrue(service.save_hidden_message(101, 10, "first"))
        self.assertTrue(service.save_hidden_message(202, 20, "second"))

        channel_rows = service.get_recent_hidden_messages(10, limit=10)
        self.assertEqual([(row[0], row[1]) for row in channel_rows], [(101, "first")])
        self.assertTrue(service.remove_hidden_message(101))
        self.assertFalse(service.remove_hidden_message(101))
        self.assertEqual(service.get_recent_hidden_messages(10, limit=10), [])
        self.assertEqual(service.get_recent_hidden_messages(20, limit=10)[0][0], 202)

    def test_message_index_preserves_row_mapping_and_channel_isolation(self):
        service = MessageIndexService(str(self.db_path), embedding_model="test-model")
        created_at = datetime.now(timezone.utc)

        self.assertTrue(service.index_bot_response(
            message_id=101,
            channel_id=10,
            guild_id=1,
            author_id=99,
            author_name="Bot",
            content_text="channel ten",
            reply_to_message_id=None,
            created_at=created_at,
        ))
        self.assertTrue(service.index_bot_response(
            message_id=202,
            channel_id=20,
            guild_id=1,
            author_id=99,
            author_name="Bot",
            content_text="channel twenty",
            reply_to_message_id=None,
            created_at=created_at,
        ))

        channel_messages = service.search_recent(
            guild_id=1,
            channel_id=10,
            cross_channel=False,
            limit=10,
        )
        self.assertEqual([message.message_id for message in channel_messages], [101])
        self.assertEqual(channel_messages[0].content_text, "channel ten")

        reloaded = MessageIndexService(str(self.db_path), embedding_model="test-model")
        self.assertEqual(reloaded.get_messages_by_ids([101])[0].author_name, "Bot")

    def test_pins_preserve_channel_scoping_and_delete_safety(self):
        service = PinService(str(self.db_path))

        pin_id = service.add_pin(10, "remember", "Ada", "Grace", message_id=99)

        self.assertIsNotNone(pin_id)
        self.assertEqual(service.get_pins(20), [])
        self.assertFalse(service.delete_pin(pin_id, channel_id=20))
        self.assertEqual(service.get_pins(10)[0][1], "remember")
        self.assertTrue(service.delete_pin(pin_id, channel_id=10))
        self.assertEqual(service.get_pins_for_prompt(10), None)

    def test_user_preferences_validate_persist_and_clear_per_user(self):
        service = UserPreferencesService(
            str(self.db_path),
            valid_models=["model-a"],
            valid_languages=["english", "spanish"],
        )

        self.assertTrue(service.set_model(1, "model-a"))
        self.assertTrue(service.set_language(1, "SPANISH"))
        self.assertFalse(service.set_model(2, "invalid"))

        preferences = service.get_preferences(1)
        self.assertEqual(preferences.preferred_model, "model-a")
        self.assertEqual(preferences.preferred_language, "spanish")
        self.assertIsNone(service.get_preferences(2).preferred_model)
        self.assertTrue(service.clear_preferences(1))
        self.assertIsNone(service.get_preferences(1).preferred_language)

    def test_reports_preserve_row_mapping_filtering_and_updates(self):
        service = ReportService(str(self.db_path))

        report = service.create_report(
            guild_id=1,
            channel_id=10,
            reporter_id=100,
            reporter_name="Ada",
            report_type="issue",
            description="  broken behavior  ",
        )
        updated = service.update_status(report.id, "triaged", admin_notes="seen")

        self.assertEqual(report.description, "broken behavior")
        self.assertEqual(updated.status, "triaged")
        self.assertEqual(updated.admin_notes, "seen")
        self.assertEqual([item.id for item in service.list_reports(status="triaged")], [report.id])
        self.assertEqual(service.list_reports(status="done"), [])

    def test_services_preserve_directory_creation_and_fallback_policies(self):
        creating_paths = {
            "index": Path(self.temp_dir.name) / "index" / "services.db",
            "reports": Path(self.temp_dir.name) / "reports" / "services.db",
            "tokens": Path(self.temp_dir.name) / "tokens" / "services.db",
        }

        MessageIndexService(str(creating_paths["index"]))
        ReportService(str(creating_paths["reports"]))
        TokenTracker(str(creating_paths["tokens"]))
        self.assertTrue(all(path.exists() for path in creating_paths.values()))

        missing_parent_db = Path(self.temp_dir.name) / "not-created" / "services.db"
        with self.assertLogs(level="ERROR"):
            channel_settings = ChannelSettingsService(str(missing_parent_db))
            visibility = MessageVisibilityService(str(missing_parent_db))
            pins = PinService(str(missing_parent_db))
            preferences = UserPreferencesService(
                str(missing_parent_db),
                valid_models=["model-a"],
            )

            self.assertEqual(channel_settings.get_personality(1), "default")
            self.assertFalse(channel_settings.set_live_enabled(1, True))
            self.assertEqual(visibility.get_recent_hidden_messages(1, 10), [])
            self.assertFalse(visibility.save_hidden_message(1, 1, "hidden"))
            self.assertEqual(pins.get_pins(1), [])
            self.assertIsNone(pins.add_pin(1, "pin", "Ada", "Grace"))
            self.assertIsNone(preferences.get_preferences(1).preferred_model)
            self.assertFalse(preferences.set_model(1, "model-a"))

        self.assertFalse(missing_parent_db.parent.exists())


class AsyncSQLiteServiceBehaviorTest(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)
        self.db_path = Path(self.temp_dir.name) / "services.db"

    async def test_token_tracker_aggregates_and_isolates_guilds(self):
        service = TokenTracker(str(self.db_path))

        await service.record_usage(
            user_id=1,
            username="Ada",
            guild_id=10,
            guild_name="Guild 10",
            input_tokens=3,
            output_tokens=5,
            total_tokens=8,
        )
        await service.record_usage(
            user_id=1,
            username="Ada",
            guild_id=20,
            guild_name="Guild 20",
            input_tokens=50,
            output_tokens=50,
            total_tokens=100,
        )

        guild_entries = await service.get_top_users(10)
        self.assertEqual(len(guild_entries), 1)
        self.assertEqual(guild_entries[0].total_tokens, 8)
        self.assertEqual(guild_entries[0].request_count, 1)


if __name__ == "__main__":
    unittest.main()
