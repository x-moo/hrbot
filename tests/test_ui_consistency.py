"""Offline checks for compact, localized Telegram screens and navigation."""
import unicodedata
import unittest
from unittest.mock import Mock, patch

from bot import app, imaams_ui
from bot.i18n import language
from bot.ui import compact_text


class UiConsistencyTests(unittest.TestCase):
    def setUp(self):
        for name in ('_DOWNLOADS', '_VIEWS', '_IMAAMS_GENERATIONS'):
            patcher = patch.object(app, name, {})
            patcher.start()
            self.addCleanup(patcher.stop)

    @staticmethod
    def buttons(markup):
        return [button for row in markup['inline_keyboard'] for button in row]

    def screen(self, command, code, db):
        preferences = Mock()
        preferences.get.return_value = code
        with patch.object(app, 'preferences', preferences), \
             patch.object(app, '_configure_menu'), \
             patch.object(app, 'tg_call', return_value={'message_id': 5}) as api:
            app.handle_update({'message': {'text': command,
                               'chat': {'id': 7, 'type': 'private'}}}, db, 'test')
        api.assert_called_once()
        self.assertEqual(api.call_args.args[1], 'sendMessage')
        return api.call_args.args[2]

    def test_menu_help_and_language_are_localized_with_four_start_buttons(self):
        expected = {
            'en': {'heading': 'Haramain media archive',
                   'menu': ['🆕 Latest', '🔎 Search', '❓ Help', '🌐 Language'],
                   'help': ['*Help*', '*Browse the archive*', '*Quran recitations*',
                            '*Downloads*', '*Preferences*', 'Back', 'Next'],
                   'language': '*Language*', 'instruction': 'Choose your language.'},
            'id': {'heading': 'Arsip media Haramain',
                   'menu': ['🆕 Terbaru', '🔎 Cari', '❓ Bantuan', '🌐 Bahasa'],
                   'help': ['*Bantuan*', '*Telusuri arsip*', '*Tilawah Al-Qur\'an*',
                            '*Unduhan*', '*Pengaturan*', 'Kembali', 'Lanjut'],
                   'language': '*Bahasa*', 'instruction': 'Pilih bahasa yang ingin digunakan.'},
        }
        for code, copy in expected.items():
            with self.subTest(language=code):
                db = Mock()
                db.stats.return_value = ((2, 2, 2025, 2026), {})
                menu = self.screen('/start', code, db)
                rows = menu['reply_markup']['inline_keyboard']
                self.assertEqual([len(row) for row in rows], [2, 2])
                self.assertEqual([button['text'] for button in self.buttons(menu['reply_markup'])],
                                 copy['menu'])
                self.assertEqual([button['callback_data']
                                  for button in self.buttons(menu['reply_markup'])],
                                 ['c:latest', 'c:search', 'c:help', 'c:language'])
                self.assertIn(copy['heading'], menu['text'])
                self.assertNotIn('imaams', str(menu['reply_markup']).lower())
                help_screen = self.screen('/help', code, db)
                for fragment in copy['help']:
                    self.assertIn(fragment, help_screen['text'])
                for command in ('/start', '/latest', '/search', '/date', '/year', '/stat',
                                '/imaams', '/status', '/cancel', '/language'):
                    self.assertIn(command, help_screen['text'])
                self.assertEqual([button['callback_data']
                                  for button in self.buttons(help_screen['reply_markup'])],
                                 ['c:menu'])
                language_screen = self.screen('/language', code, db)
                self.assertIn(copy['language'], language_screen['text'])
                self.assertIn(copy['instruction'], language_screen['text'])
                choices = {button['callback_data']: button['text']
                           for button in self.buttons(language_screen['reply_markup'])}
                self.assertEqual(choices['lang:en'], '🇬🇧 English' + (' ✓' if code == 'en' else ''))
                self.assertEqual(choices['lang:id'], '🇮🇩 Indonesia' + (' ✓' if code == 'id' else ''))
                self.assertEqual(choices['c:menu'], '🏠 Menu')

    def test_compact_text_sanitizes_controls_and_bounds_utf16_without_broken_emoji(self):
        values = ['😀' * 100, 'x' * 58 + '😀y', '😀' * 29 + 'abc',
                  ('Latest\x00\x07\x1b\tMay\r\nDownload 😀' * 20)]
        for value in values:
            with self.subTest(value=value[:20]):
                label = compact_text(value)
                self.assertLessEqual(len(label.encode('utf-16-le')) // 2, 60)
                self.assertTrue(label.endswith('…'))
                self.assertNotIn('\ufffd', label)
                self.assertFalse(any(unicodedata.category(char) == 'Cc' for char in label))
                self.assertEqual(label, ' '.join(label.split()))
        self.assertEqual(compact_text('Latest\x00\tMay\r\nDownload'), 'Latest May Download')
        self.assertEqual(compact_text('  \x00\r\n\t', fallback='Recording'), 'Recording')
        self.assertEqual(compact_text(None, fallback='Recording'), 'Recording')
        self.assertEqual(compact_text('الشيخ محمد 😀'), 'الشيخ محمد 😀')
        self.assertEqual(compact_text('😀' * 30), '😀' * 30)

    def test_literal_filenames_reciters_and_url_labels_are_not_translated(self):
        for code, expected_latest in (('en', '🆕 Latest'), ('id', '🆕 Terbaru')):
            with self.subTest(language=code):
                context = language.set(code)
                try:
                    markup = {'inline_keyboard': [
                        [{'text': 'Latest May Download.mp3', 'callback_data': 'item:literal'}],
                        [{'text': 'Latest May Download', 'callback_data': 'im:r:0:1'}],
                        [{'text': word, 'url': f'https://example.invalid/{word}'}
                         for word in ('Latest', 'May', 'Download')],
                        [{'text': '🆕 Latest', 'callback_data': 'c:latest'}],
                    ]}
                    with patch.object(app, 'tg_call', return_value={'message_id': 5}) as api:
                        app._push('test', 7, 'Choose a recording:', markup)
                    buttons = self.buttons(api.call_args.args[2]['reply_markup'])
                    self.assertEqual([button['text'] for button in buttons],
                                     ['Latest May Download.mp3', 'Latest May Download',
                                      'Latest', 'May', 'Download', expected_latest])
                    self.assertEqual([button['url'] for button in buttons if button.get('url')],
                                     [f'https://example.invalid/{word}'
                                      for word in ('Latest', 'May', 'Download')])
                    [[filename]] = app._download_rows([
                        {'fname': 'Latest May Download.mp3',
                         'url': 'https://example.invalid/Latest.mp3'}], 7)
                    self.assertEqual(filename['text'], 'Latest May Download.mp3')
                finally:
                    language.reset(context)

    def test_imaams_navigation_and_retry_localize_despite_reciter_callback_prefix(self):
        catalog = Mock()
        catalog.files.return_value = [
            {'name': f'{index}.mp3', 'title': 'Latest May Download 😀',
             'url': f'https://example.invalid/{index}.mp3'} for index in range(12)
        ]
        reciters = [dict(reciter) for reciter in imaams_ui.RECITERS]
        reciters[7]['name'] = 'Latest May Download'
        for code, back, next_, retry, reciters_label in (
                ('en', '⬅️ Back', 'Next ➡️', '🔄 Try again', '⬅️ Reciters'),
                ('id', '⬅️ Kembali', 'Lanjut ➡️', '🔄 Coba lagi', '⬅️ Daftar imam')):
            with self.subTest(language=code):
                context = language.set(code)
                try:
                    with patch.object(app, 'imaams_catalog', catalog), \
                         patch.object(imaams_ui, 'RECITERS', reciters), \
                         patch.object(app, 'tg_call', return_value={'message_id': 5}) as api:
                        app._send_imaams('test', 7, 'im:r:7:2', edit_of=5)
                        payload = api.call_args.args[2]
                        buttons = self.buttons(payload['reply_markup'])
                        labels = {button['callback_data']: button['text']
                                  for button in buttons if button.get('callback_data')}
                        self.assertEqual(labels['im:r:7:1'], back)
                        self.assertEqual(labels['im:r:7:3'], next_)
                        self.assertEqual(labels['im:rlist:2'], reciters_label)
                        self.assertEqual(labels['c:menu'], '🏠 Menu')
                        self.assertEqual([button['text'] for button in buttons if button.get('url')],
                                         ['Latest May Download 😀'] * 5)
                        self.assertIn('Latest May Download', payload['text'])
                        app._send_imaams('test', 7, 'im:rlist:2', edit_of=5)
                        labels = {button['callback_data']: button['text'] for button in
                                  self.buttons(api.call_args.args[2]['reply_markup'])}
                        self.assertEqual(labels['im:r:7:1'], 'Latest May Download')
                        for busy in (False, True):
                            text, markup = imaams_ui.unavailable('im:r:7:2', busy=busy)
                            app._push('test', 7, text, markup, edit_of=5, plain=True)
                            rows = api.call_args.args[2]['reply_markup']['inline_keyboard']
                            self.assertEqual(rows[0], [{'text': retry, 'callback_data': 'im:r:7:2'}])
                            self.assertEqual(rows[-1], [
                                {'text': reciters_label, 'callback_data': 'im:rlist:2'},
                                {'text': '🏠 Menu', 'callback_data': 'c:menu'},
                            ])
                finally:
                    language.reset(context)

    def test_empty_search_results_use_push_and_offer_search_again_and_home(self):
        for code, message, retry in (
                ('en', 'No results. Try other keywords.', '🔎 Search again'),
                ('id', 'Tidak ada hasil. Coba kata kunci lain.', '🔎 Cari lagi')):
            with self.subTest(language=code):
                db = Mock()
                db.search_page.return_value = ([], 0)
                with patch.object(app, '_push', wraps=app._push) as push, \
                     patch.object(app, 'send') as send:
                    payload = self.screen('/search Latest May Download', code, db)
                push.assert_called_once()
                self.assertEqual(push.call_args.args[:3],
                                 ('test', 7, 'No results. Try other keywords.'))
                send.assert_not_called()
                db.search_page.assert_called_once_with(['latest', 'may', 'download'])
                self.assertEqual(payload['text'], message)
                self.assertEqual(payload['reply_markup']['inline_keyboard'], [[
                    {'text': retry, 'callback_data': 'c:search'},
                    {'text': '🏠 Menu', 'callback_data': 'c:menu'},
                ]])

    def test_date_rejects_invalid_format_and_calendar_before_database_access(self):
        invalid = [
            '2026-11-16suffix', '2026-11-160', '2026-11-16 extra',
            '2026-11-16T00:00:00', '2026-1-16', '2026-11-6', '2026/11/16',
            '26-11-16', '2026-13', '2026-00', '2026-02-29', '2024-02-30',
            '1900-02-29', '2026-04-31', '2026-11-00', '0000-01-01',
            '2026-00-01', '2026-13-01', '2026-11-32', '٢٠٢٦-١١-١٦',
        ]
        for code, guidance in (('en', 'Please enter a valid date.'),
                               ('id', 'Masukkan tanggal yang valid.')):
            for value in invalid:
                with self.subTest(language=code, value=value):
                    db = Mock()
                    payload = self.screen('/date ' + value, code, db)
                    self.assertEqual(db.mock_calls, [])
                    self.assertIn(guidance, payload['text'])
                    self.assertIn('`/date YYYY-MM-DD`', payload['text'])
                    self.assertIn('`/date YYYY-MM`', payload['text'])
                    self.assertEqual([button['callback_data']
                                      for button in self.buttons(payload['reply_markup'])],
                                     ['c:years', 'c:menu'])

    def test_empty_valid_date_accepts_month_and_leap_day_with_date_year_home(self):
        for code, message, date_label, year_label in (
                ('en', 'No media found for ', '📅 By date', '🗓 Year'),
                ('id', 'Tidak ada media untuk ', '📅 Berdasarkan tanggal', '🗓 Tahun')):
            for value, date_args in (('2026-11', (2026, 11, None)),
                                     ('2026-11-16', (2026, 11, 16)),
                                     ('2024-02-29', (2024, 2, 29)),
                                     ('2000-02-29', (2000, 2, 29))):
                with self.subTest(language=code, value=value):
                    db = Mock()
                    db.by_date_page.return_value = ([], 0)
                    with patch.object(app, '_send_view') as view:
                        payload = self.screen('/date ' + value, code, db)
                    db.by_date_page.assert_called_once_with(*date_args)
                    view.assert_not_called()
                    self.assertEqual(payload['text'], message + value + '.')
                    self.assertEqual(payload['reply_markup']['inline_keyboard'], [
                        [{'text': date_label, 'callback_data': 'c:date'},
                         {'text': year_label, 'callback_data': 'c:years'}],
                        [{'text': '🏠 Menu', 'callback_data': 'c:menu'}],
                    ])


if __name__ == '__main__':
    unittest.main()
