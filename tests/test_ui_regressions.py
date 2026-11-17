"""Regression checks for deployment entity errors and SQLite row ordering."""
import sqlite3
import unittest
from unittest.mock import Mock, patch
from bot import app


class UIRegressions(unittest.TestCase):
    def test_year_screen_with_real_sqlite_rows(self):
        connection = sqlite3.connect(':memory:')
        connection.row_factory = sqlite3.Row
        try:
            rows = connection.execute("SELECT 'video', 2 UNION ALL SELECT 'audio', 3").fetchall()
            db = Mock()
            db.months_of.return_value = [(11, 5)]
            db.year_stats.return_value = rows
            text, _ = app._screen_year(db, 2026)
            self.assertIn('audio: 3', text)
        finally:
            connection.close()

    def test_record_name_is_plain_text_and_unchanged(self):
        name = 'SheikhBarhaji_Isha-2025-11-29.mp3'
        key = 'regression'
        app._DOWNLOADS[key] = {'chat': 7, 'url': 'https://example/audio.mp3',
            'row': {'fname': name, 'post': 'https://example/post'}, 'expires': float('inf')}
        calls = []
        def api(token, method, payload, **kwargs):
            calls.append((method, payload))
            return {'message_id': 1}
        try:
            with patch.object(app, 'tg_call', api):
                app._handle_callback({'id': 'cb', 'data': 'item:' + key,
                    'message': {'message_id': 1, 'chat': {'id': 7}}}, Mock(), 'test')
            payload = calls[-1][1]
            self.assertEqual(payload['text'], name)
            self.assertNotIn('parse_mode', payload)
        finally:
            app._DOWNLOADS.pop(key, None)
