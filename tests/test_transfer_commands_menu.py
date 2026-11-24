"""Expose implemented download controls in Telegram's native command menu."""
import unittest
from bot.commands import command_list


class TransferCommandMenuTests(unittest.TestCase):
    def test_status_and_cancel_are_registered_in_both_languages(self):
        for language in ('en', 'id'):
            commands = {row['command']: row['description'] for row in command_list(language)}
            self.assertIn('status', commands)
            self.assertIn('cancel', commands)
            self.assertNotIn('dl', commands)


if __name__ == '__main__':
    unittest.main()
