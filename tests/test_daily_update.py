import unittest
from datetime import datetime, timezone

from bot.scheduler import next_daily_run


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


if __name__ == "__main__":
    unittest.main()
