"""
google_calendar_client.py
Agent 5 helper - thin wrapper around the real Google Calendar API (v3),
used by ReminderAgent's "google_calendar" delivery channel.

Authentication model (OAuth2 "installed app" flow - the correct flow for a
desktop script acting on a single Google/Gmail account, e.g. a test
account):

  1. A Google Cloud project has the Calendar API enabled and an OAuth
     Client ID of type "Desktop app". The downloaded client file
     (commonly named credentials.json) is referenced by the
     GOOGLE_CALENDAR_CREDENTIALS_FILE environment variable. This file
     contains only a client_id/client_secret pair for the *application*,
     not any user's password.
  2. The FIRST time this runs, there is no cached token yet, so it opens a
     local browser window and asks the person to log into the Google/Gmail
     account they want events created on, and click "Allow". This consent
     step happens on the operator's own machine - the account password is
     never seen, requested, or transmitted by this code.
  3. The resulting OAuth token is cached to GOOGLE_CALENDAR_TOKEN_FILE
     (default: config/google_token.json) so subsequent runs are silent and
     do not require logging in again, until the token is revoked/expired.

This module never hard-codes, logs, or prints any credential, token, or
account identifier. If the required libraries or files are missing, it
raises/returns clearly so ReminderAgent can fall back honestly instead of
claiming a delivery that did not happen.
"""

import os


SCOPES = ["https://www.googleapis.com/auth/calendar.events"]


class GoogleCalendarClient:
    def __init__(self, credentials_file: str, token_file: str, logger=None):
        self.credentials_file = credentials_file
        self.token_file = token_file
        self.logger = logger
        self._service = None

    def is_configured(self) -> bool:
        return bool(self.credentials_file and os.path.exists(self.credentials_file))

    # ------------------------------------------------------------------
    def _get_credentials(self):
        # Imported lazily so the rest of the system works even if these
        # optional packages are not installed (see requirements.txt note).
        from google.auth.transport.requests import Request
        from google.oauth2.credentials import Credentials
        from google_auth_oauthlib.flow import InstalledAppFlow

        creds = None
        if os.path.exists(self.token_file):
            creds = Credentials.from_authorized_user_file(self.token_file, SCOPES)

        if not creds or not creds.valid:
            if creds and creds.expired and creds.refresh_token:
                creds.refresh(Request())
            else:
                flow = InstalledAppFlow.from_client_secrets_file(
                    self.credentials_file, SCOPES
                )
                # Opens the operator's default browser for a one-time,
                # account-owner-only consent screen.
                creds = flow.run_local_server(port=0)

            token_dir = os.path.dirname(self.token_file)
            if token_dir:
                os.makedirs(token_dir, exist_ok=True)
            with open(self.token_file, "w", encoding="utf-8") as f:
                f.write(creds.to_json())
            if self.logger:
                self.logger.info(
                    f"GoogleCalendarClient: new/refreshed token cached at {self.token_file}"
                )

        return creds

    def get_service(self):
        if self._service is not None:
            return self._service
        from googleapiclient.discovery import build

        creds = self._get_credentials()
        self._service = build("calendar", "v3", credentials=creds, cache_discovery=False)
        return self._service

    def insert_event(self, calendar_id: str, event_body: dict) -> dict:
        service = self.get_service()
        return service.events().insert(calendarId=calendar_id, body=event_body).execute()
