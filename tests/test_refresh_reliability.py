"""Deterministic refresh regressions: missed schedules and retry recovery."""

import os
import sqlite3
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

from bot import scheduler, update

ROOT = Path(__file__).resolve().parents[1]
CACHE = ROOT / ".cache"
CACHE.mkdir(exist_ok=True)


class RefreshReliabilityTests(unittest.TestCase):
    def test_restart_catches_missed_schedule_even_when_db_is_under_24_hours(self):
        """Yesterday's 04:00 import must catch up after today's 03:00 schedule."""
        with tempfile.TemporaryDirectory(dir=CACHE) as directory:
            database = Path(directory) / "archive.db"
            with sqlite3.connect(database) as db:
                db.execute("CREATE TABLE meta (k TEXT PRIMARY KEY, v TEXT)")
                db.execute(
                    "INSERT INTO meta VALUES (?,?)",
                    ("built", "2026-01-01T04:00:00+00:00"),
                )
            db.close()
            with patch.dict(os.environ, {"DB_UPDATE_TIME": "03:00"}, clear=True):
                due = update.needs_refresh(
                    database, now=datetime(2026, 1, 2, 3, 30, tzinfo=timezone.utc)
                )
            self.assertTrue(
                due, "a missed daily run must not wait for the age to reach 24h"
            )

    def test_failed_catchup_retries_without_waiting_until_tomorrow(self):
        events = []
        now = [datetime(2026, 1, 2, 4, 0, tzinfo=timezone.utc)]

        def callback():
            events.append("attempt")
            if len(events) == 1:
                raise OSError("temporary outage")

        worker_type = getattr(scheduler, "DailyWorker", None)
        self.assertIsNotNone(
            worker_type, "scheduler needs a testable retry state machine"
        )
        worker = worker_type(
            "03:00",
            callback,
            startup_callback=callback,
            clock=lambda: now[0],
            retry_seconds=300,
            logger=lambda _: None,
        )
        worker.step()
        self.assertEqual(events, ["attempt"])
        now[0] = datetime(2026, 1, 2, 4, 5, tzinfo=timezone.utc)
        worker.step()
        self.assertEqual(events, ["attempt", "attempt"])
        self.assertEqual(
            worker.next_run, datetime(2026, 1, 3, 3, 0, tzinfo=timezone.utc)
        )

    def test_refresh_is_observable_and_leaves_old_database_on_child_failure(self):
        with tempfile.TemporaryDirectory(dir=CACHE) as directory:
            root = Path(directory)
            (root / "pipeline").mkdir()
            (root / "pipeline/run.sh").write_text("exit 7\n")
            database = root / "archive.db"
            with sqlite3.connect(database) as db:
                db.execute("CREATE TABLE media (url TEXT)")
                db.execute("INSERT INTO media VALUES ('old')")
            db.close()
            with (
                patch.object(update, "ROOT", root),
                patch.dict(os.environ, {}, clear=True),
            ):
                with self.assertRaises(RuntimeError):
                    update.run_update()
            import json

            status_path = root / ".update-status.json"
            self.assertTrue(
                status_path.exists(), "refresh failures need a persistent diagnostic"
            )
            status = json.loads(status_path.read_text())
            self.assertEqual(status["state"], "failed")
            self.assertEqual(status["stage"], "sitemap")
            with sqlite3.connect(database) as db:
                self.assertEqual(
                    db.execute("SELECT url FROM media").fetchone()[0], "old"
                )
            db.close()

    def test_database_path_with_uri_characters_is_read_correctly(self):
        with tempfile.TemporaryDirectory(dir=CACHE) as directory:
            path = Path(directory) / "catalog #1?.db"
            with sqlite3.connect(path) as db:
                db.execute("CREATE TABLE meta (k TEXT PRIMARY KEY, v TEXT)")
                db.execute(
                    "INSERT INTO meta VALUES (?,?)",
                    ("built", "2026-01-02T04:00:00+00:00"),
                )
            db.close()
            self.assertIsNotNone(update.db_built_at(path))


if __name__ == "__main__":
    unittest.main()
