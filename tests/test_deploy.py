from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[1]


class DeploymentTests(unittest.TestCase):
    def test_service_does_not_default_to_root(self):
        settings = dict(line.split('=', 1) for line in
                        (ROOT / 'deploy/haramain-bot.service').read_text().splitlines()
                        if '=' in line and not line.startswith('#'))
        self.assertEqual(settings.get('User'), 'haramain')
        self.assertEqual(settings.get('NoNewPrivileges'), 'true')

    def test_template_enables_daily_archive_refresh(self):
        settings = dict(line.split('=', 1) for line in
                        (ROOT / '.env.example').read_text().splitlines()
                        if '=' in line and not line.startswith('#'))
        self.assertEqual(settings['DB_UPDATE_TIME'], '03:00')
        self.assertIn('TELEGRAM_API', settings)
        self.assertIn('TELEGRAM_HASH', settings)
