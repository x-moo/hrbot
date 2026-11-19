import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch, Mock

from bot import update, app

ROOT = Path(__file__).resolve().parents[1]
CACHE = ROOT / '.cache'
CACHE.mkdir(exist_ok=True)


class UpdateIntegrationTests(unittest.TestCase):
    def test_custom_paths_and_interpreter_are_shared_by_pipeline_and_import(self):
        with patch.dict(os.environ, {'DB_PATH': 'data/custom.db', 'DUMP': 'custom-dump'}, clear=True), \
             patch.object(update.subprocess, 'run') as run:
            update._perform_update()
        calls = run.call_args_list
        self.assertEqual(calls[0].kwargs['env']['PYTHON'], sys.executable)
        self.assertEqual(calls[0].kwargs['env']['OUT'], str(ROOT / 'custom-dump'))
        self.assertIn(str(ROOT / 'data/custom.db'), calls[-1].args[0])
        self.assertIn(str(ROOT / 'custom-dump'), calls[-1].args[0])

    def test_startup_calls_the_scheduled_callback(self):
        db = Mock()
        db.stats.return_value = ((1, 1, 2026, 2026), {})
        callback = Mock()
        def immediate_worker(hhmm, func, **kwargs):
            self.assertEqual(hhmm, '03:00')
            func()
        with patch.dict(os.environ, {'BOT_TOKEN': 'test', 'DB_PATH': 'catalog.db',
                                    'DB_UPDATE_TIME': '03:00'}, clear=True), \
             patch.object(app, 'load_env'), patch.object(app.os.path, 'exists', return_value=True), \
             patch.object(app, 'Archive', return_value=db), patch.object(app, 'run_update', callback), \
             patch('bot.scheduler.start_daily_worker', side_effect=immediate_worker), \
             patch.object(app, 'prune_downloads'), patch.object(app, '_configure_menu'), \
             patch.object(app, 'tg_call', side_effect=KeyboardInterrupt):
            app.main()
        callback.assert_called_once()

    def test_bootstrap_skips_existing_db_configured_only_in_dotenv(self):
        import shutil
        with tempfile.TemporaryDirectory(dir=CACHE) as directory:
            root = Path(directory)
            shutil.copy(ROOT / 'bot.sh', root / 'bot.sh')
            shutil.copy(ROOT / 'config.py', root / 'config.py')
            (root / '.env').write_text('DB_PATH=custom.db\n')
            (root / 'custom.db').touch()
            import subprocess
            result = subprocess.run(['bash', str(root / 'bot.sh'), 'init'],
                env={'PATH': os.environ['PATH'], 'PYTHON': sys.executable},
                text=True, capture_output=True)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn('skip', result.stdout)
