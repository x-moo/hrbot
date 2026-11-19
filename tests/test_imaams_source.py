import json
import unittest
from pathlib import Path

from bot.imaams import RECITERS

SOURCE = Path(__file__).resolve().parents[1] / '.cache' / 'imaams-source.json'


class ImaamsSourceCatalogTests(unittest.TestCase):
    def test_catalog_matches_all_deduplicated_homepage_reciter_links(self):
        if not SOURCE.is_file():
            self.skipTest('homepage source snapshot unavailable')
        data = json.loads(SOURCE.read_text(encoding='utf-8'))
        expected = [(name, identifier) for name, identifier in data['entries']]
        actual = [(entry['name'], entry['identifier']) for entry in RECITERS]
        self.assertEqual(data['raw_link_count'], 39)
        self.assertEqual(data['unique_identifier_count'], 38)
        self.assertEqual(actual, expected)


if __name__ == '__main__':
    unittest.main()
