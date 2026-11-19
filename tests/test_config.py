"""Shared environment-loading contract."""
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
(ROOT / ".cache").mkdir(exist_ok=True)


class ConfigTests(unittest.TestCase):
    def test_existing_environment_wins(self):
        from config import load_env
        with tempfile.TemporaryDirectory(dir=ROOT / '.cache') as directory:
            (Path(directory) / '.env').write_text('EXISTING=file\nNEW_VALUE="hello"\n')
            with patch.dict(os.environ, {'EXISTING': 'process'}, clear=True):
                self.assertTrue(load_env(Path(directory)))
                self.assertEqual(os.environ['EXISTING'], 'process')
                self.assertEqual(os.environ['NEW_VALUE'], 'hello')

    def test_daily_database_update_schedule_is_documented(self):
        example = (ROOT / ".env.example").read_text()
        self.assertIn("DB_UPDATE_TIME=", example)
        self.assertNotIn("DB_UPDATE_CRON", example)

    def test_env_example_only_lists_required_runtime_overrides(self):
        example = (ROOT / ".env.example").read_text()
        keys = {line.split("=", 1)[0] for line in example.splitlines()
                if line and not line.startswith("#") and "=" in line}
        self.assertEqual(keys, {
            "BOT_TOKEN", "DB_PATH", "POLL_SEC", "LONGPOLL_SEC",
            "POLL_BACKOFF_MAX_SEC", "DB_UPDATE_TIME",
            "TELEGRAM_API", "TELEGRAM_HASH", "USER_SESSION_STRING",
        })
