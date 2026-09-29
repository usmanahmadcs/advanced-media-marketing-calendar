import json
import os
import sys
import tempfile
import unittest

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if BASE_DIR not in sys.path:
    sys.path.insert(0, BASE_DIR)


class DummyLogger:
    def info(self, *a, **k):
        pass

    def warning(self, *a, **k):
        pass

    def error(self, *a, **k):
        pass


def load_settings():
    with open(os.path.join(BASE_DIR, "config", "settings.json"), encoding="utf-8") as f:
        return json.load(f)


class TempDirTestCase(unittest.TestCase):
    """Base class giving each test its own temp directory (self.tmp_dir) and
    a pre-initialized SQLite DB path (self.db_path), replicating what a
    pytest 'tmp_path' fixture would provide, without requiring pytest."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp_dir = self._tmp.name
        self.db_path = os.path.join(self.tmp_dir, "test.db")
        from src.database import init_db
        init_db(self.db_path)
        self.settings = load_settings()
        self.logger = DummyLogger()

    def tearDown(self):
        self._tmp.cleanup()
