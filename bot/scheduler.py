"""Run one daily maintenance callback without blocking the bot polling loop."""
from datetime import datetime, time as wall_time, timedelta, timezone
import threading
import re


def next_daily_run(now, hhmm):
    """Return the next UTC occurrence of an HH:MM daily schedule."""
    if not re.fullmatch(r"[0-9]{2}:[0-9]{2}", hhmm):
        raise ValueError("daily update time must use HH:MM UTC")
    parsed = wall_time.fromisoformat(hhmm)
    target = now.replace(hour=parsed.hour, minute=parsed.minute,
                         second=0, microsecond=0)
    if target <= now:
        target += timedelta(days=1)
    return target


def start_daily_worker(hhmm, callback, logger=print, clock=None, sleeper=None):
    """Start a daemon worker that calls callback daily at the configured UTC time."""
    next_daily_run(datetime.now(timezone.utc), hhmm)  # validate before starting thread
    clock = clock or (lambda: datetime.now(timezone.utc))
    sleeper = sleeper or threading.Event().wait

    def run():
        while True:
            now = clock()
            delay = max(0, (next_daily_run(now, hhmm) - now).total_seconds())
            sleeper(delay)
            try:
                callback()
            except Exception as exc:
                logger(f"daily update error: {exc}")

    worker = threading.Thread(target=run, name="daily-database-update", daemon=True)
    worker.start()
    return worker
