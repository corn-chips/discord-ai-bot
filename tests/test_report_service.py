import tempfile
import unittest
from pathlib import Path

from src.services.report_service import ReportService


class ReportServiceTest(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_path = str(Path(self.temp_dir.name) / "reports.db")

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_create_get_and_update_report(self):
        service = ReportService(self.db_path)

        created = service.create_report(
            guild_id=123,
            channel_id=456,
            reporter_id=789,
            reporter_name="Tester",
            report_type="feature",
            description="Add a report tracker",
        )

        self.assertEqual(created.status, "open")
        self.assertEqual(created.report_type, "feature")

        updated = service.update_status(
            created.id,
            "in_progress",
            admin_notes="Queued for implementation",
        )

        self.assertIsNotNone(updated)
        self.assertEqual(updated.status, "in_progress")
        self.assertEqual(updated.admin_notes, "Queued for implementation")

        reloaded = ReportService(self.db_path).get_report(created.id)
        self.assertEqual(reloaded.status, "in_progress")
        self.assertEqual(reloaded.description, "Add a report tracker")

    def test_rejects_invalid_status(self):
        service = ReportService(self.db_path)
        created = service.create_report(
            guild_id=None,
            channel_id=None,
            reporter_id=1,
            reporter_name="Tester",
            report_type="issue",
            description="Something broke",
        )

        with self.assertRaises(ValueError):
            service.update_status(created.id, "unknown")


if __name__ == "__main__":
    unittest.main()


class ReportVisibilityScopeTest(unittest.TestCase):
    """DAB-147: report ids are small sequential integers.

    An unscoped lookup by id let any member of any guild walk 1, 2, 3... through
    every report the bot had ever received -- description, reporter display name
    and raw Discord user id -- from guilds they were not in. Verified before the
    fix by executing the real service: a guild-222 member and a DM caller both
    read guild-111's report verbatim.
    """

    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)
        self.service = ReportService(str(Path(self.temp_dir.name) / "reports.db"))
        self.in_guild = self.service.create_report(
            guild_id=111,
            channel_id=1,
            reporter_id=7,
            reporter_name="Ada",
            report_type="issue",
            description="filed inside guild 111",
        )
        # /report works in a DM, where there is no guild to scope by.
        self.from_dm = self.service.create_report(
            guild_id=None,
            channel_id=2,
            reporter_id=8,
            reporter_name="Grace",
            report_type="issue",
            description="filed from a DM",
        )

    def test_a_member_of_another_guild_cannot_read_the_report(self):
        self.assertIsNone(
            self.service.get_report(
                self.in_guild.id, visible_to_guild=222, visible_to_reporter=99
            )
        )

    def test_a_member_of_the_same_guild_can_read_it(self):
        found = self.service.get_report(
            self.in_guild.id, visible_to_guild=111, visible_to_reporter=99
        )
        self.assertIsNotNone(found)
        self.assertEqual(found.description, "filed inside guild 111")

    def test_the_reporter_can_still_read_their_dm_filed_report_from_a_dm(self):
        """The flow /report advertises, and the reason there is no guild_only().

        In a DM interaction.guild_id is None, so the guild half of the scope
        matches nothing (SQL never satisfies `guild_id = NULL`). Only the
        reporter_id half can return this row, and reporter_id is INTEGER NOT
        NULL on every row the table has ever held.
        """
        found = self.service.get_report(
            self.from_dm.id, visible_to_guild=None, visible_to_reporter=8
        )
        self.assertIsNotNone(found)
        self.assertEqual(found.description, "filed from a DM")

    def test_a_stranger_in_a_dm_cannot_read_someone_elses_dm_report(self):
        self.assertIsNone(
            self.service.get_report(
                self.from_dm.id, visible_to_guild=None, visible_to_reporter=999
            )
        )

    def test_a_guild_cannot_see_a_dm_filed_report_it_did_not_receive(self):
        # guild_id IS NULL must not match an arbitrary guild scope.
        self.assertIsNone(
            self.service.get_report(
                self.from_dm.id, visible_to_guild=111, visible_to_reporter=99
            )
        )

    def test_an_unscoped_read_still_works_for_the_operator_surface(self):
        # The admin web view legitimately reads any report; Phase 2.3 locks that
        # surface to loopback. Passing no scope must stay possible and explicit.
        self.assertIsNotNone(self.service.get_report(self.in_guild.id))
