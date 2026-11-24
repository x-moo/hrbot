import unittest
from datetime import datetime, timezone

from bot.scheduler import next_daily_run, start_daily_worker


class DailyUpdateTests(unittest.TestCase):
    def test_schedules_next_time_today_when_still_ahead(self):
        now = datetime(2026, 1, 2, 1, 0, tzinfo=timezone.utc)
        self.assertEqual(next_daily_run(now, "03:30"), datetime(2026, 1, 2, 3, 30, tzinfo=timezone.utc))

    def test_schedules_tomorrow_when_time_has_passed(self):
        now = datetime(2026, 1, 2, 4, 0, tzinfo=timezone.utc)
        self.assertEqual(next_daily_run(now, "03:30"), datetime(2026, 1, 3, 3, 30, tzinfo=timezone.utc))

    def test_rejects_time_with_seconds(self):
        with self.assertRaises(ValueError):
            next_daily_run(datetime.now(timezone.utc), "03:30:01")

    def test_rejects_non_HHMM_formats(self):
        for value in ('0300', '03:00:00', '03:00+05:00', '3:00', '25:00'):
            with self.subTest(value=value), self.assertRaises(ValueError):
                next_daily_run(datetime.now(timezone.utc), value)

    def test_worker_waits_runs_and_survives_callback_failure(self):
        from datetime import timedelta
        from bot.scheduler import DailyWorker
        now = [datetime(2026, 1, 2, 1, tzinfo=timezone.utc)]
        calls, logs = [], []
        def callback():
            calls.append('ran')
            if len(calls) == 1:
                raise RuntimeError('offline')
        worker = DailyWorker('03:00', callback, logger=logs.append,
                             clock=lambda: now[0], retry_seconds=300)
        self.assertFalse(worker.step())
        now[0] += timedelta(hours=2)
        self.assertTrue(worker.step())
        self.assertEqual(worker.failures, 1)
        self.assertEqual(worker.next_run, datetime(2026, 1, 2, 3, 5, tzinfo=timezone.utc))
        now[0] += timedelta(minutes=5)
        self.assertTrue(worker.step())
        self.assertEqual(calls, ['ran', 'ran'])
        self.assertEqual(worker.failures, 0)
        self.assertTrue(any('retry in 300s' in line for line in logs))

    def test_stopped_worker_does_not_invoke_callback(self):
        from bot.scheduler import DailyWorker
        from unittest.mock import Mock
        callback = Mock()
        worker = DailyWorker('03:00', callback, startup_callback=callback)
        worker.stop()
        self.assertFalse(worker.step())
        callback.assert_not_called()


if __name__ == "__main__":
    unittest.main()
