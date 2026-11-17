"""Menu caching, safe query titles and clamped paging."""
import unittest
from unittest.mock import Mock, patch
from bot import app


class UISafetyTests(unittest.TestCase):
    def test_clamped_page_back_is_previous_visible_page(self):
        db = Mock()
        db.search_page.return_value = ([], 6)
        _, markup = app._render({'kind': 'search', 'terms': ['test']}, 5, db)
        self.assertEqual(markup['inline_keyboard'][0][0]['callback_data'], 'v:1')

    def test_search_query_is_bounded_and_preserved(self):
        db = Mock()
        db.search_page.return_value = ([], 1)
        text, _ = app._render({'kind': 'search', 'terms': ['source', 'done', '%' * 4080]}, 1, db)
        self.assertLess(len(text), 1000)
        self.assertIn('source done', app.translate(text))

    def test_menu_setup_cached(self):
        app._MENU_CACHE.clear()
        try:
            with patch.object(app, 'configure_menu') as configure:
                app._configure_menu('test', chat=7, code='id')
                app._configure_menu('test', chat=7, code='id')
                self.assertEqual(configure.call_count, 1)
                app._configure_menu('test', chat=7, code='en')
                self.assertEqual(configure.call_count, 2)
        finally:
            app._MENU_CACHE.clear()
