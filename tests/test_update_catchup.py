import os
import sqlite3
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

from bot import update
from bot.scheduler import start_daily_worker

CACHE = Path(__file__).resolve().parents[1] / '.cache'
CACHE.mkdir(exist_ok=True)


class CatchUpTests(unittest.TestCase):
    @staticmethod
    def make_db(path, built=None):
        db = sqlite3.connect(path)
        db.execute('CREATE TABLE meta (k TEXT PRIMARY KEY, v TEXT)')
        if built is not None:
            db.execute("INSERT INTO meta VALUES ('built', ?)", (str(built),))
        db.commit()
        db.close()
        return path

    def test_missing_or_unreadable_db_needs_refresh(self):
        with tempfile.TemporaryDirectory(dir=CACHE) as tmp:
            root = Path(tmp)
            self.assertTrue(update.needs_refresh(root / 'missing.db'))
            broken = root / 'broken.db'
            broken.write_text('not sqlite')
            self.assertTrue(update.needs_refresh(broken))
            self.assertFalse(update.needs_refresh(broken, max_age_hours=0))

    def test_fresh_and_stale_imports(self):
        with tempfile.TemporaryDirectory(dir=CACHE) as tmp:
            path = self.make_db(Path(tmp) / 'archive.db', datetime.now().isoformat())
            self.assertFalse(update.needs_refresh(path))
            db = sqlite3.connect(path)
            old = (datetime.now() - timedelta(hours=25)).isoformat()
            db.execute("UPDATE meta SET v=? WHERE k='built'", (old,))
            db.commit()
            db.close()
            self.assertTrue(update.needs_refresh(path))
            self.assertFalse(update.needs_refresh(path, max_age_hours=48))

    def test_refresh_if_stale_runs_update_only_when_needed(self):
        with tempfile.TemporaryDirectory(dir=CACHE) as tmp:
            path = self.make_db(Path(tmp) / 'archive.db', datetime.now().isoformat())
            logs = []
            with patch.dict(os.environ, {'DB_UPDATE_MAX_AGE_HOURS': '24'}, clear=True), \
                 patch.object(update, 'run_update') as run:
                self.assertFalse(update.refresh_if_stale(path, logger=logs.append))
                run.assert_not_called()
                old = (datetime.now() - timedelta(days=2)).isoformat()
                db = sqlite3.connect(path)
                db.execute("UPDATE meta SET v=? WHERE k='built'", (old,))
                db.commit()
                db.close()
                self.assertTrue(update.refresh_if_stale(path, logger=logs.append))
                run.assert_called_once()
            self.assertIn('database is stale — running catch-up update', logs)
            self.assertIn('catch-up update complete', logs)

    def test_worker_runs_startup_catch_up_before_the_timer(self):
        from bot.scheduler import DailyWorker
        events = []
        now = [datetime(2026, 1, 2, 1, tzinfo=timezone.utc)]
        def startup():
            events.append('startup')
        worker = DailyWorker('03:00', lambda: events.append('scheduled'),
                             startup_callback=startup, logger=lambda _: None,
                             clock=lambda: now[0])
        worker.step()
        self.assertEqual(events, ['startup'])
        now[0] = datetime(2026, 1, 2, 3, tzinfo=timezone.utc)
        worker.step()
        self.assertEqual(events, ['startup', 'scheduled'])


if __name__ == '__main__':
    unittest.main()
