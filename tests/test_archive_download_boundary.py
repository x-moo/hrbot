import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from bot import app
from leech import downloader

ROOT = Path(__file__).resolve().parents[1]
CACHE = ROOT / '.cache'
CACHE.mkdir(exist_ok=True)


class ArchiveDownloadBoundaryTests(unittest.TestCase):
    def tearDown(self):
        app._DL_POOL.clear()

    def test_archive_download_executes_in_untrusted_context(self):
        import leech.cli as cli
        observed = []
        def run_leech(*args, **kwargs):
            observed.append(downloader._UNTRUSTED.get())
            return []
        with tempfile.TemporaryDirectory(dir=CACHE) as directory, \
             patch.object(app, 'HERE', Path(directory)), \
             patch.object(cli, 'run_leech_sync', side_effect=run_leech), \
             patch.object(app, '_dl_finish'):
            job = {'id': 1, 'started': 1.0, 'lines': [], 'url': 'http://127.0.0.1/',
                   'chat': 1, 'name': None}
            app._run_leech(job, 'http://127.0.0.1/', 1, None, None, None, False, False)
        self.assertEqual(observed, [True])


if __name__ == '__main__':
    unittest.main()
