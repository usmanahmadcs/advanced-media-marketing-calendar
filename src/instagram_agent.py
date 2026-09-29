"""
instagram_agent.py
Agent 2 - Instagram Intelligence Agent

Responsible for retrieving Instagram post metadata for Advanced Media Trading
LLC's official account and matching posts to special days in the database.

Two data-acquisition paths are implemented:

1. Instagram Graph API (live path)
   Requires INSTAGRAM_ACCESS_TOKEN and INSTAGRAM_BUSINESS_ACCOUNT_ID in the
   environment (see config/config.example.env). This only works for a
   Business/Creator Instagram account connected to a Facebook Page, using a
   token generated through a registered Facebook App with the
   instagram_basic / instagram_manage_insights / pages_show_list
   permissions. If credentials are missing or the request fails, this path
   is skipped - it never fabricates posts.

2. Manual/fallback import (offline path)
   Reads a JSON/CSV file of exported or manually supplied post records
   (data/instagram_posts_sample.json is a labeled DEMO file for this
   purpose). This preserves the same schema and matching logic as the live
   path, satisfying the "fallback workflow" requirement.

Matching logic:
   For every special day with a resolved event_date, each candidate post is
   scored using:
     - Caption keyword overlap with the special day's name (and known
       synonyms/hashtag forms)
     - Date proximity between the post's publication_date and the event_date
   A weighted confidence score (0-1) is produced. Matches at or above the
   configured threshold are recorded; everything else is left as
   "Needs Review" rather than a false positive.

   Verified live posts (source == 'graph_api') are always preferred over
   demo/fallback posts once they clear the confidence threshold, even if a
   stale demo record happens to score higher on raw date-proximity - real
   data must never lose a match to leftover placeholder data.
"""

import json
import os
import re
from datetime import datetime, date

from src.database import get_connection


