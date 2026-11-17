"""MTProto lifecycle regression tests without Telegram credentials."""
import asyncio
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch
from leech import cli

ROOT = Path(__file__).resolve().parents[1]


class SessionTests(unittest.IsolatedAsyncioTestCase):
    async def test_partial_start_disconnects_before_releasing_slot(self):
        from contextlib import asynccontextmanager
        from pyrogram.methods.utilities.stop import Stop
        from pyrogram.methods.auth.disconnect import Disconnect

        events = []
        client = SimpleNamespace(is_connected=False, is_initialized=False,
            session=SimpleNamespace(stop=AsyncMock()),
            storage=SimpleNamespace(close=AsyncMock()))

        async def start():
            client.is_connected = True
            raise RuntimeError('startup failed')

        async def terminate(**kwargs):
            raise ConnectionError('Client is already terminated')

        async def stop():
            events.append('stop')
            await Stop.stop(client)

        async def disconnect():
            events.append('disconnect')
            await Disconnect.disconnect(client)

        client.start = start
        client.stop = stop
        client.terminate = terminate
        client.disconnect = disconnect

        @asynccontextmanager
        async def slot():
            try:
                yield
            finally:
                self.assertFalse(client.is_connected)
                events.append('released')

        (ROOT / '.cache').mkdir(exist_ok=True)
        with tempfile.TemporaryDirectory(dir=ROOT / '.cache') as directory:
            source = Path(directory) / 'test.mp3'
            source.write_bytes(b'audio')
            args = SimpleNamespace(out=directory, name='test.mp3', url='https://example/test.mp3',
                chat='7', header=[], jobs=4, split_size=None, thumb=None, on_line=None,
                no_split=True, dry_run=False, keep=False, as_doc=False)
            with patch.dict('os.environ', {'TELEGRAM_API': '1', 'TELEGRAM_HASH': 'test',
                    'BOT_TOKEN': 'test', 'USER_SESSION_STRING': ''}), \
                    patch.object(cli, 'Client', return_value=client), \
                    patch.object(cli, 'download', return_value=[str(source)]), \
                    patch.object(cli, 'upload_slot', slot):
                with self.assertRaisesRegex(RuntimeError, 'startup failed'):
                    await cli.run(args)
            self.assertTrue(source.exists())
        self.assertEqual(events, ['stop', 'disconnect', 'released'])

    async def test_teardown_failure_is_reported(self):
        client = Mock(stop=AsyncMock(side_effect=RuntimeError('stop failed')),
                      disconnect=AsyncMock(side_effect=RuntimeError('disconnect failed')),
                      is_connected=True)
        with patch.object(cli, 'log_line') as log:
            with self.assertRaisesRegex(RuntimeError, 'disconnect'):
                await cli._stop_client(client)
        self.assertTrue(log.called)

    async def test_bot_uses_isolated_session_and_preserves_failed_file(self):
        with tempfile.TemporaryDirectory(dir=ROOT / '.cache') as directory:
            source = Path(directory) / 'test.mp3'
            source.write_bytes(b'audio')
            args = SimpleNamespace(out=directory, name='test.mp3', url='https://example/test.mp3',
                chat='7', header=[], jobs=4, split_size=None, thumb=None, on_line=None,
                no_split=True, dry_run=False, keep=False, as_doc=False)
            client = Mock()
            client.start = AsyncMock()
            client.stop = AsyncMock()
            with patch.dict('os.environ', {'TELEGRAM_API': '1', 'TELEGRAM_HASH': 'test',
                    'BOT_TOKEN': 'test', 'USER_SESSION_STRING': ''}), patch.object(cli, 'Client', return_value=client) as factory, patch.object(cli, 'download', return_value=[str(source)]), patch.object(cli, 'upload', new=AsyncMock(side_effect=TimeoutError('connection lost'))):
                with self.assertRaisesRegex(RuntimeError, 'timed out'):
                    await cli.run(args)
            self.assertTrue(source.exists())
            self.assertTrue(factory.call_args.kwargs.get('in_memory'))
            self.assertTrue(factory.call_args.kwargs.get('no_updates'))
            client.stop.assert_awaited_once()

    async def test_upload_timeout_is_bounded(self):
        async def stuck(*args, **kwargs):
            await asyncio.Event().wait()
        with patch.object(cli, 'upload', new=stuck):
            with self.assertRaisesRegex(RuntimeError, 'timed out'):
                await cli.upload_with_timeout(Mock(), 7, 'test.mp3', timeout=0.01)

    async def test_upload_queue_serializes_jobs(self):
        from leech.session import upload_slot
        active = 0
        peak = 0
        async def job():
            nonlocal active, peak
            async with upload_slot():
                active += 1
                peak = max(peak, active)
                await asyncio.sleep(0.01)
                active -= 1
        await asyncio.gather(job(), job())
        self.assertEqual(peak, 1)
