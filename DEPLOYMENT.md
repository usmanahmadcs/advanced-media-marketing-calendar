# Deployment Guide — Windows / Windows VPS

## 1. Prerequisites

- Windows 10/11 or Windows Server (VPS).
- Python 3.9+ installed from https://www.python.org/downloads/windows/
  (check "Add Python to PATH" during install).
- Network access to: Instagram Graph API (`graph.facebook.com`), Calendarific
  (optional), Microsoft Graph / Teams (if used), Google APIs (if used).

## 2. Install

Open PowerShell:

```powershell
cd C:\Apps
git clone <your-repo-or-copy-the-project-folder-here> AdvancedMediaMarketingCalendar
cd AdvancedMediaMarketingCalendar
python -m venv venv
venv\Scripts\activate
pip install -r requirements.txt
copy config\config.example.env .env
notepad .env   # fill in real credentials
```

## 3. First Run

```powershell
python run_sync.py
```

Confirm `outputs\Advanced_Media_Special_Days_Marketing_Calendar.xlsx` was created
and open it to review the demo data.

## 4. Schedule Daily Synchronization (Windows Task Scheduler)

1. Open **Task Scheduler** → **Create Task**.
2. **General** tab: name it `AMT Marketing Calendar Sync`; select
   "Run whether user is logged on or not."
3. **Triggers** tab → New → Daily, pick a time (e.g. 06:00), recur every 1 day.
4. **Actions** tab → New:
   - Program/script: `C:\Apps\AdvancedMediaMarketingCalendar\venv\Scripts\python.exe`
   - Add arguments: `run_sync.py`
   - Start in: `C:\Apps\AdvancedMediaMarketingCalendar`
5. **Conditions/Settings**: uncheck "Start the task only if the computer is on AC
   power" if this is a server; enable "Run task as soon as possible after a
   scheduled start is missed."
6. Save, then right-click the task → **Run** to confirm it executes correctly, and
   check `logs\system.log` afterward.

This satisfies the "Calendar refresh: Daily" / "Instagram refresh: Daily" /
"Reminder processing: at least once daily" requirements from the brief using a
mechanism native to Windows (no extra scheduler service required).

## 5. Alternative: APScheduler as a long-running service

If you prefer a persistent process instead of Task Scheduler, install
`apscheduler` (`pip install apscheduler`) and wrap `Orchestrator.run_full_sync()`
in a `BlockingScheduler` with a daily cron trigger, then run it via NSSM
(https://nssm.cc/) to install it as a genuine Windows service. This repo ships
the Task Scheduler approach by default because it requires no extra service
manager and is easier for a non-technical team to inspect and restart.

## 6. File & Data Locations

- Database: `outputs\marketing_calendar.db` (back this up — it is the system of
  record; the Excel file is a generated report, not the source of truth).
- Excel workbook: `outputs\Advanced_Media_Special_Days_Marketing_Calendar.xlsx`.
- Logs: `logs\system.log`.
- Local reminder fallback files: `outputs\reminders_outbox\`.

## 7. Restart Safety

Restarting the machine or the scheduled task does not lose data: `special_days`,
`instagram_posts`, `post_matches`, `reminders` and `sync_log` all live in the
SQLite file under `outputs\`, and every write path uses `INSERT ... ON CONFLICT`
or an existence check before insert, so a re-run after a crash mid-sync will not
create duplicates (see the calendar/instagram dedup tests in `tests\`).

## 8. Required API Permissions Summary

| Integration | Permission / Scope | Where to obtain |
|---|---|---|
| Instagram Graph API | `instagram_basic`, `pages_read_engagement`, `pages_show_list`, `business_management`, `instagram_content_publish` | developers.facebook.com → your App → Graph API Explorer → Permissions |
| Calendarific (optional) | API key (no OAuth) | calendarific.com account dashboard |
| Microsoft Graph (email) | `Mail.Send` (application or delegated) | Azure Portal → App registrations → API permissions |
| Microsoft Teams | Incoming Webhook URL | Teams channel → Connectors → Incoming Webhook |
| Google Calendar | Calendar API, `calendar.events` scope | Google Cloud Console → APIs & Services → Credentials |

### 8.1 Getting a working Instagram access token (step by step)

The Graph API Explorer's default "Generate Access Token" button gives you a
**User** token that expires in about an hour and won't work for the fields
this system needs. Follow these steps exactly:

1. Go to https://developers.facebook.com/tools/explorer/, select your App and
   your Page under "User or Page", then check **all five** permissions above
   in the Permissions panel before generating the token.
2. Click **Generate Access Token** and approve the prompts.
3. Confirm the Instagram Business Account is linked: run
   `GET me/accounts?fields=name,instagram_business_account`. If the field is
   missing from the response, `instagram_basic` was not actually granted to
   this token (re-check step 1) — an account can look "connected" in
   Business Settings and still not return this field without that scope.
4. Get a **Page** token (not the User token): run
   `GET me/accounts?fields=name,access_token` and copy the `access_token`
   from the result.
5. Exchange your **User** token for a long-lived one (60 days). Find your
   App ID/Secret under App Settings → Basic, then open this URL in a browser
   (fill in the placeholders):
   ```
   https://graph.facebook.com/v26.0/oauth/access_token?grant_type=fb_exchange_token&client_id=APP_ID&client_secret=APP_SECRET&fb_exchange_token=USER_TOKEN_FROM_STEP_2
   ```
   The response's `access_token` is your long-lived User token
   (`expires_in` ≈ 5184000 seconds / 60 days).
6. Re-run step 4 (`me/accounts?fields=name,access_token`) using this
   long-lived User token in place of the short-lived one. The Page token you
   get back inherits the long-lived status and, for Pages, effectively does
   not expire on its own — this is the value to put in
   `INSTAGRAM_ACCESS_TOKEN`.

## 9. Troubleshooting

| Symptom | Likely cause | Fix |
|---|---|---|
| `ModuleNotFoundError: openpyxl` | venv not activated or deps not installed | `venv\Scripts\activate` then `pip install -r requirements.txt` |
| Workbook opens with `#NAME?` in a formula cell | A non-Excel-2007 function was introduced by a manual edit | Stick to `SUMIFS`/`COUNTIF`/`INDEX`/`MATCH`; avoid `XLOOKUP`/`FILTER` in manual edits |
| Instagram posts always come from `demo_seed` | `INSTAGRAM_ACCESS_TOKEN` / `INSTAGRAM_BUSINESS_ACCOUNT_ID` missing or invalid | Check `.env`, check `logs\system.log` for the Graph API error |
| Reminder shows `status: failed` for `ms_graph_email` | OAuth flow not yet wired for your tenant | See README section 4.3 — this is a documented pending activation step |
| Task Scheduler task doesn't run | Wrong "Start in" path, or python.exe path wrong | Re-check step 4 above; test by right-click → Run and inspect the Task Scheduler history tab |
| Duplicate-looking rows for Mother's Day | Different rows are actually different **years** or different **country variants** (UAE vs US/UK) — this is intentional, not a bug | Filter by Year/Country column in Excel |
