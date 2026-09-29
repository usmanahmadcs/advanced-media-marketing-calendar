import os
from run_sync import load_env_file

load_env_file()

from src.google_calendar_client import GoogleCalendarClient

client = GoogleCalendarClient(
    os.environ['GOOGLE_CALENDAR_CREDENTIALS_FILE'],
    os.environ.get('GOOGLE_CALENDAR_TOKEN_FILE') or 'config/google_token.json',
)
service = client.get_service()
events = service.events().list(
    calendarId='primary', q='Reminder:', maxResults=50, singleEvents=True
).execute()

for e in events.get('items', []):
    print('Deleting:', e.get('summary'))
    service.events().delete(calendarId='primary', eventId=e['id']).execute()

print('Done.')