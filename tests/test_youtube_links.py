"""YouTube references are stored apart from downloadable media."""

import sqlite3
import tempfile
import unittest
from pathlib import Path

from archive.import_dump import import_catalog
from archive.repository import Archive
from pipeline.build_dump import parse_feed_files

ROOT = Path(__file__).resolve().parents[1]
(ROOT / ".cache").mkdir(exist_ok=True)

POST = "https://archive.example/2026/10/fajr-3rd-october.html"
VIDEO = "https://www.youtube.com/watch?v=abcdefghijk"


class FeedCollectionTests(unittest.TestCase):
    def test_feed_items_keep_youtube_links_apart_from_media(self):
        with tempfile.TemporaryDirectory(dir=ROOT / ".cache") as directory:
            feeds = Path(directory)
            (feeds / "batch.xml").write_text(
                "<rss><item><link>" + POST + "</link><description>"
                '&lt;iframe src="https://www.youtube.com/embed/abcdefghijk"&gt;'
                "</description></item></rss>"
            )
            posts_seen, audio, video, youtube, items = parse_feed_files([str(feeds)])
        self.assertEqual(items, 1)
        self.assertEqual(youtube[POST], [VIDEO])
        self.assertEqual(audio[POST], [])
        self.assertEqual(video[POST], [])


class ImportTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory(dir=ROOT / ".cache")
        self.base = Path(self._tmp.name)
        self.dump = self.base / "dump"
        self.db = self.base / "archive.db"
        (self.dump / "csv").mkdir(parents=True)
        (self.dump / "csv" / "catalog.csv").write_text(
            "year,type,url,host,source_post\n"
            f'2026,"audio","https://media.example/a.mp3","media.example","{POST}"\n'
        )
        self._videos(f'"{POST}","{VIDEO}"')

    def tearDown(self):
        self._tmp.cleanup()

    def _videos(self, *rows):
        (self.dump / "csv" / "videos.csv").write_text(
            "source_post,video_url\n" + "".join(row + "\n" for row in rows)
        )

    def test_import_stores_youtube_links_in_their_own_table(self):
        result = import_catalog(self.dump, self.db)
        self.assertEqual(result["videos"], 1)
        self.assertEqual(Archive(self.db).videos_for(POST), [VIDEO])
        with sqlite3.connect(self.db) as db:
            youtube_rows = db.execute(
                "SELECT COUNT(*) FROM media WHERE url LIKE '%youtube%'"
            ).fetchone()[0]
            media_rows = db.execute("SELECT COUNT(*) FROM media").fetchone()[0]
        self.assertEqual(youtube_rows, 0)
        self.assertEqual(media_rows, 1)

    def test_duplicate_rows_are_stored_once(self):
        self._videos(f'"{POST}","{VIDEO}"', f'"{POST}","{VIDEO}"')
        self.assertEqual(import_catalog(self.dump, self.db)["videos"], 1)

    def test_dump_without_a_videos_file_still_imports(self):
        (self.dump / "csv" / "videos.csv").unlink()
        result = import_catalog(self.dump, self.db)
        self.assertEqual(result["videos"], 0)
        self.assertEqual(Archive(self.db).videos_for(POST), [])

    def test_lookalike_video_urls_are_skipped(self):
        self._videos(
            f'"{POST}","https://evil.example/watch?v=abcdefghijk"',
            f'"{POST}","http://www.youtube.com/watch?v=abcdefghijk"',
        )
        self.assertEqual(import_catalog(self.dump, self.db)["videos"], 0)

    def test_repository_tolerates_databases_without_the_table(self):
        with sqlite3.connect(self.db) as db:
            db.execute("CREATE TABLE media (id INTEGER PRIMARY KEY, url TEXT)")
        self.assertEqual(Archive(self.db).videos_for(POST), [])


if __name__ == "__main__":
    unittest.main()
