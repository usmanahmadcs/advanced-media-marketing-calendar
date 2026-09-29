"""
excel_agent.py
Agent 4 - Excel Reporting Agent

Builds/refreshes the Advanced_Media_Special_Days_Marketing_Calendar.xlsx
workbook with five worksheets:

  1. Marketing Calendar   - full record set with formulas + conditional formatting
  2. Upcoming Campaigns   - filtered view (formula-linked, not a static copy)
  3. Instagram Post Audit - post-level matching audit
  4. Settings             - configurable rules, visible + editable
  5. Dashboard            - summary counters and formulas

Notes:
  - Uses openpyxl exclusively (per project defaults).
  - "Days Remaining" is written as a live formula (=Event Date - TODAY()),
    not a hardcoded number, so it recalculates whenever the file is reopened.
    All date-bearing columns (Event Date, Preparation Deadline, Reminder
    Date, Post Publication Date) are written as real openpyxl/Excel date
    values (not text) so date arithmetic and comparisons - including the
    Dashboard formulas - evaluate correctly instead of silently failing on
    text-typed dates.
  - Hyperlinks are real openpyxl Hyperlink objects on the Instagram Post URL
    and Source URL columns. A hyperlink/URL is only ever shown for a post
    that came from the live Instagram Graph API; unverified demo/fallback
    data is always labeled as such and never hyperlinked as if it were a
    real, published post.
  - Conditional formatting highlights Critical/High priority rows and
    overdue preparation deadlines.
  - Everything is wrapped in a native Excel Table (ListObject) with filters.
  - After saving, the workbook is optionally recalculated with a headless
    LibreOffice pass (if available) so formula results are also correct for
    tools that only read cached values; this is best-effort and never fails
    the build if LibreOffice isn't installed - the formulas remain live and
    Excel will recalculate them automatically on open either way.
"""

import os
import shutil
import subprocess
import tempfile
from datetime import date as date_cls
from datetime import datetime

from openpyxl import Workbook
from openpyxl.styles import Font, PatternFill, Alignment, Border, Side
from openpyxl.worksheet.table import Table, TableStyleInfo
from openpyxl.formatting.rule import CellIsRule, FormulaRule
from openpyxl.utils import get_column_letter
from openpyxl.worksheet.datavalidation import DataValidation

from src.database import get_connection

DEMO_SOURCE_LABEL = "Demo Data (Unverified - no live Instagram credentials configured)"
LIVE_SOURCE_LABEL = "Live Instagram Graph API (Verified)"
NOT_VERIFIED_TEXT = "Not verified - demo data only (no live Instagram credentials configured)"
NO_MATCH_TEXT = "No matching post found"

FONT_NAME = "Arial"
HEADER_FILL = PatternFill(start_color="1F3864", end_color="1F3864", fill_type="solid")
HEADER_FONT = Font(name=FONT_NAME, bold=True, color="FFFFFF", size=10)
BODY_FONT = Font(name=FONT_NAME, size=10)
TITLE_FONT = Font(name=FONT_NAME, bold=True, size=14, color="1F3864")
THIN = Side(style="thin", color="D9D9D9")
BORDER = Border(left=THIN, right=THIN, top=THIN, bottom=THIN)

CALENDAR_HEADERS = [
    "ID", "Special Day", "Category", "Country/Region", "Event Date", "Year",
    "Days Remaining", "Marketing Priority", "Priority Score", "Priority Reason",
    "Suggested Campaign", "Preparation Deadline", "Reminder Date",
    "Instagram Post Published", "Instagram Post URL", "Instagram Caption",
    "Post Publication Date", "Matching Confidence", "Data Source", "Source URL",
    "Verification Status", "Last Updated",
]


