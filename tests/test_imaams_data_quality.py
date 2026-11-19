import json
import sqlite3
import tempfile
import time
from contextlib import closing
from unittest.mock import patch
import unittest
from pathlib import Path

from bot.imaams import ImaamsCatalog

ROOT = Path(__file__).resolve().parents[1]
CACHE = ROOT / '.cache'
CACHE.mkdir(exist_ok=True)


class ImaamsOrderingAndStaleCacheTests(unittest.TestCase):
    def test_sqlite_connections_are_closed_after_every_operation(self):
        connections = []
        connect = sqlite3.connect
        def tracked_connect(*args, **kwargs):
            db = connect(*args, **kwargs)
            connections.append(db)
            return db
        with tempfile.TemporaryDirectory(dir=CACHE) as tmp:
            with patch('bot.imaams.sqlite3.connect', side_effect=tracked_connect):
                catalog = ImaamsCatalog(Path(tmp) / 'cache.db', fetcher=lambda _: {'files': []})
                catalog.files('HaramainQuraafi')
                catalog.files('HaramainQuraafi')
                catalog.cached_files('HaramainQuraafi')
            try:
                for db in connections:
                    with self.assertRaises(sqlite3.ProgrammingError):
                        db.execute('SELECT 1')
            finally:
                for db in connections:
                    db.close()

    def test_malformed_optional_audio_metadata_does_not_hide_valid_tracks(self):
        payload = {'metadata': None, 'files': [
            None, 42, [], 'bad', {'name': None, 'source': 'original'},
            {'name': 42, 'source': 'original'},
            {'name': '../escape.mp3', 'source': 'original'},
            {'name': '2.mp3', 'source': 'original', 'title': ['broken'],
             'track': {}, 'length': [], 'size': {'bad': 1}},
            {'name': '10.mp3', 'source': 'original', 'title': None,
             'track': None, 'length': 'NaN', 'size': '-2'},
        ]}
        with tempfile.TemporaryDirectory(dir=CACHE) as tmp:
            catalog = ImaamsCatalog(Path(tmp) / 'cache.db', fetcher=lambda _: payload)
            files = catalog.files('HaramainQuraafi')
        self.assertEqual([f['name'] for f in files], ['2.mp3', '10.mp3'])
        self.assertEqual([f['title'] for f in files], ['2', '10'])
        self.assertTrue(all(f.get('size') is None and f.get('length') is None for f in files))

    def test_stale_cache_survives_api_failures_with_persistent_retry_cooldown(self):
        payload = {'files': [{'name': '001.mp3', 'source': 'original'}]}
        failures = [OSError('offline'), {'error': 'offline'}, {'files': None}, []]
        for failure in failures:
            calls = []
            def fetch(_):
                calls.append(1)
                if len(calls) == 1 or len(calls) >= 3:
                    return payload
                if isinstance(failure, Exception):
                    raise failure
                return failure
            with self.subTest(failure=failure), tempfile.TemporaryDirectory(dir=CACHE) as tmp:
                path = Path(tmp) / 'cache.db'
                catalog = ImaamsCatalog(path, fetcher=fetch)
                expected = catalog.files('HaramainQuraafi')
                with closing(sqlite3.connect(path)) as db, db:
                    db.execute('UPDATE metadata SET fetched=0')
                with patch('time.time', return_value=1000000):
                    self.assertEqual(catalog.files('HaramainQuraafi'), expected)
                    # A new catalogue must share the same retry cooldown.
                    catalog = ImaamsCatalog(path, fetcher=fetch)
                    self.assertEqual(catalog.files('HaramainQuraafi'), expected)
                    self.assertEqual(len(calls), 2)
                with patch('time.time', return_value=1000301):
                    self.assertEqual(catalog.files('HaramainQuraafi'), expected)
                    self.assertEqual(len(calls), 3)

    def test_cold_failures_do_not_retry_until_cooldown_expires(self):
        calls = []
        def fetch(_):
            calls.append(1)
            raise OSError('offline')
        with tempfile.TemporaryDirectory(dir=CACHE) as tmp:
            path = Path(tmp) / 'cache.db'
            with patch('time.time', return_value=1000000):
                for _ in range(3):
                    catalog = ImaamsCatalog(path, fetcher=fetch)
                    with self.assertRaises((OSError, RuntimeError)):
                        catalog.files('HaramainQuraafi')
                self.assertEqual(len(calls), 1)
            with patch('time.time', return_value=1000300):
                with self.assertRaises(OSError):
                    catalog.files('HaramainQuraafi')
                self.assertEqual(len(calls), 2)

    def test_cache_is_fresh_for_six_hours_then_refreshes(self):
        calls = []
        def fetch(_):
            calls.append(1)
            return {'files': [{'name': f'{len(calls):03}.mp3', 'source': 'original'}]}
        with tempfile.TemporaryDirectory(dir=CACHE) as tmp:
            catalog = ImaamsCatalog(Path(tmp) / 'cache.db', fetcher=fetch)
            with patch('time.time', return_value=1000000):
                first = catalog.files('HaramainQuraafi')
            with patch('time.time', return_value=1021599):
                self.assertEqual(catalog.files('HaramainQuraafi'), first)
                self.assertEqual(catalog.cached_files('HaramainQuraafi'), first)
                self.assertEqual(len(calls), 1)
            with patch('time.time', return_value=1021600):
                self.assertIsNone(catalog.cached_files('HaramainQuraafi'))
                self.assertNotEqual(catalog.files('HaramainQuraafi'), first)
                self.assertEqual(len(calls), 2)

    def test_corrupt_cached_metadata_is_replaced_not_served_as_stale(self):
        for bad in ('not json', '{"files": null}', '{"error": "old API failure"}'):
            with self.subTest(bad=bad), tempfile.TemporaryDirectory(dir=CACHE) as tmp:
                catalog = ImaamsCatalog(Path(tmp) / 'cache.db', fetcher=lambda _: {'files': []})
                with closing(sqlite3.connect(catalog.path)) as db, db:
                    db.execute('INSERT INTO metadata VALUES (?, ?, ?)',
                               ('HaramainQuraafi', bad, time.time()))
                self.assertIsNone(catalog.cached_files('HaramainQuraafi'))
                self.assertEqual(catalog.files('HaramainQuraafi'), [])

    def test_malformed_track_numbers_fall_back_to_numeric_filename(self):
        payload = {'files': [
            {'name': '10.mp3', 'source': 'original', 'track': 'not a track'},
            {'name': '3.mp3', 'source': 'original', 'track': '9' * 5000},
            {'name': '2.mp3', 'source': 'original', 'track': 0},
            {'name': '99.mp3', 'source': 'original', 'track': ' 1/114 '},
            {'name': 'unknown.mp3', 'source': 'original'},
        ]}
        with tempfile.TemporaryDirectory(dir=CACHE) as tmp:
            catalog = ImaamsCatalog(Path(tmp) / 'cache.db', fetcher=lambda _: payload)
            self.assertEqual([f['name'] for f in catalog.files('HaramainQuraafi')],
                             ['99.mp3', '2.mp3', '3.mp3', '10.mp3', 'unknown.mp3'])

    def test_tracks_are_sorted_numerically(self):
        payload = {'files': [
            {'name': '010.mp3', 'source': 'original', 'title': 'Surah 10', 'track': '10/114'},
            {'name': '002.mp3', 'source': 'original', 'title': 'Surah 2', 'track': '2/114'},
            {'name': '001.mp3', 'source': 'original', 'title': 'Surah 1', 'track': '1/114'},
        ]}
        with tempfile.TemporaryDirectory(dir=CACHE) as tmp:
            catalog = ImaamsCatalog(Path(tmp) / 'cache.db', fetcher=lambda _: payload)
            self.assertEqual([f['title'] for f in catalog.files('HaramainQuraafi')],
                             ['Surah 1', 'Surah 2', 'Surah 10'])

    def test_stale_metadata_is_used_when_archive_api_is_unavailable(self):
        payload = {'files': [
            {'name': '001.mp3', 'source': 'original', 'title': 'Surah 1', 'track': '1/114'}
        ]}
        calls = []
        def fetcher(identifier):
            calls.append(identifier)
            if len(calls) == 1:
                return payload
            raise OSError('archive unavailable')
        with tempfile.TemporaryDirectory(dir=CACHE) as tmp:
            catalog = ImaamsCatalog(Path(tmp) / 'cache.db', fetcher=fetcher)
            catalog.files('HaramainQuraafi')
            with catalog.path.open('rb'):
                pass
            with closing(sqlite3.connect(catalog.path)) as db, db:
                db.execute('UPDATE metadata SET fetched=0 WHERE identifier=?', ('HaramainQuraafi',))
            stale = catalog.files('HaramainQuraafi')
        self.assertEqual(stale[0]['title'], 'Surah 1')
        self.assertEqual(calls, ['HaramainQuraafi', 'HaramainQuraafi'])


if __name__ == '__main__':
    unittest.main()
