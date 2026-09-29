import sqlite3

conn = sqlite3.connect('outputs/marketing_calendar.db')
cur = conn.cursor()

cur.execute(
    "SELECT id, name FROM special_days WHERE name LIKE ? AND year = 2026",
    ("Mother's Day%",),
)
rows = cur.fetchall()
if rows:
    for day_id, name in rows:
        cur.execute("DELETE FROM post_matches WHERE special_day_id = ?", (day_id,))
        cur.execute("DELETE FROM reminders WHERE special_day_id = ?", (day_id,))
        print(f"Reset '{name}' (id={day_id}) to unmatched / no reminder sent.")
else:
    print("No Mother's Day 2026 records found.")

conn.commit()