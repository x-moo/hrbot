import os
import unittest
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import unquote, urlsplit

from bot.imaams import RECITERS

ROOT = Path(__file__).resolve().parents[1]
SOURCE = Path(os.environ.get(
    'IMAAMS_SOURCE_SNAPSHOT',
    ROOT / 'tests' / 'fixtures' / 'imaams' / 'haramain-info-reciter-section.html',
))


class ReciterLinks(HTMLParser):
    def __init__(self):
        super().__init__()
        self.links = []
        self.current = None

    def handle_starttag(self, tag, attrs):
        if tag == 'a':
            self.current = [dict(attrs).get('href', ''), '']

    def handle_data(self, data):
        if self.current is not None:
            self.current[1] += data

    def handle_endtag(self, tag):
        if tag == 'a' and self.current is not None:
            href, name = self.current
            if href.startswith('https://archive.org/details/'):
                identifier = unquote(urlsplit(href).path.split('/')[2])
                self.links.append((' '.join(name.split()), identifier))
            self.current = None


class ImaamsSourceCatalogTests(unittest.TestCase):
    def test_catalog_matches_all_deduplicated_homepage_reciter_links(self):
        parser = ReciterLinks()
        parser.feed(SOURCE.read_text(encoding='utf-8'))
        entries = list(dict((identifier, (name, identifier))
                            for name, identifier in parser.links).values())
        actual = [(entry['name'], entry['identifier']) for entry in RECITERS]
        self.assertEqual(len(parser.links), 39)
        self.assertEqual(len(entries), 38)
        self.assertEqual(actual, entries)


if __name__ == '__main__':
    unittest.main()
