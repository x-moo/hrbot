"""Exercise the complete offline dump build and legacy entry points."""
import os
from pathlib import Path
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
(ROOT / ".cache").mkdir(exist_ok=True)


class PipelineTests(unittest.TestCase):
    def test_legacy_build_from_workspace(self):
        with tempfile.TemporaryDirectory(dir=ROOT / '.cache') as directory:
            base = Path(directory)
            work = base / 'work'
            feeds = work / 'feeds'
            feeds.mkdir(parents=True)
            post = 'https://archive.example/2026/10/fajr-3rd-october.html'
            (work / 'posts.txt').write_text(post + '\n')
            (feeds / 'batch.xml').write_text(
                '<rss><item><link>' + post + '</link><description>'
                'https://media.example/fajr.mp3 '
                '&lt;iframe src="https://www.youtube.com/embed/abcdefghijk"&gt;'
                '</description></item></rss>')
            output = base / 'dump'
            result = subprocess.run(['bash', str(ROOT / 'scripts/run.sh'), 'build'],
                cwd=base, env={**os.environ, 'WORK': str(work), 'OUT': str(output)},
                capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual((output / 'index/all_audio_urls.txt').read_text(),
                             'https://media.example/fajr.mp3\n')
            self.assertEqual(
                (output / 'csv/videos.csv').read_text(),
                'source_post,video_url\n'
                f'"{post}","https://www.youtube.com/watch?v=abcdefghijk"\n')


if __name__ == '__main__':
    unittest.main()
