import os
import tempfile
import threading
import unicodedata
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

from bot import app
from bot.commands import command_list
from bot.i18n import language
from leech import cli, downloader


class ImmediateExecutor:
    def submit(self, function, *args):
        function(*args)


class ImaamsUiTests(unittest.TestCase):
    def setUp(self):
        context = language.set('en')
        self.addCleanup(language.reset, context)
        for name in ('_DOWNLOADS', '_IMAAMS_GENERATIONS'):
            patcher = patch.object(app, name, {})
            patcher.start()
            self.addCleanup(patcher.stop)
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
        rows = app.menu_markup()['inline_keyboard']
        self.assertEqual([len(row) for row in rows], [2, 2])
        self.assertEqual([[button['callback_data'] for button in row] for row in rows],
                         [['c:latest', 'c:search'], ['c:help', 'c:language']])
        self.assertNotIn('imaams', str(rows).lower())

    def test_imaams_command_shows_paginated_reciter_list(self):
        with patch.object(app, '_push') as push:
            app._handle_update({'message': {'text': '/imaams@mybot', 'chat': {'id': 1}}}, Mock(), 'token')
        _, _, text, markup = push.call_args.args
        self.assertIn('IMAAMS', text)
        buttons = [b for row in markup['inline_keyboard'] for b in row]
        reciters = [b for b in buttons if b.get('callback_data', '').startswith('im:r:')]
        self.assertEqual(len(reciters), 5)
        self.assertEqual(markup['inline_keyboard'][-1][-1]['callback_data'], 'c:menu')
        self.catalog.files.assert_not_called()

    def test_reciter_selection_shows_only_one_audio_page(self):
        with patch.object(app, '_IMAAMS_EXECUTOR', ImmediateExecutor(), create=True), \
             patch.object(app, 'tg_call', return_value=True), patch.object(app, '_push') as push:
            app._handle_callback(self.callback('im:r:0:1'), Mock(), 'token')
        _, _, text, markup = push.call_args.args
        self.assertEqual(text,
                         f"🎙 {app.imaams_ui.RECITERS[0]['name']}\n"
                         '12 audio tracks · Page 1/3\n\nChoose a recording:')
        self.assertNotIn('Surat', text)
        audio = [b for row in markup['inline_keyboard'] for b in row if b.get('url')]
        self.assertEqual([button['text'] for button in audio],
                         [f'Surat {index}' for index in range(1, 6)])
        self.assertTrue(all(b['url'].startswith('https://archive.org/download/') for b in audio))
        self.assertEqual(markup['inline_keyboard'][-1][-1]['callback_data'], 'c:menu')

    def test_track_download_queues_the_displayed_url_to_the_same_chat(self):
        with patch.object(app, '_DOWNLOADS', {}), \
             patch.object(app, '_IMAAMS_EXECUTOR', ImmediateExecutor()), \
             patch.object(app, 'tg_call'), patch.object(app, '_push') as push, \
             patch.object(app, 'send'), \
             patch.object(app, 'start_leech', return_value=('started', True)) as start:
            app._handle_callback(self.callback('im:r:0:2'), Mock(), 'token')
            rows = push.call_args.args[3]['inline_keyboard']
            track_rows = [row for row in rows if row[0].get('url')]
            self.assertEqual(len(track_rows), 5)
            for row in track_rows:
                self.assertEqual(len(row), 2)
                self.assertTrue(row[1]['callback_data'].startswith('get:'))
                self.assertLessEqual(len(row[1]['callback_data'].encode()), 64)
            # A changed catalog must not change what a displayed button downloads.
            self.catalog.files.return_value = []
            self.catalog.files.reset_mock()
            app._handle_callback(self.callback(track_rows[0][1]['callback_data']), Mock(), 'token')
            self.catalog.files.assert_not_called()
            self.assertEqual(start.call_args.args[:3],
                             ('token', 2, 'https://archive.org/download/Test/006.mp3'))
            self.assertFalse(start.call_args.kwargs['to_self'])

    def test_track_download_rejects_other_chats_and_expired_buttons(self):
        with patch.object(app, '_DOWNLOADS', {}), patch.object(app, '_push') as push, \
             patch.object(app, 'tg_call') as api, patch.object(app, 'start_leech') as start:
            app._send_imaams('token', 2, 'im:r:0:1', edit_of=4)
            data = push.call_args.args[3]['inline_keyboard'][0][1]['callback_data']
            other_chat = self.callback(data)
            other_chat['message']['chat']['id'] = 3
            app._handle_callback(other_chat, Mock(), 'token')
            self.assertTrue(api.call_args.args[2]['show_alert'])
            app._DOWNLOADS[data[4:]]['expires'] = 0
            app._handle_callback(self.callback(data), Mock(), 'token')
            self.assertTrue(api.call_args.args[2]['show_alert'])
            start.assert_not_called()

    def test_track_download_uploads_audio_through_shared_leech_worker(self):
        url = 'https://archive.org/download/Test/001.mp3'
        self.catalog.files.return_value = [{'name': '001.mp3', 'url': url}]
        observed = []
        client = Mock(start=AsyncMock(), stop=AsyncMock(),
                      send_audio=AsyncMock(return_value=SimpleNamespace(id=42)))

        def download(source, out_dir, name, *args):
            observed.append((source, downloader._UNTRUSTED.get()))
            path = Path(out_dir) / name
            path.write_bytes(b'fixture audio')
            return [str(path)]

        cache = app.HERE / '.cache'
        cache.mkdir(exist_ok=True)
        with tempfile.TemporaryDirectory(dir=cache) as temp, patch.object(app, 'HERE', Path(temp)), \
             patch.object(app, '_DOWNLOADS', {}), patch.object(app, '_DL_POOL', {}), \
             patch.object(app, '_LEECH_EXECUTOR', ImmediateExecutor()), \
             patch.object(app, '_push') as push, patch.object(app, 'send'), \
             patch.object(app, 'send_long'), patch.object(app, 'tg_call', return_value={'message_id': 5}), \
             patch.dict(os.environ, {'TELEGRAM_API': '1', 'TELEGRAM_HASH': 'test', 'BOT_TOKEN': 'test',
                                     'USER_SESSION_STRING': '', 'KEEP_FILES': '0', 'LEECH_SPLIT_SIZE': ''}), \
             patch.object(cli, 'Client', return_value=client), patch.object(cli, 'download', side_effect=download):
            app._send_imaams('token', 2, 'im:r:0:1', edit_of=4)
            data = push.call_args.args[3]['inline_keyboard'][0][1]['callback_data']
            app._handle_callback(self.callback(data), Mock(), 'token')
            self.assertEqual(observed, [(url, True)])
            client.send_audio.assert_awaited_once()
            chat, path = client.send_audio.call_args.args
            self.assertEqual(chat, 2)
            self.assertFalse(Path(path).exists())
            self.assertFalse(app._DL_POOL)

    def test_next_page_and_back_preserves_reciter_list_page(self):
        with patch.object(app, '_IMAAMS_EXECUTOR', ImmediateExecutor(), create=True), \
             patch.object(app, 'tg_call', return_value=True), patch.object(app, '_push') as push:
            app._handle_callback(self.callback('im:r:7:2'), Mock(), 'token')
        _, _, text, markup = push.call_args.args
        self.assertIn('12 audio tracks · Page 2/3', text)
        self.assertNotIn('Surat', text)
        audio = [button for row in markup['inline_keyboard'] for button in row
                 if button.get('url')]
        self.assertEqual([button['text'] for button in audio],
                         [f'Surat {index}' for index in range(6, 11)])
        self.assertIn('im:rlist:2', str(markup))
        self.assertEqual(markup['inline_keyboard'][-1][-1]['callback_data'], 'c:menu')
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
        label = push.call_args.args[3]['inline_keyboard'][0][0]['text']
        self.assertLessEqual(len(label.encode('utf-16-le')) // 2, 60)
        self.assertNotIn(' MB', label)

    def test_title_and_size_labels_are_single_line_and_utf16_bounded(self):
        title = '\x00Latest\tMay\nDownload\r😀' * 100
        self.catalog.files.return_value = [{'name': '001.mp3', 'title': title,
            'size': 2 * 1024 * 1024, 'url': 'https://archive.org/download/Test/001.mp3'}]
        with patch.object(app, '_push') as push:
            app._send_imaams('token', 2, 'im:r:0:1', edit_of=4)
        text, markup = push.call_args.args[2:4]
        label = markup['inline_keyboard'][0][0]['text']
        self.assertTrue(label.startswith('Latest May Download 😀'))
        self.assertTrue(label.endswith(' · 2.0 MB'))
        self.assertFalse(any(unicodedata.category(char) == 'Cc' for char in label))
        self.assertLessEqual(len(label.encode('utf-16-le')) // 2, 60)
        self.assertNotIn('Latest', text)

    def test_empty_reciter_still_has_reciters_return_and_home(self):
        self.catalog.files.return_value = []
        with patch.object(app, '_push') as push:
            app._send_imaams('token', 2, 'im:r:7:2', edit_of=4)
        text, markup = push.call_args.args[2:4]
        self.assertIn('0 audio tracks · Page 1/1', text)
        self.assertIn('No audio is available', text)
        self.assertFalse(any(button.get('url') for row in markup['inline_keyboard']
                             for button in row))
        self.assertEqual([button['callback_data'] for button in markup['inline_keyboard'][-1]],
                         ['im:rlist:2', 'c:menu'])

    def test_network_error_retries_original_audio_page_and_reciter_page(self):
        self.catalog.files.side_effect = OSError('offline')
        with patch.object(app, '_push') as push, \
             patch.object(app.imaams_ui, 'unavailable', wraps=app.imaams_ui.unavailable) as unavailable:
            app._send_imaams('token', 2, 'im:r:7:2', edit_of=4)
        unavailable.assert_called_once_with('im:r:7:2')
        self.assertIn('temporarily unavailable', push.call_args.args[2])
        self.assertEqual([[button['callback_data'] for button in row]
                          for row in push.call_args.args[3]['inline_keyboard']],
                         [['im:r:7:2'], ['im:rlist:2', 'c:menu']])

    def test_queue_capacity_is_bounded_and_slots_are_released(self):
        pending = []
        executor = Mock()
        executor.submit.side_effect = lambda function, *args: pending.append((function, args))
        with patch.object(app, '_IMAAMS_EXECUTOR', executor), \
             patch.object(app, '_IMAAMS_SLOTS', threading.BoundedSemaphore(1)), \
             patch.object(app, '_push') as push, \
             patch.object(app.imaams_ui, 'unavailable', wraps=app.imaams_ui.unavailable) as unavailable:
            app._queue_imaams('token', 2, 'im:r:7:2', 4)
            app._queue_imaams('token', 2, 'im:r:7:2', 4)
            self.assertEqual(len(pending), 1)
            unavailable.assert_called_once_with('im:r:7:2', busy=True)
            self.assertIn('busy', push.call_args.args[2])
            self.assertEqual([[button['callback_data'] for button in row]
                              for row in push.call_args.args[3]['inline_keyboard']],
                             [['im:r:7:2'], ['im:rlist:2', 'c:menu']])
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

    def test_home_navigation_discards_pending_audio_page(self):
        pending = []
        executor = Mock()
        executor.submit.side_effect = lambda function, *args: pending.append((function, args))
        db = Mock()
        db.stats.return_value = ((1, 1, 2026, 2026), {})
        with patch.object(app, '_IMAAMS_EXECUTOR', executor), \
             patch.object(app, 'tg_call', return_value=True), patch.object(app, '_push') as push:
            app._handle_callback(self.callback('im:r:7:2'), db, 'token')
            self.assertEqual(len(pending), 1)
            app._handle_callback(self.callback('c:menu'), db, 'token')
            self.assertEqual(push.call_count, 1)
            self.assertIn('Haramain media archive', push.call_args.args[2])
            self.assertEqual(push.call_args.kwargs['edit_of'], 4)
            pending[0][0](*pending[0][1])
            self.assertEqual(push.call_count, 1)
        self.catalog.files.assert_not_called()

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
