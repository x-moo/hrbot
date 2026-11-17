"""Pure helpers stay usable without opening network connections."""
import unittest


class HelperTests(unittest.TestCase):
    def test_telegram_chunking_preserves_short_lines(self):
        from bot.telegram import chunk_text
        self.assertEqual(chunk_text(['abc', 'def', 'ghi'], limit=7), ['abc\ndef', 'ghi'])

    def test_downloader_progress_excludes_url(self):
        from leech.downloader import progress_of
        self.assertEqual(progress_of('[10%] https://example.invalid/?secret=abc'),
                         'downloading... 10%')
