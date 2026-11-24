"""Startup wiring for default automatic refresh and explicit opt-out."""

import os
import unittest
from contextlib import ExitStack
from pathlib import Path
from unittest.mock import Mock, patch

from bot import app

ROOT = Path(__file__).resolve().parents[1]


class AppStartupTests(unittest.TestCase):
    def run_app(self, env):
        db = Mock()
        db.stats.return_value = ((1, 1, 2026, 2026), {"audio": 1})
        with ExitStack() as stack:
            stack.enter_context(
                patch.dict(os.environ, {"BOT_TOKEN": "test", **env}, clear=True)
            )
            stack.enter_context(patch.object(app, "load_env"))
            stack.enter_context(patch.object(app.os.path, "exists", return_value=True))
            stack.enter_context(patch.object(app, "database_usable", return_value=True))
            stack.enter_context(patch.object(app, "Archive", return_value=db))
            stack.enter_context(patch.object(app, "prune_downloads"))
            stack.enter_context(patch.object(app, "_configure_menu"))
            stack.enter_context(
                patch.object(app, "tg_call", side_effect=KeyboardInterrupt)
            )
            worker = Mock()
            start = stack.enter_context(
                patch("bot.scheduler.start_daily_worker", return_value=worker)
            )
            app.main()
        return start, worker, db

    def test_auto_refresh_is_enabled_when_time_was_never_configured(self):
        start, worker, db = self.run_app({})
        start.assert_called_once()
        self.assertEqual(start.call_args.args[0], "03:00")
        worker.stop.assert_called_once()
        db.db.close.assert_called_once()

    def test_explicit_blank_disables_auto_refresh(self):
        start, worker, _ = self.run_app({"DB_UPDATE_TIME": ""})
        start.assert_not_called()

    def test_config_validation_rejects_nan_before_polling(self):
        with (
            patch.dict(
                os.environ, {"BOT_TOKEN": "test", "POLL_SEC": "nan"}, clear=True
            ),
            patch.object(app, "load_env"),
            patch.object(app, "Archive") as archive,
        ):
            with self.assertRaisesRegex(SystemExit, "POLL_SEC"):
                app.main()
        archive.assert_not_called()

    def test_existing_unusable_database_is_repaired_before_polling(self):
        import sqlite3
        import tempfile

        with tempfile.TemporaryDirectory(dir=ROOT / ".cache") as directory:
            database = Path(directory) / "archive.db"
            with sqlite3.connect(database) as db:
                db.execute("CREATE TABLE meta (k TEXT PRIMARY KEY, v TEXT)")
            db.close()
            calls = []

            def repair():
                calls.append("refresh")
                with sqlite3.connect(database) as db:
                    db.execute(
                        "CREATE TABLE media (url TEXT, post TEXT, kind TEXT, year INT, month INT, day INT, slug TEXT, fname TEXT)"
                    )
                db.close()

            with (
                patch.dict(
                    os.environ,
                    {"BOT_TOKEN": "test", "DB_PATH": str(database)},
                    clear=True,
                ),
                patch.object(app, "load_env"),
                patch.object(app, "run_update", side_effect=repair) as refresh,
                patch.object(
                    app,
                    "Archive",
                    side_effect=lambda path: (
                        calls.append(("archive", path)) or self._stats_db()
                    ),
                ),
                patch.object(app, "prune_downloads"),
                patch.object(app, "_configure_menu"),
                patch.object(app, "tg_call", side_effect=KeyboardInterrupt),
                patch("bot.scheduler.start_daily_worker", return_value=Mock()),
            ):
                app.main()
            refresh.assert_called_once()
            self.assertEqual(calls[0], "refresh")
            self.assertEqual(calls[1][1], str(database))

    @staticmethod
    def _stats_db():
        db = Mock()
        db.stats.return_value = ((1, 1, 2026, 2026), {"audio": 1})
        return db

    def test_unusable_database_with_refresh_disabled_exits_with_repair_command(self):
        import sqlite3
        import tempfile

        with tempfile.TemporaryDirectory(dir=ROOT / ".cache") as directory:
            database = Path(directory) / "archive.db"
            with sqlite3.connect(database) as db:
                db.execute("CREATE TABLE meta (k TEXT PRIMARY KEY, v TEXT)")
            db.close()
            with (
                patch.dict(
                    os.environ,
                    {
                        "BOT_TOKEN": "test",
                        "DB_UPDATE_TIME": "",
                        "DB_PATH": str(database),
                    },
                    clear=True,
                ),
                patch.object(app, "load_env"),
                patch.object(app, "run_update") as refresh,
                patch.object(app, "Archive") as archive,
            ):
                with self.assertRaisesRegex(SystemExit, "python3 -m bot.update"):
                    app.main()
            refresh.assert_not_called()
            archive.assert_not_called()

    def test_worker_start_failure_still_closes_archive_connection(self):
        db = Mock()
        with (
            patch.dict(os.environ, {"BOT_TOKEN": "test"}, clear=True),
            patch.object(app, "load_env"),
            patch.object(app.os.path, "exists", return_value=True),
            patch.object(app, "database_usable", return_value=True),
            patch.object(app, "Archive", return_value=db),
            patch(
                "bot.scheduler.start_daily_worker",
                side_effect=RuntimeError("cannot start"),
            ),
        ):
            with self.assertRaisesRegex(RuntimeError, "cannot start"):
                app.main()
        db.db.close.assert_called_once()


if __name__ == "__main__":
    unittest.main()
