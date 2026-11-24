"""Human-facing maintenance diagnostics must not expose runtime secrets."""

import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from bot import update

ROOT = Path(__file__).resolve().parents[1]


class UpdateStatusTests(unittest.TestCase):
    def test_success_preserves_last_success_after_a_failed_attempt(self):
        with tempfile.TemporaryDirectory(dir=ROOT / ".cache") as directory:
            root = Path(directory)
            with (
                patch.object(update, "ROOT", root),
                patch("config.load_env"),
                patch.object(update, "_perform_update"),
            ):
                update.run_update()
                success = update.read_status()["last_success"]
            with (
                patch.object(update, "ROOT", root),
                patch("config.load_env"),
                patch.object(
                    update,
                    "_perform_update",
                    side_effect=OSError("private /path token=secret"),
                ),
            ):
                with self.assertRaises(OSError):
                    update.run_update()
                status = update.read_status()
                self.assertEqual(status["last_success"], success)
                self.assertEqual(status["state"], "failed")
                serialized = (root / ".update-status.json").read_text()
                self.assertNotIn("secret", serialized)
                self.assertNotIn("/path", serialized)

    def test_readonly_status_cli_does_not_require_bot_credentials(self):
        result = subprocess.run(
            [sys.executable, "-m", "bot.update", "--status"],
            cwd=ROOT,
            env={"PATH": os.environ["PATH"]},
            capture_output=True,
            text=True,
            timeout=10,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        import json

        self.assertIn("state", json.loads(result.stdout))

    def test_non_object_status_file_does_not_block_refresh(self):
        with tempfile.TemporaryDirectory(dir=ROOT / ".cache") as directory:
            root = Path(directory)
            (root / ".update-status.json").write_text('["last_success"]')
            with (
                patch.object(update, "ROOT", root),
                patch("config.load_env"),
                patch.object(update, "_perform_update"),
            ):
                update.run_update()
                self.assertEqual(update.read_status()["state"], "succeeded")


if __name__ == "__main__":
    unittest.main()
