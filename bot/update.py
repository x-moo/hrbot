"""Run archive refreshes safely from the long-lived bot process."""

import datetime as dt
import fcntl
import json
import math
import os
import shutil
import signal
import sqlite3
import subprocess
import sys
import tempfile
import time
from contextlib import closing, contextmanager
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _resolve(value):
    """Relative pipeline paths are always anchored to the checkout root."""
    path = Path(value)
    return path if path.is_absolute() else ROOT / path


def db_path():
    """The same database path the bot uses, resolved against the checkout."""
    return _resolve(os.environ.get("DB_PATH") or os.environ.get("DB") or "archive.db")


def db_built_at(path=None):
    """Timestamp of the last successful import (meta.built), or None."""
    target = Path(path) if path is not None else db_path()
    if not target.is_absolute():
        target = ROOT / target
    try:
        with closing(
            sqlite3.connect(target.resolve().as_uri() + "?mode=ro", uri=True)
        ) as db:
            row = db.execute("SELECT v FROM meta WHERE k='built'").fetchone()
    except (sqlite3.Error, OSError):
        return None
    if not row:
        return None
    try:
        return dt.datetime.fromisoformat(str(row[0]))
    except (TypeError, ValueError):
        return None


def needs_refresh(path=None, max_age_hours=24, now=None):
    """True when the imported dump is missing, unreadable or older than max age."""
    if not math.isfinite(max_age_hours) or max_age_hours < 0:
        raise ValueError("DB_UPDATE_MAX_AGE_HOURS must be finite and nonnegative")
    if max_age_hours == 0:
        return False
    built = db_built_at(path)
    if built is None:
        return True
    from bot.scheduler import as_utc, next_daily_run

    now = as_utc(now or dt.datetime.now(dt.timezone.utc))
    built = as_utc(built)
    schedule = os.environ.get("DB_UPDATE_TIME", "").strip()
    if schedule:
        last_scheduled = next_daily_run(now, schedule) - dt.timedelta(days=1)
        if built < last_scheduled:
            return True
    return (now - built).total_seconds() >= max_age_hours * 3600


def refresh_if_stale(path=None, logger=print):
    """Catch up after a restart if the scheduled run was missed.

    Hosted processes are redeployed and restarted at arbitrary times; a strict
    HH:MM timer alone can skip a day. The lock in run_update() keeps this from
    racing a manual or scheduled refresh.
    """
    try:
        max_age = float(os.environ.get("DB_UPDATE_MAX_AGE_HOURS", "24"))
    except ValueError:
        max_age = 24.0
    if not needs_refresh(path, max_age):
        return False
    logger("database is stale — running catch-up update")
    run_update()
    logger("catch-up update complete")
    return True


class UpdateError(RuntimeError):
    """A failed or timed-out refresh, safe to retry without discarding the DB."""


class UpdateBusy(UpdateError):
    """Another updater still owns the advisory lock."""


def _status_path():
    return ROOT / ".update-status.json"


