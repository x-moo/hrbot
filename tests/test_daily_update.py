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
        from unittest.mock import patch
        calls, logs, delays = [], [], []
        times = iter([datetime(2026, 1, 2, 1, tzinfo=timezone.utc),
                      datetime(2026, 1, 2, 4, tzinfo=timezone.utc)])
        def callback():
            calls.append('ran')
            if len(calls) == 1:
                raise RuntimeError('offline')
        def sleeper(delay):
            delays.append(delay)
        class StopLoop(BaseException):
            pass
        def clock():
            try:
                return next(times)
            except StopIteration:
                raise StopLoop
        with patch('bot.scheduler.threading.Thread') as thread:
            start_daily_worker('03:00', callback, logs.append, clock, sleeper)
        thread.return_value.start.assert_called_once()
        with self.assertRaises(StopLoop):
            thread.call_args.kwargs['target']()
        self.assertEqual(delays, [7200, 82800])
        self.assertEqual(calls, ['ran', 'ran'])
        self.assertEqual(logs, ['daily update error: offline'])


if __name__ == "__main__":
    unittest.main()
