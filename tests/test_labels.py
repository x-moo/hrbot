"""Label exports honor configuration and propagate failures."""
import os
from pathlib import Path
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
(ROOT / ".cache").mkdir(exist_ok=True)


class LabelTests(unittest.TestCase):
    def test_custom_menu_and_failed_group(self):
        with tempfile.TemporaryDirectory(dir=ROOT / '.cache') as directory:
            base = Path(directory)
            menu = base / 'menu.json'
            menu.write_text('{"Group": [{"label": "Example", "search_url": "https://example.invalid"}]}')
            # A file where a directory is needed forces a real export failure.
            output = base / 'output'
            output.write_text('occupied')
            result = subprocess.run(['bash', str(ROOT / 'pipeline/dump_all_groups.sh')],
                env={**os.environ, 'MENU': str(menu), 'OUT': str(output),
                     'FEED1': str(base / 'feeds'), 'FEED2': str(base / 'feeds2')},
                capture_output=True, text=True)
            self.assertNotEqual(result.returncode, 0)
            self.assertIn('groups=1 failed=1', result.stdout)
            self.assertFalse(list((ROOT / 'pipeline').glob('.groups.*')))
