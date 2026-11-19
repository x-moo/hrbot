import json
import os
import unittest
from pathlib import Path

from bot.imaams import ImaamsCatalog

ROOT = Path(__file__).resolve().parents[1]
METADATA = Path(os.environ.get(
    'IMAAMS_ARCHIVE_METADATA',
    ROOT / 'tests' / 'fixtures' / 'imaams' / 'archive-metadata-quraafi.json',
))


class ArchiveMetadataLiveTests(unittest.TestCase):
    def test_quraafi_archive_metadata_contains_filtered_audio_files(self):
        payload = json.loads(METADATA.read_text(encoding='utf-8'))
        files = ImaamsCatalog._audio_files('HaramainQuraafi', payload)
        self.assertGreater(len(files), 0)
        self.assertTrue(all(f['source'] == 'original' for f in files))
        self.assertTrue(all(f['url'].startswith('https://archive.org/download/HaramainQuraafi/')
                            for f in files))


if __name__ == '__main__':
    unittest.main()
