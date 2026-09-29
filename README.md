# Advanced Media Trading LLC — Special Days Calendar & Instagram Marketing Intelligence System

A working, modular system that:

- Maintains a database of international, family, professional, UAE, religious and
  commercial special days.
- Matches Advanced Media Trading LLC's Instagram posts to those special days.
- Scores and classifies marketing priority (Critical / High / Medium / Low) with a
  transparent, configurable model.
- Generates a five-sheet Excel workbook with live formulas, hyperlinks, conditional
  formatting and filters.
- Computes and (where configured) delivers advance reminders, without duplicates.

This is a real, runnable Python project — not a mockup. Running `python run_sync.py`
today, with zero credentials configured, produces a fully populated demo workbook
using an offline seed dataset (clearly labeled as demo data) so you can see the
entire system working end-to-end before connecting any live APIs.

## 1. Project Structure

```
project/
├── run_sync.py                  # CLI entry point
├── requirements.txt
├── config/
│   ├── settings.json            # all configurable business rules
│   └── config.example.env       # copy to ".env" and fill in real credentials
├── data/
│   ├── holidays_seed.json       # offline special-day dataset (fallback + demo)
│   └── instagram_posts_sample.json  # DEMO Instagram posts (fallback + demo)
├── src/
│   ├── database.py              # SQLite schema + connection helper
│   ├── calendar_agent.py        # Agent 1 — Calendar Intelligence
│   ├── instagram_agent.py       # Agent 2 — Instagram Intelligence
│   ├── priority_agent.py        # Agent 3 — Marketing Priority
│   ├── excel_agent.py           # Agent 4 — Excel Reporting
│   ├── reminder_agent.py        # Agent 5 — Reminders
│   ├── orchestrator.py          # Central orchestrator
│   └── utils/logger.py
├── tests/                       # unittest suite (13 tests, see section 6)
├── outputs/                     # generated database + Excel workbook + reminders
└── logs/system.log
```

## 2. Installation

Requirements: Python 3.9+.

```bash
cd project
python -m venv venv
source venv/bin/activate          # Windows: venv\Scripts\activate
pip install -r requirements.txt
```

`openpyxl`, `pandas` and `requests` are the only hard dependencies. `pytest` is
optional — the test suite also runs with the standard library's `unittest`.

## 3. First Run (no credentials required)

```bash
cp config/config.example.env .env      # leave values blank for now
python run_sync.py
```

This will:
1. Initialize `outputs/marketing_calendar.db`.
2. Load ~30 seed special days across all required categories for the current year
   + next 2 years (configurable).
3. Load the demo Instagram post dataset (`data/instagram_posts_sample.json`)
   because no live Instagram credentials are configured.
4. Match posts to special days, score priorities, compute reminders.
5. Write `outputs/Advanced_Media_Special_Days_Marketing_Calendar.xlsx`.
6. Write any due reminders as JSON files under `outputs/reminders_outbox/`
   (the local fallback channel — see section 5).

Run it again any time with `python run_sync.py`. Re-running never duplicates
records; it updates them.

To only see the last sync results without re-running:
```bash
python run_sync.py --summary
```

## 4. Activating Live Integrations

Nothing here is faked. Every live integration is implemented but stays inactive
until you supply real credentials in `.env`.

### 4.1 Instagram Graph API (Agent 2)

Required to replace the demo post dataset with real Advanced Media Trading LLC
posts.

**What you need:**
- A Facebook Page linked to an Instagram **Business or Creator** account (personal
  accounts are not supported by the Graph API).
- A Facebook App (developers.facebook.com) with the following permissions granted
  on the token you generate:
  `instagram_basic`, `pages_read_engagement`, `pages_show_list`,
  `business_management`, `instagram_content_publish`.
  (`instagram_basic` is required for the `instagram_business_account` field to
  even appear on the `/me/accounts` response — without it, the Page lookup used
  to find your Instagram Business Account ID will silently omit that field.)
- A **Page** access token (not a User token) for that app — obtained by calling
  `GET /me/accounts?fields=name,access_token` — exchanged for a long-lived
  version via the standard `fb_exchange_token` OAuth flow so it doesn't expire
  every ~1-2 hours. See `DEPLOYMENT.md` for the exact token-exchange steps.
- The Instagram Business Account ID, obtained via
  `GET /me/accounts?fields=name,instagram_business_account`.

**Set in `.env`:**
```
INSTAGRAM_ACCESS_TOKEN=...       # long-lived PAGE token, not user token
INSTAGRAM_BUSINESS_ACCOUNT_ID=...
FACEBOOK_APP_ID=...
FACEBOOK_APP_SECRET=...
```

**Limitations to know before you rely on this:**
- The Graph API only returns posts published *after* the account was converted to a
  Business/Creator account, plus whatever historical media the API exposes for that
  account (typically all existing media, but Stories/Reels visibility rules differ).
  The system does not claim to have "all" historical posts — it reports exactly
  what the API returns.
- If the official Instagram username/URL is not supplied, the system cannot do
  account-specific matching — set it in `config/settings.json` →
  `company.instagram_username` / `company.instagram_profile_url`.

If the token/ID are missing or the API call fails, `InstagramAgent` automatically
falls back to `data/instagram_posts_sample.json` and logs the reason — it never
silently reports 0 posts as "no posts exist."

### 4.2 Calendarific (optional, Agent 1)

Only needed if you want live holiday lookups in addition to the seed dataset.

```
CALENDARIFIC_API_KEY=...
```
Get a free/paid key at https://calendarific.com/. Without a key, the seed dataset
(which already covers all the categories in the brief) is used.

