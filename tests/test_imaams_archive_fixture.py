import unittest
from pathlib import Path

from bot.imaams import ImaamsCatalog

ROOT = Path(__file__).resolve().parents[1]
METADATA = ROOT / '.cache' / 'imaams-research' / 'archive-metadata-quraafi.json'


class ArchiveMetadataLiveTests(unittest.TestCase):
    @unittest.skipUnless(METADATA.is_file(), 'saved public Archive.org metadata is absent')
    def test_quraafi_archive_metadata_contains_filtered_audio_files(self):
        import json
        payload = json.loads(METADATA.read_text(encoding='utf-8'))
        files = ImaamsCatalog._audio_files('HaramainQuraafi', payload)
        self.assertGreater(len(files), 0)
        self.assertTrue(all(f['source'] == 'original' for f in files))
        self.assertTrue(all(f['url'].startswith('https://archive.org/download/HaramainQuraafi/')
                            for f in files))


if __name__ == '__main__':
    unittest.main()
