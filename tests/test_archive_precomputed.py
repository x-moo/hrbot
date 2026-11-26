"""Import-time aggregates keep /stat and the year menu at O(1)."""

import csv
import json
import sqlite3
import tempfile
import unittest
from pathlib import Path

from archive import import_dump
from archive.repository import Archive

ROOT = Path(__file__).resolve().parents[1]


class PrecomputedAggregateTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory(dir=ROOT / ".cache")
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)
        dump = self.root / "dump"
        (dump / "csv").mkdir(parents=True)
        with (dump / "csv" / "catalog.csv").open("w", newline="", encoding="utf-8") as stream:
            writer = csv.writer(stream)
            writer.writerow(["year", "type", "url", "host", "source_post"])
            for year, kind, name in (
                (2025, "audio", "a.mp3"),
                (2026, "video", "b.mp4"),
                (2026, "audio", "c.mp3"),
            ):
                writer.writerow([
                    year, kind, f"https://media.example/{name}", "media.example",
                    f"https://site.example/{year}/11/{name[:-4]}.html",
                ])
        self.db_path = self.root / "archive.db"
        import_dump.import_catalog(dump, self.db_path)
        self.archive = Archive(self.db_path)
        self.addCleanup(self.archive.db.close)

    def test_import_stores_aggregates_matching_the_catalog(self):
        with sqlite3.connect(self.db_path) as db:
            row = db.execute("SELECT v FROM meta WHERE k='stats'").fetchone()
        payload = json.loads(row[0])
        self.assertEqual(payload["total"], 3)
        self.assertEqual(payload["unique"], 3)
        self.assertEqual(payload["min_year"], 2025)
        self.assertEqual(payload["max_year"], 2026)
        self.assertEqual(payload["kinds"], {"audio": 2, "video": 1})
        self.assertEqual(payload["years"], [[2026, 2], [2025, 1]])

    def test_stats_and_years_read_the_precomputed_payload(self):
        statements = []
        self.archive.db.set_trace_callback(statements.append)
        self.archive.stats()
        self.assertEqual(self.archive.years(), [(2026, 2), (2025, 1)])
        self.assertFalse(
            [sql for sql in statements if "COUNT(" in sql.upper()],
            "aggregates must not be recomputed when the importer stored them",
        )

    def test_missing_payload_falls_back_to_sql(self):
        with sqlite3.connect(self.db_path) as db:
            db.execute("DELETE FROM meta WHERE k='stats'")
        self.assertEqual(tuple(self.archive.stats()[0]), (3, 3, 2025, 2026))
        self.assertEqual(self.archive.years(), [(2026, 2), (2025, 1)])

    def test_malformed_payload_falls_back_to_sql(self):
        with sqlite3.connect(self.db_path) as db:
            db.execute("UPDATE meta SET v='not json' WHERE k='stats'")
        self.assertEqual(tuple(self.archive.stats()[0]), (3, 3, 2025, 2026))
        self.assertEqual(self.archive.years(), [(2026, 2), (2025, 1)])

    def test_old_database_without_meta_table_still_works(self):
        path = self.root / "legacy.db"
        with sqlite3.connect(path) as db:
            db.execute("CREATE TABLE media (url TEXT, kind TEXT, year INTEGER)")
            db.execute("INSERT INTO media VALUES ('u1', 'audio', 2024)")
        archive = Archive(path)
        self.addCleanup(archive.db.close)
        self.assertEqual(tuple(archive.stats()[0]), (1, 1, 2024, 2024))
        self.assertEqual(archive.years(), [(2024, 1)])
        self.assertIsNone(archive.built())

    def test_built_timestamp_and_stat_screen_expose_the_import_time(self):
        self.assertTrue(self.archive.built().endswith("+00:00"))
        from bot import app

        text, _ = app._screen_stat(self.archive)
        self.assertIn("• updated: ", text)
        self.assertIn(" UTC", text)


if __name__ == "__main__":
    unittest.main()
