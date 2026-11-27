"""Language selection persists and changes menu output per chat."""
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
(ROOT / '.cache').mkdir(exist_ok=True)


class LanguageTests(unittest.TestCase):
    def test_translation_protects_links_and_commands(self):
        from bot.i18n import language, translate
        context = language.set('id')
        try:
            self.assertEqual(translate('Latest [Latest.mp3](https://example/Latest.mp3) `/search`'),
                             'Terbaru [Latest.mp3](https://example/Latest.mp3) `/search`')
        finally:
            language.reset(context)

    def test_translation_keeps_spaces_before_numeric_values(self):
        from bot.i18n import language, translate
        context = language.set('id')
        try:
            self.assertEqual(translate('page 1/2'), 'halaman 1/2')
            self.assertEqual(translate('No media found for 2026-11-27.'),
                             'Tidak ada media untuk 2026-11-27.')
            self.assertEqual(translate('No data for 2026.'),
                             'Tidak ada data untuk 2026.')
            self.assertEqual(translate('100 URLs across 1 year'),
                             '100 URL dalam 1 tahun')
        finally:
            language.reset(context)

    def test_selection_survives_reopening(self):
        from bot.i18n import Preferences
        with tempfile.TemporaryDirectory(dir=ROOT / '.cache') as directory:
            path = Path(directory) / 'preferences.db'
            store = Preferences(path)
            self.assertEqual(store.get(1), 'id')
            store.set(1, 'en')
            self.assertEqual(Preferences(path).get(1), 'en')
            self.assertEqual(store.get(2), 'id')
            with self.assertRaises(ValueError):
                store.set(1, 'xx')

    def test_language_callback_updates_menu(self):
        from bot import app
        from bot.i18n import Preferences
        with tempfile.TemporaryDirectory(dir=ROOT / '.cache') as directory:
            store = Preferences(Path(directory) / 'preferences.db')
            db = unittest.mock.Mock()
            db.stats.return_value = ((1, 1, 2026, 2026), {})
            calls = []
            def call(token, method, payload, **kwargs):
                calls.append((method, payload))
                if method == 'getMyCommands':
                    from bot.commands import command_list
                    return command_list(store.get(1))
                return {'message_id': 5}
            with patch.object(app, 'preferences', store), patch.object(app, 'tg_call', call):
                app.handle_update({'callback_query': {'id': 'cb', 'data': 'lang:en',
                    'message': {'chat': {'id': 1}, 'message_id': 5}}}, db, 'test')
            self.assertEqual(store.get(1), 'en')
            screen = [payload for method, payload in calls if method == 'editMessageText'][-1]
            self.assertIn('Choose a menu', screen['text'])
            self.assertIn('🌐 Language', str(screen['reply_markup']))
            calls.clear()
            with patch.object(app, 'preferences', store), patch.object(app, 'tg_call', call):
                app.handle_update({'callback_query': {'id': 'cb2', 'data': 'lang:id',
                    'message': {'chat': {'id': 1}, 'message_id': 5}}}, db, 'test')
            screen = [payload for method, payload in calls if method == 'editMessageText'][-1]
            self.assertIn('Pilih menu', screen['text'])
            self.assertIn('🌐 Bahasa', str(screen['reply_markup']))
