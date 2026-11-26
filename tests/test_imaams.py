import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from bot.imaams import ImaamsCatalog, RECITERS, archive_download_url, _fetch_metadata

ROOT = Path(__file__).resolve().parents[1]
CACHE = ROOT / '.cache'
CACHE.mkdir(exist_ok=True)


class ImaamsCatalogTests(unittest.TestCase):
    def test_curated_catalog_has_unique_archive_ids(self):
        # Homepage IMAAMS QU'RAN RECITATIONS: 39 links, 38 unique items.
        # HaramainDossary is linked twice; distinct legacy items stay distinct.
        self.assertEqual(len(RECITERS), 38)
        self.assertEqual([r['name'] for r in RECITERS
                          if r['identifier'] in ('HaramainMaher', 'HaramainBaUthmaan')],
                         ['Sheikh Maher Al Muayqali', 'Sheikh Salaah Ba Uthmaan'])
        self.assertEqual({r['identifier'] for r in RECITERS}, set('''
            HaramainQuraafi HaramainTurki HaramainBurhaji HaramainBaUthman
            HaramainDossary HaramainMaahir HaramainBuayjaan HaramainHameed
            HaramainBaleelah HaramainThubaiti HaramainQasim HaramainSudais
            HaramainHanif HaramainJuhany HaramainKhayaaat HaramainKhulayfee
            HaramainKalbani HaramainHudhayfeee HaramainHudhayfee HaramainAliJaber
            HaramainGhazzawi HaramainSheikh HaramainAkhdhar HaramainHafiz
            HaramainGhamdi HaramainMuhanna HaramainMaher HaramainKQari
            HaramainAyub HaramainMQari HaramainSubbayil HaramainGhamdii
            HaramainBudair HaramainBaUthmaan HaramainTaleb HaramainHumaid
            HaramainShuraim HaramainKhayaat
        '''.split()))
        ids = [entry['identifier'] for entry in RECITERS]
        self.assertEqual(len(ids), len(set(ids)))
        self.assertTrue(all(entry['url'] == f"https://archive.org/details/{entry['identifier']}"
                            for entry in RECITERS))

    def test_metadata_download_reads_a_bounded_response_and_closes_it(self):
        import io
        class Response(io.BytesIO):
            def read(self, size=-1):
                self.read_size = size
                return super().read(size)
        response = Response(b'{"files": []}')
        with patch('bot.imaams.urllib.request.urlopen', return_value=response) as opener:
            self.assertEqual(_fetch_metadata('HaramainQuraafi'), {'files': []})
        self.assertGreater(response.read_size, 0)
        self.assertLessEqual(response.read_size, 8 * 1024 * 1024 + 1)
        self.assertTrue(response.closed)
        self.assertEqual(opener.call_args.kwargs['timeout'], 30)
        oversized = Response(b' ' * response.read_size)
        with patch('bot.imaams.urllib.request.urlopen', return_value=oversized):
            with self.assertRaisesRegex(ValueError, 'too large'):
                _fetch_metadata('HaramainQuraafi')
        self.assertTrue(oversized.closed)

    def test_metadata_returns_only_original_audio_and_caches_results(self):
        payload = {'metadata': {'title': 'Reciter item'}, 'files': [
            {'name': '002.mp3', 'source': 'original', 'title': 'Surat Baqara',
             'track': '2/114', 'size': '2048', 'length': '32.5'},
            {'name': '002.png', 'source': 'derivative', 'title': 'cover'},
            {'name': '003.mp3', 'source': 'original', 'title': 'Surat Imran',
             'track': '3/114', 'size': '4096', 'length': '64'},
            {'name': 'readme.txt', 'source': 'original', 'title': 'Readme'},
        ]}
        calls = []
        def fetch(identifier):
            calls.append(identifier)
            return payload
        with tempfile.TemporaryDirectory(dir=CACHE) as temp:
            catalog = ImaamsCatalog(Path(temp) / 'cache.db', fetcher=fetch)
            first = catalog.files('HaramainQuraafi')
            second = catalog.files('HaramainQuraafi')
        self.assertEqual(len(calls), 1)
        self.assertEqual(first, second)
        self.assertEqual([f['name'] for f in first], ['002.mp3', '003.mp3'])
        self.assertEqual(first[0]['title'], 'Surat Baqara')

    def test_invalid_api_schema_is_not_cached(self):
        for payload in (None, [], 'bad', {}, {'files': None}, {'files': {}},
                        {'files': 'bad'}, {'is_dark': True, 'files': []}):
            with self.subTest(payload=payload), tempfile.TemporaryDirectory(dir=CACHE) as temp:
                catalog = ImaamsCatalog(Path(temp) / 'cache.db', fetcher=lambda _: payload)
                with self.assertRaisesRegex((ValueError, RuntimeError), 'metadata|unavailable'):
                    catalog.files('HaramainQuraafi')
                self.assertIsNone(catalog.cached_files('HaramainQuraafi'))

    def test_metadata_error_is_reported_instead_of_showing_empty_list(self):
        with tempfile.TemporaryDirectory(dir=CACHE) as temp:
            catalog = ImaamsCatalog(Path(temp) / 'cache.db', fetcher=lambda _: {'error': 'offline'})
            with self.assertRaisesRegex(RuntimeError, 'offline'):
                catalog.files('HaramainQuraafi')

    def test_item_identifier_is_validated_and_filename_is_quoted(self):
        self.assertEqual(archive_download_url('HaramainQuraafi', '001.mp3'),
                         'https://archive.org/download/HaramainQuraafi/001.mp3')
        self.assertEqual(archive_download_url('HaramainQuraafi', 'audio/001 Fatiha.mp3'),
                         'https://archive.org/download/HaramainQuraafi/audio/001%20Fatiha.mp3')
        for filename in ('../secret.mp3', 'audio/../secret.mp3', '/001.mp3',
                         'audio//001.mp3', 'audio/./001.mp3', 'audio\\\\001.mp3',
                         'audio/%2e%2e/001.mp3', 'audio/%252e%252e/001.mp3',
                         '001\x00.mp3', '001\n.mp3', None, 12):
            with self.subTest(filename=filename), self.assertRaises(ValueError):
                archive_download_url('HaramainQuraafi', filename)
        for identifier in ('../private', '', None, 12, 'HaramainQuraafi\n'):
            with self.subTest(identifier=identifier), self.assertRaises(ValueError):
                archive_download_url(identifier, '001.mp3')


if __name__ == '__main__':
    unittest.main()
