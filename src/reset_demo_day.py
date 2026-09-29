import sqlite3

conn = sqlite3.connect('outputs/marketing_calendar.db')
cur = conn.cursor()

cur.execute("SELECT id FROM special_days WHERE name = ? AND year = 2026", ("Father's Day",))
row = cur.fetchone()
if row:
    day_id = row[0]
    cur.execute("DELETE FROM post_matches WHERE special_day_id = ?", (day_id,))
    cur.execute("DELETE FROM reminders WHERE special_day_id = ?", (day_id,))
    print(f"Reset Father's Day (id={day_id}) to unmatched / no reminder sent.")
else:
    print("Father's Day 2026 not found.")

conn.commit()