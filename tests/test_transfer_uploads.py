"""Only explicit server rejections may trigger another send."""
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from leech import cli


class TransferUploadTests(unittest.IsolatedAsyncioTestCase):
    async def test_repeated_floodwait_stops_after_three_retries(self):
        client = SimpleNamespace(send_audio=AsyncMock(side_effect=[
            cli.FloodWait(1), cli.FloodWait(1), cli.FloodWait(1), cli.FloodWait(1),
            SimpleNamespace(id=42)]))
        with patch.object(cli.asyncio, 'sleep', new=AsyncMock()) as sleep:
            with self.assertRaisesRegex(RuntimeError, 'FloodWait'):
                await cli.upload(client, 7, 'file.mp3')
        self.assertEqual(client.send_audio.await_count, 4)
        self.assertEqual(sleep.await_count, 3)

    async def test_excessive_floodwait_is_not_slept_or_retried(self):
        client = SimpleNamespace(send_audio=AsyncMock(side_effect=[
            cli.FloodWait(3600), SimpleNamespace(id=42)]))
        with patch.object(cli.asyncio, 'sleep', new=AsyncMock()) as sleep:
            with self.assertRaisesRegex(RuntimeError, 'FloodWait'):
                await cli.upload(client, 7, 'file.mp3')
        self.assertEqual(client.send_audio.await_count, 1)
        sleep.assert_not_awaited()

    async def test_filename_caption_cannot_inject_html(self):
        client = SimpleNamespace(send_audio=AsyncMock(return_value=SimpleNamespace(id=42)))
        await cli.upload(client, 7, '/tmp/<b>title & audio.mp3')
        self.assertEqual(client.send_audio.call_args.kwargs['caption'],
                         '&lt;b&gt;title &amp; audio.mp3')

    async def test_explicit_media_rejection_keeps_document_fallback(self):
        from pyrogram.errors import MediaInvalid
        client = SimpleNamespace(send_audio=AsyncMock(side_effect=MediaInvalid()),
            send_document=AsyncMock(return_value=SimpleNamespace(id=42)))
        result = await cli.upload(client, 7, 'file.mp3')
        self.assertEqual(result.id, 42)
        client.send_audio.assert_awaited_once()
        client.send_document.assert_awaited_once()

    async def test_ambiguous_rpc_error_mentioning_media_is_not_resent(self):
        from pyrogram.errors import InternalServerError
        client = SimpleNamespace(send_audio=AsyncMock(
            side_effect=InternalServerError('MEDIA transport failure')),
            send_document=AsyncMock(return_value=SimpleNamespace(id=42)))
        with self.assertRaises(InternalServerError):
            await cli.upload(client, 7, 'file.mp3')
        client.send_audio.assert_awaited_once()
        client.send_document.assert_not_awaited()

    async def test_cancellation_interrupts_floodwait_sleep(self):
        import asyncio
        import threading
        from leech.control import TransferCancelled, transfer_control
        cancel = threading.Event()
        rejected = asyncio.Event()

        async def send(*args, **kwargs):
            rejected.set()
            raise cli.FloodWait(60)

        client = SimpleNamespace(send_audio=send)

        async def sending():
            with transfer_control(cancel_event=cancel):
                return await cli.upload(client, 7, 'file.mp3')

        task = asyncio.create_task(sending())
        await rejected.wait()
        cancel.set()
        done, _ = await asyncio.wait({task}, timeout=0.5)
        if not done:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
        self.assertTrue(done, 'cancellation did not interrupt FloodWait sleep')
        with self.assertRaises(TransferCancelled):
            await task
