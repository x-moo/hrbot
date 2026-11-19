"""Offline regression tests for archive stats caching and snapshot isolation."""
import sqlite3
import tempfile
import unittest
from pathlib import Path

from archive.repository import Archive


class ArchiveStatsCacheTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        path = Path(directory.name) / "archive.db"
        self.writer = sqlite3.connect(path)
        self.addCleanup(self.writer.close)
        self.writer.execute("PRAGMA journal_mode=WAL")
        self.writer.execute("CREATE TABLE media (url TEXT, kind TEXT, year INTEGER)")
        self.writer.executemany(
            "INSERT INTO media VALUES (?, ?, ?)",
            [("u1", "audio", 2024), ("u1", "audio", 2024), ("u2", "video", 2025)],
        )
        self.writer.commit()
        self.archive = Archive(path)
        self.addCleanup(self.archive.db.close)
        self.statements = []
        self.archive.db.set_trace_callback(self.statements.append)

    def assert_stats(self, totals, kinds, *, cached=False):
        self.statements.clear()
        actual_totals, actual_kinds = self.archive.stats()
        self.assertIsInstance(actual_totals, sqlite3.Row)
        self.assertEqual(tuple(actual_totals), totals)
        self.assertEqual(actual_kinds, kinds)
        if cached:
            self.assertFalse([sql for sql in self.statements if "COUNT(" in sql.upper()])
        return actual_kinds

    def test_repeated_stats_avoids_aggregate_queries(self):
        self.assert_stats((3, 2, 2024, 2025), {"audio": 1, "video": 1})
        self.assert_stats((3, 2, 2024, 2025), {"audio": 1, "video": 1}, cached=True)

    def test_mutating_returned_dict_does_not_affect_cached_stats(self):
        kinds = self.assert_stats((3, 2, 2024, 2025), {"audio": 1, "video": 1})
        kinds["audio"] = 999
        kinds = self.assert_stats((3, 2, 2024, 2025), {"audio": 1, "video": 1}, cached=True)
        kinds.clear()
        self.assert_stats((3, 2, 2024, 2025), {"audio": 1, "video": 1}, cached=True)

    def test_external_commit_invalidates_cache_even_when_count_is_unchanged(self):
        self.assert_stats((3, 2, 2024, 2025), {"audio": 1, "video": 1})
        self.writer.execute("UPDATE media SET kind='document', year=2026 WHERE url='u2'")
        self.assert_stats((3, 2, 2024, 2025), {"audio": 1, "video": 1}, cached=True)
        self.writer.commit()
        self.assert_stats((3, 2, 2024, 2026), {"audio": 1, "document": 1})
        self.assert_stats((3, 2, 2024, 2026), {"audio": 1, "document": 1}, cached=True)

    def test_stats_totals_and_kinds_come_from_one_snapshot(self):
        commits = []

        def commit_before_kind_query(sql):
            if "GROUP BY KIND" in sql.upper() and not commits:
                self.writer.execute("INSERT INTO media VALUES ('u3', 'audio', 2026)")
                self.writer.commit()
                commits.append(True)

        self.archive.db.set_trace_callback(commit_before_kind_query)
        totals, kinds = self.archive.stats()
        self.assertEqual(commits, [True])
        self.assertEqual(tuple(totals), (3, 2, 2024, 2025))
        self.assertEqual(kinds, {"audio": 1, "video": 1})
        self.archive.db.set_trace_callback(self.statements.append)
        self.assert_stats((4, 3, 2024, 2026), {"audio": 2, "video": 1})
        self.assert_stats((4, 3, 2024, 2026), {"audio": 2, "video": 1}, cached=True)

    def test_failed_refresh_does_not_poison_cache(self):
        self.assert_stats((3, 2, 2024, 2025), {"audio": 1, "video": 1})
        self.writer.execute("UPDATE media SET kind='document' WHERE url='u2'")
        self.writer.commit()
        db = self.archive.db
        # Deny the kind read after the totals query has succeeded.
        db.set_authorizer(
            lambda action, table, column, *_: sqlite3.SQLITE_DENY
            if action == sqlite3.SQLITE_READ and table == "media" and column == "kind"
            else sqlite3.SQLITE_OK
        )
        try:
            with self.assertRaisesRegex(sqlite3.DatabaseError, r"media\.kind is prohibited"):
                self.archive.stats()
        finally:
            db.set_authorizer(None)
        self.assertFalse(db.in_transaction)
        self.assert_stats((3, 2, 2024, 2025), {"audio": 1, "document": 1})
        self.assert_stats((3, 2, 2024, 2025), {"audio": 1, "document": 1}, cached=True)
