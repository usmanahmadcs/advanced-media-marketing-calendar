#!/usr/bin/env python3
"""
run_sync.py
Command-line entry point for Advanced Media Trading LLC's
Special Days & Instagram Marketing Intelligence System.

Usage:
    python run_sync.py                 # run full synchronization
    python run_sync.py --summary        # show last sync log entries only
    python run_sync.py --demo-reminder  # insert + fire one clearly-labeled
                                         # test reminder due today, through
                                         # whichever channel is configured
                                         # (e.g. google_calendar), so a demo
                                         # audience sees a real delivery
                                         # without waiting for a real special
                                         # day's reminder_date to arrive.
"""

import argparse
import json
import os
import sys
from datetime import date, datetime

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, BASE_DIR)

from src.database import get_connection  # noqa: E402
from src.orchestrator import Orchestrator  # noqa: E402


def load_settings():
    settings_path = os.path.join(BASE_DIR, "config", "settings.json")
    with open(settings_path, "r", encoding="utf-8") as f:
        return json.load(f)


def load_env_file():
    """Lightweight .env loader (no external dependency required)."""
    env_path = os.path.join(BASE_DIR, ".env")
    if not os.path.exists(env_path):
        return
    with open(env_path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, value = line.partition("=")
            if key and value:
                os.environ.setdefault(key.strip(), value.strip())


def insert_demo_reminder_record(db_path: str) -> None:
    """Insert (if not already present) one clearly-labeled demo special day
    whose reminder_date is today, so a demo audience can see a real
    reminder delivery fire through whichever channel is configured,
    without waiting for a genuine special day's reminder_date to arrive.
    This never touches or duplicates real business data - the record is
    unambiguously named and always in the 'Demo' category."""
    today_iso = date.today().isoformat()
    now_iso = datetime.utcnow().isoformat()
    with get_connection(db_path) as conn:
        existing = conn.execute(
            "SELECT id FROM special_days WHERE name=? AND country=? AND year=?",
            ("[DEMO] Live Reminder Test", "Global", date.today().year),
        ).fetchone()
        if existing:
            conn.execute(
                "UPDATE special_days SET reminder_date=?, event_date=?, last_updated=? WHERE id=?",
                (today_iso, today_iso, now_iso, existing["id"]),
            )
        else:
            conn.execute(
                """INSERT INTO special_days
                   (name, category, country, event_date, year, date_type,
                    verification_status, data_source, source_url, priority,
                    reminder_date, last_updated)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    "[DEMO] Live Reminder Test", "Demo", "Global", today_iso,
                    date.today().year, "fixed", "Confirmed", "demo_script",
                    "https://example.com", "High", today_iso, now_iso,
                ),
            )
        # Clear any prior "sent" status for this specific demo record so it
        # fires again on every demo run - never affects real special days.
        conn.execute(
            "DELETE FROM reminders WHERE special_day_id=(SELECT id FROM special_days "
            "WHERE name=? AND country=? AND year=?)",
            ("[DEMO] Live Reminder Test", "Global", date.today().year),
        )


def main():
    parser = argparse.ArgumentParser(description="Advanced Media Marketing Calendar sync")
    parser.add_argument("--summary", action="store_true", help="print last sync log entries")
    parser.add_argument(
        "--demo-reminder", action="store_true",
        help="insert + fire one clearly-labeled test reminder due today "
             "through the configured delivery channel, for demo purposes",
    )
    args = parser.parse_args()

    load_env_file()
    settings = load_settings()
    orchestrator = Orchestrator(settings, BASE_DIR)

    if args.summary:
        for entry in orchestrator.get_last_sync_summary():
            print(entry)
        return

    if args.demo_reminder:
        print(f"Inserting demo reminder record (channel: "
              f"{settings.get('reminders', {}).get('delivery_channel')})...")
        insert_demo_reminder_record(orchestrator.db_path)
        summary = orchestrator.reminder_agent.process_due_reminders(orchestrator.db_path)
        print(json.dumps(summary, indent=2))
        if summary.get("sent"):
            print("\nDemo reminder delivered successfully through the configured channel.")
        elif summary.get("failed"):
            print("\nDemo reminder FAILED to deliver - check the reminders table 'detail' "
                  "column or logs/reminder_agent.log for the exact reason.")
        return

    print("Starting synchronization...")
    results = orchestrator.run_full_sync()
    print(json.dumps(results, indent=2))
    print(f"\nWorkbook: {orchestrator.excel_agent.output_path}")
    print(f"Database: {orchestrator.db_path}")


if __name__ == "__main__":
    main()
