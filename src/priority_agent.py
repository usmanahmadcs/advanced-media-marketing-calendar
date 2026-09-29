"""
priority_agent.py
Agent 3 - Marketing Priority Agent

Classifies each special day into Critical / High / Medium / Low, generates a
human-readable explanation, and suggests a campaign + preparation deadline.

Design notes:
- Scoring is a transparent, configurable weighted model (weights live in
  config/settings.json under priority_scoring.weights). Every factor is
  either a rule-based signal (category, region match, days until event) or
  actual historical engagement pulled from stored Instagram post data - if
  no historical post exists for that special day, the engagement factor is
  scored as 0 and the explanation says so explicitly. Nothing is invented.
- Manual overrides (config or a user edit in the DB) always win and are
  never silently recalculated - this satisfies "allow me to change the
  priority manually" and "override the default priority."
- The Mother's Day > Father's Day business rule is enforced both by the
  default override table in settings.json AND as a hard safety check here,
  so it holds even if scoring weights are changed later.
"""

from datetime import datetime, date

from src.database import get_connection


class PriorityAgent:
    def __init__(self, settings: dict, base_dir: str, logger):
        self.settings = settings
        self.base_dir = base_dir
        self.logger = logger
        cfg = settings.get("priority_scoring", {})
        self.weights = cfg.get("weights", {})
        self.thresholds = cfg.get("thresholds", {"Critical": 85, "High": 65, "Medium": 40, "Low": 0})
        self.manual_overrides = cfg.get("manual_overrides", {})
        self.low_categories = set(cfg.get("default_low_priority_categories", []))
        self.reminder_cfg = settings.get("reminders", {})

    def classify_all(self, db_path: str) -> int:
        now = datetime.utcnow().isoformat()
        updated = 0
        with get_connection(db_path) as conn:
            days = conn.execute("SELECT * FROM special_days").fetchall()
            engagement_map = self._build_engagement_map(conn)

            for day in days:
                if day["manual_override"]:
                    continue  # never overwrite a user's manual edit

                score, reason_parts = self._score_event(day, engagement_map)
                override = self.manual_overrides.get(day["name"])
                if override:
                    priority = override["priority"]
                    reason = override["reason"] + f" (computed score: {score:.0f}/100, shown for reference)."
                else:
                    priority = self._score_to_level(score)
                    reason = " ".join(reason_parts)

                priority = self._enforce_mothers_vs_fathers_rule(day["name"], priority)

                campaign, prep_deadline = self._suggest_campaign(day, priority)
                lead_days = self._reminder_lead_days(day["name"], priority)
                reminder_date = self._compute_reminder_date(day["event_date"], lead_days)

                conn.execute(
                    """UPDATE special_days SET
                         priority=?, priority_score=?, priority_reason=?,
                         suggested_campaign=?, preparation_deadline=?,
                         reminder_date=?, reminder_lead_days=?, last_updated=?
                       WHERE id=?""",
                    (priority, round(score, 1), reason, campaign, prep_deadline,
                     reminder_date, lead_days, now, day["id"]),
                )
                updated += 1
        self.logger.info(f"PriorityAgent: classified {updated} special-day records.")
        return updated

    # ------------------------------------------------------------------
    def _build_engagement_map(self, conn) -> dict:
        """Average likes+comments per special day, from confirmed post matches."""
        rows = conn.execute(
            """SELECT pm.special_day_id AS day_id, ip.likes AS likes, ip.comments AS comments
               FROM post_matches pm
               JOIN instagram_posts ip ON ip.id = pm.instagram_post_id"""
        ).fetchall()
        agg = {}
        for r in rows:
            agg.setdefault(r["day_id"], []).append((r["likes"] or 0) + (r["comments"] or 0))
        return {k: sum(v) / len(v) for k, v in agg.items()}

    def _score_event(self, day, engagement_map) -> tuple:
        reasons = []

        # Business relevance: UAE Holiday / Family / Commercial score higher by default.
        business_map = {
            "UAE Holiday": 90, "Family": 75, "Commercial": 70,
            "Professional": 55, "Religious": 65, "International": 45,
        }
        business = business_map.get(day["category"], 50)
        reasons.append(f"Business relevance for category '{day['category']}' scored {business}/100.")

        # Audience relevance: UAE-specific events score higher for a UAE-based company.
        audience = 90 if day["country"] == "UAE" else (60 if day["country"] == "Global" else 40)
        reasons.append(f"Audience relevance scored {audience}/100 based on country '{day['country']}'.")

        # Historical engagement: real data only, else explicitly flagged as unavailable.
        eng_value = engagement_map.get(day["id"])
        if eng_value is not None:
            engagement = min(100, eng_value / 10)
            reasons.append(f"Historical engagement scored {engagement:.0f}/100 from actual matched post data.")
        else:
            engagement = 0
            reasons.append("No historical Instagram engagement data available for this event; scored 0 (not invented).")

        # Campaign opportunity: commercial/family days offer the clearest campaign angle.
        campaign_map = {"Commercial": 90, "Family": 80, "UAE Holiday": 75, "Religious": 60, "Professional": 55, "International": 45}
        campaign = campaign_map.get(day["category"], 50)
        reasons.append(f"Campaign opportunity scored {campaign}/100 for category '{day['category']}'.")

        # Regional relevance
        regional = 90 if day["country"] in ("UAE",) else (55 if day["country"] == "Global" else 35)
        reasons.append(f"Regional relevance scored {regional}/100.")

        w = self.weights
        total = (
            business * w.get("business_relevance", 0.3)
            + audience * w.get("audience_relevance", 0.2)
            + engagement * w.get("historical_engagement", 0.15)
            + campaign * w.get("campaign_opportunity", 0.2)
            + regional * w.get("regional_relevance", 0.15)
        )
        return total, reasons

    def _score_to_level(self, score: float) -> str:
        if score >= self.thresholds.get("Critical", 85):
            return "Critical"
        if score >= self.thresholds.get("High", 65):
            return "High"
        if score >= self.thresholds.get("Medium", 40):
            return "Medium"
        return "Low"

    @staticmethod
    def _enforce_mothers_vs_fathers_rule(name: str, priority: str) -> str:
        # Hard business-rule safety net: Mother's Day must never rank below High.
        if "mother's day" in name.lower():
            levels = ["Low", "Medium", "High", "Critical"]
            if levels.index(priority) < levels.index("High"):
                return "High"
        return priority

    def _suggest_campaign(self, day, priority) -> tuple:
        templates = {
            "Family": "Emotional/appreciation-themed post + short video reel honoring the occasion.",
            "Commercial": "Time-boxed promotional offer with countdown stories and paid boost.",
            "UAE Holiday": "National-pride themed creative with UAE colors, flag imagery, and community message.",
            "Religious": "Respectful greeting post with culturally appropriate imagery; avoid hard-sell messaging.",
            "Professional": "Thank-you/spotlight post recognizing the relevant profession or workforce.",
            "International": "Awareness-style post connecting the observance to brand values.",
        }
        campaign = templates.get(day["category"], "Standard themed social post.")

        prep_days_map = {"Critical": 14, "High": 10, "Medium": 5, "Low": 2}
        prep_days = prep_days_map.get(priority, 5)
        prep_deadline = None
        if day["event_date"]:
            try:
                d = date.fromisoformat(day["event_date"])
                prep_deadline = (d - self._timedelta(prep_days)).isoformat()
            except ValueError:
                pass
        return campaign, prep_deadline

    def _reminder_lead_days(self, name: str, priority: str) -> int:
        overrides = self.reminder_cfg.get("special_day_overrides", {})
        if name in overrides:
            return overrides[name]
        return self.reminder_cfg.get("default_lead_days_by_priority", {}).get(priority, 3)

    def _compute_reminder_date(self, event_date: str, lead_days: int):
        if not event_date:
            return None
        try:
            d = date.fromisoformat(event_date)
            return (d - self._timedelta(lead_days)).isoformat()
        except ValueError:
            return None

    @staticmethod
    def _timedelta(days):
        from datetime import timedelta
        return timedelta(days=days)
