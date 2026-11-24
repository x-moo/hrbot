import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch, Mock

from bot import update, app

ROOT = Path(__file__).resolve().parents[1]
CACHE = ROOT / ".cache"
CACHE.mkdir(exist_ok=True)


class UpdateIntegrationTests(unittest.TestCase):
    def test_custom_paths_and_interpreter_are_shared_by_pipeline_and_import(self):
        with (
            patch.dict(
                os.environ,
                {"DB_PATH": "data/custom.db", "DUMP": "custom-dump"},
                clear=True,
            ),
            patch.object(update, "_run_command") as run,
            patch.object(update, "_verify_database"),
            tempfile.TemporaryDirectory(dir=CACHE) as directory,
            update.update_lock(Path(directory) / "update.lock") as lock_fd,
        ):
            update._perform_update(lock_fd)
        calls = run.call_args_list
        self.assertEqual(calls[0].kwargs["env"]["PYTHON"], sys.executable)
        self.assertEqual(calls[0].kwargs["env"]["OUT"], str(ROOT / "custom-dump"))
        self.assertIn(str(ROOT / "data/custom.db"), calls[-1].args[0])
        self.assertIn(str(ROOT / "custom-dump"), calls[-1].args[0])
        self.assertTrue(all(call.kwargs["lock_fd"] == lock_fd for call in calls))

    def test_startup_calls_the_scheduled_callback(self):
        db = Mock()
        db.stats.return_value = ((1, 1, 2026, 2026), {})
        callback = Mock()

        def immediate_worker(hhmm, func, **kwargs):
            self.assertEqual(hhmm, "03:00")
            func()
            kwargs.get("startup_callback", lambda: None)()

        with (
            patch.dict(
                os.environ,
                {
                    "BOT_TOKEN": "test",
                    "DB_PATH": "catalog.db",
                    "DB_UPDATE_TIME": "03:00",
                },
                clear=True,
            ),
            patch.object(app, "load_env"),
            patch.object(app, "database_usable", return_value=True),
            patch.object(app.os.path, "exists", return_value=True),
            patch.object(app, "Archive", return_value=db),
            patch.object(app, "run_update", callback),
            patch.object(app, "refresh_if_stale") as refresh,
            patch("bot.scheduler.start_daily_worker", side_effect=immediate_worker),
            patch.object(app, "prune_downloads"),
            patch.object(app, "_configure_menu"),
            patch.object(app, "tg_call", side_effect=KeyboardInterrupt),
        ):
            app.main()
        callback.assert_called_once()
        self.assertEqual(refresh.call_args.args[0], os.path.abspath("catalog.db"))

    def test_startup_and_refresh_share_relative_db_path_outside_checkout_root(self):
        with tempfile.TemporaryDirectory(dir=CACHE) as directory:
            base = Path(directory)
            cwd = base / "launch"
            cwd.mkdir()
            db_path = ROOT / "catalog.db"
            db = Mock()
            db.stats.return_value = ((0, 0, None, None), {})
            real_lock = update.update_lock
            previous = Path.cwd()
            try:
                os.chdir(cwd)
                with (
                    patch.dict(
                        os.environ,
                        {
                            "BOT_TOKEN": "test",
                            "DB_PATH": "catalog.db",
                            "DB": "legacy.db",
                            "DB_UPDATE_TIME": "03:00",
                        },
                        clear=True,
                    ),
                    patch("config.load_env"),
                    patch.object(app, "load_env"),
                    patch.object(app, "database_usable", return_value=True),
                    patch.object(app.os.path, "exists", return_value=True),
                    patch.object(app, "Archive", return_value=db) as archive,
                    patch.object(
                        update,
                        "update_lock",
                        side_effect=lambda: real_lock(base / "update.lock"),
                    ),
                    patch.object(update, "_run_command") as run,
                    patch.object(update, "_verify_database"),
                    patch(
                        "bot.scheduler.start_daily_worker",
                        side_effect=lambda _, func, **kw: func(),
                    ),
                    patch.object(app, "prune_downloads"),
                    patch.object(app, "_configure_menu"),
                    patch.object(app, "tg_call", side_effect=KeyboardInterrupt),
                ):
                    app.main()
            finally:
                os.chdir(previous)
            archive.assert_called_once_with(str(db_path))
            self.assertEqual(run.call_args.args[0][-2:], ["--db", str(db_path)])

    @staticmethod
    def _fake_python(root):
        """Report startup/harvest instead of running the real bot or pipeline."""
        wrapper = root / "fake-python"
        wrapper.write_text(
            "#!/usr/bin/env bash\n"
            'if [ "$1" = "bot.py" ]; then echo FAKE_STARTED; exit 0; fi\n'
            'if [ "$1" = "-m" ] && [ "$2" = "bot.update" ]; then echo FAKE_HARVEST; exit 0; fi\n'
            'exec "$REAL_PY" "$@"\n'
        )
        wrapper.chmod(0o755)
        return wrapper

    @classmethod
    def _run_start(cls, root, wrapper, extra_env=None):
        import subprocess

        env = {
            "PATH": os.environ["PATH"],
            "PYTHON": str(wrapper),
            "REAL_PY": sys.executable,
            "INSTALL_DEPS": "0",
        }
        if extra_env:
            env.update(extra_env)
        return subprocess.run(
            ["bash", str(root / "start.sh")], env=env, text=True, capture_output=True
        )

    def test_bootstrap_skips_existing_db_configured_only_in_dotenv(self):
        import shutil

        with tempfile.TemporaryDirectory(dir=CACHE) as directory:
            root = Path(directory)
            shutil.copy(ROOT / "start.sh", root / "start.sh")
            shutil.copy(ROOT / "config.py", root / "config.py")
            (root / ".env").write_text("DB_PATH=custom.db\n")
            (root / "custom.db").touch()
            result = self._run_start(root, self._fake_python(root))
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn("skip bootstrap", result.stdout)
            self.assertIn("custom.db already exists", result.stdout)
            self.assertIn("FAKE_STARTED", result.stdout)
            self.assertNotIn("FAKE_HARVEST", result.stdout)

    def test_bootstrap_prefers_db_path_to_legacy_db(self):
        import shutil

        with tempfile.TemporaryDirectory(dir=CACHE) as directory:
            root = Path(directory)
            shutil.copy(ROOT / "start.sh", root / "start.sh")
            shutil.copy(ROOT / "config.py", root / "config.py")
            (root / ".env").write_text("DB_PATH=custom.db\nDB=legacy.db\n")
            (root / "legacy.db").touch()
            result = self._run_start(root, self._fake_python(root))
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn("custom.db missing -> harvesting", result.stdout)
            self.assertIn("FAKE_HARVEST", result.stdout)
            self.assertIn("FAKE_STARTED", result.stdout)
