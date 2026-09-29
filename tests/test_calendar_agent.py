import unittest

from tests.base import TempDirTestCase, BASE_DIR
from src.calendar_agent import CalendarAgent
from src.database import get_connection


class TestCalendarAgent(TempDirTestCase):

    def test_variable_date_rules_resolve_to_real_calendar_days(self):
        agent = CalendarAgent(self.settings, BASE_DIR, self.logger)
        # Mother's Day (US) 2026 = second Sunday of May 2026 = May 10, 2026
        self.assertEqual(agent._apply_rule("second_sunday_of_may", 2026), "2026-05-10")
        # Father's Day 2026 = third Sunday of June 2026 = June 21, 2026
        self.assertEqual(agent._apply_rule("third_sunday_of_june", 2026), "2026-06-21")

    def test_lunar_dates_are_not_guessed(self):
        agent = CalendarAgent(self.settings, BASE_DIR, self.logger)
        events = agent.collect_special_days([2026])
        eid = next(e for e in events if e["name"] == "Eid al-Fitr" and e["year"] == 2026)
        self.assertIsNone(eid["event_date"])
        self.assertEqual(eid["verification_status"], "Provisional")

    def test_no_duplicate_special_days_on_repeated_sync(self):
        agent = CalendarAgent(self.settings, BASE_DIR, self.logger)
        events = agent.collect_special_days([2026])
        written_first = agent.save_to_db(self.db_path, events)
        written_second = agent.save_to_db(self.db_path, events)  # re-run, same data

        self.assertGreater(written_first, 0)
        self.assertGreater(written_second, 0)  # updates existing rows, not duplicates

        with get_connection(self.db_path) as conn:
            total = conn.execute("SELECT COUNT(*) AS c FROM special_days").fetchone()["c"]
        self.assertEqual(total, len(events))

    def test_manual_override_protected_from_recollection(self):
        agent = CalendarAgent(self.settings, BASE_DIR, self.logger)
        events = agent.collect_special_days([2026])
        agent.save_to_db(self.db_path, events)

        with get_connection(self.db_path) as conn:
            conn.execute(
                "UPDATE special_days SET manual_override=1, category='Manually Edited' "
                "WHERE name='Valentine''s Day' AND year=2026"
            )

        agent.save_to_db(self.db_path, events)
        with get_connection(self.db_path) as conn:
            row = conn.execute(
                "SELECT category FROM special_days WHERE name='Valentine''s Day' AND year=2026"
            ).fetchone()
        self.assertEqual(row["category"], "Manually Edited")


if __name__ == "__main__":
    unittest.main()
