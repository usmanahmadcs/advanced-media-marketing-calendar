"""
orchestrator.py
Coordinates Agents 1-5 into the full synchronization workflow described in
the project brief (section 10):

  1. Fetch calendar info -> 2. Normalize/validate -> 3. Update DB (no dupes)
  4. Retrieve Instagram posts -> 5. Match posts -> 6. Recalculate priorities
  7. Update Excel workbook -> 8/9. Identify + send due reminders
  10. Record synchronization results

Each step is wrapped so that a failure in one component does not stop the
others (section 11 - reliability requirement), and every run is written to
the sync_log table.
"""

import os
from datetime import datetime, date

from src.database import init_db, get_db_path, get_connection
from src.calendar_agent import CalendarAgent
from src.instagram_agent import InstagramAgent
from src.priority_agent import PriorityAgent
from src.excel_agent import ExcelAgent
from src.reminder_agent import ReminderAgent
from src.utils.logger import get_logger


class Orchestrator:
    def __init__(self, settings: dict, base_dir: str):
        self.settings = settings
        self.base_dir = base_dir
        self.logger = get_logger("orchestrator", base_dir)
        self.db_path = get_db_path(settings, base_dir)
        init_db(self.db_path)

        self.calendar_agent = CalendarAgent(settings, base_dir, get_logger("calendar_agent", base_dir))
        self.instagram_agent = InstagramAgent(settings, base_dir, get_logger("instagram_agent", base_dir))
        self.priority_agent = PriorityAgent(settings, base_dir, get_logger("priority_agent", base_dir))
        self.excel_agent = ExcelAgent(settings, base_dir, get_logger("excel_agent", base_dir))
        self.reminder_agent = ReminderAgent(settings, base_dir, get_logger("reminder_agent", base_dir))

    def run_full_sync(self) -> dict:
        results = {}
        current_year = date.today().year
        years_ahead = self.settings.get("calendar", {}).get("years_ahead", 2)
        years = list(range(current_year, current_year + years_ahead + 1))

        results["calendar"] = self._run_step("calendar", lambda: self._sync_calendar(years))
        results["instagram"] = self._run_step("instagram", self._sync_instagram)
        results["matching"] = self._run_step("matching", self._sync_matching)
        results["priority"] = self._run_step("priority", self._sync_priority)
        results["excel"] = self._run_step("excel", self._sync_excel)
        results["reminder"] = self._run_step("reminder", self._sync_reminders)

        self.logger.info(f"Orchestrator: full sync complete. Summary: {results}")
        return results

    # ------------------------------------------------------------------
    def _run_step(self, component: str, fn):
        started = datetime.utcnow().isoformat()
        try:
            outcome = fn()
            status = "success"
            message = str(outcome)
        except Exception as exc:  # noqa: BLE001 - isolate failures per component
            outcome = None
            status = "failed"
            message = f"{type(exc).__name__}: {exc}"
            self.logger.error(f"{component} step failed: {message}")
        finished = datetime.utcnow().isoformat()

        with get_connection(self.db_path) as conn:
            conn.execute(
                """INSERT INTO sync_log
                   (run_started_at, run_finished_at, component, status,
                    records_processed, message)
                   VALUES (?,?,?,?,?,?)""",
                (started, finished, component, status,
                 outcome if isinstance(outcome, int) else None, message),
            )
        return {"status": status, "detail": message}

    def _sync_calendar(self, years):
        events = self.calendar_agent.collect_special_days(years)
        return self.calendar_agent.save_to_db(self.db_path, events)

    def _sync_instagram(self):
        posts = self.instagram_agent.collect_posts()
        return self.instagram_agent.save_posts(self.db_path, posts)

    def _sync_matching(self):
        summary = self.instagram_agent.match_posts_to_days(self.db_path)
        return summary["matched"]

    def _sync_priority(self):
        return self.priority_agent.classify_all(self.db_path)

    def _sync_excel(self):
        path = self.excel_agent.build_workbook(self.db_path)
        return path

    def _sync_reminders(self):
        summary = self.reminder_agent.process_due_reminders(self.db_path)
        return summary["sent"]

    def get_last_sync_summary(self) -> list:
        with get_connection(self.db_path) as conn:
            rows = conn.execute(
                "SELECT * FROM sync_log ORDER BY id DESC LIMIT 20"
            ).fetchall()
            return [dict(r) for r in rows]
