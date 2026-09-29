import unittest

from tests.base import TempDirTestCase, BASE_DIR
from src.database import get_connection
from src.instagram_agent import InstagramAgent


def seed_days(conn):
    conn.execute(
        """INSERT INTO special_days (name, category, country, event_date, year,
           date_type, verification_status, data_source, source_url, last_updated)
           VALUES ('UAE National Day','UAE Holiday','UAE','2026-12-02',2026,
                   'fixed','Confirmed','test','https://example.com','2026-01-01')"""
    )
    conn.execute(
        """INSERT INTO special_days (name, category, country, event_date, year,
           date_type, verification_status, data_source, source_url, last_updated)
           VALUES ('Father''s Day','Family','Global','2026-06-21',2026,
                   'fixed','Confirmed','test','https://example.com','2026-01-01')"""
    )


class TestInstagramAgent(TempDirTestCase):

    def setUp(self):
        super().setUp()
        with get_connection(self.db_path) as conn:
            seed_days(conn)

    def test_generic_hashtag_does_not_cause_false_match(self):
        """Regression test: '#UAE' on an unrelated Mother's Day post must not
        be treated as a match for UAE National Day."""
        with get_connection(self.db_path) as conn:
            conn.execute(
                """INSERT INTO instagram_posts (post_url, caption, publication_date,
                   likes, comments, source, last_updated)
                   VALUES ('https://instagram.com/p/unrelated/', 'Happy Mothers Day! #UAE #love',
                           '2026-03-21', 10, 2, 'demo_seed', '2026-01-01')"""
            )

        agent = InstagramAgent(self.settings, BASE_DIR, self.logger)
        agent.match_posts_to_days(self.db_path)

        with get_connection(self.db_path) as conn:
            row = conn.execute(
                """SELECT pm.id FROM special_days sd
                   LEFT JOIN post_matches pm ON pm.special_day_id = sd.id
                   WHERE sd.name = 'UAE National Day'"""
            ).fetchone()
        self.assertIsNone(row["id"])

    def test_correct_keyword_and_date_produces_match(self):
        with get_connection(self.db_path) as conn:
            conn.execute(
                """INSERT INTO instagram_posts (post_url, caption, publication_date,
                   likes, comments, source, last_updated)
                   VALUES ('https://instagram.com/p/national/', 'Happy UAE National Day! #UAENationalDay',
                           '2026-12-01', 100, 20, 'demo_seed', '2026-01-01')"""
            )

        agent = InstagramAgent(self.settings, BASE_DIR, self.logger)
        summary = agent.match_posts_to_days(self.db_path)
        self.assertEqual(summary["matched"], 1)

        with get_connection(self.db_path) as conn:
            row = conn.execute(
                """SELECT ip.post_url FROM special_days sd
                   JOIN post_matches pm ON pm.special_day_id = sd.id
                   JOIN instagram_posts ip ON ip.id = pm.instagram_post_id
                   WHERE sd.name = 'UAE National Day'"""
            ).fetchone()
        self.assertEqual(row["post_url"], "https://instagram.com/p/national/")

    def test_no_post_leaves_needs_review(self):
        agent = InstagramAgent(self.settings, BASE_DIR, self.logger)
        summary = agent.match_posts_to_days(self.db_path)
        self.assertEqual(summary["matched"], 0)
        self.assertEqual(summary["needs_review"], 2)


if __name__ == "__main__":
    unittest.main()
