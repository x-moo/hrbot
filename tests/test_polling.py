import unittest

from bot.app import poll_retry_delay


class PollRetryTests(unittest.TestCase):
    def test_retry_delay_grows_exponentially_and_caps(self):
        self.assertEqual([poll_retry_delay(n, 3) for n in range(1, 8)], [3, 6, 12, 24, 48, 96, 192])
        self.assertEqual(poll_retry_delay(20, 3), 300)

    def test_retry_delay_respects_configured_poll_minimum(self):
        self.assertEqual(poll_retry_delay(1, 10), 10)


if __name__ == "__main__":
    unittest.main()
