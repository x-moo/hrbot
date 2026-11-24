import select
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

from bot.update import update_lock

CACHE = Path(__file__).resolve().parents[1] / ".cache"
CACHE.mkdir(exist_ok=True)


class UpdateLockTests(unittest.TestCase):
    def test_surviving_pipeline_child_keeps_lock_after_updater_is_killed(self):
        with tempfile.TemporaryDirectory(dir=CACHE) as tmp:
            root = Path(tmp)
            pipeline = root / 'pipeline'
            pipeline.mkdir()
            (pipeline / 'run.sh').write_text('exec "$PYTHON" child.py\n')
            (pipeline / 'child.py').write_text(
                'import select, sys\n'
                'print("ready", flush=True)\n'
                'if select.select([sys.stdin], [], [], 10)[0]:\n'
                '    sys.stdin.readline()\n'
            )
            lock = root / '.update.lock'
            code = ('import sys\nfrom pathlib import Path\nfrom bot import update\n'
                    'update.ROOT = Path(sys.argv[1])\nupdate.run_update()\n')
            with subprocess.Popen([sys.executable, '-c', code, str(root)],
                    stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True) as parent:
                try:
                    # Stage logs precede the child readiness line.
                    self.assertTrue(select.select([parent.stdout], [], [], 5)[0])
                    self.assertIn('[update] sitemap: started', parent.stdout.readline())
                    self.assertTrue(select.select([parent.stdout], [], [], 5)[0])
                    self.assertEqual(parent.stdout.readline().strip(), 'ready')
                    parent.kill()
                    parent.wait(timeout=5)
                    with self.assertRaisesRegex(RuntimeError, 'already running'):
                        with update_lock(lock):
                            pass
                finally:
                    if parent.poll() is None:
                        parent.kill()
                    # EOF releases the waiting child; wait for its stdout to close.
                    parent.communicate(timeout=12)
            deadline = time.monotonic() + 2
            while True:
                try:
                    with update_lock(lock):
                        break
                except RuntimeError:
                    if time.monotonic() >= deadline:
                        raise
                    time.sleep(0.01)

    def test_prevents_two_refreshes_from_running_at_once(self):
        with tempfile.TemporaryDirectory(dir=CACHE) as tmp:
            lock_path = Path(tmp) / "update.lock"
            with update_lock(lock_path):
                with self.assertRaisesRegex(RuntimeError, "already running"):
                    with update_lock(lock_path):
                        pass
            with update_lock(lock_path):
                pass


if __name__ == "__main__":
    unittest.main()
