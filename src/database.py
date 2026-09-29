"""
database.py
Persistent storage layer (SQLite) for the Advanced Media Marketing Calendar system.

Responsible for:
- Creating/initializing schema
- Storing special days, Instagram posts, matches, reminders and sync logs
- De-duplicating records on insert
"""

import sqlite3
import os
from contextlib import contextmanager

SCHEMA = """
CREATE TABLE IF NOT EXISTS special_days (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    category TEXT NOT NULL,
    country TEXT NOT NULL,
    event_date TEXT,              -- ISO date, NULL if unresolved (e.g. lunar TBD)
    year INTEGER NOT NULL,
    date_type TEXT,               -- fixed | variable_rule | lunar_estimated
    verification_status TEXT,     -- Confirmed | Provisional | Needs Review
    data_source TEXT,
    source_url TEXT,
    notes TEXT,
    priority TEXT,                -- Critical | High | Medium | Low
    priority_score REAL,
    priority_reason TEXT,
    suggested_campaign TEXT,
    preparation_deadline TEXT,
    reminder_date TEXT,
    reminder_lead_days INTEGER,
    manual_override INTEGER DEFAULT 0,
    last_updated TEXT,
    UNIQUE(name, country, year)
);

CREATE TABLE IF NOT EXISTS instagram_posts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    post_url TEXT NOT NULL UNIQUE,
    caption TEXT,
    publication_date TEXT,
    likes INTEGER,
    comments INTEGER,
    source TEXT,                  -- 'graph_api' | 'manual_import' | 'demo_seed'
    last_updated TEXT
);

CREATE TABLE IF NOT EXISTS post_matches (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    special_day_id INTEGER NOT NULL,
    instagram_post_id INTEGER NOT NULL,
    matching_confidence REAL,
    match_method TEXT,            -- 'keyword' | 'date_proximity' | 'manual'
    confirmed_by_user INTEGER DEFAULT 0,
    last_updated TEXT,
    FOREIGN KEY (special_day_id) REFERENCES special_days(id),
    FOREIGN KEY (instagram_post_id) REFERENCES instagram_posts(id),
    UNIQUE(special_day_id, instagram_post_id)
);

CREATE TABLE IF NOT EXISTS reminders (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    special_day_id INTEGER NOT NULL,
    reminder_date TEXT NOT NULL,
    channel TEXT,
    status TEXT,                  -- 'pending' | 'sent' | 'failed' | 'skipped_duplicate'
    sent_at TEXT,
    detail TEXT,
    FOREIGN KEY (special_day_id) REFERENCES special_days(id),
    UNIQUE(special_day_id, reminder_date)
);

CREATE TABLE IF NOT EXISTS sync_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    run_started_at TEXT,
    run_finished_at TEXT,
    component TEXT,               -- 'calendar' | 'instagram' | 'priority' | 'excel' | 'reminder'
    status TEXT,                  -- 'success' | 'partial' | 'failed'
    records_processed INTEGER,
    message TEXT
);
"""


def get_db_path(settings: dict, base_dir: str) -> str:
    rel_path = settings.get("database", {}).get("path", "outputs/marketing_calendar.db")
    return os.path.join(base_dir, rel_path)


def init_db(db_path: str) -> None:
    os.makedirs(os.path.dirname(db_path), exist_ok=True)
    conn = sqlite3.connect(db_path)
    try:
        conn.executescript(SCHEMA)
        conn.commit()
    finally:
        conn.close()


@contextmanager
def get_connection(db_path: str):
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON;")
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()
