"""Stoppable UTC maintenance worker with deterministic retry scheduling."""

import math
import re
import threading
from datetime import datetime, timedelta, timezone
from datetime import time as wall_time


def utc_now():
    return datetime.now(timezone.utc)


def as_utc(value):
    """Legacy import timestamps without offsets are interpreted as UTC."""
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def next_daily_run(now, hhmm):
    """Return the next UTC HH:MM; preserve naive input for legacy callers."""
    if not isinstance(hhmm, str) or not re.fullmatch(r"[0-9]{2}:[0-9]{2}", hhmm):
        raise ValueError("daily update time must use HH:MM UTC")
    parsed = wall_time.fromisoformat(hhmm)
    normalized = as_utc(now) if now.tzinfo is not None else now
    target = normalized.replace(
        hour=parsed.hour, minute=parsed.minute, second=0, microsecond=0
    )
    return target + timedelta(days=1) if target <= normalized else target


class DailyWorker:
    """A single callback at a time; failures retry with capped backoff.

    step() is deliberately independent of threads/sleep so missed schedules,
    clock changes and retry behavior can be tested without waiting a day.
    """

    def __init__(
        self,
        hhmm,
        callback,
        *,
        startup_callback=None,
        logger=print,
        clock=None,
        retry_seconds=300,
        max_retry_seconds=3600,
    ):
        if not math.isfinite(retry_seconds) or retry_seconds <= 0:
            raise ValueError("retry_seconds must be finite and positive")
        self.hhmm = hhmm
        self.callback = callback
        self.startup_callback = startup_callback
        self.logger = logger
        self.clock = clock or utc_now
        self.retry_seconds = retry_seconds
        self.max_retry_seconds = max(retry_seconds, max_retry_seconds)
        self.stop_event = threading.Event()
        self.failures = 0
        self._startup_pending = startup_callback is not None
        now = as_utc(self.clock())
        scheduled = next_daily_run(now, hhmm)  # validate before launching
        self.next_run = now if self._startup_pending else scheduled
        self.thread = None

    def step(self):
        """Attempt one due operation, scheduling either a retry or next day."""
        if self.stop_event.is_set() or as_utc(self.clock()) < self.next_run:
            return False
        action = self.startup_callback if self._startup_pending else self.callback
        try:
            action()
        except Exception as exc:
            self.failures += 1
            delay = min(
                self.max_retry_seconds,
                self.retry_seconds * 2 ** min(self.failures - 1, 10),
            )
            self.next_run = as_utc(self.clock()) + timedelta(seconds=delay)
            self.logger(
                f"daily update error: {exc}; retry in {delay:g}s "
                f"at {self.next_run:%Y-%m-%d %H:%M:%S} UTC"
            )
        else:
            self._startup_pending = False
            self.failures = 0
            self.next_run = next_daily_run(as_utc(self.clock()), self.hhmm)
            self.logger(f"next database update: {self.next_run:%Y-%m-%d %H:%M:%S} UTC")
        return True

    def run(self, sleeper=None):
        wait = sleeper or self.stop_event.wait
        while not self.stop_event.is_set():
            self.step()
            delay = max(0, (self.next_run - as_utc(self.clock())).total_seconds())
            # Re-evaluate wall time after clock adjustments instead of sleeping 24h.
            if wait(min(60, delay)):
                break

    def start(self, sleeper=None):
        self.thread = threading.Thread(
            target=lambda: self.run(sleeper), name="daily-database-update", daemon=True
        )
        self.thread.start()
        return self

    def stop(self, timeout=5):
        """Stop waiting; an in-flight update finishes under its own deadline."""
        self.stop_event.set()
        if self.thread is not None and self.thread is not threading.current_thread():
            self.thread.join(timeout)


def start_daily_worker(
    hhmm,
    callback,
    logger=print,
    clock=None,
    sleeper=None,
    startup_callback=None,
    retry_seconds=300,
):
    return DailyWorker(
        hhmm,
        callback,
        startup_callback=startup_callback,
        logger=logger,
        clock=clock,
        retry_seconds=retry_seconds,
    ).start(sleeper)
