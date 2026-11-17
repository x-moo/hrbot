"""Shared environment-loading contract."""
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
(ROOT / ".cache").mkdir(exist_ok=True)


class ConfigTests(unittest.TestCase):
    def test_existing_environment_wins(self):
        from config import load_env
        with tempfile.TemporaryDirectory(dir=ROOT / '.cache') as directory:
            (Path(directory) / '.env').write_text('EXISTING=file\nNEW_VALUE="hello"\n')
            with patch.dict(os.environ, {'EXISTING': 'process'}, clear=True):
                self.assertTrue(load_env(Path(directory)))
                self.assertEqual(os.environ['EXISTING'], 'process')
                self.assertEqual(os.environ['NEW_VALUE'], 'hello')
