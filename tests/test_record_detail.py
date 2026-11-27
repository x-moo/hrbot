"""Archive detail and YouTube screens preserve the original result list."""
import unittest
from unittest.mock import Mock, patch

from bot import app
from bot.i18n import language


class DetailTests(unittest.TestCase):
    def setUp(self):
        context = language.set('en')
        self.addCleanup(language.reset, context)
        context = app.current_chat.set(7)
        self.addCleanup(app.current_chat.reset, context)
        for name in ('_DOWNLOADS', '_VIEWS'):
            patcher = patch.object(app, name, {})
            patcher.start()
            self.addCleanup(patcher.stop)
        self.row = {'url': 'https://media.example/test.mp3', 'fname': 'test.mp3',
                    'post': 'https://archive.example/post', 'slug': 'test',
                    'year': 2026, 'month': 11, 'day': 16, 'kind': 'audio'}
        self.db = Mock()
        self.db.latest.return_value = [self.row]
        self.calls = []

        def api(token, method, payload, **kwargs):
            self.calls.append((method, payload))
            return {'message_id': 3}

        self.api = api

    def tap(self, data):
        app._handle_callback({'id': 'cb', 'data': data, 'message': {
            'message_id': 3, 'chat': {'id': 7}}}, self.db, 'test')

    def latest_screen(self):
        return next(payload for method, payload in reversed(self.calls)
                    if method in ('sendMessage', 'editMessageText'))

    def test_list_detail_and_back(self):
        video = 'https://www.youtube.com/watch?v=abcdefghijk'
        self.db.videos_for.return_value = [video]
        text, markup = app._screen_latest(self.db)
        self.assertNotIn('test.mp3', text)
        item = markup['inline_keyboard'][0][0]
        self.assertTrue(item['callback_data'].startswith('item:'))
        key = item['callback_data'].split(':', 1)[1]
        with patch.object(app, 'tg_call', self.api), patch.object(app, 'start_leech') as start:
            app._push('test', 7, text, markup)
            self.tap(item['callback_data'])
            start.assert_not_called()
            detail = self.latest_screen()
            rows = detail['reply_markup']['inline_keyboard']
            self.assertEqual(rows[0], [{'text': '⬇️ Download', 'callback_data': 'get:' + key}])
            url_rows = [row for row in rows if any(button.get('url') for button in row)]
            self.assertEqual([[button['url'] for button in row] for row in url_rows],
                             [[self.row['post'], video]])
            self.assertTrue(all(len(row) <= 2 for row in url_rows))
            self.assertEqual([button['callback_data'] for button in rows[-1]],
                             ['list:' + key, 'c:menu'])
            self.assertEqual(detail['text'], self.row['fname'])
            self.assertNotIn('parse_mode', detail)
            self.assertEqual(detail['message_id'], 3)
            self.tap(rows[-1][0]['callback_data'])
            self.assertEqual(self.latest_screen()['text'], text)
            self.assertEqual(self.latest_screen()['reply_markup'], markup)

    def test_full_youtube_list_returns_through_detail_to_original_search_page(self):
        videos = [f'https://www.youtube.com/watch?v=video{index}' for index in range(4)]
        self.db.videos_for.return_value = videos
        page_rows = [self.row] + [
            {**self.row, 'fname': f'other-{index}.mp3',
             'url': f'https://media.example/other-{index}.mp3'}
            for index in range(1, 5)
        ]
        self.db.search_page.return_value = (page_rows, 12)
        spec = {'kind': 'search', 'terms': ['test']}
        with patch.object(app, 'tg_call', self.api), patch.object(app, 'start_leech') as start:
            app._send_view('test', 7, spec, 2, self.db)
            original = self.latest_screen()
            self.assertIn('page 2/3', original['text'])
            item = original['reply_markup']['inline_keyboard'][1][0]
            key = item['callback_data'].split(':', 1)[1]
            original_parent = app._DOWNLOADS[key]['parent']
            self.tap(item['callback_data'])
            detail = self.latest_screen()
            rows = detail['reply_markup']['inline_keyboard']
            buttons = [button for row in rows for button in row]
            self.assertEqual([button['url'] for button in buttons
                              if button.get('url') in videos], videos[:2])
            self.assertTrue(all(len(row) <= 2 for row in rows if row[0].get('url')))
            self.assertEqual([button['callback_data'] for button in rows[0]], ['get:' + key])
            more = next(button for button in buttons
                        if button.get('callback_data', '').startswith('yt:'))
            self.tap(more['callback_data'])
            full_list = self.latest_screen()
            full_rows = full_list['reply_markup']['inline_keyboard']
            self.assertEqual([button['url'] for row in full_rows for button in row
                              if button.get('url')], videos)
            self.assertEqual([button['callback_data'] for button in full_rows[-1]],
                             ['detail:' + key, 'c:menu'])
            self.assertEqual(full_list['message_id'], 3)
            self.assertEqual(app._DOWNLOADS[key]['parent'], original_parent)
            self.tap(full_rows[-1][0]['callback_data'])
            restored_detail = self.latest_screen()
            self.assertEqual(restored_detail, detail)
            self.assertEqual(app._DOWNLOADS[key]['parent'], original_parent)
            self.tap(restored_detail['reply_markup']['inline_keyboard'][-1][0]['callback_data'])
            restored_list = self.latest_screen()
            self.assertEqual(restored_list['text'], original['text'])
            self.assertEqual(restored_list['reply_markup'], original['reply_markup'])
            self.assertEqual(restored_list['message_id'], 3)
            self.assertEqual(app._recall(7, 3), spec)
            self.assertEqual(app._DOWNLOADS[key]['parent'], original_parent)
            self.db.search_page.assert_called_once_with(['test'], 2)
            self.db.latest.assert_not_called()
            start.assert_not_called()
        self.assertEqual([method for method, _ in self.calls
                          if method in ('sendMessage', 'editMessageText')],
                         ['sendMessage'] + ['editMessageText'] * 4)
