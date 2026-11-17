"""Keep the start keyboard focused on four primary actions."""
import unittest
from bot.app import menu_markup


class StartMenuTests(unittest.TestCase):
    def test_four_primary_actions(self):
        rows = menu_markup()['inline_keyboard']
        self.assertEqual([len(row) for row in rows], [2, 2])
        self.assertEqual([button['callback_data'] for row in rows for button in row],
                         ['c:latest', 'c:search', 'c:help', 'c:language'])
