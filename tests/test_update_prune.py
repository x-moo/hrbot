"""Optional post-import pruning must never risk the committed catalog."""

import contextlib
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from bot import update

ROOT = Path(__file__).resolve().parents[1]


class PruneTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory(dir=ROOT / ".cache")
        self.root = Path(self._tmp.name)
        self.work = self.root / "work"
        self.dump = self.root / "haramain"
        self.db = self.root / "archive.db"
        self.db.write_bytes(b"db")
        self._seed(self.work)
        self._seed(self.dump)

    def tearDown(self):
        self._tmp.cleanup()

    @staticmethod
    def _seed(directory, payload=b"payload"):
        directory.mkdir(parents=True, exist_ok=True)
        (directory / "payload.bin").write_bytes(payload)
        return directory

    def _environ(self, **extra):
        values = {
            "DB_PATH": str(self.db),
            "WORK": str(self.work),
            "OUT": str(self.dump),
        }
        values.update(extra)
        return patch.dict(os.environ, values, clear=True)

    def _patches(self, **environ):
        return [
            patch.object(update, "ROOT", self.root),
            patch("config.load_env"),
            self._environ(**environ),
        ]

    def _run_update(self, *, enabled=True, side_effect=None, rmtree=None):
        extra = {"DB_UPDATE_PRUNE": "1"} if enabled else {}
        with contextlib.ExitStack() as stack:
            for item in self._patches(**extra):
                stack.enter_context(item)
            stack.enter_context(
                patch.object(
                    update, "_perform_update", side_effect=side_effect
                )
            )
            if rmtree is not None:
                stack.enter_context(
                    patch.object(update.shutil, "rmtree", side_effect=rmtree)
                )
            update.run_update()
            return update.read_status()

    def _prune(self, **environ):
        values = {"DB_UPDATE_PRUNE": "1"}
        values.update(environ)
        lines = []
        with contextlib.ExitStack() as stack:
            for item in self._patches(**values):
                stack.enter_context(item)
            update.prune_pipeline_artifacts(logger=lines.append)
        return lines

    def test_disabled_by_default_keeps_every_artifact(self):
        status = self._run_update(enabled=False)
        self.assertEqual(status["state"], "succeeded")
        self.assertTrue(self.work.is_dir())
        self.assertTrue(self.dump.is_dir())

    def test_successful_update_removes_rebuildable_output(self):
        status = self._run_update()
        self.assertEqual(status["state"], "succeeded")
        self.assertFalse(self.work.exists())
        self.assertFalse(self.dump.exists())
        self.assertEqual(self.db.read_bytes(), b"db")

    def test_failed_update_keeps_every_artifact_for_debugging(self):
        with contextlib.ExitStack() as stack:
            for item in self._patches(DB_UPDATE_PRUNE="1"):
                stack.enter_context(item)
            stack.enter_context(
                patch.object(
                    update,
                    "_perform_update",
                    side_effect=update.UpdateError("refresh stage failed"),
                )
            )
            with self.assertRaises(update.UpdateError):
                update.run_update()
            status = update.read_status()
        self.assertEqual(status["state"], "failed")
        self.assertTrue(self.work.is_dir())
        self.assertTrue(self.dump.is_dir())

    def test_cleanup_failure_never_fails_a_committed_update(self):
        status = self._run_update(rmtree=OSError("device busy"))
        self.assertEqual(status["state"], "succeeded")
        self.assertTrue(self.work.is_dir())
        self.assertTrue(self.dump.is_dir())

    def test_missing_directories_are_not_an_error(self):
        for directory in (self.work, self.dump):
            (directory / "payload.bin").unlink()
            directory.rmdir()
        status = self._run_update()
        self.assertEqual(status["state"], "succeeded")

    def test_switch_is_required(self):
        lines = self._prune(DB_UPDATE_PRUNE="0")
        self.assertTrue(self.work.is_dir())
        self.assertTrue(self.dump.is_dir())
        self.assertEqual(lines, [])

    def test_refuses_the_checkout_root(self):
        lines = self._prune(WORK=str(self.root), OUT=str(self.root))
        self.assertTrue(self.root.is_dir())
        self.assertTrue(self.db.is_file())
        self.assertTrue(any("kept" in line for line in lines))

    def test_refuses_source_directories(self):
        source = self._seed(self.root / "bot")
        lines = self._prune(WORK=str(source))
        self.assertTrue(source.is_dir())
        self.assertFalse(self.dump.exists())  # only the refused entry stays
        self.assertTrue(any("kept" in line for line in lines))

    def test_refuses_a_directory_that_contains_the_database(self):
        nested = self._seed(self.work / "nested")
        db = nested / "archive.db"
        db.write_bytes(b"db")
        lines = self._prune(DB_PATH=str(db), WORK=str(nested))
        self.assertTrue(nested.is_dir())
        self.assertTrue(db.is_file())
        self.assertTrue(any("kept" in line for line in lines))

    def test_refuses_symlinks(self):
        real = self._seed(self.root / "real")
        link = self.root / "link"
        link.symlink_to(real, target_is_directory=True)
        self._prune(WORK=str(link))
        self.assertTrue(real.is_dir())
        self.assertTrue((real / "payload.bin").is_file())

    def test_same_directory_for_work_and_dump_is_removed_once(self):
        lines = self._prune(WORK=str(self.work), OUT=str(self.work))
        self.assertFalse(self.work.exists())
        self.assertEqual(len([line for line in lines if "removed" in line]), 1)

    def test_removes_stale_swap_leftovers(self):
        stale = self._seed(self.root / f".{self.dump.name}.old-crashed")
        self._prune()
        self.assertFalse(stale.exists())
        self.assertFalse(self.dump.exists())
        self.assertFalse(self.work.exists())

    def test_relative_paths_resolve_against_the_checkout(self):
        self._prune(WORK="work", OUT="haramain")
        self.assertFalse(self.work.exists())
        self.assertFalse(self.dump.exists())


if __name__ == "__main__":
    unittest.main()
