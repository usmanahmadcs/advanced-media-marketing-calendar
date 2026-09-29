# Acceptance Test Report

Run against the demo dataset produced by `python run_sync.py` on a clean database,
then verified by inspecting `outputs/Advanced_Media_Special_Days_Marketing_Calendar.xlsx`
after recalculation, and by the automated suite in `tests/` (`python -m unittest
discover -s tests -v` — 13/13 passed).

| # | Test | Result | Evidence |
|---|---|---|---|
| 1 | Mother's Day appears with correct country-specific date + 10-day reminder | **PASS** | "Mother's Day (UAE/Arab World)" → 2026-03-21, reminder 2026-03-11 (10 days before). "Mother's Day (US/UK)" → 2026-05-10 (2nd Sunday of May), reminder 2026-04-30. Verified by `tests/test_priority_agent.py::test_reminder_lead_days_override_for_mothers_and_fathers_day`. |
| 2 | Father's Day appears with correct country-specific date + 5-day reminder | **PASS** | "Father's Day" → 2026-06-21 (3rd Sunday of June), reminder 2026-06-16 (5 days before). Same test as above. |
| 3 | Mother's Day receives higher default priority than Father's Day | **PASS** | Mother's Day = High, Father's Day = Medium, enforced by both the default override table and a hard safety check in `PriorityAgent._enforce_mothers_vs_fathers_rule`. Verified by `tests/test_priority_agent.py::test_mothers_day_outranks_fathers_day`. |
| 4 | Matching Instagram post associated with correct special day, exact URL as clickable link | **PASS** | UAE National Day 2026 correctly linked to `DEMO_UAENATIONALDAY_2025` post (not the Mother's Day post — this was a bug found and fixed during development, see `tests/test_instagram_agent.py::test_generic_hashtag_does_not_cause_false_match`). Cell is a real openpyxl hyperlink. |
| 5 | No matching post → "No matching post found" / "Needs Review", never an invented URL | **PASS** | Father's Day 2026 shows `Instagram Post Published = No`, `Instagram Post URL = "No matching post found"`. 69 of 90 demo records show this state, none show a fabricated URL. |
| 6 | New post detected → record updated, no duplicate events created | **PASS** | `InstagramAgent.save_posts` upserts on `post_url` UNIQUE constraint; `CalendarAgent.save_to_db` upserts on `(name, country, year)` UNIQUE constraint. Verified by `tests/test_calendar_agent.py::test_no_duplicate_special_days_on_repeated_sync` (re-running sync twice produces the same row count). |
| 7 | Reminder processing never sends duplicates | **PASS** | `reminders` table has a `UNIQUE(special_day_id, reminder_date)` constraint and the agent checks `status == 'sent'` before resending. Verified by `tests/test_reminder_agent.py::test_reminder_not_duplicated_on_second_run`. |
| 8 | User can change priority and reminder timing via configuration/interface | **PASS** | `config/settings.json` → `priority_scoring.manual_overrides` and `reminders.special_day_overrides` are read every run. Setting `manual_override=1` on a database row also permanently protects it. Verified by `tests/test_priority_agent.py::test_manual_override_is_never_recalculated` and `tests/test_calendar_agent.py::test_manual_override_protected_from_recollection`. |
| 9 | Workbook includes all required columns, worksheets, filters, hyperlinks | **PASS** | All 22 required columns present on "Marketing Calendar"; 5 worksheets present (Marketing Calendar, Upcoming Campaigns, Instagram Post Audit, Settings, Dashboard); native Excel Tables provide filter dropdowns on all data sheets; Instagram Post URL and Source URL columns are real hyperlinks. Confirmed via direct openpyxl inspection and `recalc.py` (0 formula errors across 174 formulas). |
| 10 | API failures logged; existing valid records preserved | **PASS** | Every orchestrator step is wrapped in `_run_step`, which catches exceptions, logs them, writes a `sync_log` row with `status='failed'`, and lets the remaining steps continue rather than aborting or wiping data. Calendar/Instagram agents specifically catch and log live-API request failures and fall back to existing/seed data rather than deleting anything. |
| 11 | Country-specific dates, variable-date and provisional religious dates handled correctly | **PASS** | Mother's Day resolves differently for UAE (fixed March 21) vs. US/UK (2nd Sunday of May); Father's Day/Black Friday/Cyber Monday use real calendar-arithmetic rules per year; Eid al-Fitr, Eid al-Adha, Ramadan start, Islamic New Year and Prophet Muhammad's Birthday are all written with `event_date = NULL` and `Verification Status = Provisional` rather than a guessed date. Verified by `tests/test_calendar_agent.py::test_lunar_dates_are_not_guessed`. |
| 12 | System can be restarted without losing stored events, matches, or reminder history | **PASS** | All state lives in `outputs/marketing_calendar.db` (SQLite); re-running `python run_sync.py` after a full process restart reuses and updates existing rows rather than recreating the database. Verified manually by running `run_sync.py` twice in sequence and confirming record counts stay stable (90 special days, no growth on the second run) and `sync_log` accumulates a new entry per run rather than resetting. |

## Tests Requiring External Credentials to Fully Verify in Production

The following are implemented and unit-tested against their *logic*, but their
*live* behavior can only be confirmed once real credentials are supplied (see
README.md section 4):

- Instagram Graph API retrieval of Advanced Media Trading LLC's actual historical
  and new posts (currently exercised against the labeled demo dataset).
- Calendarific live holiday lookups (currently exercised against the offline seed
  dataset, which already satisfies all category/coverage requirements).
- Microsoft Graph email and Google Calendar reminder delivery (credential-presence
  check implemented; final send call intentionally left as a documented activation
  step rather than faked — see `src/reminder_agent.py`).
- Microsoft Teams webhook delivery is fully implemented and will work as soon as
  `MS_TEAMS_WEBHOOK_URL` is set; not exercised here because no webhook was
  supplied.
