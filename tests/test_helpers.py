"""Pure helpers stay usable without opening network connections."""
import unittest


class HelperTests(unittest.TestCase):
    def test_telegram_chunking_preserves_short_lines(self):
        from bot.telegram import chunk_text
        self.assertEqual(chunk_text(['abc', 'def', 'ghi'], limit=7), ['abc\ndef', 'ghi'])

    def test_telegram_chunking_splits_oversized_line(self):
        from bot.telegram import chunk_text
        text = 'abcdefghijk'
        chunks = chunk_text([text], limit=4)
        self.assertEqual(chunks, ['abcd', 'efgh', 'ijk'])
        self.assertEqual(''.join(chunks), text)
        self.assertTrue(all(0 < len(chunk) <= 4 for chunk in chunks))

    def test_telegram_chunking_counts_utf16_units(self):
        from bot.telegram import chunk_text
        for lines, expected in [
            (['😀😀😀'], ['😀😀', '😀']),
            (['a😀b', 'x'], ['a😀b', 'x']),
            (['😀', 'x'], ['😀\nx']),
            (['abc😀z'], ['abc', '😀z']),
        ]:
            with self.subTest(lines=lines):
                chunks = chunk_text(lines, limit=4)
                self.assertEqual(chunks, expected)
                self.assertTrue(all(0 < len(chunk.encode('utf-16-le')) // 2 <= 4
                                    for chunk in chunks))

    def test_telegram_chunking_rejects_unusable_limits(self):
        from bot.telegram import chunk_text
        for lines, limit in [(['abc'], 0), ([], -1), (['😀'], 1)]:
            with self.subTest(lines=lines, limit=limit):
                with self.assertRaises(ValueError):
                    chunk_text(lines, limit=limit)

    def test_telegram_chunking_empty_input(self):
        from bot.telegram import chunk_text
        for lines in [[], [''], ['', '']]:
            with self.subTest(lines=lines):
                self.assertEqual(chunk_text(lines, limit=4), [])

    def test_telegram_chunking_exact_boundary(self):
        from bot.telegram import chunk_text
        self.assertEqual(chunk_text(['abcd'], limit=4), ['abcd'])
        self.assertEqual(chunk_text(['abcd', 'efgh'], limit=4), ['abcd', 'efgh'])
        self.assertEqual(chunk_text(['abcdefgh'], limit=4), ['abcd', 'efgh'])

    def test_telegram_chunking_preserves_embedded_newlines(self):
        from bot.telegram import chunk_text
        text = '\nab\n\ncdefgh\n'
        chunks = chunk_text([text], limit=4)
        self.assertEqual(''.join(chunks), text)
        self.assertTrue(all(0 < len(chunk) <= 4 for chunk in chunks))
        self.assertEqual(chunk_text(['a\nb', 'c', 'defgh', 'i'], limit=5),
                         ['a\nb\nc', 'defgh', 'i'])
        self.assertEqual(chunk_text(['a', '', 'b'], limit=4), ['a\n\nb'])
        self.assertEqual(chunk_text(['a', 'bcdefg', 'h'], limit=4),
                         ['a', 'bcde', 'fg\nh'])
