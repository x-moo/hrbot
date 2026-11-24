"""Cancellation keeps source data and removes only owned partial output."""
import io
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from leech import downloader
from leech.control import TransferCancelled, transfer_control


class TransferDownloadTests(unittest.TestCase):
    def test_cancellation_during_download_removes_owned_partial(self):
        cancel = threading.Event()
        response = Mock()
        response.headers = {}
        response.__enter__ = Mock(return_value=response)
        response.__exit__ = Mock(return_value=False)

        chunks = iter([b'first', b'payload', b''])

        def read(size):
            cancel.set()
            return next(chunks)

        response.read.side_effect = read
        with tempfile.TemporaryDirectory() as directory:
            stages = []
            with patch('urllib.request.OpenerDirector.open', return_value=response), \
                    transfer_control(cancel_event=cancel, on_stage=stages.append):
                with self.assertRaises(TransferCancelled):
                    downloader.download_python('https://example/file', directory, 'file', [])
            self.assertEqual(list(Path(directory).iterdir()), [])
            self.assertEqual(stages, ['downloading'])

    def test_failed_split_source_cleanup_rolls_back_parts(self):
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / 'source'
            source.write_bytes(b'0123456789')
            original_unlink = Path.unlink

            def unlink(path, *args, **kwargs):
                if path == source:
                    raise PermissionError('source is busy')
                return original_unlink(path, *args, **kwargs)

            with patch.object(Path, 'unlink', unlink):
                with self.assertRaises(PermissionError):
                    downloader.split_file(source, 4)
            self.assertEqual(list(Path(directory).iterdir()), [source])
            self.assertEqual(source.read_bytes(), b'0123456789')

    def test_cancelled_raw_reader_stops_before_socket_read(self):
        import time
        from leech.network import DeadlineFile
        cancel = threading.Event()
        cancel.set()
        raw = Mock()
        raw.readinto.return_value = 1
        stream = Mock()
        with DeadlineFile(raw, stream, time.monotonic() + 60) as reader:
            with transfer_control(cancel_event=cancel):
                with self.assertRaises(TransferCancelled):
                    reader.readinto(bytearray(1))
        raw.readinto.assert_not_called()

    def test_cancellation_mid_split_keeps_source_removes_new_parts(self):
        cancel = threading.Event()
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / 'source'
            source.write_bytes(b'0123456789')
            original_open = Path.open

            class CancellingReader(io.BytesIO):
                def read(self, size=-1):
                    data = super().read(size)
                    cancel.set()
                    return data

            def opened(path, *args, **kwargs):
                if path == source and args == ('rb',):
                    return CancellingReader(b'0123456789')
                return original_open(path, *args, **kwargs)

            stages = []
            with patch.object(Path, 'open', opened), transfer_control(
                    cancel_event=cancel, on_stage=stages.append):
                with self.assertRaises(TransferCancelled):
                    downloader.split_file(source, 4)
            self.assertEqual(source.read_bytes(), b'0123456789')
            self.assertEqual(list(Path(directory).iterdir()), [source])
            self.assertEqual(stages, ['splitting'])
