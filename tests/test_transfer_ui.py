"""Transfer presentation stays localized without changing lifecycle or raw data."""
from contextlib import contextmanager
from pathlib import Path
import unittest
from unittest.mock import Mock, patch

from bot.downloads import DownloadService, progress_text
from bot.i18n import language
from bot.jobs import JobRegistry, JobState


@contextmanager
def locale(code):
    token = language.set(code)
    try:
        yield
    finally:
        language.reset(token)


class TransferUiTests(unittest.TestCase):
    def setUp(self):
        self.registry = JobRegistry()
        self.api = Mock(return_value={'message_id': 3})
        self.notify = Mock()
        self.service = DownloadService(
            registry=self.registry, executor=lambda: Mock(), api=self.api,
            notify=self.notify, root=lambda: Path('.'))
        self.job = self.registry.add(
            token='token', chat=7, url='https://latest.example/Latest_Download.mp3')

    def test_status_stages_and_buttons_follow_chat_language(self):
        labels = {
            'id': ['Dalam antrean', 'Mengunduh', 'Membagi berkas',
                   'Menunggu giliran unggah', 'Mengunggah', 'Selesai', 'Gagal', 'Dibatalkan'],
            'en': ['Queued', 'Downloading', 'Splitting files',
                   'Waiting to upload', 'Uploading', 'Completed', 'Failed', 'Cancelled'],
        }
        stages = ['queued', 'downloading', 'splitting', 'waiting_upload',
                  'uploading', 'succeeded', 'failed', 'cancelled']
        for code, expected in labels.items():
            with self.subTest(code=code), locale(code):
                self.job.state, self.job.stage = JobState.QUEUED, 'queued'
                buttons = self.service.markup(self.job)['inline_keyboard'][0]
                self.assertEqual([button['text'] for button in buttons],
                                 ['Batalkan', 'Lihat status'] if code == 'id'
                                 else ['Cancel', 'Status'])
                self.assertEqual([button['callback_data'] for button in buttons],
                                 ['cancel:1', 'status:1'])
                for stage, label in zip(stages, expected):
                    self.job.state = JobState.DOWNLOADING if stage == 'splitting' else JobState(stage)
                    self.job.stage = stage
                    self.assertIn(f' · {label} · ', self.service.text(self.job))
                    self.assertNotIn('waiting_upload', self.service.text(self.job))
                self.assertEqual(self.service.markup(self.job), {'inline_keyboard': []})

    def test_splitting_label_does_not_change_download_admission_state(self):
        self.registry.begin(self.job)
        self.registry.stage(self.job, 'splitting')
        with locale('id'):
            self.assertIn('Membagi berkas', self.service.text(self.job))
        self.assertIs(self.job.state, JobState.DOWNLOADING)
        self.assertEqual(self.registry.counts(), {'downloading': 1})

    def test_control_usage_missing_and_empty_responses_are_bilingual(self):
        for code, usage, missing, empty in [
                ('id', 'Gunakan: /cancel <id>', 'Unduhan tidak ditemukan di chat ini.',
                 'Tidak ada unduhan dalam antrean atau sedang berjalan.'),
                ('en', 'Usage: /cancel <id>', 'Download not found in this chat.',
                 'No queued or active downloads.')]:
            with self.subTest(code=code), locale(code):
                self.assertEqual(self.service.control('cancel', 7, 'invalid'), usage)
                self.assertEqual(self.service.control('status', 8, 1), missing)
                self.assertEqual(self.service.control('status', 8), empty)
                self.assertIn('#1', self.service.control('status', 7))
                self.service.command('token', 7, 'cancel', [])
                self.assertEqual(self.api.call_args.args[2]['text'], usage)
                self.assertNotIn('parse_mode', self.api.call_args.args[2])
                self.assertEqual(self.api.call_args.kwargs['tries'], 0)
        self.assertFalse(self.job.cancel_event.is_set())

    def test_running_cancel_ack_describes_a_request_until_worker_acknowledges(self):
        self.registry.begin(self.job)
        for code, requested in [('id', 'Permintaan pembatalan diterima'),
                                ('en', 'Cancellation requested')]:
            with self.subTest(code=code), locale(code):
                text = self.service.control('cancel', 7, 1)
                self.assertIn(requested, text)
                self.assertNotIn('Dibatalkan', text)
                self.assertNotIn('Cancelled', text)
                self.assertTrue(self.job.cancel_event.is_set())
                self.assertIs(self.job.state, JobState.DOWNLOADING)
                self.assertEqual(self.registry.counts(), {'downloading': 1})

    def test_duplicate_rejection_is_localized_without_starting_work(self):
        for code, expected in [('id', 'Rekaman ini sudah dalam antrean.'),
                               ('en', 'This recording is already queued.')]:
            with self.subTest(code=code), locale(code):
                text, started = self.service.start(
                    'token', 7, self.job.url, None, None, None, False, False)
                self.assertFalse(started)
                self.assertEqual(text, expected)
                self.assertEqual(self.registry.counts(), {'queued': 1})
        self.api.assert_not_called()

    def test_progress_is_presented_bilingually_and_raw_history_is_unchanged(self):
        cases = [
            ('python: downloading', 'Mengunduh', 'Downloading'),
            ('downloading... 42%', 'Mengunduh… 42%', 'Downloading… 42%'),
            ('downloaded 3.4 MB', 'Terunduh 3.4 MB', 'Downloaded 3.4 MB'),
            ('downloaded 3.4 MB in 2.1s', 'Terunduh 3.4 MB dalam 2.1 dtk',
             'Downloaded 3.4 MB in 2.1s'),
            ('splitting 30 MB into 10 MB parts',
             'Membagi 30 MB menjadi bagian berukuran 10 MB',
             'Splitting 30 MB into 10 MB parts'),
            ('3 parts ready', '3 bagian siap', '3 parts ready'),
            ('waiting for upload slot', 'Menunggu giliran unggah', 'Waiting to upload'),
            ('uploading 1/2 · Latest_Download.mp3 · 3.4 MB · audio',
             'Mengunggah 1/2 · Latest_Download.mp3 · 3.4 MB · Audio',
             'Uploading 1/2 · Latest_Download.mp3 · 3.4 MB · Audio'),
            ('sent 1/2 ✓', 'Terkirim 1/2 ✓', 'Sent 1/2 ✓'),
            ('storage freed: 3.4 MB', 'Penyimpanan sementara dibersihkan · 3.4 MB',
             'Temporary storage cleared · 3.4 MB'),
            ('done: 2 file(s) in 4.2s', 'Berkas terkirim: 2 · 4.2s',
             'Files sent: 2 · 4.2s'),
        ]
        self.job.lines = [raw for raw, _, _ in cases]
        original = list(self.job.lines)
        for code, column in [('id', 1), ('en', 2)]:
            with self.subTest(code=code), locale(code):
                for case in cases:
                    self.assertEqual(progress_text(case[0]), case[column])
                self.assertIn('Latest_Download.mp3', self.service.text(self.job))
                self.assertEqual(progress_text('Latest.mp3'), 'Latest.mp3')
                self.assertEqual(self.service._size(self.job), '3.4 MB')
                self.assertEqual(self.job.lines, original)

    def test_status_budget_keeps_latest_progress_and_valid_utf16(self):
        self.job.lines = [f'old-{index} ' + '😀' * 480 for index in range(9)] + ['sent 2/2 ✓']
        original = list(self.job.lines)
        for code, newest in [('id', 'Terkirim 2/2 ✓'), ('en', 'Sent 2/2 ✓')]:
            with self.subTest(code=code), locale(code):
                text = self.service.text(self.job)
                self.assertLessEqual(len(text.encode('utf-16-le')) // 2, 3900)
                self.assertTrue(text.endswith(newest))
                self.assertIn('old-8', text)
                self.assertNotIn('old-0', text)
                self.assertLess(text.index('old-8'), text.index(newest))
                self.assertIn('Latest_Download.mp3', text.split('\n', 1)[0])
                self.assertEqual(self.job.lines, original)

    def test_queued_cancel_receipt_does_not_claim_any_retained_files(self):
        self.registry.cancel(1, chat=7)
        with locale('id'):
            self.service.finish(self.job)
        self.assertEqual(self.notify.call_args.args[2], ['🚫 #1 · Dibatalkan'])
        self.assertEqual(self.registry.counts(), {})
        self.service.finish(self.job)
        self.notify.assert_called_once()

    def test_queued_cancel_worker_keeps_the_request_language(self):
        executor, runner = Mock(), Mock()
        self.service.executor = lambda: executor
        with locale('en'):
            _, started = self.service.start(
                'token', 8, 'https://example/cancel.mp3', None, None, None,
                False, False, runner=runner)
            self.service.cancel(2, chat=8)
        self.assertTrue(started)
        # A worker's ambient/default language must not choose the receipt language.
        with locale('id'):
            executor.submit.call_args.args[0]()
        runner.assert_not_called()
        self.assertEqual(self.notify.call_args.args[2], ['🚫 #2 · Cancelled'])
        self.assertTrue(self.registry.get(2, chat=8).finalized)

    def test_worker_error_receipt_keeps_the_originating_language(self):
        executor = Mock()
        self.service.executor = lambda: executor
        runner = Mock(side_effect=RuntimeError('internal path'))
        with locale('en'):
            self.service.start('token', 8, 'https://example/fail.mp3', None,
                               None, None, False, False, runner=runner)
        with locale('id'):
            executor.submit.call_args.args[0]()
        runner.assert_called_once()
        self.assertEqual(self.notify.call_args.args[2], [
            '❌ #2 · Failed', 'Download failed. Please try again later.'])
        self.assertTrue(self.registry.get(2, chat=8).finalized)

    def test_partial_receipt_keeps_ack_counts_and_original_basename_in_both_languages(self):
        self.job.error = 'TransferError'
        self.job.sent, self.job.total = 1, 2
        self.job.retained = ['/srv/private/Latest_Download.mp3']
        self.registry.complete(self.job, JobState.FAILED)
        for code, failure, confirmed, kept in [
                ('id', 'Unduhan gagal. Coba lagi nanti.', 'Terkonfirmasi terkirim: 1/2',
                 'Berkas disimpan di server: Latest_Download.mp3'),
                ('en', 'Download failed. Please try again later.', 'Confirmed sent: 1/2',
                 'Files kept on server: Latest_Download.mp3')]:
            with self.subTest(code=code), locale(code):
                lines = self.service.receipt_lines(self.job)
                self.assertIn(failure, lines)
                self.assertIn(confirmed, lines)
                self.assertIn(kept, lines)
                self.assertNotIn('/srv/private', '\n'.join(lines))
                self.assertNotIn('TransferError', '\n'.join(lines))
                self.assertEqual((self.job.sent, self.job.total), (1, 2))
                self.assertEqual(self.job.error, 'TransferError')
                self.assertEqual(self.job.retained, ['/srv/private/Latest_Download.mp3'])

    def test_actual_card_wiring_does_not_translate_filename_twice(self):
        from bot import app, telegram
        for code in ('id', 'en'):
            with self.subTest(code=code), locale(code), \
                    patch.object(app, '_DL_POOL', {}), \
                    patch.object(app, '_DOWNLOAD_SERVICE', None), \
                    patch.object(app, 'tg_call', Mock()), \
                    patch.object(telegram, '_send_one') as sent:
                service = app._download_service()
                job = service.registry.add(token='token', chat=7, url='https://latest.example/a')
                job.files = ['Latest.mp3', 'Download.mp3']
                service.registry.complete(job, JobState.SUCCEEDED)
                service.finish(job)
                text = '\n'.join(call.args[2] for call in sent.call_args_list)
                self.assertIn('Latest.mp3', text)
                self.assertIn('Download.mp3', text)
                self.assertIn('latest.example', text)
                self.assertNotIn('Terbaru.mp3', text)
                self.assertNotIn('Unduh.mp3', text)
                self.assertIn('Berkas terkirim: 2' if code == 'id' else 'Files sent: 2', text)

    def test_actual_partial_receipt_wiring_preserves_filename_and_escapes_markdown(self):
        from bot import app, telegram
        for code in ('id', 'en'):
            with self.subTest(code=code), locale(code), \
                    patch.object(app, '_DL_POOL', {}), \
                    patch.object(app, '_DOWNLOAD_SERVICE', None), \
                    patch.object(app, 'tg_call', Mock()), \
                    patch.object(telegram, '_send_one') as sent:
                service = app._download_service()
                job = service.registry.add(token='token', chat=7, url='https://example/a')
                job.error, job.sent, job.total = 'TransferError', 1, 2
                job.retained = ['/srv/private/Latest_Download[1].mp3']
                service.registry.complete(job, JobState.FAILED)
                service.finish(job)
                text = '\n'.join(call.args[2] for call in sent.call_args_list)
                self.assertIn('Latest\\_Download\\[1\\].mp3', text)
                self.assertIn('1/2', text)
                self.assertNotIn('/srv/private', text)
                self.assertNotIn('TransferError', text)
                self.assertNotIn('Terbaru', text)
                self.assertNotIn('Unduh\\[1', text)


if __name__ == '__main__':
    unittest.main()
