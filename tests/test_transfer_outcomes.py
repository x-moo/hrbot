"""Truthful partial outcomes and acknowledgement-only cleanup."""
import os
import tempfile
import threading
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

from leech import cli
from leech.control import transfer_control, TransferCancelled


class TransferOutcomeTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.directory = Path(self.enterContext(tempfile.TemporaryDirectory()))
        self.files = [self.directory / name for name in ('one.mp3', 'two.mp3', 'three.mp3')]
        for path in self.files:
            path.write_bytes(b'audio')
        self.args = SimpleNamespace(out=str(self.directory), name='one.mp3',
            url='https://example/one.mp3', chat='7', header=[], jobs=4,
            split_size=None, thumb=None, on_line=None, no_split=True,
            dry_run=False, keep=False, as_doc=False)
        self.enterContext(patch.dict(os.environ, {'TELEGRAM_API': '1',
            'TELEGRAM_HASH': 'test', 'BOT_TOKEN': 'test',
            'USER_SESSION_STRING': '', 'KEEP_FILES': '0'}))
        self.client = SimpleNamespace(start=AsyncMock(), stop=AsyncMock(),
                                      disconnect=AsyncMock())
        self.enterContext(patch.object(cli, 'Client', return_value=self.client))
        self.enterContext(patch.object(cli, 'download', return_value=list(map(str, self.files))))

    async def test_second_upload_failure_reports_acknowledged_count_retains_rest(self):
        upload = AsyncMock(side_effect=[SimpleNamespace(id=42), ConnectionError('offline')])
        with patch.object(cli, 'upload', upload):
            with self.assertRaises(Exception) as caught:
                await cli.run(self.args)
        error_type = getattr(cli, 'TransferError', None)
        self.assertIsNotNone(error_type, 'structured transfer error is missing')
        self.assertIsInstance(caught.exception, error_type)
        self.assertEqual(caught.exception.sent, 1)
        self.assertEqual(caught.exception.total, 3)
        self.assertEqual(caught.exception.retained, list(map(str, self.files[1:])))
        self.assertFalse(self.files[0].exists())
        self.assertTrue(all(path.exists() for path in self.files[1:]))
        self.assertEqual(upload.await_count, 2)
        self.client.stop.assert_awaited_once()

    async def test_stubborn_upload_keeps_slot_until_after_client_cleanup_and_exit(self):
        import asyncio
        from leech import session
        cancel = threading.Event()
        started = asyncio.Event()
        finish = asyncio.Event()
        entered = asyncio.Event()
        stopped = asyncio.Event()
        upload_tasks = []

        async def upload(*args, **kwargs):
            upload_tasks.append(asyncio.current_task())
            started.set()
            try:
                await finish.wait()
            except asyncio.CancelledError:
                await finish.wait()  # Emulate slow library cancellation cleanup.

        async def stop():
            self.assertTrue(session._UPLOAD_LOCK.locked())
            await asyncio.sleep(0)
            stopped.set()

        self.client.stop = stop

        async def first():
            with transfer_control(cancel_event=cancel):
                return await cli.run(self.args)

        async def second():
            async with session.upload_slot():
                entered.set()

        with patch.object(cli, 'upload', upload):
            first_task = asyncio.create_task(first())
            await started.wait()
            cancel.set()
            await asyncio.wait({first_task}, timeout=2)
            second_task = asyncio.create_task(second())
            await asyncio.sleep(0.15)
            overlapped = entered.is_set()
            cleanup_completed = stopped.is_set()
            finish.set()
            await asyncio.gather(*upload_tasks, return_exceptions=True)
            await asyncio.wait_for(second_task, 1)
            with self.assertRaises(TransferCancelled):
                await first_task
        self.assertTrue(cleanup_completed)
        self.assertFalse(overlapped, 'slot released while an upload task was still live')
        self.assertFalse(session._UPLOAD_LOCK.locked())

    async def test_oversized_bot_file_is_rejected_before_send(self):
        with patch.object(cli, 'DEFAULT_SPLIT', 4), patch.object(cli, 'upload', new=AsyncMock(
                return_value=SimpleNamespace(id=42))) as upload:
            with self.assertRaisesRegex(cli.TransferError, 'limit') as caught:
                await cli.run(self.args)
        upload.assert_not_awaited()
        self.assertEqual(caught.exception.sent, 0)
        self.assertEqual(caught.exception.retained, list(map(str, self.files)))

    async def test_oversized_nonpremium_user_is_rejected_before_send(self):
        self.enterContext(patch.dict(os.environ, {'USER_SESSION_STRING': 'session'}))
        user = SimpleNamespace(start=AsyncMock(), stop=AsyncMock(),
            get_me=AsyncMock(return_value=SimpleNamespace(is_premium=False)))
        with patch.object(cli, 'Client', side_effect=[self.client, user]), patch.object(
                cli, 'DEFAULT_SPLIT', 4), patch.object(cli, 'upload', new=AsyncMock(
                    return_value=SimpleNamespace(id=42))) as upload:
            with self.assertRaisesRegex(cli.TransferError, 'limit') as caught:
                await cli.run(self.args)
        upload.assert_not_awaited()
        self.assertEqual(caught.exception.sent, 0)
        self.assertEqual(caught.exception.retained, list(map(str, self.files)))

    async def test_small_saved_messages_file_has_no_oversized_warning(self):
        self.args.chat = 'me'
        self.enterContext(patch.dict(os.environ, {'USER_SESSION_STRING': 'session'}))
        user = SimpleNamespace(start=AsyncMock(), stop=AsyncMock(),
            get_me=AsyncMock(return_value=SimpleNamespace(is_premium=False)))
        with patch.object(cli, 'Client', side_effect=[self.client, user]), patch.object(
                cli, 'upload', new=AsyncMock(return_value=SimpleNamespace(id=42))), \
                patch.object(cli, 'log_line') as log:
            await cli.run(self.args)
        self.assertFalse(any('exceeds' in str(call) for call in log.call_args_list))

    async def test_split_progress_failure_retains_the_new_parts(self):
        self.args.no_split = False
        self.enterContext(patch.object(cli, 'download', return_value=[str(self.files[0])]))
        parts = [str(self.files[0]) + '.001', str(self.files[0]) + '.002']

        def split(*args):
            for part in parts:
                Path(part).write_bytes(b'data')
            self.files[0].unlink()
            return parts

        def progress(line):
            if line == '2 parts ready':
                raise RuntimeError('progress unavailable')

        self.args.on_line = progress
        with patch.object(cli, 'split_file', split):
            with self.assertRaises(cli.TransferError) as caught:
                await cli.run(self.args)
        self.assertEqual(caught.exception.sent, 0)
        self.assertEqual(caught.exception.total, 2)
        self.assertEqual(caught.exception.retained, parts)

    async def test_clients_do_not_hide_floodwait_retries(self):
        with patch.object(cli, 'Client', return_value=self.client) as factory, patch.object(
                cli, 'upload', new=AsyncMock(return_value=SimpleNamespace(id=42))):
            await cli.run(self.args)
        self.assertEqual(factory.call_args.kwargs.get('sleep_threshold'), 0,
                         'transfer layer must own FloodWait sleep/retry policy')

    async def test_pre_cancelled_transfer_never_downloads_or_creates_client(self):
        cancel = threading.Event()
        cancel.set()
        with patch.object(cli, 'download') as download, patch.object(cli, 'Client') as client, \
                transfer_control(cancel_event=cancel):
            with self.assertRaises(TransferCancelled):
                await cli.run(self.args)
        download.assert_not_called()
        client.assert_not_called()

    async def test_success_does_not_delete_unowned_sidecars(self):
        sidecar = Path(str(self.files[0]) + '.part')
        sidecar.write_bytes(b'another download')
        with patch.object(cli, 'upload', new=AsyncMock(return_value=SimpleNamespace(id=42))):
            result = await cli.run(self.args)
        self.assertEqual(result, list(map(str, self.files)))
        self.assertTrue(sidecar.exists(), 'cleanup removed an unowned partial file')
        self.assertEqual(sidecar.read_bytes(), b'another download')

    async def test_cancel_after_first_ack_reports_partial_without_sending_next(self):
        cancel = threading.Event()

        def progress(line):
            if line.startswith('sent 1/3'):
                cancel.set()

        self.args.on_line = progress
        upload = AsyncMock(return_value=SimpleNamespace(id=42))
        with patch.object(cli, 'upload', upload), transfer_control(cancel_event=cancel):
            with self.assertRaises(TransferCancelled) as caught:
                await cli.run(self.args)
        self.assertEqual(getattr(caught.exception, 'sent', None), 1,
                         'cancellation must expose confirmed partial delivery')
        self.assertEqual(caught.exception.total, 3)
        self.assertEqual(caught.exception.retained, list(map(str, self.files[1:])))
        self.assertEqual(upload.await_count, 1)
        self.assertFalse(self.files[0].exists())
        self.assertTrue(all(path.exists() for path in self.files[1:]))

    async def test_real_transfer_reports_stages_returns_paths_with_keep(self):
        from leech.downloader import download
        self.args.name = 'download.mp3'
        self.args.no_split = False
        self.args.split_size = '4'
        self.args.keep = True
        stages = []
        response = Mock()
        response.headers = {'Content-Length': '6'}
        response.__enter__ = Mock(return_value=response)
        response.__exit__ = Mock(return_value=False)
        response.read.side_effect = [b'abcdef', b'']
        with patch.object(cli, 'download', download), patch(
                'urllib.request.OpenerDirector.open', return_value=response), patch.object(
                cli, 'upload', new=AsyncMock(return_value=SimpleNamespace(id=42))), \
                transfer_control(on_stage=stages.append):
            result = await cli.run(self.args)
        self.assertEqual(stages, ['downloading', 'splitting', 'waiting_upload', 'uploading'])
        self.assertEqual(result, [str(self.directory / 'download.mp3.001'),
                                  str(self.directory / 'download.mp3.002')])
        self.assertEqual(b''.join(Path(path).read_bytes() for path in result), b'abcdef')

    async def test_cancel_during_client_start_stops_client_before_releasing_slot(self):
        import asyncio
        from leech import session
        cancel = threading.Event()
        started = asyncio.Event()
        closed = asyncio.Event()

        async def start():
            started.set()
            await asyncio.Event().wait()

        async def stop():
            self.assertTrue(session._UPLOAD_LOCK.locked())
            closed.set()

        self.client.start = start
        self.client.stop = stop

        async def sending():
            with transfer_control(cancel_event=cancel):
                return await cli.run(self.args)

        task = asyncio.create_task(sending())
        await started.wait()
        cancel.set()
        done, _ = await asyncio.wait({task}, timeout=0.5)
        if not done:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
        self.assertTrue(done, 'cancellation did not interrupt session startup')
        with self.assertRaises(TransferCancelled) as caught:
            await task
        self.assertEqual(caught.exception.sent, 0)
        self.assertEqual(caught.exception.retained, list(map(str, self.files)))
        self.assertTrue(closed.is_set())
        self.assertFalse(session._UPLOAD_LOCK.locked())

    async def test_teardown_failure_does_not_mask_partial_delivery_error(self):
        self.client.stop.side_effect = RuntimeError('stop failed')
        self.client.disconnect.side_effect = RuntimeError('disconnect failed')
        with patch.object(cli, 'upload', new=AsyncMock(side_effect=[
                SimpleNamespace(id=42), RuntimeError('upload rejected')])):
            with self.assertRaisesRegex(cli.TransferError, 'upload rejected') as caught:
                await cli.run(self.args)
        self.assertEqual(caught.exception.sent, 1)
        self.assertEqual(caught.exception.total, 3)
        self.assertEqual(caught.exception.retained, list(map(str, self.files[1:])))
