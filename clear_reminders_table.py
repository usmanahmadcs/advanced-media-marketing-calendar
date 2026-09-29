import sqlite3

conn = sqlite3.connect('outputs/marketing_calendar.db')
conn.execute('DELETE FROM reminders')
conn.commit()
print('Cleared reminders table.')