def _write_status(state, **values):
    """Publish diagnostics atomically; never include command output or secrets."""
    path = _status_path()
    previous = {}
    try:
        previous = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(previous, dict):
            previous = {}
    except (OSError, ValueError):
        pass
    if state == "running" and values.get("stage") == "starting":
        previous = {key: previous[key] for key in ("last_success",) if key in previous}
    payload = {**previous, "state": state, **values}
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(
        mode="w",
        encoding="utf-8",
        dir=path.parent,
        prefix=".update-status-",
        delete=False,
    ) as stream:
        temporary = Path(stream.name)
        try:
            json.dump(payload, stream, sort_keys=True)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        except BaseException:
            temporary.unlink(missing_ok=True)
            raise
    try:
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def read_status():
    """Read-only operational status for CLI/support checks."""
    try:
        return json.loads(_status_path().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {"state": "never_run"}


@contextmanager
def update_lock(path=None):
    """Prevent scheduled/manual refreshes from mutating shared pipeline files concurrently."""
    lock_path = Path(path or ROOT / ".update.lock")
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    with lock_path.open("w") as lock_file:
        try:
            fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise UpdateBusy("archive update already running") from exc
        # The lock is released when the last copy of this descriptor closes.
        # Explicitly unlocking here would also unlock any surviving child.
        yield lock_file.fileno()


def _run_command(command, *, cwd, env, lock_fd, timeout):
    """Bound the whole process group, including shell grandchildren."""
    with subprocess.Popen(
        command, cwd=cwd, env=env, pass_fds=(lock_fd,), start_new_session=True
    ) as process:
        try:
            returncode = process.wait(timeout=timeout)
        except (subprocess.TimeoutExpired, KeyboardInterrupt):
            try:
                os.killpg(process.pid, signal.SIGTERM)
                try:
                    process.wait(timeout=2)
                except subprocess.TimeoutExpired:
                    pass
            except ProcessLookupError:
                pass
            finally:
                # The shell may already have exited while a grandchild ignores
                # TERM; always kill the group, not only the tracked shell PID.
                try:
                    os.killpg(process.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
                process.wait()
            raise UpdateError("refresh stage timed out or was interrupted") from None
        if returncode:
            raise UpdateError(f"refresh stage exited with status {returncode}")


def _perform_update(lock_fd):
    """Run logged pipeline stages and a transactional import under one deadline."""
    env = os.environ.copy()

    def absolute(value):
        return str(_resolve(value))

    env["PYTHON"] = sys.executable
    env["PYTHONUNBUFFERED"] = "1"
    env["DB"] = env["DB_PATH"] = absolute(
        env.get("DB_PATH") or env.get("DB") or "archive.db"
    )
    env["DUMP"] = env["OUT"] = absolute(env.get("DUMP") or env.get("OUT") or "haramain")
    env["WORK"] = absolute(env.get("WORK") or "work")
    timeout = float(env.get("DB_UPDATE_TIMEOUT_SEC", "7200"))
    if not math.isfinite(timeout) or timeout <= 0:
        raise ValueError("DB_UPDATE_TIMEOUT_SEC must be finite and positive")
    deadline = time.monotonic() + timeout
    stages = (
        ("sitemap", "feed", "sweep", "build")
        if env.get("SWEEP") == "1"
        else ("sitemap", "feed", "build")
    )
    commands = [
        (stage, ["bash", "run.sh", stage], ROOT / "pipeline") for stage in stages
    ]
    commands.append(
        (
            "import",
            [
                sys.executable,
                "archive/import_dump.py",
                "--dump",
                env["DUMP"],
                "--db",
                env["DB"],
            ],
            ROOT,
        )
    )
    for stage, command, cwd in commands:
        budget = deadline - time.monotonic()
        if budget <= 0:
            raise UpdateError("refresh deadline exceeded")
        if stage == "import":
            import_started = dt.datetime.now(dt.timezone.utc)
        _write_status("running", stage=stage)
        print(f"[update] {stage}: started", flush=True)
        stage_started = time.monotonic()
        _run_command(command, cwd=cwd, env=env, lock_fd=lock_fd, timeout=budget)
        print(
            f"[update] {stage}: complete in {time.monotonic() - stage_started:.1f}s",
            flush=True,
        )
    _write_status("running", stage="verify")
    return _verify_database(env["DB"], import_started)


def _verify_database(path, import_started):
    """Read the committed catalog, not just the child process exit code."""
    try:
        with closing(
            sqlite3.connect(Path(path).resolve().as_uri() + "?mode=ro", uri=True)
        ) as db:
            count = db.execute("SELECT COUNT(*) FROM media").fetchone()[0]
            rows = dict(db.execute("SELECT k,v FROM meta WHERE k IN ('rows','built')"))
            if count < 1 or int(rows.get("rows", 0)) != count:
                raise ValueError("catalog row count mismatch")
            built = dt.datetime.fromisoformat(rows["built"])
            from bot.scheduler import as_utc

            if as_utc(built) < import_started - dt.timedelta(seconds=1):
                raise ValueError("import timestamp was not advanced")
    except (OSError, sqlite3.Error, ValueError, KeyError) as exc:
        raise UpdateError("database post-import verification failed") from exc
    print(f"[update] verified: {count} committed rows", flush=True)
    return {"rows": count, "built": rows["built"]}


# Directories that must never be pruned even if WORK/OUT point at them.
_SOURCE_DIRS = (
    "bot", "leech", "pipeline", "archive", "tests", "docs",
    "deploy", "scripts", ".git", ".cache",
)


def _human_size(count):
    for unit in ("B", "KB", "MB", "GB"):
        if count < 1024 or unit == "GB":
            return f"{count:.0f} {unit}" if unit == "B" else f"{count:.1f} {unit}"
        count /= 1024
    return f"{count:.1f} GB"


def _tree_size(path):
    total = 0
    for base, _dirs, files in os.walk(path):
        for name in files:
            try:
                total += os.path.getsize(os.path.join(base, name))
            except OSError:
                pass
    return total


def _label(path):
    try:
        return path.resolve().relative_to(ROOT.resolve()).as_posix()
    except ValueError:
        return str(path)


def _prunable(path, protected):
    """Only rebuildable output directories may be removed recursively."""
    target = Path(path)
    if target.is_symlink() or not target.is_dir():
        return False
    resolved = target.resolve()
    root = ROOT.resolve()
    if resolved == root or resolved in root.parents:
        return False
    if resolved.parent == root and resolved.name in _SOURCE_DIRS:
        return False
    for item in protected:
        kept = Path(item).resolve()
        if kept == resolved or resolved in kept.parents:
            return False
    return True


def _stale_swaps(dump):
    """Backup directories left behind by a dump swap that was interrupted."""
    try:
        entries = list(dump.parent.glob(f".{dump.name}.old-*"))
    except OSError:
        return []
    return [entry for entry in entries if not entry.is_symlink() and entry.is_dir()]


def prune_pipeline_artifacts(logger=print):
    """Delete rebuildable pipeline output after a committed import.

    Opt-in through DB_UPDATE_PRUNE=1. Runs only after the database import was
    committed and verified, and never raises: the refresh has already
    succeeded, so a failed cleanup must stay cosmetic.
    """
    if os.environ.get("DB_UPDATE_PRUNE") != "1":
        return ()
    protected = [
        db_path(),
        _resolve(os.environ.get("DOWNLOAD_DIR") or "downloads"),
    ]
    dump = _resolve(os.environ.get("DUMP") or os.environ.get("OUT") or "haramain")
    candidates = (
        os.environ.get("WORK") or "work",
        dump,
        *_stale_swaps(dump),
    )
    removed = []
    seen = set()
    for value in candidates:
        path = _resolve(value)
        key = str(path.resolve()) if path.exists() else str(path)
        if key in seen:  # WORK and DUMP may point at the same directory
            continue
        seen.add(key)
        label = _label(path)
        try:
            if not path.exists():
                continue
            if not _prunable(path, protected):
                logger(f"[update] prune: kept {label} (not rebuildable output)")
                continue
            size = _tree_size(path)
            shutil.rmtree(path)
            removed.append(str(path))
            logger(f"[update] prune: removed {label} ({_human_size(size)})")
        except Exception as exc:  # noqa: BLE001 - never fail a successful update
            logger(f"[update] prune: could not remove {label} ({type(exc).__name__})")
    return tuple(removed)


def run_update():
    """Refresh with a cross-process lock and durable success/failure diagnostics."""
    from config import load_env

    load_env(ROOT)
    with update_lock() as lock_fd:
        started = dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")
        _write_status("running", stage="starting", started_at=started)
        try:
            _perform_update(lock_fd)
        except Exception as exc:
            # Do not persist raw exceptions (may contain remote URLs or secrets).
            _write_status(
                "failed",
                error=type(exc).__name__,
                finished_at=dt.datetime.now(dt.timezone.utc).isoformat(
                    timespec="seconds"
                ),
            )
            print(f"[update] failed: {type(exc).__name__}", flush=True)
            raise
        else:
            # Opt-in storage relief: only after the import is committed and
            # verified, so a failed refresh still keeps every artifact.
            prune_pipeline_artifacts()
            finished = dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")
            _write_status(
                "succeeded",
                stage="complete",
                finished_at=finished,
                last_success=finished,
                error=None,
            )
            print("[update] complete: database import committed", flush=True)


def main():
    """Manual refresh or a read-only JSON status check."""
    import argparse

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--status", action="store_true", help="read last refresh result without network"
    )
    args = parser.parse_args()
    if args.status:
        print(json.dumps(read_status(), indent=2, sort_keys=True))
    else:
        run_update()


if __name__ == "__main__":
    main()
