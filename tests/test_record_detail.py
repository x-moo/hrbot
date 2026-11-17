"""Archive list opens a separate detail screen and returns to the same list."""
import unittest
from unittest.mock import Mock, patch
from bot import app


class DetailTests(unittest.TestCase):
    def test_list_detail_and_back(self):
        row = {'url': 'https://media.example/test.mp3', 'fname': 'test.mp3',
               'post': 'https://archive.example/post', 'slug': 'test',
               'year': 2026, 'month': 11, 'day': 16, 'kind': 'audio'}
        db = Mock()
        db.latest.return_value = [row]
        context = app.current_chat.set(7)
        calls = []
        def api(token, method, payload, **kwargs):
            calls.append((method, payload))
            return {'message_id': 3}
        try:
            text, markup = app._screen_latest(db)
            self.assertNotIn('test.mp3', text)
            item = markup['inline_keyboard'][0][0]
            self.assertTrue(item['callback_data'].startswith('item:'))
            def tap(data):
                app._handle_callback({'id': 'cb', 'data': data, 'message': {
                    'message_id': 3, 'chat': {'id': 7}}}, db, 'test')
            with patch.object(app, 'tg_call', api), patch.object(app, 'start_leech') as start:
                app._push('test', 7, text, markup)
                tap(item['callback_data'])
                start.assert_not_called()
                detail = calls[-1][1]
                buttons = [b for r in detail['reply_markup']['inline_keyboard'] for b in r]
                self.assertEqual(next(b['url'] for b in buttons if 'url' in b), row['post'])
                self.assertTrue(any(b.get('callback_data', '').startswith('get:') for b in buttons))
                tap(next(b['callback_data'] for b in buttons if b.get('callback_data', '').startswith('list:')))
                self.assertEqual(calls[-1][1]['text'], app.translate(text))
        finally:
            app.current_chat.reset(context)
