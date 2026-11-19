"""Run archive refreshes safely from the long-lived bot process."""
import fcntl
import os
from pathlib import Path
import subprocess
import sys
from contextlib import contextmanager

ROOT = Path(__file__).resolve().parents[1]


@contextmanager
def update_lock(path=None):
    """Prevent scheduled/manual refreshes from mutating shared pipeline files concurrently."""
    lock_path = Path(path or ROOT / ".update.lock")
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    with lock_path.open("w") as lock_file:
        try:
            fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise RuntimeError("archive update already running") from exc
        try:
            yield
        finally:
            fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)


def _perform_update():
    """Run the archive pipeline and import using the current process config."""
    env = os.environ.copy()
    def absolute(value):
        path = Path(value)
        return str(path if path.is_absolute() else ROOT / path)
    env['PYTHON'] = sys.executable
    env['DB'] = absolute(env.get('DB') or env.get('DB_PATH') or 'archive.db')
    env['DUMP'] = absolute(env.get('DUMP') or env.get('OUT') or 'haramain')
    env['OUT'] = env['DUMP']
    env['WORK'] = absolute(env.get('WORK') or 'work')
    if os.getenv("SWEEP", "0") == "1":
        stages = ("sitemap", "feed", "sweep", "build")
    else:
        stages = ("sitemap", "feed", "build")
    for stage in stages:
        subprocess.run(["bash", "run.sh", stage], check=True,
                       cwd=ROOT / "pipeline", env=env)
    subprocess.run([sys.executable, "archive/import_dump.py", "--dump",
                    env["DUMP"], "--db", env["DB"]], check=True,
                   cwd=ROOT, env=env)


def run_update():
    """Run the scheduled refresh with a process-wide lock."""
    from config import load_env
    load_env(ROOT)
    with update_lock():
        _perform_update()


def main():
    """CLI entry point for a manually requested safe refresh."""
    run_update()


if __name__ == "__main__":
    main()
