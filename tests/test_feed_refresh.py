import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from pipeline import build_dump, feed_dump

CACHE = Path(__file__).resolve().parents[1] / ".cache"
CACHE.mkdir(exist_ok=True)


class FeedRefreshTests(unittest.TestCase):
    def test_cached_month_is_refreshed_when_only_old_month_covers_post(self):
        with tempfile.TemporaryDirectory(dir=CACHE) as temp:
            base = Path(temp)
            post = 'https://site.example/2026/11/post.html'
            posts = base / 'posts.txt'
            posts.write_text(post + '\n')
            cached = base / '202611_p00001.xml'
            cached.write_text(f'<rss><item><link>{post}</link>'
                              '<description>https://media.example/old.mp3</description></item></rss>')
            fresh = cached.read_text().replace('old.mp3', 'fresh.mp3')
            with patch.object(feed_dump, 'fetch', return_value=fresh), patch.object(feed_dump.time, 'sleep'):
                feed_dump.cmd_fill(SimpleNamespace(dir=temp, posts=str(posts), max_index=100,
                    page_size=150, site='https://site.example', jobs=1))
            self.assertEqual(cached.read_text(), fresh)

    def test_fresh_global_post_takes_precedence_over_cached_month_duplicate(self):
        with tempfile.TemporaryDirectory(dir=CACHE) as temp:
            base = Path(temp)
            post = 'https://site.example/2026/11/post.html'
            month_only = 'https://site.example/2026/11/month-only.html'

            def item(link, name):
                return (f'<item><link>{link}</link><title>{name}</title>'
                        f'<description>https://media.example/{name}.mp3</description></item>')

            (base / 'f_000001.xml').write_text(f'<rss>{item(post, "fresh")}</rss>')
            (base / '202611_p00001.xml').write_text(
                f'<rss>{item(post, "old")}{item(month_only, "month-only")}</rss>')
            seen, audio, _, _ = build_dump.parse_feed_files([base])
            self.assertEqual(seen[post][0], 'fresh')
            self.assertEqual(audio[post], ['https://media.example/fresh.mp3'])
            self.assertEqual(audio[month_only], ['https://media.example/month-only.mp3'])

    def test_refreshes_cached_global_and_month_pages(self):
        with tempfile.TemporaryDirectory(dir=CACHE) as temp:
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
