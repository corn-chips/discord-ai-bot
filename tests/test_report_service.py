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
