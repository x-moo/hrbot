from pathlib import Path
import subprocess
import sys
from unittest.mock import patch
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]

CACHE = Path(__file__).resolve().parents[1] / ".cache"
CACHE.mkdir(exist_ok=True)


class BuildSafetyTests(unittest.TestCase):
    def test_empty_or_missing_inputs_do_not_remove_existing_output(self):
        with tempfile.TemporaryDirectory(dir=CACHE) as temp:
            base = Path(temp)
            posts = base / "posts.txt"
            posts.write_text("\n")
            output = base / "dump"
            output.mkdir()
            sentinel = output / "keep.txt"
            sentinel.write_text("still here")
            result = subprocess.run([
                sys.executable, str(ROOT / "pipeline/build_dump.py"),
                "--posts", str(posts), "--out", str(output),
            ], capture_output=True, text=True)
            self.assertNotEqual(result.returncode, 0)
            self.assertEqual(sentinel.read_text(), "still here")

    def test_no_media_index_ignores_posts_with_only_excluded_urls(self):
        with tempfile.TemporaryDirectory(dir=CACHE) as temp:
            base = Path(temp)
            post = "https://archive.example/2026/01/test.html"
            posts = base / "posts.txt"
            posts.write_text(post + "\n")
            feed_dir = base / "feeds"
            feed_dir.mkdir()
            (feed_dir / "data.xml").write_text(
                f"<item><link>{post}</link><description>"
                "https://google.com/clip.mp4</description></item>")
            output = base / "dump"
            result = subprocess.run([
                sys.executable, str(ROOT / "pipeline/build_dump.py"),
                "--posts", str(posts), "--feeds", str(feed_dir), "--out", str(output),
            ], capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual((output / "index/posts_without_media.txt").read_text(), post + "\n")

    def test_late_write_failure_keeps_previous_dump_and_removes_staging(self):
        from pipeline import build_dump
        with tempfile.TemporaryDirectory(dir=CACHE) as directory:
            root = Path(directory)
            posts = root / 'posts.txt'
            post = 'https://archive.example/2026/01/test.html'
            posts.write_text(post + '\n')
            feeds = root / 'feeds'
            feeds.mkdir()
            (feeds / 'one.xml').write_text(f'<item><link>{post}</link><description>https://example.org/a.mp3</description></item>')
            out = root / 'dump'
            (out / 'csv').mkdir(parents=True)
            (out / 'csv/catalog.csv').write_text('last good catalog')
            real_open = open
            def failing_open(path, mode='r', *args, **kwargs):
                if str(path).endswith('/README.md') and mode == 'w':
                    raise OSError('disk full')
                return real_open(path, mode, *args, **kwargs)
            with patch.object(sys, 'argv', ['build', '--posts', str(posts), '--feeds', str(feeds), '--out', str(out)]), \
                 patch('builtins.open', side_effect=failing_open):
                with self.assertRaisesRegex(OSError, 'disk full'):
                    build_dump.main()
            self.assertEqual((out / 'csv/catalog.csv').read_text(), 'last good catalog')
            self.assertFalse(list(root.glob('.dump.build-*')))

    def test_output_cannot_be_checkout_ancestor_or_unrelated_directory(self):
        for kind in ('parent', 'unrelated'):
            with self.subTest(kind=kind), tempfile.TemporaryDirectory(dir=CACHE) as directory:
                root = Path(directory)
                posts = root / 'posts.txt'
                posts.write_text('https://archive.example/2026/01/test.html\n')
                out = root if kind == 'parent' else root / 'unrelated'
                out.mkdir(exist_ok=True)
                keep = out / 'keep'
                keep.write_text('important')
                proc = subprocess.run([sys.executable, str(ROOT / 'pipeline/build_dump.py'),
                    '--posts', str(posts), '--out', str(out)], capture_output=True)
                self.assertNotEqual(proc.returncode, 0)
                self.assertEqual(keep.read_text(), 'important')


if __name__ == "__main__":
    unittest.main()
