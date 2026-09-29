import os
import unittest
from datetime import date
from unittest import mock

from tests.base import TempDirTestCase
from src.database import get_connection
from src.reminder_agent import ReminderAgent


class TestReminderAgent(TempDirTestCase):

    def setUp(self):
        super().setUp()
        self.settings["reminders"]["delivery_channel"] = "local_fallback"
        with get_connection(self.db_path) as conn:
            conn.execute(
                """INSERT INTO special_days (name, category, country, event_date, year,
                   date_type, verification_status, data_source, source_url,
                   priority, reminder_date, last_updated)
                   VALUES ('Test Day','Family','UAE','2026-01-10',2026,'fixed','Confirmed',
                           'test','https://example.com','High','2026-01-01','2026-01-01')"""
            )

    def test_due_reminder_is_sent_via_local_fallback(self):
        agent = ReminderAgent(self.settings, self.tmp_dir, self.logger)
        summary = agent.process_due_reminders(self.db_path, as_of=date(2026, 1, 2))

        self.assertEqual(summary["sent"], 1)
        with get_connection(self.db_path) as conn:
            row = conn.execute("SELECT status FROM reminders").fetchone()
        self.assertEqual(row["status"], "sent")

    def test_reminder_not_duplicated_on_second_run(self):
        agent = ReminderAgent(self.settings, self.tmp_dir, self.logger)
        agent.process_due_reminders(self.db_path, as_of=date(2026, 1, 2))
        summary_second = agent.process_due_reminders(self.db_path, as_of=date(2026, 1, 2))

        self.assertEqual(summary_second["skipped_duplicate"], 1)
        with get_connection(self.db_path) as conn:
            count = conn.execute("SELECT COUNT(*) AS c FROM reminders").fetchone()["c"]
        self.assertEqual(count, 1)

    def test_reminder_not_due_yet_is_skipped(self):
        agent = ReminderAgent(self.settings, self.tmp_dir, self.logger)
        summary = agent.process_due_reminders(self.db_path, as_of=date(2025, 12, 1))
        self.assertEqual(summary["not_due"], 1)
        self.assertEqual(summary["sent"], 0)

    def test_google_calendar_without_credentials_file_fails_honestly(self):
        self.settings["reminders"]["delivery_channel"] = "google_calendar"
        with mock.patch.dict(os.environ, {}, clear=False):
            os.environ.pop("GOOGLE_CALENDAR_CREDENTIALS_FILE", None)
            agent = ReminderAgent(self.settings, self.tmp_dir, self.logger)
            summary = agent.process_due_reminders(self.db_path, as_of=date(2026, 1, 2))

        self.assertEqual(summary["sent"], 0)
        self.assertEqual(summary["failed"], 1)
        with get_connection(self.db_path) as conn:
            row = conn.execute("SELECT status, detail FROM reminders").fetchone()
        self.assertEqual(row["status"], "failed")
        self.assertIn("credentials file not configured", row["detail"])

    def test_google_calendar_creates_real_event_when_configured(self):
        self.settings["reminders"]["delivery_channel"] = "google_calendar"

        fake_creds_path = os.path.join(self.tmp_dir, "fake_credentials.json")
        with open(fake_creds_path, "w", encoding="utf-8") as f:
            f.write("{}")

        mock_client_instance = mock.MagicMock()
        mock_client_instance.insert_event.return_value = {
            "htmlLink": "https://calendar.google.com/event?eid=fake123"
        }

        with mock.patch.dict(
            os.environ,
            {
                "GOOGLE_CALENDAR_CREDENTIALS_FILE": fake_creds_path,
                "GOOGLE_CALENDAR_ID": "primary",
            },
        ), mock.patch(
            "src.google_calendar_client.GoogleCalendarClient",
            return_value=mock_client_instance,
        ):
            agent = ReminderAgent(self.settings, self.tmp_dir, self.logger)
            summary = agent.process_due_reminders(self.db_path, as_of=date(2026, 1, 2))

        self.assertEqual(summary["sent"], 1)
        mock_client_instance.insert_event.assert_called_once()
        called_calendar_id, called_body = mock_client_instance.insert_event.call_args[0]
        self.assertEqual(called_calendar_id, "primary")
        self.assertIn("Test Day", called_body["summary"])
        with get_connection(self.db_path) as conn:
            row = conn.execute("SELECT status, detail FROM reminders").fetchone()
        self.assertEqual(row["status"], "sent")
        self.assertIn("calendar.google.com", row["detail"])


if __name__ == "__main__":
    unittest.main()
