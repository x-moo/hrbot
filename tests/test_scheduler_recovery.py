"""Scheduler timing, backoff and stop behavior without real waiting."""

import unittest
from datetime import datetime, timedelta, timezone

from bot.scheduler import DailyWorker, next_daily_run


class SchedulerRecoveryTests(unittest.TestCase):
    def test_injected_sleeper_advances_to_schedule_and_stop_is_clean(self):
        now = [datetime(2026, 1, 2, 2, 59, tzinfo=timezone.utc)]
        events = []
        worker = None

        def callback():
            events.append("updated")
            worker.stop()

        worker = DailyWorker(
            "03:00", callback, clock=lambda: now[0], logger=lambda _: None
        )

        def sleep(seconds):
            self.assertLessEqual(seconds, 60)
            now[0] += timedelta(seconds=seconds)
            return worker.stop_event.is_set()

        worker.run(sleeper=sleep)
        self.assertEqual(events, ["updated"])

    def test_retry_backoff_caps_and_resets_after_success(self):
        now = [datetime(2026, 1, 2, tzinfo=timezone.utc)]
        attempts = []

        def fail_then_succeed():
            attempts.append(1)
            if len(attempts) < 4:
                raise OSError("offline")

        worker = DailyWorker(
            "03:00",
            fail_then_succeed,
            startup_callback=fail_then_succeed,
            clock=lambda: now[0],
            logger=lambda _: None,
            retry_seconds=10,
            max_retry_seconds=25,
        )
        for expected in (10, 20, 25):
            worker.step()
            self.assertEqual((worker.next_run - now[0]).total_seconds(), expected)
            now[0] = worker.next_run
        worker.step()
        self.assertEqual(worker.failures, 0)
        self.assertEqual(worker.next_run, datetime(2026, 1, 2, 3, tzinfo=timezone.utc))

    def test_schedule_uses_utc_for_offset_aware_clock(self):
        local = timezone(timedelta(hours=7))
        now = datetime(2026, 1, 2, 9, 0, tzinfo=local)
        self.assertEqual(
            next_daily_run(now, "03:00"), datetime(2026, 1, 2, 3, tzinfo=timezone.utc)
        )


if __name__ == "__main__":
    unittest.main()
