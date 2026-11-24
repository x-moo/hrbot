"""Transactional import tests against a real SQLite reader and CSV input."""

import csv
import sqlite3
import tempfile
import unittest
from pathlib import Path

from archive import import_dump
from archive.repository import Archive

ROOT = Path(__file__).resolve().parents[1]


class ImportTransactionTests(unittest.TestCase):
    def test_failed_replacement_rolls_back_and_reader_sees_only_committed_import(self):
        importer = getattr(import_dump, "import_catalog", None)
        self.assertIsNotNone(
            importer, "import should be a reusable transactional operation"
        )
        with tempfile.TemporaryDirectory(dir=ROOT / ".cache") as directory:
            root = Path(directory)
            dump = root / "dump"
            (dump / "csv").mkdir(parents=True)
            csv_path = dump / "csv/catalog.csv"

            def catalog(name):
                with csv_path.open("w", newline="", encoding="utf-8") as stream:
                    writer = csv.writer(stream)
                    writer.writerow(["year", "type", "url", "host", "source_post"])
                    writer.writerow(
                        [
                            2026,
                            "audio",
                            "https://media.example/" + name,
                            "media.example",
                            "https://site.example/2026/11/fajr-24th.html",
                        ]
                    )

            database = root / "data/catalog #1?.db"
            catalog("old.mp3")
            importer(dump, database)
            reader = Archive(database)
            try:
                with sqlite3.connect(database) as db:
                    db.execute(
                        "CREATE TRIGGER fail_insert BEFORE INSERT ON media "
                        "BEGIN SELECT RAISE(ABORT, 'test failure'); END"
                    )
                db.close()
                catalog("new.mp3")
                with self.assertRaises(sqlite3.IntegrityError):
                    importer(dump, database)
                self.assertTrue(reader.latest(1)[0]["url"].endswith("old.mp3"))
                with sqlite3.connect(database) as db:
                    db.execute("DROP TRIGGER fail_insert")
                db.close()
                importer(dump, database)
                self.assertTrue(reader.latest(1)[0]["url"].endswith("new.mp3"))
                with sqlite3.connect(database) as db:
                    built = db.execute("SELECT v FROM meta WHERE k='built'").fetchone()[
                        0
                    ]
                db.close()
                self.assertTrue(
                    built.endswith("+00:00"),
                    "new imports must have explicit UTC timestamps",
                )
            finally:
                reader.db.close()


if __name__ == "__main__":
    unittest.main()
