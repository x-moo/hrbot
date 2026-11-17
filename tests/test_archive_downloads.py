"""Only archive result buttons can start bot downloads."""
import unittest
from unittest.mock import Mock, patch
from bot import app
from bot.commands import command_list


class ArchiveDownloadTests(unittest.TestCase):
    def test_free_url_command_cannot_download(self):
        with patch.object(app, 'start_leech') as start, patch.object(app, 'send'):
            app._handle_update({'message': {'chat': {'id': 7},
                               'text': '/dl https://example.org/free.mp3'}}, Mock(), 'test')
        start.assert_not_called()
        self.assertNotIn('dl', [c['command'] for c in command_list('en')])

    def test_archive_button_is_bound_to_chat_and_url_snapshot(self):
        rows = [{'url': 'https://archive.example/audio.mp3', 'fname': 'audio.mp3'}]
        buttons = app._download_rows(rows, 7)
        data = buttons[0][0]['callback_data']
        def callback(chat):
            return {'id': 'tap', 'data': data, 'message': {
                'message_id': 3, 'chat': {'id': chat, 'type': 'private'}}}
        with patch.object(app, 'start_leech', return_value=('started', True)) as start, patch.object(app, 'tg_call'), patch.object(app, 'send'):
            app._handle_callback(callback(8), Mock(), 'test')
            start.assert_not_called()
            app._handle_callback(callback(7), Mock(), 'test')
            self.assertEqual(start.call_args.args[2], rows[0]['url'])
        self.assertLessEqual(len(data.encode()), 64)
        app._DOWNLOADS[data[4:]]['expires'] = 0
        with patch.object(app, 'start_leech') as start, patch.object(app, 'tg_call'):
            app._handle_callback(callback(7), Mock(), 'test')
            start.assert_not_called()

    def test_latest_and_paged_results_have_download_buttons(self):
        row = {'url': 'https://archive.example/audio.mp3', 'fname': 'audio.mp3',
               'slug': 'audio', 'year': 2026, 'month': 10, 'day': 1,
               'kind': 'audio', 'post': 'https://archive.example/post'}
        db = Mock()
        db.latest.return_value = [row]
        db.search_page.return_value = ([row], 1)
        context = app.current_chat.set(7)
        try:
            for _, markup in [app._screen_latest(db),
                              app._render({'kind': 'search', 'terms': ['audio']}, 1, db)]:
                self.assertTrue(any(button.get('callback_data', '').startswith('get:')
                                    for line in markup['inline_keyboard'] for button in line))
        finally:
            app.current_chat.reset(context)
