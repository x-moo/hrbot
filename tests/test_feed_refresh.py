import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from pipeline import feed_dump


class FeedRefreshTests(unittest.TestCase):
    def test_refreshes_cached_global_and_month_pages(self):
        with tempfile.TemporaryDirectory() as temp:
            base = Path(temp)
            global_dir = base / "global"
            global_dir.mkdir()
            (global_dir / "f_000001.xml").write_text("<rss><item><link>old</link></item></rss>")
            with patch.object(feed_dump, "fetch", return_value="<rss><item><link>fresh-global</link></item></rss>"):
                feed_dump.cmd_global(SimpleNamespace(dir=str(global_dir), max_index=100,
                    page_size=150, site="https://site.example"))
            global_text = (global_dir / "f_000001.xml").read_text()
            self.assertIn("fresh-global", global_text)

            posts = base / "posts.txt"
            posts.write_text("https://site.example/2026/11/new.html\n")
            month_dir = base / "month"
            month_dir.mkdir()
            (month_dir / "202611_p00001.xml").write_text("<rss><item><link>old</link></item></rss>")
            with patch.object(feed_dump, "fetch", return_value=f"<rss><item><link>{posts.read_text().strip()}</link></item></rss>"), patch.object(feed_dump.time, "sleep"):
                feed_dump.cmd_fill(SimpleNamespace(dir=str(month_dir), posts=str(posts),
                    max_index=100, page_size=150, site="https://site.example", jobs=1))
            month_text = (month_dir / "202611_p00001.xml").read_text()
            self.assertIn("https://site.example/2026/11/new.html", month_text)


if __name__ == "__main__":
    unittest.main()
