import unittest

from tests.base import TempDirTestCase, BASE_DIR
from src.database import get_connection
from src.priority_agent import PriorityAgent


def insert_day(conn, name, category, country, event_date, year=2026):
    conn.execute(
        """INSERT INTO special_days (name, category, country, event_date, year,
           date_type, verification_status, data_source, source_url, last_updated)
           VALUES (?,?,?,?,?,?,?,?,?,?)""",
        (name, category, country, event_date, year, "fixed", "Confirmed",
         "test", "https://example.com", "2026-01-01T00:00:00"),
    )


class TestPriorityAgent(TempDirTestCase):

    def test_mothers_day_outranks_fathers_day(self):
        with get_connection(self.db_path) as conn:
            insert_day(conn, "Mother's Day (UAE/Arab World)", "Family", "UAE", "2026-03-21")
            insert_day(conn, "Father's Day", "Family", "Global", "2026-06-21")

        agent = PriorityAgent(self.settings, BASE_DIR, self.logger)
        agent.classify_all(self.db_path)

        with get_connection(self.db_path) as conn:
            mother = conn.execute(
                "SELECT priority FROM special_days WHERE name=?",
                ("Mother's Day (UAE/Arab World)",),
            ).fetchone()
            father = conn.execute(
                "SELECT priority FROM special_days WHERE name=?", ("Father's Day",),
            ).fetchone()

        levels = ["Low", "Medium", "High", "Critical"]
        self.assertGreater(levels.index(mother["priority"]), levels.index(father["priority"]))
        self.assertEqual(mother["priority"], "High")
        self.assertEqual(father["priority"], "Medium")

    def test_manual_override_is_never_recalculated(self):
        with get_connection(self.db_path) as conn:
            insert_day(conn, "Custom Test Day", "International", "Global", "2026-05-05")
            conn.execute(
                "UPDATE special_days SET manual_override=1, priority='Critical' WHERE name=?",
                ("Custom Test Day",),
            )

        agent = PriorityAgent(self.settings, BASE_DIR, self.logger)
        agent.classify_all(self.db_path)

        with get_connection(self.db_path) as conn:
            row = conn.execute(
                "SELECT priority FROM special_days WHERE name=?", ("Custom Test Day",)
            ).fetchone()
        self.assertEqual(row["priority"], "Critical")

    def test_reminder_lead_days_override_for_mothers_and_fathers_day(self):
        with get_connection(self.db_path) as conn:
            insert_day(conn, "Mother's Day (UAE/Arab World)", "Family", "UAE", "2026-03-21")
            insert_day(conn, "Father's Day", "Family", "Global", "2026-06-21")

        agent = PriorityAgent(self.settings, BASE_DIR, self.logger)
        agent.classify_all(self.db_path)

        with get_connection(self.db_path) as conn:
            mother = conn.execute(
                "SELECT reminder_lead_days, reminder_date FROM special_days WHERE name=?",
                ("Mother's Day (UAE/Arab World)",),
            ).fetchone()
            father = conn.execute(
                "SELECT reminder_lead_days, reminder_date FROM special_days WHERE name=?",
                ("Father's Day",),
            ).fetchone()

        self.assertEqual(mother["reminder_lead_days"], 10)
        self.assertEqual(mother["reminder_date"], "2026-03-11")
        self.assertEqual(father["reminder_lead_days"], 5)
        self.assertEqual(father["reminder_date"], "2026-06-16")


if __name__ == "__main__":
    unittest.main()
