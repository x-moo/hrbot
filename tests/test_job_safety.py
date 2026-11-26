"""Resource and cleanup regressions for background downloads."""
import threading
import unittest
from unittest.mock import patch
import tempfile
from pathlib import Path
from bot import app


class JobSafetyTests(unittest.TestCase):
    def test_workers_get_unique_directories(self):
        from leech import cli
        paths = []
        def leech(*args, **kwargs):
            paths.append(kwargs['target_dir'])
            return []
        with tempfile.TemporaryDirectory(dir=app.HERE / '.cache') as directory:
            with patch.object(app, 'HERE', Path(directory)), patch.object(cli, 'run_leech_sync', leech), patch.object(app, '_dl_finish'):
                for index in (1, 2):
                    job = {'id': index, 'started': 1000.1, 'lines': []}
                    app._run_leech(job, 'https://example/a', 1, None, None, None, False, False)
        self.assertNotEqual(paths[0], paths[1])

    def tearDown(self):
        app._DL_POOL.clear()

    def test_notification_failure_releases_slot(self):
        pool = {1: 'active'}
        job = {'id': 1, 'lines': [], 'note': '', 'error': 'failure', 'files': [],
               'token': 'test', 'chat': 1, 'message_id': 1,
               'lock': threading.Lock(), 'pool': pool}
        with patch.object(app, 'send_long', side_effect=RuntimeError('offline')), patch.object(app, 'tg_call'):
            app._dl_finish(job)
        self.assertEqual(pool, {})

    def test_queue_has_global_limit(self):
        with patch.object(app, 'tg_call', return_value={'message_id': 1}), patch.object(app, '_LEECH_EXECUTOR'):
            with patch.dict('os.environ', {'LEECH_QUEUE_SIZE': '2'}):
                self.assertTrue(app.start_leech('test', 1, 'https://example/a', None, None, None, False, False)[1])
                self.assertTrue(app.start_leech('test', 2, 'https://example/b', None, None, None, False, False)[1])
                self.assertFalse(app.start_leech('test', 3, 'https://example/c', None, None, None, False, False)[1])

    def test_duplicate_request_rejected(self):
        with patch.object(app, 'tg_call', return_value={'message_id': 1}), patch.object(app, '_LEECH_EXECUTOR'):
            app.start_leech('test', 1, 'https://example/a', None, None, None, False, False)
            self.assertFalse(app.start_leech('test', 1, 'https://example/a', None, None, None, False, False)[1])
