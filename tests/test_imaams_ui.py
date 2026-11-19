import threading
import unittest
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import Mock, patch

from bot import app
from bot.commands import command_list
from bot.i18n import language


class ImmediateExecutor:
    def submit(self, function, *args):
        function(*args)


class ImaamsUiTests(unittest.TestCase):
    def setUp(self):
        self.catalog = Mock()
        self.catalog.files.return_value = [
            {'name': f'{i:03}.mp3', 'title': f'Surat {i}', 'track': f'{i}/114',
             'url': f'https://archive.org/download/Test/{i:03}.mp3'}
            for i in range(1, 13)
        ]
        self.catalog_patch = patch.object(app, 'imaams_catalog', self.catalog)
        self.catalog_patch.start()
        self.addCleanup(self.catalog_patch.stop)

    def callback(self, data):
        return {'id': 'cb', 'data': data,
                'message': {'message_id': 4, 'chat': {'id': 2}}}

    def test_help_mentions_imaams_without_changing_start(self):
        self.assertIn('/imaams', app.help_text())

    def test_native_menu_registers_only_imaams_once_and_start_is_unchanged(self):
        commands = [c['command'] for c in command_list('id')]
        self.assertEqual(commands.count('imaams'), 1)
        self.assertNotIn('reciter', commands)
        self.assertNotIn('imaams', str(app.menu_markup()).lower())

    def test_imaams_command_shows_paginated_reciter_list(self):
        with patch.object(app, '_push') as push:
            app._handle_update({'message': {'text': '/imaams@mybot', 'chat': {'id': 1}}}, Mock(), 'token')
        _, _, text, markup = push.call_args.args
        self.assertIn('IMAAMS', text)
        buttons = [b for row in markup['inline_keyboard'] for b in row]
        reciters = [b for b in buttons if b.get('callback_data', '').startswith('im:r:')]
        self.assertEqual(len(reciters), 5)
        self.catalog.files.assert_not_called()

    def test_reciter_selection_shows_only_one_audio_page(self):
        with patch.object(app, '_IMAAMS_EXECUTOR', ImmediateExecutor(), create=True), \
             patch.object(app, 'tg_call', return_value=True), patch.object(app, '_push') as push:
            app._handle_callback(self.callback('im:r:0:1'), Mock(), 'token')
        _, _, text, markup = push.call_args.args
        self.assertIn('Surat 1', text)
        audio = [b for row in markup['inline_keyboard'] for b in row if b.get('url')]
        self.assertEqual(len(audio), 5)
        self.assertTrue(all(b['url'].startswith('https://archive.org/download/') for b in audio))

    def test_next_page_and_back_preserves_reciter_list_page(self):
        with patch.object(app, '_IMAAMS_EXECUTOR', ImmediateExecutor(), create=True), \
             patch.object(app, 'tg_call', return_value=True), patch.object(app, '_push') as push:
            app._handle_callback(self.callback('im:r:7:2'), Mock(), 'token')
        _, _, text, markup = push.call_args.args
        self.assertIn('Surat 6', text)
        self.assertNotIn('• Surat 1\n', text)
        self.assertIn('im:rlist:2', str(markup))
        with patch.object(app, 'tg_call', return_value=True), patch.object(app, '_push') as push:
            app._handle_callback(self.callback('im:rlist:2'), Mock(), 'token')
        self.assertIn('2/', push.call_args.args[2])

    def test_slow_metadata_does_not_block_callback_or_localization(self):
        entered, release, finished = threading.Event(), threading.Event(), threading.Event()
        def fetch(_):
            entered.set()
            release.wait(3)
            return []
        self.catalog.files.side_effect = fetch
        def sent(*args, **kwargs):
            self.assertIn('Belum ada audio', args[2])
            finished.set()
        with ThreadPoolExecutor(max_workers=1) as executor:
            with patch.object(app, '_IMAAMS_EXECUTOR', executor, create=True), \
                 patch.object(app, 'tg_call', return_value=True) as api, patch.object(app, '_push', side_effect=sent):
                context = language.set('id')
                try:
                    app._handle_callback(self.callback('im:r:0:1'), Mock(), 'token')
                    self.assertTrue(entered.wait(1))
                    self.assertFalse(finished.is_set())
                    self.assertEqual(api.call_args.args[1], 'answerCallbackQuery')
                finally:
                    language.reset(context)
                    release.set()
                self.assertTrue(finished.wait(2))

    def test_optional_size_and_long_title_do_not_break_screen(self):
        self.catalog.files.return_value = [{'name': '001.mp3', 'title': '*[Title]*' * 2000,
            'size': 'unknown', 'url': 'https://archive.org/download/Test/001.mp3'}]
        with patch.object(app, '_push') as push:
            app._send_imaams('token', 2, 'im:r:0:1', edit_of=4)
        self.assertLess(len(push.call_args.args[2]), 4096)
        self.assertIn('https://archive.org/download/Test/001.mp3', str(push.call_args))
        self.assertTrue(push.call_args.kwargs['plain'])

    def test_network_error_offers_reciter_list_retry(self):
        self.catalog.files.side_effect = OSError('offline')
        with patch.object(app, '_push') as push:
            app._send_imaams('token', 2, 'im:r:0:1', edit_of=4)
        self.assertIn('im:rlist:1', str(push.call_args))

    def test_queue_capacity_is_bounded_and_slots_are_released(self):
        pending = []
        executor = Mock()
        executor.submit.side_effect = lambda function, *args: pending.append((function, args))
        with patch.object(app, '_IMAAMS_EXECUTOR', executor), \
             patch.object(app, '_IMAAMS_SLOTS', threading.BoundedSemaphore(1)), \
             patch.object(app, '_push') as push:
            app._queue_imaams('token', 2, 'im:r:0:1', 4)
            app._queue_imaams('token', 2, 'im:r:0:1', 4)
            self.assertEqual(len(pending), 1)
            self.assertIn('im:rlist:1', str(push.call_args))
            function, args = pending.pop()
            function(*args)
            app._queue_imaams('token', 2, 'im:r:0:1', 4)
            self.assertEqual(len(pending), 1)
            function, args = pending.pop()
            function(*args)

    def test_back_navigation_discards_pending_audio_page(self):
        pending = []
        executor = Mock()
        executor.submit.side_effect = lambda function, *args: pending.append((function, args))
        with patch.object(app, '_IMAAMS_EXECUTOR', executor), \
             patch.object(app, 'tg_call', return_value=True), patch.object(app, '_push') as push:
            app._handle_callback(self.callback('im:r:0:1'), Mock(), 'token')
            app._handle_callback(self.callback('im:rlist:2'), Mock(), 'token')
            self.assertEqual(push.call_count, 1)
            pending[0][0](*pending[0][1])
        self.assertEqual(push.call_count, 1)
        self.assertIn('IMAAMS', push.call_args.args[2])
        self.assertNotIn('Sheikh Abdullah Quraafi', push.call_args.args[2])

    def test_submit_failure_releases_queue_slot(self):
        executor = Mock()
        executor.submit.side_effect = RuntimeError('executor closed')
        slots = threading.BoundedSemaphore(1)
        with patch.object(app, '_IMAAMS_EXECUTOR', executor), \
             patch.object(app, '_IMAAMS_SLOTS', slots), patch.object(app, '_push') as push:
            app._queue_imaams('token', 2, 'im:r:0:1', 4)
        self.assertTrue(slots.acquire(blocking=False))
        self.assertIn('im:rlist:1', str(push.call_args))

    def test_malformed_and_unknown_callback_never_fetch_metadata(self):
        with patch.object(app, '_IMAAMS_EXECUTOR', ImmediateExecutor(), create=True), \
             patch.object(app, 'tg_call', return_value=True), patch.object(app, '_push'):
            for data in ('im:r:-1:1', 'im:r:99999:1', 'im:r:x:y', 'im:unknown', 'im:x'):
                app._handle_callback(self.callback(data), Mock(), 'token')
        self.catalog.files.assert_not_called()


if __name__ == '__main__':
    unittest.main()
