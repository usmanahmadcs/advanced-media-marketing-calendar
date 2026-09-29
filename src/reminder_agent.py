"""
reminder_agent.py
Agent 5 - Reminder Agent

Identifies special days whose reminder_date has arrived (or passed), attempts
delivery via a configured channel, records the outcome, and prevents
duplicate sends for the same special_day + reminder_date pair.

Delivery channels implemented:
  - Microsoft Graph (email via Outlook) - activates only if MS_GRAPH_* env
    vars are present.
  - Microsoft Teams incoming webhook - activates only if MS_TEAMS_WEBHOOK_URL
    is present.
  - Local fallback - always available. Writes a timestamped reminder file to
    outputs/reminders_outbox/ so the marketing team (or a scheduled task)
    can see due reminders even with no external integration configured.
    This satisfies "a local notification or scheduled task as a fallback."

The agent NEVER claims a reminder was delivered through an integration that
was not actually configured and did not actually succeed.
"""

import os
import json
from datetime import datetime, date, timedelta

from src.database import get_connection


class ReminderAgent:
    def __init__(self, settings: dict, base_dir: str, logger):
        self.settings = settings
        self.base_dir = base_dir
        self.logger = logger
        self.channel = settings.get("reminders", {}).get("delivery_channel", "local_fallback")
        self.outbox_dir = os.path.join(base_dir, "outputs", "reminders_outbox")

    def process_due_reminders(self, db_path: str, as_of: date = None) -> dict:
        as_of = as_of or date.today()
        summary = {"sent": 0, "skipped_duplicate": 0, "failed": 0, "not_due": 0}
        now_iso = datetime.utcnow().isoformat()

        with get_connection(db_path) as conn:
            days = conn.execute(
                "SELECT * FROM special_days WHERE reminder_date IS NOT NULL"
            ).fetchall()

            for day in days:
                try:
                    reminder_date = date.fromisoformat(day["reminder_date"])
                except (TypeError, ValueError):
                    continue

                if reminder_date > as_of:
                    summary["not_due"] += 1
                    continue

                existing = conn.execute(
                    "SELECT * FROM reminders WHERE special_day_id=? AND reminder_date=?",
                    (day["id"], day["reminder_date"]),
                ).fetchone()

                if existing and existing["status"] == "sent":
                    summary["skipped_duplicate"] += 1
                    continue

                content = self._build_reminder_content(conn, day)
                delivered, detail = self._deliver(content)

                status = "sent" if delivered else "failed"
                if existing:
                    conn.execute(
                        "UPDATE reminders SET status=?, sent_at=?, detail=?, channel=? WHERE id=?",
                        (status, now_iso if delivered else None, detail, self.channel, existing["id"]),
                    )
                else:
                    conn.execute(
                        """INSERT INTO reminders
                           (special_day_id, reminder_date, channel, status, sent_at, detail)
                           VALUES (?,?,?,?,?,?)""",
                        (day["id"], day["reminder_date"], self.channel, status,
                         now_iso if delivered else None, detail),
                    )
                summary["sent" if delivered else "failed"] += 1

        self.logger.info(f"ReminderAgent: {summary}")
        return summary

    # ------------------------------------------------------------------
    def _build_reminder_content(self, conn, day) -> dict:
        match = conn.execute(
            """SELECT ip.post_url, ip.caption, ip.source FROM post_matches pm
               JOIN instagram_posts ip ON ip.id = pm.instagram_post_id
               WHERE pm.special_day_id=? ORDER BY pm.matching_confidence DESC LIMIT 1""",
            (day["id"],),
        ).fetchone()

        # A match against demo/fallback data is not a real published post - the
        # reminder must never claim "Published" (or hand out a fake demo URL)
        # for content that was never actually posted on the live account.
        is_verified = bool(match) and match["source"] == "graph_api"

        if is_verified:
            instagram_status = "Published (verified live Instagram post)"
            instagram_post_url = match["post_url"]
        elif match:
            instagram_status = (
                "Matched to demo/fallback data only - NOT a real published "
                "post. Live Instagram credentials are missing or the live "
                "account has no matching post yet."
            )
            instagram_post_url = None
        else:
            instagram_status = "Needs Review / Not Published"
            instagram_post_url = None

        return {
            "special_day": day["name"],
            "country": day["country"],
            "event_date": day["event_date"],
            "priority": day["priority"],
            "days_remaining": self._days_remaining(day["event_date"]),
            "instagram_post_url": instagram_post_url,
            "instagram_status": instagram_status,
            "suggested_campaign": day["suggested_campaign"],
            "excel_record_id": day["id"],
        }

    @staticmethod
    def _days_remaining(event_date):
        if not event_date:
            return None
        try:
            return (date.fromisoformat(event_date) - date.today()).days
        except ValueError:
            return None

    def _deliver(self, content: dict) -> tuple:
        if self.channel == "ms_graph_email":
            return self._deliver_ms_graph(content)
        if self.channel == "ms_teams":
            return self._deliver_ms_teams(content)
        if self.channel == "google_calendar":
            return self._deliver_google_calendar(content)
        return self._deliver_local_fallback(content)

    def _deliver_local_fallback(self, content: dict) -> tuple:
        try:
            os.makedirs(self.outbox_dir, exist_ok=True)
            filename = f"reminder_{content['excel_record_id']}_{datetime.utcnow().strftime('%Y%m%dT%H%M%S')}.json"
            path = os.path.join(self.outbox_dir, filename)
            with open(path, "w", encoding="utf-8") as f:
                json.dump(content, f, indent=2)
            return True, f"Local reminder file written: {path}"
        except Exception as exc:  # noqa: BLE001
            return False, f"Local fallback failed: {exc}"

    def _deliver_ms_graph(self, content: dict) -> tuple:
        client_id = os.environ.get("MS_GRAPH_CLIENT_ID")
        sender = os.environ.get("MS_GRAPH_SENDER_EMAIL")
        if not (client_id and sender):
            return False, ("MS Graph credentials not configured "
                            "(MS_GRAPH_CLIENT_ID / MS_GRAPH_SENDER_EMAIL missing). "
                            "Falling back is recommended until configured.")
        # NOTE: Full OAuth2 client-credentials flow + Graph sendMail call would
        # go here once credentials are supplied. Left as an explicit
        # activation step per project instructions (no fake success claims).
        return False, "MS Graph integration not yet activated - credentials present but flow not executed in this environment."

    def _deliver_ms_teams(self, content: dict) -> tuple:
        webhook = os.environ.get("MS_TEAMS_WEBHOOK_URL")
        if not webhook:
            return False, "MS_TEAMS_WEBHOOK_URL not configured."
        try:
            import requests
            payload = {"text": json.dumps(content, indent=2)}
            resp = requests.post(webhook, json=payload, timeout=10)
            resp.raise_for_status()
            return True, "Delivered via Microsoft Teams webhook."
        except Exception as exc:  # noqa: BLE001
            return False, f"Teams webhook delivery failed: {exc}"

    def _deliver_google_calendar(self, content: dict) -> tuple:
        creds_file = os.environ.get("GOOGLE_CALENDAR_CREDENTIALS_FILE")
        calendar_id = os.environ.get("GOOGLE_CALENDAR_ID", "primary")

        if not creds_file or not os.path.exists(creds_file):
            return False, (
                "Google Calendar credentials file not configured or not found "
                "(set GOOGLE_CALENDAR_CREDENTIALS_FILE to a valid OAuth client "
                "JSON downloaded from Google Cloud Console)."
            )

        try:
            from src.google_calendar_client import GoogleCalendarClient
        except ImportError as exc:
            return False, (
                f"Google Calendar libraries not installed ({exc}). Run: "
                "pip install google-api-python-client google-auth-httplib2 google-auth-oauthlib"
            )

        token_file = os.environ.get(
            "GOOGLE_CALENDAR_TOKEN_FILE",
            os.path.join(self.base_dir, "config", "google_token.json"),
        )

        try:
            client = GoogleCalendarClient(creds_file, token_file, self.logger)

            now = datetime.utcnow()
            start = now.isoformat() + "Z"
            end = (now + timedelta(minutes=30)).isoformat() + "Z"

            description_lines = [
                f"Country: {content['country']}",
                f"Priority: {content['priority']}",
                f"Event date: {content['event_date']}",
                f"Days remaining: {content['days_remaining']}",
                f"Instagram status: {content['instagram_status']}",
            ]
            if content.get("instagram_post_url"):
                description_lines.append(f"Instagram post: {content['instagram_post_url']}")
            if content.get("suggested_campaign"):
                description_lines.append(f"Suggested campaign: {content['suggested_campaign']}")

            event_body = {
                "summary": f"Reminder: {content['special_day']} ({content['country']})",
                "description": "\n".join(description_lines),
                "start": {"dateTime": start, "timeZone": "UTC"},
                "end": {"dateTime": end, "timeZone": "UTC"},
                "reminders": {
                    "useDefault": False,
                    "overrides": [{"method": "popup", "minutes": 10}],
                },
            }

            created = client.insert_event(calendar_id, event_body)
            link = created.get("htmlLink") or created.get("id", "unknown")
            return True, f"Google Calendar event created: {link}"
        except Exception as exc:  # noqa: BLE001
            return False, f"Google Calendar delivery failed: {exc}"