### 4.3 Reminder Delivery (Agent 5)

Set `reminders.delivery_channel` in `config/settings.json` to one of:

| Value | Requires |
|---|---|
| `local_fallback` (default) | Nothing — writes JSON reminder files to `outputs/reminders_outbox/` |
| `ms_teams` | `MS_TEAMS_WEBHOOK_URL` in `.env` (Teams → Channel → Connectors → Incoming Webhook) |
| `ms_graph_email` | Azure AD App Registration with `Mail.Send`, plus `MS_GRAPH_CLIENT_ID`, `MS_GRAPH_CLIENT_SECRET`, `MS_GRAPH_TENANT_ID`, `MS_GRAPH_SENDER_EMAIL` |
| `google_calendar` | OAuth Client ID JSON (Desktop app type) + `GOOGLE_CALENDAR_CREDENTIALS_FILE`, `GOOGLE_CALENDAR_ID` |

**Current implementation status:** `local_fallback`, `ms_teams`, and `google_calendar`
are fully wired and will actually deliver. `ms_graph_email` detects whether
credentials are present but the send call is stubbed with an explicit
"not yet activated" message rather than a fake success — wiring in the real
`msal` call once you provide credentials and confirm your tenant's consent flow
is the remaining activation step (see `src/reminder_agent.py`, method
`_deliver_ms_graph`).

#### 4.3.1 Google Calendar setup (`google_calendar` channel)

This uses the standard OAuth2 "installed app" flow — correct for a script
acting on a single Google/Gmail account (e.g. a test account), and it never
requires handing over an account password.

1. Go to https://console.cloud.google.com/ and create (or select) a project.
2. Enable the **Google Calendar API** for that project (APIs & Services → Library).
3. Go to APIs & Services → Credentials → Create Credentials → OAuth Client ID.
   - Application type: **Desktop app**.
   - If prompted to configure the OAuth consent screen first, choose **External**,
     fill in the required fields, and add the target Gmail account as a **test user**.
4. Download the resulting JSON file and save it locally, e.g. `config/google_credentials.json`.
5. In `.env`, set:
   ```
   GOOGLE_CALENDAR_CREDENTIALS_FILE=config/google_credentials.json
   GOOGLE_CALENDAR_ID=primary
   ```
6. Install the optional dependencies (already in `requirements.txt`):
   ```
   pip install google-api-python-client google-auth-httplib2 google-auth-oauthlib
   ```
7. The **first time** a `google_calendar` reminder fires, a browser window opens
   asking you to log into the target Google account and click **Allow**. After
   that, a token is cached to `config/google_token.json` and future runs are
   silent — no repeated login.
8. Each due reminder creates a real Calendar event (title, event date, priority,
   Instagram status, campaign notes) with a 10-minute popup reminder.

Do not commit `google_credentials.json` or `google_token.json` to version
control — both are already covered by `.gitignore` patterns for `.env`-style
secrets; add them explicitly if they are not.

## 5. Configuration Reference (`config/settings.json`)

All business rules are editable without touching code:

- `company` — name, Instagram username/URL.
- `countries` — which regions to track.
- `calendar.years_ahead` — how many future years to generate (default 2).
- `reminders.default_lead_days_by_priority` — Critical/High/Medium/Low lead times.
- `reminders.special_day_overrides` — per-event overrides (Mother's Day = 10 days,
  Father's Day = 5 days, honored even if their priority category changes).
- `priority_scoring.weights` — the 5 scoring factors (must sum to ~1.0).
- `priority_scoring.thresholds` — score cut-offs for Critical/High/Medium/Low.
- `priority_scoring.manual_overrides` — force a specific priority + reason for a
  named special day, regardless of the computed score.

**Manual overrides at the data level:** setting `manual_override = 1` on a row in
the `special_days` table (e.g. via a small SQLite browser, or a future admin UI)
permanently protects that record from being overwritten by Agent 1 or reclassified
by Agent 3 on the next sync — this is how a marketing team member's manual edit
survives daily automated re-runs.

## 6. Tests

```bash
python -m unittest discover -s tests -v
```
13 tests covering: variable-date resolution (Mother's/Father's Day), lunar dates
never being guessed, no-duplicate-on-resync, manual-override protection, Instagram
matching precision (including a regression test for a false-positive hashtag bug
that was found and fixed during development), and reminder de-duplication.

If `pytest` is installed, the same suite runs with `pytest tests/`.

## 7. Generating / Refreshing the Excel Workbook Only

The Excel workbook is regenerated as part of every `python run_sync.py` run. There
is no separate command needed — Agent 4 always rebuilds the workbook from the
current database state after Agents 1–3 have run.

## 8. Known Limitations (see also DEPLOYMENT.md and Acceptance Test Report below)

- Religious/lunar dates (Eid al-Fitr, Eid al-Adha, Ramadan start, Islamic New Year,
  Prophet Muhammad's Birthday) are intentionally left with `event_date = NULL` and
  `Verification Status = Provisional` until you supply a confirmed date — the
  system will not guess these.
- Instagram historical depth depends entirely on what the Graph API exposes for
  your specific account; this is a platform limitation, not a system limitation.
- `ms_graph_email` and `google_calendar` reminder channels are implemented up to
  the credential/connectivity check; the final send call is a documented
  activation step (see 4.3).

## 9. Acceptance Test Report

See `ACCEPTANCE_TESTS.md` for the full pass/fail table against the 12 tests
specified in the project brief.
