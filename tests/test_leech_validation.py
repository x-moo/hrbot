"""Validate delivery confirmation and targets before destructive work."""
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

from leech import cli

ROOT = Path(__file__).resolve().parents[1]


class ValidationTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        (ROOT / '.cache').mkdir(exist_ok=True)
        directory = self.enterContext(tempfile.TemporaryDirectory(dir=ROOT / '.cache'))
        self.source = Path(directory) / 'test.mp3'
        self.source.write_bytes(b'audio')
        self.args = SimpleNamespace(out=directory, name='test.mp3',
            url='https://example/test.mp3', chat='7', header=[], jobs=4,
            split_size=None, thumb=None, on_line=None, no_split=True,
            dry_run=False, keep=False, as_doc=False)
        self.enterContext(patch.dict('os.environ', {'TELEGRAM_API': '1',
            'TELEGRAM_HASH': 'test', 'BOT_TOKEN': 'test',
            'USER_SESSION_STRING': '', 'LEECH_CHAT': '', 'KEEP_FILES': '0'}))
        client = Mock(start=AsyncMock(), stop=AsyncMock())
        self.factory = self.enterContext(patch.object(cli, 'Client', return_value=client))
        self.download = self.enterContext(patch.object(cli, 'download',
            return_value=[str(self.source)]))

    async def test_unconfirmed_upload_retains_file(self):
        for result in (None, object(), SimpleNamespace(id=None), SimpleNamespace(id=0)):
            with self.subTest(result=result):
                self.source.parent.mkdir(parents=True, exist_ok=True)
                self.source.write_bytes(b'audio')
                progress = Mock()
                self.args.on_line = progress
                with patch.object(cli, 'upload', new=AsyncMock(return_value=result)):
                    with self.assertRaisesRegex(RuntimeError, 'confirm'):
                        await cli.run(self.args)
                self.assertTrue(self.source.exists())
                self.assertFalse(any('sent 1/1' in str(call) for call in progress.call_args_list))

    async def test_confirmed_upload_deletes_file(self):
        with patch.object(cli, 'upload', new=AsyncMock(return_value=SimpleNamespace(id=42))):
            await cli.run(self.args)
        self.assertFalse(self.source.exists())

    async def test_empty_target_is_rejected_before_download(self):
        self.args.chat = ''
        with patch.object(cli, 'download') as download:
            with self.assertRaisesRegex(SystemExit, 'no target chat'):
                await cli.run(self.args)
        download.assert_not_called()

    async def test_dry_run_allows_empty_target(self):
        self.args.chat = ''
        self.args.dry_run = True
        with patch.object(cli, 'split_file', return_value=[str(self.source)]):
            result = await cli.run(self.args)
        self.assertEqual(result, [str(self.source)])

    async def test_user_client_is_lazy_when_bot_can_upload(self):
        self.enterContext(patch.dict('os.environ', {'USER_SESSION_STRING': 'session'}))
        with patch.object(cli, 'upload', new=AsyncMock(return_value=SimpleNamespace(id=42))):
            await cli.run(self.args)
        self.assertEqual(self.factory.call_count, 1)
        self.assertTrue(self.factory.call_args.kwargs.get('bot_token'))

    async def test_user_client_is_enabled_for_saved_messages(self):
        self.args.chat = 'me'
        self.enterContext(patch.dict('os.environ', {'USER_SESSION_STRING': 'session'}))
        user = Mock(start=AsyncMock(), stop=AsyncMock(),
                    get_me=AsyncMock(return_value=SimpleNamespace(is_premium=True)))
        self.factory.side_effect = [self.factory.return_value, user]
        with patch.object(cli, 'upload', new=AsyncMock(return_value=SimpleNamespace(id=42))) as upload:
            await cli.run(self.args)
        self.assertEqual(self.factory.call_count, 2)
        self.assertIs(upload.call_args.args[0], user)

    async def test_user_client_handles_large_files_with_bot_configured(self):
        self.args.keep = True
        self.enterContext(patch.dict('os.environ', {'USER_SESSION_STRING': 'session'}))
        user = Mock(start=AsyncMock(), stop=AsyncMock(),
                    get_me=AsyncMock(return_value=SimpleNamespace(is_premium=True)))
        self.factory.side_effect = [self.factory.return_value, user]
        with patch.object(cli, 'DEFAULT_SPLIT', 4), patch.object(cli, 'upload',
                new=AsyncMock(return_value=SimpleNamespace(id=42))) as upload:
            await cli.run(self.args)
        self.assertIs(upload.call_args.args[0], user)
