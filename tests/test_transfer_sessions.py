"""Upload queue and bounded client-wait cancellation regressions."""
import asyncio
import threading
import unittest
from unittest.mock import patch

from leech import session
from leech.control import TransferCancelled, transfer_control


class TransferSessionTests(unittest.IsolatedAsyncioTestCase):
    async def test_cancelled_upload_slot_wait_does_not_acquire_or_leak_lock(self):
        lock = threading.Lock()
        lock.acquire()
        cancel = threading.Event()
        stages = []

        async def waiting():
            with transfer_control(cancel_event=cancel, on_stage=stages.append):
                async with session.upload_slot():
                    self.fail('cancelled waiter entered the upload slot')

        with patch.object(session, '_UPLOAD_LOCK', lock):
            task = asyncio.create_task(waiting())
            await asyncio.sleep(0)
            cancel.set()
            done, _ = await asyncio.wait({task}, timeout=0.5)
            if not done:
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)
            self.assertTrue(done, 'event cancellation did not interrupt slot wait')
            with self.assertRaises(TransferCancelled):
                await task
            self.assertTrue(lock.locked())
            lock.release()
            async with session.upload_slot():
                self.assertTrue(lock.locked())
            self.assertFalse(lock.locked())
        self.assertEqual(stages, ['waiting_upload'])

    async def test_event_cancels_inflight_upload_without_resend(self):
        from leech import cli
        cancel = threading.Event()
        started = asyncio.Event()
        stopped = asyncio.Event()
        calls = []

        async def stuck(*args, **kwargs):
            calls.append(args)
            started.set()
            try:
                await asyncio.Event().wait()
            finally:
                stopped.set()

        async def sending():
            with transfer_control(cancel_event=cancel):
                return await cli.upload_with_timeout(None, 7, 'file.mp3', timeout=2)

        with patch.object(cli, 'upload', stuck):
            task = asyncio.create_task(sending())
            await started.wait()
            cancel.set()
            done, _ = await asyncio.wait({task}, timeout=0.5)
            if not done:
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)
            self.assertTrue(done, 'event cancellation did not interrupt upload')
            with self.assertRaises(TransferCancelled):
                await task
        self.assertTrue(stopped.is_set())
        self.assertEqual(len(calls), 1)

    async def test_upload_ack_wins_simultaneous_cancel_for_truthful_count(self):
        from leech import cli
        from types import SimpleNamespace
        cancel = threading.Event()

        async def confirmed(*args, **kwargs):
            cancel.set()
            return SimpleNamespace(id=42)

        with patch.object(cli, 'upload', confirmed), transfer_control(cancel_event=cancel):
            result = await cli.upload_with_timeout(None, 7, 'file.mp3', timeout=1)
        self.assertEqual(result.id, 42)

    async def test_unconfirmed_upload_does_not_retry_on_connection_loss(self):
        from leech import cli
        from unittest.mock import AsyncMock
        upload = AsyncMock(side_effect=ConnectionError('delivery status unknown'))
        with patch.object(cli, 'upload', upload):
            with self.assertRaises(ConnectionError):
                await cli.upload_with_timeout(None, 7, 'file.mp3', timeout=1)
        upload.assert_awaited_once()