class InstagramAgent:
    def __init__(self, settings: dict, base_dir: str, logger):
        self.settings = settings
        self.base_dir = base_dir
        self.logger = logger
        self.demo_path = os.path.join(base_dir, "data", "instagram_posts_sample.json")
        self.threshold = settings.get("instagram", {}).get(
            "matching_confidence_threshold", 0.55
        )

    # ------------------------------------------------------------------
    # Acquisition
    # ------------------------------------------------------------------
    def collect_posts(self) -> list:
        posts = self._try_graph_api()
        if posts:
            self.logger.info(f"InstagramAgent: retrieved {len(posts)} posts via Graph API.")
            return posts

        self.logger.info(
            "InstagramAgent: Graph API unavailable/not configured - "
            "using fallback import dataset."
        )
        return self._load_fallback_posts()

    def _try_graph_api(self) -> list:
        token = os.environ.get("INSTAGRAM_ACCESS_TOKEN")
        account_id = os.environ.get("INSTAGRAM_BUSINESS_ACCOUNT_ID")
        if not (token and account_id):
            return []
        try:
            import requests
        except ImportError:
            self.logger.warning("InstagramAgent: 'requests' package not installed.")
            return []

        posts = []
        url = f"https://graph.facebook.com/v19.0/{account_id}/media"
        params = {
            "fields": "id,caption,permalink,timestamp,like_count,comments_count",
            "access_token": token,
            "limit": 100,
        }
        try:
            while url:
                resp = requests.get(url, params=params, timeout=15)
                resp.raise_for_status()
                payload = resp.json()
                for item in payload.get("data", []):
                    posts.append({
                        "post_url": item.get("permalink"),
                        "caption": item.get("caption", ""),
                        "publication_date": (item.get("timestamp") or "")[:10],
                        "likes": item.get("like_count", 0),
                        "comments": item.get("comments_count", 0),
                        "source": "graph_api",
                    })
                url = payload.get("paging", {}).get("next")
                params = None  # 'next' already contains full query string
        except Exception as exc:  # noqa: BLE001
            self.logger.warning(f"InstagramAgent: Graph API request failed: {exc}")
            return []
        return posts

    def _load_fallback_posts(self) -> list:
        if not os.path.exists(self.demo_path):
            return []
        with open(self.demo_path, "r", encoding="utf-8") as f:
            raw = json.load(f)
        for p in raw:
            p["source"] = "demo_seed"
        return raw

    # ------------------------------------------------------------------
    # Persistence
    # ------------------------------------------------------------------
    def save_posts(self, db_path: str, posts: list) -> int:
        now = datetime.utcnow().isoformat()
        written = 0
        with get_connection(db_path) as conn:
            for p in posts:
                if not p.get("post_url"):
                    continue
                existing = conn.execute(
                    "SELECT id FROM instagram_posts WHERE post_url=?",
                    (p["post_url"],),
                ).fetchone()
                if existing:
                    conn.execute(
                        """UPDATE instagram_posts SET caption=?, publication_date=?,
                           likes=?, comments=?, source=?, last_updated=? WHERE id=?""",
                        (p.get("caption", ""), p.get("publication_date"),
                         p.get("likes", 0), p.get("comments", 0), p.get("source"),
                         now, existing["id"]),
                    )
                else:
                    conn.execute(
                        """INSERT INTO instagram_posts
                           (post_url, caption, publication_date, likes, comments,
                            source, last_updated) VALUES (?,?,?,?,?,?,?)""",
                        (p["post_url"], p.get("caption", ""), p.get("publication_date"),
                         p.get("likes", 0), p.get("comments", 0), p.get("source"), now),
                    )
                written += 1
        return written

    # ------------------------------------------------------------------
    # Matching
    # ------------------------------------------------------------------
    SYNONYMS = {
        "mother's day (uae/arab world)": ["mother's day", "mothers day", "#mothersday"],
        "mother's day (us/uk)": ["mother's day", "mothers day", "#mothersday"],
        "father's day": ["father's day", "fathers day", "#fathersday"],
        "uae national day": ["national day", "uaenationalday"],
        "uae national day (day 2)": ["national day", "uaenationalday"],
        "international women's day": ["women's day", "iwd", "#iwd"],
        "valentine's day": ["valentine", "#valentinesday"],
        "black friday": ["black friday", "#blackfriday"],
    }

    def match_posts_to_days(self, db_path: str) -> dict:
        """For every special day with a resolved date, find the best-matching
        post (if any) and write a post_matches record. Returns summary counts.

        Verified live posts (source == 'graph_api') are always preferred over
        demo/fallback posts once they clear the confidence threshold, even if
        a stale demo record happens to score higher on raw date-proximity -
        real data must never lose a match to leftover placeholder data."""
        summary = {"matched": 0, "needs_review": 0}
        now = datetime.utcnow().isoformat()
        with get_connection(db_path) as conn:
            days = conn.execute(
                "SELECT * FROM special_days WHERE event_date IS NOT NULL"
            ).fetchall()
            posts = conn.execute("SELECT * FROM instagram_posts").fetchall()

            for day in days:
                candidates = []  # (score, date_delta, post)
                for post in posts:
                    score, date_delta = self._score_match(day, post)
                    if score > 0:
                        candidates.append((score, date_delta, post))

                def _rank(entry):
                    score, date_delta, _post = entry
                    return (score, -(date_delta if date_delta is not None else 10**9))

                live_candidates = [c for c in candidates if c[2]["source"] == "graph_api"]
                fallback_candidates = [c for c in candidates if c[2]["source"] != "graph_api"]

                chosen = None
                if live_candidates:
                    best_live = max(live_candidates, key=_rank)
                    if best_live[0] >= self.threshold:
                        chosen = best_live
                if chosen is None and fallback_candidates:
                    best_fallback = max(fallback_candidates, key=_rank)
                    if best_fallback[0] >= self.threshold:
                        chosen = best_fallback

                if chosen:
                    best_score, _delta, best_post = chosen
                    conn.execute(
                        """INSERT INTO post_matches
                           (special_day_id, instagram_post_id, matching_confidence,
                            match_method, last_updated)
                           VALUES (?,?,?,?,?)
                           ON CONFLICT(special_day_id, instagram_post_id)
                           DO UPDATE SET matching_confidence=excluded.matching_confidence,
                                         last_updated=excluded.last_updated""",
                        (day["id"], best_post["id"], round(best_score, 2),
                         "keyword_and_date_proximity", now),
                    )
                    # Remove any other stale match rows for this special day so
                    # a leftover demo match doesn't keep sitting alongside the
                    # now-preferred verified one (e.g. reminder/audit views
                    # picking the wrong row).
                    conn.execute(
                        """DELETE FROM post_matches
                           WHERE special_day_id=? AND instagram_post_id != ?""",
                        (day["id"], best_post["id"]),
                    )
                    summary["matched"] += 1
                else:
                    conn.execute(
                        "DELETE FROM post_matches WHERE special_day_id=?", (day["id"],)
                    )
                    summary["needs_review"] += 1
        return summary

    def _score_match(self, day, post) -> tuple:
        caption = (post["caption"] or "").lower()
        name_key = day["name"].strip().lower()
        keywords = self.SYNONYMS.get(name_key, [name_key])

        keyword_hit = any(
            re.search(re.escape(k.replace("#", "")), caption) for k in keywords
        )
        keyword_score = 1.0 if keyword_hit else 0.0

        date_score = 0.0
        delta_days = None
        if post["publication_date"] and day["event_date"]:
            try:
                d1 = date.fromisoformat(day["event_date"])
                d2 = date.fromisoformat(post["publication_date"])
                delta_days = abs((d1 - d2).days)
                if delta_days <= 3:
                    date_score = 1.0
                elif delta_days <= 7:
                    date_score = 0.6
                elif delta_days <= 14:
                    date_score = 0.3
            except ValueError:
                pass

        # Require at least a keyword hit OR very close date proximity - a
        # bare date-only match with no textual signal is not trustworthy.
        if not keyword_hit and date_score < 1.0:
            return 0.0, delta_days

        # Weighted combination: caption keyword match matters most.
        score = (keyword_score * 0.7) + (date_score * 0.3)
        return score, delta_days