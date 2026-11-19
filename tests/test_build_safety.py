from pathlib import Path
import subprocess
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
                "python", str(ROOT / "pipeline/build_dump.py"),
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
                "python", str(ROOT / "pipeline/build_dump.py"),
                "--posts", str(posts), "--feeds", str(feed_dir), "--out", str(output),
            ], capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual((output / "index/posts_without_media.txt").read_text(), post + "\n")


if __name__ == "__main__":
    unittest.main()
