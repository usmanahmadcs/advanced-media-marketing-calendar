import datetime
import os
import unittest

import openpyxl

from tests.base import TempDirTestCase
from src.database import get_connection
from src.excel_agent import (
    ExcelAgent,
    NOT_VERIFIED_TEXT,
    NO_MATCH_TEXT,
    DEMO_SOURCE_LABEL,
    LIVE_SOURCE_LABEL,
)

TODAY = datetime.date.today()


def insert_special_day(conn, name, event_date, category="Family", country="UAE",
                        year=None, priority="Medium", verification_status="Confirmed",
                        preparation_deadline=None, reminder_date=None):
    year = year or (datetime.date.fromisoformat(event_date).year if event_date else TODAY.year)
    conn.execute(
        """INSERT INTO special_days
           (name, category, country, event_date, year, date_type, verification_status,
            data_source, source_url, priority, priority_score, priority_reason,
            suggested_campaign, preparation_deadline, reminder_date, last_updated)
           VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        (name, category, country, event_date, year, "fixed", verification_status,
         "test", "https://example.com", priority, 50.0, "test reason",
         "test campaign", preparation_deadline, reminder_date, "2026-01-01T00:00:00"),
    )
    return conn.execute("SELECT id FROM special_days WHERE name=? AND year=?", (name, year)).fetchone()["id"]


def insert_post(conn, url, caption, publication_date, likes=10, comments=1, source="demo_seed"):
    conn.execute(
        """INSERT INTO instagram_posts (post_url, caption, publication_date, likes, comments,
           source, last_updated) VALUES (?,?,?,?,?,?,?)""",
        (url, caption, publication_date, likes, comments, source, "2026-01-01T00:00:00"),
    )
    return conn.execute("SELECT id FROM instagram_posts WHERE post_url=?", (url,)).fetchone()["id"]


def insert_match(conn, day_id, post_id, confidence):
    conn.execute(
        """INSERT INTO post_matches (special_day_id, instagram_post_id, matching_confidence,
           match_method, last_updated) VALUES (?,?,?,?,?)""",
        (day_id, post_id, confidence, "keyword_and_date_proximity", "2026-01-01T00:00:00"),
    )


class TestExcelAgent(TempDirTestCase):

    def setUp(self):
        super().setUp()
        self.agent = ExcelAgent(self.settings, self.tmp_dir, self.logger)

    def _load(self, data_only=False):
        return openpyxl.load_workbook(self.agent.output_path, data_only=data_only)

    # ------------------------------------------------------------------
    # 1. Days Remaining
    # ------------------------------------------------------------------
    def test_event_date_and_days_remaining_use_real_dates_not_text(self):
        future = (TODAY + datetime.timedelta(days=40)).isoformat()
        with get_connection(self.db_path) as conn:
            insert_special_day(conn, "Test Future Day", future)

        self.agent.build_workbook(self.db_path)
        wb = self._load()
        ws = wb["Marketing Calendar"]

        event_date_cell = ws.cell(row=2, column=5)
        # Must be a real date/datetime value, not a plain ISO text string,
        # otherwise Excel's date arithmetic (and the Dashboard's SUMPRODUCT
        # comparisons) silently fail.
        self.assertIsInstance(event_date_cell.value, (datetime.date, datetime.datetime))

        days_cell = ws.cell(row=2, column=7)
        self.assertEqual(days_cell.value, f'=IF(E2="","",E2-TODAY())')
        self.assertEqual(days_cell.number_format, "0")

    def test_days_remaining_computes_correct_value_when_recalculated(self):
        future = (TODAY + datetime.timedelta(days=40)).isoformat()
        past = (TODAY - datetime.timedelta(days=15)).isoformat()
        with get_connection(self.db_path) as conn:
            insert_special_day(conn, "Future Event", future)
            insert_special_day(conn, "Past Event", past)

        self.agent.build_workbook(self.db_path)
        wb = self._load(data_only=True)
        ws = wb["Marketing Calendar"]

        # If LibreOffice wasn't available for pre-calculation, the cached
        # value will be None (formula stays live for Excel to compute) -
        # skip the numeric assertion in that case rather than fail the
        # environment that has no LibreOffice.
        values = {}
        for row in range(2, ws.max_row + 1):
            values[ws.cell(row=row, column=2).value] = ws.cell(row=row, column=7).value

        if values["Future Event"] is not None:
            self.assertEqual(values["Future Event"], 40)
            self.assertEqual(values["Past Event"], -15)

    # ------------------------------------------------------------------
    # 2. Upcoming Campaigns excludes past events
    # ------------------------------------------------------------------
    def test_upcoming_campaigns_excludes_past_events(self):
        future = (TODAY + datetime.timedelta(days=10)).isoformat()
        past = (TODAY - datetime.timedelta(days=10)).isoformat()
        today_iso = TODAY.isoformat()
        with get_connection(self.db_path) as conn:
            insert_special_day(conn, "Future Campaign", future)
            insert_special_day(conn, "Past Campaign", past)
            insert_special_day(conn, "Today Campaign", today_iso)

        self.agent.build_workbook(self.db_path)
        wb = self._load()
        ws = wb["Upcoming Campaigns"]

        names = [ws.cell(row=r, column=1).value for r in range(2, ws.max_row + 1)]
        # Only rows through the note; filter out None/note text rows.
        names = [n for n in names if n in ("Future Campaign", "Past Campaign", "Today Campaign")]

        self.assertIn("Future Campaign", names)
        self.assertIn("Today Campaign", names)
        self.assertNotIn("Past Campaign", names)

    # ------------------------------------------------------------------
    # 3. Instagram Post Audit de-duplication
    # ------------------------------------------------------------------
    def test_instagram_audit_has_one_row_per_post_even_with_multiple_matches(self):
        with get_connection(self.db_path) as conn:
            day1 = insert_special_day(conn, "Mother's Day (UAE/Arab World)", "2026-03-21")
            day2 = insert_special_day(conn, "Mother's Day (US/UK)", "2026-05-10")
            post_id = insert_post(
                conn, "https://www.instagram.com/p/DEMO_MOTHERSDAY_2026/",
                "Happy Mother's Day! #MothersDay", "2026-03-21",
            )
            # Same post matched to two different special days - this used
            # to create two duplicate rows in the audit sheet.
            insert_match(conn, day1, post_id, 0.9)
            insert_match(conn, day2, post_id, 0.7)

        self.agent.build_workbook(self.db_path)
        wb = self._load()
        ws = wb["Instagram Post Audit"]

        urls = [ws.cell(row=r, column=1).value for r in range(2, ws.max_row + 1)]
        self.assertEqual(urls.count("https://www.instagram.com/p/DEMO_MOTHERSDAY_2026/"), 1)

        # The kept match should be the higher-confidence one.
        row = next(r for r in range(2, ws.max_row + 1)
                   if ws.cell(row=r, column=1).value == "https://www.instagram.com/p/DEMO_MOTHERSDAY_2026/")
        self.assertEqual(ws.cell(row=row, column=6).value, "Mother's Day (UAE/Arab World)")
        self.assertEqual(ws.cell(row=row, column=7).value, 0.9)

    def test_instagram_audit_labels_demo_posts_as_unverified(self):
        with get_connection(self.db_path) as conn:
            insert_post(conn, "https://www.instagram.com/p/DEMO_X/", "caption",
                        "2026-03-21", source="demo_seed")

        self.agent.build_workbook(self.db_path)
        wb = self._load()
        ws = wb["Instagram Post Audit"]
        source_value = ws.cell(row=2, column=9).value
        self.assertEqual(source_value, DEMO_SOURCE_LABEL)
        # Demo rows must not be hyperlinked as if they were verified posts.
        self.assertIsNone(ws.cell(row=2, column=1).hyperlink)

    def test_instagram_audit_labels_live_posts_as_verified(self):
        with get_connection(self.db_path) as conn:
            insert_post(conn, "https://www.instagram.com/p/REAL_X/", "caption",
                        "2026-03-21", source="graph_api")

        self.agent.build_workbook(self.db_path)
        wb = self._load()
        ws = wb["Instagram Post Audit"]
        self.assertEqual(ws.cell(row=2, column=9).value, LIVE_SOURCE_LABEL)
        self.assertIsNotNone(ws.cell(row=2, column=1).hyperlink)

    # ------------------------------------------------------------------
    # 4. Never present demo Instagram links as genuine in the calendar
    # ------------------------------------------------------------------
    def test_marketing_calendar_marks_demo_matches_as_not_verified(self):
        with get_connection(self.db_path) as conn:
            day_id = insert_special_day(conn, "Valentine's Day", "2026-02-14", category="Commercial", country="Global")
            post_id = insert_post(conn, "https://www.instagram.com/p/DEMO_VAL/", "caption",
                                   "2026-02-14", source="demo_seed")
            insert_match(conn, day_id, post_id, 0.9)

        self.agent.build_workbook(self.db_path)
        wb = self._load()
        ws = wb["Marketing Calendar"]

        ig_published = ws.cell(row=2, column=14).value
        ig_url = ws.cell(row=2, column=15).value
        self.assertEqual(ig_published, "Demo Only (Unverified)")
        self.assertEqual(ig_url, NOT_VERIFIED_TEXT)
        self.assertNotIn("DEMO_VAL", str(ig_url))
        self.assertIsNone(ws.cell(row=2, column=15).hyperlink)

    def test_marketing_calendar_shows_genuine_link_for_verified_post(self):
        with get_connection(self.db_path) as conn:
            day_id = insert_special_day(conn, "Valentine's Day", "2026-02-14", category="Commercial", country="Global")
            post_id = insert_post(conn, "https://www.instagram.com/p/REAL_VAL/", "caption",
                                   "2026-02-14", source="graph_api")
            insert_match(conn, day_id, post_id, 0.9)

        self.agent.build_workbook(self.db_path)
        wb = self._load()
        ws = wb["Marketing Calendar"]

        self.assertEqual(ws.cell(row=2, column=14).value, "Yes")
        self.assertEqual(ws.cell(row=2, column=15).value, "https://www.instagram.com/p/REAL_VAL/")
        self.assertIsNotNone(ws.cell(row=2, column=15).hyperlink)

    def test_marketing_calendar_shows_no_matching_post_when_unmatched(self):
        with get_connection(self.db_path) as conn:
            insert_special_day(conn, "Random Day", "2026-05-01", verification_status="Confirmed")

        self.agent.build_workbook(self.db_path)
        wb = self._load()
        ws = wb["Marketing Calendar"]
        self.assertEqual(ws.cell(row=2, column=14).value, "No")
        self.assertEqual(ws.cell(row=2, column=15).value, NO_MATCH_TEXT)

    # ------------------------------------------------------------------
    # 5. Dashboard formulas
    # ------------------------------------------------------------------
    def test_dashboard_formula_ranges_match_actual_row_count(self):
        with get_connection(self.db_path) as conn:
            insert_special_day(conn, "Day One", "2026-05-01")
            insert_special_day(conn, "Day Two", "2026-06-01")

        self.agent.build_workbook(self.db_path)
        wb = self._load()
        ws = wb["Dashboard"]

        formulas = {ws.cell(row=r, column=1).value: ws.cell(row=r, column=2).value
                    for r in range(5, ws.max_row) if ws.cell(row=r, column=1).value}

        self.assertEqual(formulas["Total Special Days Tracked"], "=COUNTA('Marketing Calendar'!A2:A3)")
        self.assertIn("H2:H3", formulas["Critical Priority Events"])
        self.assertIn("N2:N3", formulas["Missing / Needs Review Posts"])
        self.assertIn("G2:G3", formulas["Events in Next 30 Days"])
        self.assertIn("L2:L3", formulas["Overdue Preparation (post not verified as published)"])

    def test_dashboard_values_correct_when_recalculated(self):
        with get_connection(self.db_path) as conn:
            d1 = insert_special_day(conn, "Critical Day", "2026-05-01", priority="Critical")
            d2 = insert_special_day(conn, "High Day", "2026-06-01", priority="High")
            insert_special_day(conn, "Medium Day", "2026-07-01", priority="Medium")
            post_id = insert_post(conn, "https://www.instagram.com/p/REAL/", "c", "2026-05-01", source="graph_api")
            insert_match(conn, d1, post_id, 0.9)

        self.agent.build_workbook(self.db_path)
        wb = self._load(data_only=True)
        ws = wb["Dashboard"]

        values = {ws.cell(row=r, column=1).value: ws.cell(row=r, column=2).value
                  for r in range(5, ws.max_row) if ws.cell(row=r, column=1).value}

        if values["Total Special Days Tracked"] is not None:
            self.assertEqual(values["Total Special Days Tracked"], 3)
            self.assertEqual(values["Critical Priority Events"], 1)
            self.assertEqual(values["High Priority Events"], 1)
            self.assertEqual(values["Medium Priority Events"], 1)
            self.assertEqual(values["Instagram Posts Published (Verified Only)"], 1)


if __name__ == "__main__":
    unittest.main()
