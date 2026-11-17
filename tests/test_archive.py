"""Offline archive import/query integration checks."""
import csv
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
(ROOT / ".cache").mkdir(exist_ok=True)


class ArchiveTests(unittest.TestCase):
    def test_import_and_query_archive(self):
        from archive.repository import Archive
        with tempfile.TemporaryDirectory(dir=ROOT / '.cache') as directory:
            base = Path(directory)
            (base / 'csv').mkdir()
            with (base / 'csv/catalog.csv').open('w', newline='') as stream:
                writer = csv.writer(stream)
                writer.writerow(['year', 'kind', 'url', 'host', 'source_post'])
                for day in range(1, 8):
                    writer.writerow([2026, 'audio', f'https://media.example/fajr{day}.mp3',
                                     'media.example',
                                     f'https://archive.example/2026/10/fajr-{day}th-october.html'])
            database = base / 'archive.db'
            subprocess.run([sys.executable, str(ROOT / 'import_dump.py'),
                            '--dump', str(base), '--db', str(database)], check=True,
                           capture_output=True, text=True)
            archive = Archive(database)
            try:
                self.assertEqual(tuple(archive.stats()[0]), (7, 7, 2026, 2026))
                self.assertEqual(len(archive.search_page(['fajr'])[0]), 5)
                self.assertEqual(len(archive.search_page(['fajr'], page=2)[0]), 2)
                self.assertEqual(archive.by_date_page(2026, 10, 3)[1], 1)
                self.assertEqual(archive.latest(1)[0]['day'], 7)
            finally:
                archive.db.close()


if __name__ == '__main__':
    unittest.main()
