import sys
import tempfile
import unittest
from pathlib import Path
from subprocess import CompletedProcess
from unittest.mock import patch

from pipeline import scrape_pages, sitemap_posts

ROOT = Path(__file__).resolve().parents[1]
CACHE = ROOT / '.cache'
CACHE.mkdir(exist_ok=True)


class FetchTests(unittest.TestCase):
    def test_bad_sitemap_keeps_existing_post_list(self):
        with tempfile.TemporaryDirectory(dir=CACHE) as tmp:
            out = Path(tmp) / 'posts.txt'
            out.write_text('last good sitemap\n')
            with patch.object(sys, 'argv', ['sitemap', '-o', str(out)]), \
                 patch.object(sitemap_posts, 'fetch', return_value='<html>maintenance</html>'):
                with self.assertRaises(SystemExit):
                    sitemap_posts.main()
            self.assertEqual(out.read_text(), 'last good sitemap\n')

    def test_partial_curl_transfer_is_not_used(self):
        def short_transfer(cmd, **kwargs):
            Path(cmd[cmd.index('-o') + 1]).write_text('https://example.test/audio.mp3')
            return CompletedProcess(cmd, 18, stdout='200', stderr='')
        with patch.object(scrape_pages.subprocess, 'run', side_effect=short_transfer), \
             patch.object(scrape_pages.time, 'sleep'):
            status, body = scrape_pages.fetch_page('https://example.test/', tries=1)
        self.assertNotEqual(status, '200')
        self.assertEqual(body, '')