class ExcelAgent:
    def __init__(self, settings: dict, base_dir: str, logger):
        self.settings = settings
        self.base_dir = base_dir
        self.logger = logger
        self.output_path = os.path.join(
            base_dir, settings.get("excel", {}).get(
                "output_path", "outputs/Advanced_Media_Special_Days_Marketing_Calendar.xlsx"
            )
        )
        self.title = settings.get("excel", {}).get(
            "workbook_title", "Advanced Media Trading LLC - Special Days Marketing Calendar"
        )

    # ------------------------------------------------------------------
    def build_workbook(self, db_path: str) -> str:
        rows = self._fetch_rows(db_path)
        post_rows = self._fetch_post_audit_rows(db_path)

        wb = Workbook()
        ws1 = wb.active
        ws1.title = "Marketing Calendar"
        self._write_calendar_sheet(ws1, rows)

        ws2 = wb.create_sheet("Upcoming Campaigns")
        self._write_upcoming_sheet(ws2, rows)

        ws3 = wb.create_sheet("Instagram Post Audit")
        self._write_instagram_audit_sheet(ws3, post_rows)

        ws4 = wb.create_sheet("Settings")
        self._write_settings_sheet(ws4)

        ws5 = wb.create_sheet("Dashboard")
        self._write_dashboard_sheet(ws5, len(rows))

        os.makedirs(os.path.dirname(self.output_path), exist_ok=True)
        wb.save(self.output_path)
        self.logger.info(f"ExcelAgent: workbook written to {self.output_path}")
        self._recalculate_with_libreoffice()
        return self.output_path

    # ------------------------------------------------------------------
    @staticmethod
    def _parse_date(value):
        """Convert a stored ISO date string (or date/datetime) into a real
        python date object for writing to a cell, so Excel treats it as a
        true date value instead of text. Returns None for blank/unparseable
        values (the cell is then left blank, never a fabricated date)."""
        if not value:
            return None
        if isinstance(value, datetime):
            return value.date()
        if isinstance(value, date_cls):
            return value
        try:
            return date_cls.fromisoformat(str(value)[:10])
        except (TypeError, ValueError):
            return None

    def _recalculate_with_libreoffice(self):
        """Best-effort: use headless LibreOffice to recalculate all formulas
        and write their computed values into the saved workbook, so tools
        that only read cached values (rather than recalculating formulas
        themselves) still see correct Days Remaining / Dashboard numbers.
        This never raises - if LibreOffice isn't installed, the workbook's
        formulas remain live and Excel will recalculate them automatically
        on open (fullCalcOnLoad is set), so nothing is lost by skipping."""
        soffice = shutil.which("soffice") or shutil.which("libreoffice")
        if not soffice:
            self.logger.info(
                "ExcelAgent: LibreOffice not found on PATH - skipping formula "
                "pre-calculation. Formulas are still live and will calculate "
                "correctly when the workbook is opened in Excel."
            )
            return
        profile_dir = tempfile.mkdtemp(prefix="lo_profile_")
        # Converting a file "in place" (same input/output path) fails - the
        # source is still open/locked while LibreOffice tries to overwrite
        # it. Convert into a separate temp directory, then atomically
        # replace the original with the recalculated copy.
        convert_out_dir = tempfile.mkdtemp(prefix="lo_out_")
        try:
            # LibreOffice does not recalculate formulas on a plain
            # --convert-to by default (it keeps whatever cached values were
            # already in the file, which for a freshly openpyxl-written
            # workbook is "none"). Pre-seed a fresh user profile with
            # "always recalculate on load" for both OOXML and ODF so the
            # converted file's cached formula values are actually correct.
            user_dir = os.path.join(profile_dir, "user")
            os.makedirs(user_dir, exist_ok=True)
            with open(os.path.join(user_dir, "registrymodifications.xcu"), "w", encoding="utf-8") as f:
                f.write(
                    '<?xml version="1.0" encoding="UTF-8"?>\n'
                    '<oor:items xmlns:oor="http://openoffice.org/2001/registry" '
                    'xmlns:xs="http://www.w3.org/2001/XMLSchema" '
                    'xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance">\n'
                    ' <item oor:path="/org.openoffice.Office.Calc/Formula/Load">'
                    '<prop oor:name="OOXMLRecalcMode" oor:op="fuse"><value>0</value></prop></item>\n'
                    ' <item oor:path="/org.openoffice.Office.Calc/Formula/Load">'
                    '<prop oor:name="ODFRecalcMode" oor:op="fuse"><value>0</value></prop></item>\n'
                    '</oor:items>\n'
                )
            subprocess.run(
                [
                    soffice, "--headless", "--norestore", "--nologo",
                    f"-env:UserInstallation=file://{profile_dir}",
                    "--convert-to", "xlsx:Calc MS Excel 2007 XML",
                    "--outdir", convert_out_dir,
                    self.output_path,
                ],
                check=True, timeout=120,
                capture_output=True,
            )
            converted_path = os.path.join(convert_out_dir, os.path.basename(self.output_path))
            if os.path.exists(converted_path) and os.path.getsize(converted_path) > 0:
                shutil.move(converted_path, self.output_path)
                self.logger.info(
                    "ExcelAgent: pre-calculated formula values with LibreOffice "
                    "for compatibility with tools that don't recalculate formulas."
                )
            else:
                self.logger.warning(
                    "ExcelAgent: LibreOffice did not produce an output file - "
                    "keeping the original workbook with live (uncached) formulas."
                )
        except Exception as exc:  # noqa: BLE001 - purely best-effort
            self.logger.warning(
                f"ExcelAgent: LibreOffice recalculation skipped ({exc}); "
                "formulas remain live and will calculate in Excel on open."
            )
        finally:
            shutil.rmtree(profile_dir, ignore_errors=True)
            shutil.rmtree(convert_out_dir, ignore_errors=True)

    def _fetch_rows(self, db_path: str) -> list:
        with get_connection(db_path) as conn:
            query = """
                SELECT sd.*, ip.post_url AS ig_url, ip.caption AS ig_caption,
                       ip.publication_date AS ig_pub_date, pm.matching_confidence AS ig_conf,
                       ip.source AS ig_source
                FROM special_days sd
                LEFT JOIN post_matches pm ON pm.special_day_id = sd.id
                LEFT JOIN instagram_posts ip ON ip.id = pm.instagram_post_id
                ORDER BY sd.event_date IS NULL, sd.event_date ASC
            """
            return [dict(r) for r in conn.execute(query).fetchall()]

    def _fetch_post_audit_rows(self, db_path: str) -> list:
        """One row per genuine Instagram post. A post can legitimately match
        more than one special day in the database (e.g. the same caption
        matching both 'Mother's Day (UAE/Arab World)' and 'Mother's Day
        (US/UK)', or matching a recurring day across multiple tracked
        years); without de-duplication that produced multiple audit rows
        for the same underlying post. Here we keep only the single
        highest-confidence match per post so each real post appears exactly
        once."""
        with get_connection(db_path) as conn:
            query = """
                SELECT ip.id AS post_id, ip.post_url, ip.publication_date, ip.caption,
                       ip.likes, ip.comments, ip.source,
                       sd.name AS matched_day, pm.matching_confidence, pm.confirmed_by_user
                FROM instagram_posts ip
                LEFT JOIN post_matches pm ON pm.instagram_post_id = ip.id
                LEFT JOIN special_days sd ON sd.id = pm.special_day_id
                ORDER BY ip.publication_date DESC
            """
            rows = [dict(r) for r in conn.execute(query).fetchall()]

        best_by_post = {}
        for r in rows:
            pid = r["post_id"]
            current = best_by_post.get(pid)
            if current is None:
                best_by_post[pid] = r
                continue
            cur_conf = r["matching_confidence"] or 0
            best_conf = current["matching_confidence"] or 0
            if cur_conf > best_conf:
                best_by_post[pid] = r

        deduped = list(best_by_post.values())
        deduped.sort(key=lambda r: r["publication_date"] or "", reverse=True)
        return deduped

    # ------------------------------------------------------------------
    def _style_header_row(self, ws, row_idx, ncols):
        for col in range(1, ncols + 1):
            cell = ws.cell(row=row_idx, column=col)
            cell.font = HEADER_FONT
            cell.fill = HEADER_FILL
            cell.alignment = Alignment(horizontal="center", vertical="center", wrap_text=True)
            cell.border = BORDER

    def _write_calendar_sheet(self, ws, rows):
        ws.sheet_view.showGridLines = False
        ws.append(CALENDAR_HEADERS)
        self._style_header_row(ws, 1, len(CALENDAR_HEADERS))

        for r in rows:
            is_verified_post = bool(r["ig_url"]) and r.get("ig_source") == "graph_api"
            is_demo_post = bool(r["ig_url"]) and r.get("ig_source") != "graph_api"

            if is_verified_post:
                ig_published = "Yes"
                ig_url_value = r["ig_url"]
            elif is_demo_post:
                ig_published = "Demo Only (Unverified)"
                ig_url_value = NOT_VERIFIED_TEXT
            elif r["verification_status"] == "Needs Review":
                ig_published = "Needs Review"
                ig_url_value = NO_MATCH_TEXT
            else:
                ig_published = "No"
                ig_url_value = NO_MATCH_TEXT

            row_data = [
                r["id"], r["name"], r["category"], r["country"],
                self._parse_date(r["event_date"]), r["year"],
                None,  # Days Remaining - formula, filled below
                r["priority"], r["priority_score"], r["priority_reason"],
                r["suggested_campaign"], self._parse_date(r["preparation_deadline"]),
                self._parse_date(r["reminder_date"]),
                ig_published,
                ig_url_value,
                r["ig_caption"] or "" if is_verified_post else "",
                self._parse_date(r["ig_pub_date"]) if is_verified_post else None,
                r["ig_conf"] if (r["ig_conf"] is not None and is_verified_post) else "",
                r["data_source"], r["source_url"],
                r["verification_status"], r["last_updated"],
            ]
            ws.append(row_data)

        last_row = ws.max_row
        col = {name: i + 1 for i, name in enumerate(CALENDAR_HEADERS)}

        for row in range(2, last_row + 1):
            date_cell = ws.cell(row=row, column=col["Event Date"])
            days_cell = ws.cell(row=row, column=col["Days Remaining"])
            date_ref = date_cell.coordinate
            days_cell.value = f'=IF({date_ref}="","",{date_ref}-TODAY())'
            days_cell.number_format = "0"

            for c in range(1, len(CALENDAR_HEADERS) + 1):
                ws.cell(row=row, column=c).font = BODY_FONT
                ws.cell(row=row, column=c).border = BORDER

            ig_cell = ws.cell(row=row, column=col["Instagram Post URL"])
            if isinstance(ig_cell.value, str) and ig_cell.value.startswith("http"):
                ig_cell.hyperlink = ig_cell.value
                ig_cell.font = Font(name=FONT_NAME, size=10, color="0563C1", underline="single")

            src_cell = ws.cell(row=row, column=col["Source URL"])
            if isinstance(src_cell.value, str) and src_cell.value.startswith("http"):
                src_cell.hyperlink = src_cell.value
                src_cell.font = Font(name=FONT_NAME, size=10, color="0563C1", underline="single")

            for date_col_name in ("Event Date", "Preparation Deadline", "Reminder Date", "Post Publication Date"):
                c = ws.cell(row=row, column=col[date_col_name])
                if c.value not in (None, ""):
                    c.number_format = "yyyy-mm-dd"

        # Column widths
        widths = {
            "Special Day": 30, "Category": 16, "Country/Region": 14, "Event Date": 12,
            "Days Remaining": 12, "Marketing Priority": 14, "Priority Score": 12,
            "Priority Reason": 45, "Suggested Campaign": 40, "Preparation Deadline": 16,
            "Reminder Date": 14, "Instagram Post Published": 16, "Instagram Post URL": 38,
            "Instagram Caption": 40, "Post Publication Date": 16, "Matching Confidence": 14,
            "Data Source": 22, "Source URL": 38, "Verification Status": 16, "Last Updated": 18,
        }
        for name, width in widths.items():
            ws.column_dimensions[get_column_letter(col[name])].width = width
        ws.column_dimensions[get_column_letter(col["ID"])].width = 6

        ws.freeze_panes = "B2"

        table_ref = f"A1:{get_column_letter(len(CALENDAR_HEADERS))}{last_row}"
        table = Table(displayName="MarketingCalendarTable", ref=table_ref)
        table.tableStyleInfo = TableStyleInfo(
            name="TableStyleMedium2", showRowStripes=True, showFirstColumn=False
        )
        ws.add_table(table)

        # Conditional formatting / data validation only make sense when
        # there's at least one data row; with zero rows last_row == 1 and
        # a "2:1" range is invalid (reversed), so guard against that.
        if last_row >= 2:
            # Conditional formatting: highlight Critical/High priority rows
            priority_col_letter = get_column_letter(col["Marketing Priority"])
            full_range = f"{priority_col_letter}2:{priority_col_letter}{last_row}"
            ws.conditional_formatting.add(
                full_range,
                CellIsRule(operator="equal", formula=['"Critical"'],
                           fill=PatternFill(start_color="F8696B", end_color="F8696B", fill_type="solid")),
            )
            ws.conditional_formatting.add(
                full_range,
                CellIsRule(operator="equal", formula=['"High"'],
                           fill=PatternFill(start_color="FFC7CE", end_color="FFC7CE", fill_type="solid")),
            )
            ws.conditional_formatting.add(
                full_range,
                CellIsRule(operator="equal", formula=['"Medium"'],
                           fill=PatternFill(start_color="FFEB9C", end_color="FFEB9C", fill_type="solid")),
            )
            ws.conditional_formatting.add(
                full_range,
                CellIsRule(operator="equal", formula=['"Low"'],
                           fill=PatternFill(start_color="C6E0B4", end_color="C6E0B4", fill_type="solid")),
            )

            # Conditional formatting: overdue preparation deadlines (deadline < TODAY() and IG not published)
            prep_col_letter = get_column_letter(col["Preparation Deadline"])
            ig_col_letter = get_column_letter(col["Instagram Post Published"])
            overdue_range = f"{prep_col_letter}2:{prep_col_letter}{last_row}"
            ws.conditional_formatting.add(
                overdue_range,
                FormulaRule(
                    formula=[f'AND({prep_col_letter}2<>"",{prep_col_letter}2<TODAY(),{ig_col_letter}2<>"Yes")'],
                    fill=PatternFill(start_color="FF0000", end_color="FF0000", fill_type="solid"),
                    font=Font(color="FFFFFF", bold=True),
                ),
            )

            # Data validation dropdown for manual priority edits
            dv = DataValidation(type="list", formula1='"Critical,High,Medium,Low"', allow_blank=False)
            ws.add_data_validation(dv)
            dv.add(f"{priority_col_letter}2:{priority_col_letter}{last_row}")

    def _write_upcoming_sheet(self, ws, rows):
        """A formula-linked filtered view: pulls only *upcoming* rows from the
        Marketing Calendar - i.e. events with a resolved date that is today
        or later. Past events (event_date < today) are excluded entirely,
        not just formula-hidden, so the sheet only ever lists real,
        still-actionable campaigns."""
        ws.sheet_view.showGridLines = False
        headers = ["Special Day", "Category", "Country/Region", "Event Date",
                   "Days Remaining", "Marketing Priority", "Preparation Deadline",
                   "Instagram Post Published", "Instagram Post URL"]
        ws.append(headers)
        self._style_header_row(ws, 1, len(headers))

        today_iso = date_cls.today().isoformat()
        upcoming = [
            r for r in rows
            if r.get("event_date") and r["event_date"] >= today_iso
        ]
        # Sort by event_date ascending (already sorted upstream but ensure)
        upcoming.sort(key=lambda r: r["event_date"])

        row_i = 2
        for r in upcoming:
            is_verified_post = bool(r["ig_url"]) and r.get("ig_source") == "graph_api"
            is_demo_post = bool(r["ig_url"]) and r.get("ig_source") != "graph_api"
            if is_verified_post:
                ig_published, ig_url_value = "Yes", r["ig_url"]
            elif is_demo_post:
                ig_published, ig_url_value = "Demo Only (Unverified)", NOT_VERIFIED_TEXT
            else:
                ig_published, ig_url_value = "No", NO_MATCH_TEXT

            ws.append([
                r["name"], r["category"], r["country"], self._parse_date(r["event_date"]),
                f'=IF(D{row_i}="","",D{row_i}-TODAY())',
                r["priority"], self._parse_date(r["preparation_deadline"]),
                ig_published,
                ig_url_value,
            ])
            row_i += 1

        last_row = ws.max_row
        for row in range(2, last_row + 1):
            for c in range(1, len(headers) + 1):
                ws.cell(row=row, column=c).font = BODY_FONT
                ws.cell(row=row, column=c).border = BORDER
            date_cell = ws.cell(row=row, column=4)
            if date_cell.value:
                date_cell.number_format = "yyyy-mm-dd"
            ws.cell(row=row, column=5).number_format = "0"
            prep_cell = ws.cell(row=row, column=7)
            if prep_cell.value:
                prep_cell.number_format = "yyyy-mm-dd"
            url_cell = ws.cell(row=row, column=9)
            if isinstance(url_cell.value, str) and url_cell.value.startswith("http"):
                url_cell.hyperlink = url_cell.value
                url_cell.font = Font(name=FONT_NAME, size=10, color="0563C1", underline="single")

        widths = [30, 16, 14, 12, 14, 14, 16, 18, 38]
        for i, w in enumerate(widths, start=1):
            ws.column_dimensions[get_column_letter(i)].width = w

        ws.freeze_panes = "A2"
        if last_row > 1:
            table = Table(displayName="UpcomingCampaignsTable",
                           ref=f"A1:{get_column_letter(len(headers))}{last_row}")
            table.tableStyleInfo = TableStyleInfo(name="TableStyleMedium4", showRowStripes=True)
            ws.add_table(table)

        note = ws.cell(row=last_row + 3, column=1,
                        value=f"Note: shows only upcoming special days (Event Date >= {today_iso}), "
                              "sorted soonest-first; past events are excluded. "
                              "Filter 'Days Remaining' in Excel (e.g. 0-60) to focus on the immediate planning window.")
        note.font = Font(name=FONT_NAME, italic=True, size=9, color="808080")

    def _write_instagram_audit_sheet(self, ws, post_rows):
        """post_rows is already de-duplicated to one row per genuine
        Instagram post (see _fetch_post_audit_rows). Demo/fallback posts are
        never hyperlinked and their Source column is spelled out explicitly
        so they can't be mistaken for genuine, verified posts."""
        ws.sheet_view.showGridLines = False
        headers = ["Post URL", "Publication Date", "Caption", "Likes", "Comments",
                   "Matched Special Day", "Matching Confidence", "Confirmed by User", "Source"]
        ws.append(headers)
        self._style_header_row(ws, 1, len(headers))

        for r in post_rows:
            is_live = r["source"] == "graph_api"
            source_label = LIVE_SOURCE_LABEL if is_live else DEMO_SOURCE_LABEL
            ws.append([
                r["post_url"], self._parse_date(r["publication_date"]), r["caption"],
                r["likes"], r["comments"],
                r["matched_day"] if r["matched_day"] else "Needs Review",
                r["matching_confidence"] if r["matching_confidence"] is not None else "",
                "Yes" if r["confirmed_by_user"] else "No",
                source_label,
            ])

        last_row = ws.max_row
        for idx, r in enumerate(post_rows):
            row = idx + 2
            is_live = r["source"] == "graph_api"
            for c in range(1, len(headers) + 1):
                cell = ws.cell(row=row, column=c)
                cell.font = BODY_FONT if is_live else Font(name=FONT_NAME, size=10, italic=True, color="808080")
                cell.border = BORDER
            url_cell = ws.cell(row=row, column=1)
            if is_live and isinstance(url_cell.value, str) and url_cell.value.startswith("http"):
                url_cell.hyperlink = url_cell.value
                url_cell.font = Font(name=FONT_NAME, size=10, color="0563C1", underline="single")
            date_cell = ws.cell(row=row, column=2)
            if date_cell.value:
                date_cell.number_format = "yyyy-mm-dd"

        widths = [38, 16, 45, 8, 10, 30, 16, 14, 14]
        for i, w in enumerate(widths, start=1):
            ws.column_dimensions[get_column_letter(i)].width = w
        ws.freeze_panes = "A2"

        if last_row > 1:
            table = Table(displayName="InstagramAuditTable",
                           ref=f"A1:{get_column_letter(len(headers))}{last_row}")
            table.tableStyleInfo = TableStyleInfo(name="TableStyleMedium6", showRowStripes=True)
            ws.add_table(table)

    def _write_settings_sheet(self, ws):
        ws.sheet_view.showGridLines = False
        ws.column_dimensions["A"].width = 34
        ws.column_dimensions["B"].width = 50
        ws.column_dimensions["C"].width = 45

        title = ws.cell(row=1, column=1, value="System Settings (edit values, then re-run synchronization)")
        title.font = TITLE_FONT
        ws.merge_cells("A1:C1")

        sections = [
            ("Company", [
                ("Company Name", self.settings["company"]["name"]),
                ("Instagram Username", self.settings["company"]["instagram_username"]),
                ("Instagram Profile URL", self.settings["company"]["instagram_profile_url"]),
            ]),
            ("Countries / Regions Covered", [
                ("Countries", ", ".join(self.settings.get("countries", []))),
            ]),
            ("Reminder Lead Times (days before event)", [
                (k, v) for k, v in self.settings["reminders"]["default_lead_days_by_priority"].items()
            ] + [
                (f"Override: {k}", v) for k, v in self.settings["reminders"]["special_day_overrides"].items()
            ]),
            ("Priority Scoring Weights", [
                (k, v) for k, v in self.settings["priority_scoring"]["weights"].items()
            ]),
            ("Priority Score Thresholds", [
                (k, v) for k, v in self.settings["priority_scoring"]["thresholds"].items()
            ]),
            ("Manual Priority Overrides", [
                (name, ov["priority"]) for name, ov in self.settings["priority_scoring"]["manual_overrides"].items()
            ]),
            ("Integration Status", [
                ("Calendar refresh interval (hours)", self.settings["calendar"]["refresh_interval_hours"]),
                ("Instagram refresh interval (hours)", self.settings["instagram"]["refresh_interval_hours"]),
                ("Reminder delivery channel", self.settings["reminders"]["delivery_channel"]),
                ("Excel output path", self.settings["excel"]["output_path"]),
            ]),
        ]

        row = 3
        for section_title, kv_list in sections:
            cell = ws.cell(row=row, column=1, value=section_title)
            cell.font = Font(name=FONT_NAME, bold=True, size=12, color="1F3864")
            row += 1
            for k, v in kv_list:
                kc = ws.cell(row=row, column=1, value=str(k))
                vc = ws.cell(row=row, column=2, value=str(v))
                kc.font = BODY_FONT
                vc.font = Font(name=FONT_NAME, size=10, color="0000FF")  # blue = editable input
                kc.border = BORDER
                vc.border = BORDER
                row += 1
            row += 1

        note = ws.cell(row=row + 1, column=1,
                        value="Blue text = editable configuration values. Changing values here is for reference; "
                              "the live system reads config/settings.json. Update that file (or the future admin UI) "
                              "to change behavior, then re-run synchronization to refresh this workbook.")
        note.font = Font(name=FONT_NAME, italic=True, size=9, color="808080")
        ws.merge_cells(start_row=row + 1, start_column=1, end_row=row + 1, end_column=3)
        note.alignment = Alignment(wrap_text=True)

    def _write_dashboard_sheet(self, ws, total_records):
        ws.sheet_view.showGridLines = False
        ws.column_dimensions["A"].width = 34
        ws.column_dimensions["B"].width = 18
        ws.column_dimensions["C"].width = 55

        title = ws.cell(row=1, column=1, value=self.title)
        title.font = TITLE_FONT
        ws.merge_cells("A1:C1")

        subtitle = ws.cell(row=2, column=1,
                            value=f"Dashboard generated {datetime.utcnow().strftime('%Y-%m-%d %H:%M UTC')}")
        subtitle.font = Font(name=FONT_NAME, italic=True, size=9, color="808080")

        # Note: these formulas depend on Event Date / Preparation Deadline in
        # the Marketing Calendar sheet being real Excel date values (not
        # text) - see _write_calendar_sheet / _parse_date - otherwise the
        # SUMPRODUCT date comparisons below silently evaluate to 0 or error.
        cal_ref = "'Marketing Calendar'"
        last_data_row = 1 + total_records if total_records > 0 else 2
        metrics = [
            ("Total Special Days Tracked", f"=COUNTA({cal_ref}!A2:A{last_data_row})"),
            ("Critical Priority Events", f'=COUNTIF({cal_ref}!H2:H{last_data_row},"Critical")'),
            ("High Priority Events", f'=COUNTIF({cal_ref}!H2:H{last_data_row},"High")'),
            ("Medium Priority Events", f'=COUNTIF({cal_ref}!H2:H{last_data_row},"Medium")'),
            ("Low Priority Events", f'=COUNTIF({cal_ref}!H2:H{last_data_row},"Low")'),
            ("Instagram Posts Matched (Verified or Demo)",
             f'=SUMPRODUCT(({cal_ref}!N2:N{last_data_row}<>"No")*({cal_ref}!N2:N{last_data_row}<>"Needs Review")*({cal_ref}!N2:N{last_data_row}<>""))'),
            ("Instagram Posts Published (Verified Only)", f'=COUNTIF({cal_ref}!N2:N{last_data_row},"Yes")'),
            ("Missing / Needs Review Posts", f'=COUNTIF({cal_ref}!N2:N{last_data_row},"No")+COUNTIF({cal_ref}!N2:N{last_data_row},"Needs Review")'),
            ("Events in Next 30 Days", f'=SUMPRODUCT(({cal_ref}!G2:G{last_data_row}<>"")*({cal_ref}!G2:G{last_data_row}>=0)*({cal_ref}!G2:G{last_data_row}<=30))'),
            ("Overdue Preparation (post not verified as published)",
             f'=SUMPRODUCT(({cal_ref}!L2:L{last_data_row}<>"")*({cal_ref}!L2:L{last_data_row}<TODAY())*({cal_ref}!N2:N{last_data_row}<>"Yes"))'),
        ]

        row = 4
        header_cell = ws.cell(row=row, column=1, value="Key Metric")
        header_cell.font = HEADER_FONT
        header_cell.fill = HEADER_FILL
        header_cell2 = ws.cell(row=row, column=2, value="Value")
        header_cell2.font = HEADER_FONT
        header_cell2.fill = HEADER_FILL
        row += 1

        for label, formula in metrics:
            lc = ws.cell(row=row, column=1, value=label)
            vc = ws.cell(row=row, column=2, value=formula)
            lc.font = BODY_FONT
            vc.font = Font(name=FONT_NAME, bold=True, size=11)
            lc.border = BORDER
            vc.border = BORDER
            row += 1

        row += 2
        note = ws.cell(row=row, column=1,
                        value="All figures above are live formulas referencing the 'Marketing Calendar' sheet - "
                              "they recalculate automatically when that sheet's data changes.")
        note.font = Font(name=FONT_NAME, italic=True, size=9, color="808080")
        ws.merge_cells(start_row=row, start_column=1, end_row=row, end_column=3)
        note.alignment = Alignment(wrap_text=True)
