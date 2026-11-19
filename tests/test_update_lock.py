import tempfile
import unittest
from pathlib import Path

from bot.update import update_lock


class UpdateLockTests(unittest.TestCase):
    def test_prevents_two_refreshes_from_running_at_once(self):
        with tempfile.TemporaryDirectory() as tmp:
            lock_path = Path(tmp) / "update.lock"
            with update_lock(lock_path):
                with self.assertRaisesRegex(RuntimeError, "already running"):
                    with update_lock(lock_path):
                        pass
            with update_lock(lock_path):
                pass


if __name__ == "__main__":
    unittest.main()
