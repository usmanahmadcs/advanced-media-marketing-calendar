"""
calendar_agent.py
Agent 1 - Calendar Intelligence Agent

Responsible for collecting, validating, categorizing, and updating special-day
information, and writing it into the SQLite database without duplicates.

Data sources implemented:
  1. Local seed dataset (data/holidays_seed.json) - always available, offline.
     This acts as the fallback / demo dataset and covers all required
     categories from the project brief.
  2. Optional live provider hook (Calendarific-style REST API) - only runs if
     CALENDARIFIC_API_KEY is present in the environment. Network access must
     be available in the deployment environment for this to activate; if the
     request fails, the agent logs the failure and continues with the seed
     data rather than crashing or inventing data.

Variable-date rules (Mother's Day US, Father's Day, Black Friday, Cyber
Monday) are resolved with real calendar arithmetic per year. Lunar/religious
dates are marked "Provisional" and are NOT guessed - the record is written
with event_date = NULL and a note that official confirmation is required,
which satisfies the "do not assume lunar dates" requirement.
"""

import json
import os
import calendar as pycal
from datetime import date, datetime, timedelta

from src.database import get_connection


class CalendarAgent:
    def __init__(self, settings: dict, base_dir: str, logger):
        self.settings = settings
        self.base_dir = base_dir
        self.logger = logger
        self.seed_path = os.path.join(base_dir, "data", "holidays_seed.json")

    # ------------------------------------------------------------------
    # Public entry point
    # ------------------------------------------------------------------
    def collect_special_days(self, years: list) -> list:
        """Return a de-duplicated list of resolved special-day dicts for the
        given years, ready to be written to the database."""
        seed_events = self._load_seed_events()
        resolved = []
        for year in years:
            for event in seed_events:
                resolved_event = self._resolve_event_for_year(event, year)
                resolved.append(resolved_event)

        live_events = self._try_live_provider(years)
        resolved.extend(live_events)

        deduped = self._deduplicate(resolved)
        self.logger.info(
            f"CalendarAgent: resolved {len(deduped)} special-day records "
            f"for years {years} (from {len(resolved)} raw candidates)."
        )
        return deduped

    def save_to_db(self, db_path: str, events: list) -> int:
        """Insert or update events in the database. Returns count written."""
        written = 0
        now = datetime.utcnow().isoformat()
        with get_connection(db_path) as conn:
            for e in events:
                existing = conn.execute(
                    "SELECT id, manual_override FROM special_days "
                    "WHERE name=? AND country=? AND year=?",
                    (e["name"], e["country"], e["year"]),
                ).fetchone()

                if existing:
                    if existing["manual_override"]:
                        # Never clobber a manually-edited record.
                        continue
                    conn.execute(
                        """UPDATE special_days SET
                             category=?, event_date=?, date_type=?,
                             verification_status=?, data_source=?, source_url=?,
                             notes=?, last_updated=?
                           WHERE id=?""",
                        (
                            e["category"], e["event_date"], e["date_type"],
                            e["verification_status"], e["data_source"],
                            e["source_url"], e.get("notes", ""), now,
                            existing["id"],
                        ),
                    )
                else:
                    conn.execute(
                        """INSERT INTO special_days
                           (name, category, country, event_date, year, date_type,
                            verification_status, data_source, source_url, notes,
                            last_updated)
                           VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
                        (
                            e["name"], e["category"], e["country"], e["event_date"],
                            e["year"], e["date_type"], e["verification_status"],
                            e["data_source"], e["source_url"], e.get("notes", ""),
                            now,
                        ),
                    )
                written += 1
        return written

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------
    def _load_seed_events(self) -> list:
        with open(self.seed_path, "r", encoding="utf-8") as f:
            return json.load(f)

    def _resolve_event_for_year(self, event: dict, year: int) -> dict:
        date_type = event["date_type"]
        event_date = None
        notes = event.get("notes", "")

        if date_type == "fixed":
            event_date = date(year, event["month"], event["day"]).isoformat()

        elif date_type == "variable_rule":
            event_date = self._apply_rule(event["rule"], year)

        elif date_type == "lunar_estimated":
            event_date = None  # Deliberately not guessed.
            notes = (notes + " Awaiting official moon-sighting confirmation "
                            "for this year.").strip()

        return {
            "name": event["name"],
            "category": event["category"],
            "country": event["country"],
            "event_date": event_date,
            "year": year,
            "date_type": date_type,
            "verification_status": event["verification_status"] if event_date else "Provisional",
            "data_source": event["source"],
            "source_url": event["source_url"],
            "notes": notes,
        }

    @staticmethod
    def _apply_rule(rule: str, year: int) -> str:
        """Resolve a small set of well-known variable-date rules."""

        def nth_weekday_of_month(year, month, weekday, n):
            # weekday: Monday=0 ... Sunday=6
            c = pycal.Calendar()
            days = [d for d in c.itermonthdates(year, month)
                    if d.month == month and d.weekday() == weekday]
            return days[n - 1]

        def last_weekday_of_month(year, month, weekday):
            c = pycal.Calendar()
            days = [d for d in c.itermonthdates(year, month)
                    if d.month == month and d.weekday() == weekday]
            return days[-1]

        if rule == "second_sunday_of_may":
            return nth_weekday_of_month(year, 5, 6, 2).isoformat()

        if rule == "third_sunday_of_june":
            return nth_weekday_of_month(year, 6, 6, 3).isoformat()

        if rule == "friday_after_us_thanksgiving":
            # US Thanksgiving = 4th Thursday of November
            thanksgiving = nth_weekday_of_month(year, 11, 3, 4)
            return (thanksgiving + timedelta(days=1)).isoformat()

        if rule == "monday_after_black_friday":
            thanksgiving = nth_weekday_of_month(year, 11, 3, 4)
            black_friday = thanksgiving + timedelta(days=1)
            # Black Friday always falls on a Friday, so +3 days always lands
            # on the following Monday (Cyber Monday).
            return (black_friday + timedelta(days=3)).isoformat()

        return None

    def _try_live_provider(self, years: list) -> list:
        """Optional live holiday API integration (Calendarific-style).
        Only attempted if an API key is configured; failures are logged and
        swallowed so the seed dataset remains the reliable fallback."""
        api_key = os.environ.get("CALENDARIFIC_API_KEY")
        if not api_key:
            self.logger.info(
                "CalendarAgent: CALENDARIFIC_API_KEY not configured - "
                "skipping live provider, using seed dataset only."
            )
            return []
        try:
            import requests  # imported lazily; optional dependency
        except ImportError:
            self.logger.warning(
                "CalendarAgent: 'requests' package not installed - "
                "cannot call live holiday API."
            )
            return []

        events = []
        countries = self.settings.get("countries", ["AE"])
        for year in years:
            for country in countries:
                try:
                    resp = requests.get(
                        "https://calendarific.com/api/v2/holidays",
                        params={"api_key": api_key, "country": country, "year": year},
                        timeout=10,
                    )
                    resp.raise_for_status()
                    payload = resp.json()
                    for h in payload.get("response", {}).get("holidays", []):
                        events.append({
                            "name": h.get("name"),
                            "category": ", ".join(h.get("type", [])) or "Other",
                            "country": country,
                            "event_date": h.get("date", {}).get("iso", "")[:10],
                            "year": year,
                            "date_type": "fixed",
                            "verification_status": "Confirmed",
                            "data_source": "Calendarific API",
                            "source_url": "https://calendarific.com/",
                            "notes": "",
                        })
                except Exception as exc:  # noqa: BLE001 - log and continue
                    self.logger.warning(
                        f"CalendarAgent: live provider request failed for "
                        f"{country}/{year}: {exc}"
                    )
        return events

    @staticmethod
    def _deduplicate(events: list) -> list:
        seen = {}
        for e in events:
            key = (e["name"].strip().lower(), e["country"], e["year"])
            if key not in seen:
                seen[key] = e
        return list(seen.values())
