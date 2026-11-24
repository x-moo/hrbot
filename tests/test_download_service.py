"""Real bounded service behavior with deterministic worker scheduling."""
import importlib
import os
import tempfile
import threading
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest.mock import Mock, patch


class HeldExecutor:
    def __init__(self):
        self.tasks = []

    def submit(self, function, *args):
        self.tasks.append((function, args))
        return Mock()

    def run_next(self):
        function, args = self.tasks.pop(0)
        function(*args)


class DownloadServiceTests(unittest.TestCase):
    def setUp(self):
        self.module = importlib.import_module('bot.downloads')
        self.jobs = importlib.import_module('bot.jobs')
        self.registry = self.jobs.JobRegistry()
        self.executor = HeldExecutor()
        self.api = Mock(return_value={'message_id': 3})
        self.notify = Mock()
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.service = self.module.DownloadService(
            registry=self.registry, executor=lambda: self.executor,
            api=lambda *args, **kw: self.api(*args, **kw),
            notify=self.notify, root=lambda: Path(self.temp.name))
        env = patch.dict(os.environ, {'DOWNLOAD_DIR': '', 'LEECH_QUEUE_SIZE': '10'})
        env.start()
        self.addCleanup(env.stop)

    def start(self, chat=7, url='https://example.org/a', **options):
        return self.service.start('token', chat, url, None, None, None, False, False,
                                  **options)

    def test_start_posts_truthful_queued_status_and_dispatches_two_workers(self):
        for chat in range(3):
            note, started = self.start(chat, f'https://example.org/{chat}')
            self.assertTrue(started)
            self.assertIn('queued', note)
        self.assertEqual(self.registry.counts(), {'queued': 3})
        self.assertEqual(len(self.executor.tasks), 2)
        payload = self.api.call_args_list[0].args[2]
        self.assertIn('queued', payload['text'])
        self.assertNotIn('parse_mode', payload)
        buttons = payload['reply_markup']['inline_keyboard'][0]
        self.assertEqual(buttons[0]['callback_data'], 'cancel:1')
        self.assertEqual(buttons[1]['callback_data'], 'status:1')

    def test_cancel_queued_removes_pending_without_polling_network_calls(self):
        for index in range(3):
            self.start(index, f'https://example.org/{index}')
        job = self.registry.get(3, chat=2)
        before = self.api.call_count
        with patch('time.sleep', side_effect=AssertionError('polling must not sleep')):
            self.assertIsNone(self.service.cancel(3, chat=99))
            self.assertIs(self.service.cancel(3, chat=2), job)
        self.assertEqual(self.api.call_count, before)
        self.assertIs(job.state, self.jobs.JobState.CANCELLED)
        self.assertNotIn(3, [entry[0].id for entry in self.service._pending])
        self.assertEqual(self.registry.counts(), {'queued': 2})

    def test_status_edits_are_plain_diffed_and_throttled_by_monotonic_time(self):
        with patch.object(self.module.time, 'monotonic', return_value=10):
            self.start()
        job = self.registry.get(1, chat=7)
        self.api.reset_mock()
        with patch.object(self.module.time, 'monotonic', return_value=10):
            self.service.status(job)
            job.lines.append('untrusted ``` [name] <tag>')
            self.service.status(job)
        self.api.assert_not_called()
        with patch.object(self.module.time, 'monotonic', return_value=13):
            self.service.status(job)
            self.service.status(job)
        self.api.assert_called_once()
        self.assertEqual(self.api.call_args.kwargs['tries'], 0)
        payload = self.api.call_args.args[2]
        self.assertNotIn('parse_mode', payload)
        self.assertIn('untrusted ``` [name] <tag>', payload['text'])
        self.assertLessEqual(len(payload['text'].encode('utf-16-le')) // 2, 4000)

    def test_status_edit_failure_is_best_effort_and_throttles_retry(self):
        with patch.object(self.module.time, 'monotonic', return_value=10):
            self.start()
        job = self.registry.get(1, chat=7)
        job.lines.append('changed')
        self.api.reset_mock()
        self.api.side_effect = RuntimeError('flood wait')
        with patch.object(self.module.time, 'monotonic', return_value=13):
            self.service.status(job)
        with patch.object(self.module.time, 'monotonic', return_value=14):
            self.service.status(job)
        self.api.assert_called_once()
        self.assertEqual(self.registry.counts(), {'queued': 1})

    def test_cancel_before_first_status_ack_does_not_enqueue_cancelled_job(self):
        def status_ack(*args, **kwargs):
            self.service.cancel(1, chat=7)
            return {'message_id': 3}
        self.api.side_effect = status_ack
        note, started = self.start()
        self.assertFalse(started)
        self.assertIn('cancelled', note)
        self.assertFalse(self.service._pending)
        self.assertEqual(self.registry.counts(), {})

    def test_cancellation_ack_is_not_lost_if_worker_wins_begin_race(self):
        from leech import cli
        self.start()
        begin = self.registry.begin
        def cancelled_begin(job):
            self.service.cancel(job.id, chat=job.chat)
            return begin(job)
        with patch.object(self.registry, 'begin', side_effect=cancelled_begin), \
                patch.object(cli, 'run_leech_sync') as transfer:
            self.executor.run_next()
        transfer.assert_not_called()
        self.assertEqual(self.registry.counts(), {})
        self.assertEqual(self.service._workers, 0)

    def test_initial_status_failure_releases_admission(self):
        self.api.side_effect = RuntimeError('offline')
        note, started = self.start()
        self.assertFalse(started)
        self.assertIn('cannot post status', note)
        self.assertEqual(self.registry.counts(), {})

    def test_executor_failure_releases_admission_and_allows_next_dispatch(self):
        self.executor.submit = Mock(side_effect=RuntimeError('shutdown'))
        note, started = self.start()
        self.assertFalse(started)
        self.assertIn('unavailable', note)
        self.assertEqual(self.registry.counts(), {})
        self.assertEqual(self.service._workers, 0)
        self.assertIs(self.registry.get(1, chat=7).state, self.jobs.JobState.FAILED)
        self.assertIn('deleteMessage', [call.args[1] for call in self.api.call_args_list])
        self.executor = HeldExecutor()
        self.assertTrue(self.start()[1])
        self.assertEqual(len(self.executor.tasks), 1)

    def test_worker_runs_in_untrusted_context_and_publishes_true_stages(self):
        from leech import cli, control, downloader
        stages, targets = [], []
        def transfer(*args, **kwargs):
            self.assertTrue(downloader._UNTRUSTED.get())
            self.assertIs(self.registry.get(1, chat=7).state, self.jobs.JobState.DOWNLOADING)
            targets.append(kwargs['target_dir'])
            for stage in ('splitting', 'waiting_upload', 'uploading'):
                control.report_stage(stage)
                stages.append(self.registry.counts())
            kwargs['progress']('100% uploaded')
            return ['track.mp3']
        self.start()
        with patch.object(cli, 'run_leech_sync', side_effect=transfer):
            self.executor.run_next()
        job = self.registry.get(1, chat=7)
        self.assertIs(job.state, self.jobs.JobState.SUCCEEDED)
        self.assertEqual(stages, [{'downloading': 1}, {'waiting_upload': 1}, {'uploading': 1}])
        self.assertEqual(self.registry.counts(), {})
        self.assertEqual(self.service._workers, 0)
        self.assertFalse(Path(targets[0]).exists())
        self.notify.assert_called_once()

    def test_running_cancellation_is_cooperative_and_releases_slot_after_ack(self):
        from leech import cli, control
        def transfer(*args, **kwargs):
            self.service.cancel(1, chat=7)
            self.assertEqual(self.registry.counts(), {'downloading': 1})
            control.check_cancelled()
            self.fail('cancelled transfer continued')
        self.start()
        with patch.object(cli, 'run_leech_sync', side_effect=transfer):
            self.executor.run_next()
        self.assertIs(self.registry.get(1, chat=7).state, self.jobs.JobState.CANCELLED)
        self.assertEqual(self.registry.counts(), {})

    def test_failure_is_terminal_and_notifications_are_idempotent_even_offline(self):
        from leech import cli
        self.start()
        self.notify.side_effect = RuntimeError('offline')
        with patch.object(cli, 'run_leech_sync', side_effect=ValueError('broken ``` file')):
            self.executor.run_next()
        job = self.registry.get(1, chat=7)
        self.assertIs(job.state, self.jobs.JobState.FAILED)
        self.assertIn('ValueError', job.error)
        self.assertEqual(self.registry.counts(), {})
        self.service.finish(job)
        self.notify.assert_called_once()

    def test_finalization_removes_stale_status_even_before_next_edit_window(self):
        from leech import cli
        with patch.object(self.module.time, 'monotonic', return_value=10):
            self.start()
            with patch.object(cli, 'run_leech_sync', return_value=[]):
                self.executor.run_next()
        methods = [call.args[1] for call in self.api.call_args_list]
        self.assertIn('deleteMessage', methods)
        self.assertNotIn('editMessageText', methods)

    def test_errors_do_not_expose_secrets_urls_or_local_paths_to_chat(self):
        from leech import cli
        self.start()
        secret = '123456:ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefgh123456'
        message = f'GET https://private.example/file?token=secret {secret} /srv/private/media.mp3'
        with patch.object(cli, 'run_leech_sync', side_effect=ValueError(message)):
            self.executor.run_next()
        notification = '\n'.join(self.notify.call_args.args[2])
        self.assertIn('ValueError', notification)
        for private in (secret, 'private.example', 'token=secret', '/srv/private'):
            self.assertNotIn(private, notification)

    def test_progress_redacts_credentials_before_status_or_final_notification(self):
        from leech import cli
        secret = '123456:ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefgh123456'
        def transfer(*args, **kwargs):
            kwargs['progress']('progress ' + secret)
            return []
        self.start()
        with patch.object(cli, 'run_leech_sync', side_effect=transfer):
            self.executor.run_next()
        self.assertNotIn(secret, '\n'.join(self.notify.call_args.args[2]))

    def test_retained_notification_shows_filenames_not_local_paths(self):
        job = self.registry.add(token='t', chat=7, url='https://example.org/a')
        job.error, job.sent, job.total = 'TransferError', 1, 2
        job.retained = ['/srv/private/job123/remaining.mp3']
        self.registry.complete(job, self.jobs.JobState.FAILED)
        self.service.finish(job)
        notification = '\n'.join(self.notify.call_args.args[2])
        self.assertIn('remaining.mp3', notification)
        self.assertNotIn('/srv/private', notification)

    def test_worker_honors_configured_download_directory(self):
        from leech import cli
        target = []
        def transfer(*args, **kwargs):
            target.append(Path(kwargs['target_dir']))
            return []
        self.start()
        with patch.dict(os.environ, {'DOWNLOAD_DIR': 'custom/media'}), \
                patch.object(cli, 'run_leech_sync', side_effect=transfer):
            self.executor.run_next()
        self.assertEqual(target[0].parent, Path(self.temp.name) / 'custom/media')

    def test_configured_absolute_download_directory_is_not_rebased(self):
        configured = Path(self.temp.name) / 'absolute'
        with patch.dict(os.environ, {'DOWNLOAD_DIR': str(configured)}):
            self.assertEqual(self.module.download_root('/another/root'), configured)

    def test_prune_uses_configured_directory_and_removes_only_empty_runs(self):
        configured = Path(self.temp.name) / 'configured'
        (configured / 'empty').mkdir(parents=True)
        with patch.dict(os.environ, {'DOWNLOAD_DIR': str(configured)}):
            self.module.prune_downloads('/another/root')
        self.assertFalse(configured.exists())

    def test_status_not_modified_is_not_treated_as_permanent_failure(self):
        from bot.telegram import TgError
        self.start()
        job = self.registry.get(1, chat=7)
        job.lines.append('changed')
        self.api.side_effect = TgError('editMessageText', {
            'error_code': 400, 'description': 'Bad Request: message is not modified'})
        with patch.object(self.module.time, 'monotonic', return_value=job.last_edit + 3):
            self.service.status(job)
        self.assertEqual(job.last_text, self.service.text(job))
        self.assertFalse(job.status_disabled)

    def test_permanently_missing_status_disables_edits_but_keeps_transfer(self):
        from bot.telegram import TgError
        self.start()
        job = self.registry.get(1, chat=7)
        job.lines.append('changed')
        self.api.reset_mock()
        self.api.side_effect = TgError('editMessageText', {
            'error_code': 400, 'description': 'Bad Request: message to edit not found'})
        for now in (job.last_edit + 3, job.last_edit + 6):
            with patch.object(self.module.time, 'monotonic', return_value=now):
                self.service.status(job)
        self.api.assert_called_once()
        self.assertTrue(job.status_disabled)
        self.assertEqual(self.registry.counts(), {'queued': 1})

    def test_other_status_failures_log_exception_class_not_sensitive_message(self):
        self.start()
        job = self.registry.get(1, chat=7)
        job.lines.append('changed')
        self.api.side_effect = RuntimeError('TOP-SECRET https://private.example/token')
        with patch('builtins.print') as logged, \
                patch.object(self.module.time, 'monotonic', return_value=job.last_edit + 3):
            self.service.status(job)
        self.assertTrue(logged.called)
        output = str(logged.call_args)
        self.assertIn('RuntimeError', output)
        self.assertNotIn('TOP-SECRET', output)

    def test_missing_leech_dependency_is_failed_and_releases_admission(self):
        self.start()
        original_import = __import__
        def unavailable(name, *args, **kwargs):
            if name == 'leech':
                raise ImportError('optional dependency missing')
            return original_import(name, *args, **kwargs)
        with patch('builtins.__import__', side_effect=unavailable):
            self.executor.run_next()
        job = self.registry.get(1, chat=7)
        self.assertIs(job.state, self.jobs.JobState.FAILED)
        self.assertIn('unavailable', job.note)
        self.assertEqual(self.registry.counts(), {})

    def test_queued_cancel_never_runs_transfer_and_can_be_repeated(self):
        from leech import cli
        self.start()
        job = self.service.cancel(1, chat=7)
        self.service.cancel(1, chat=7)
        with patch.object(cli, 'run_leech_sync') as transfer:
            self.executor.run_next()
        transfer.assert_not_called()
        self.assertTrue(job.finalized)
        self.assertEqual(self.service._workers, 0)
        self.notify.assert_called_once()

    def test_partial_failure_preserves_counts_and_retained_files(self):
        from leech import cli
        class PartialError(RuntimeError):
            sent, total = 1, 2
            retained = ['remaining.mp3']
        def transfer(*args, **kwargs):
            (Path(kwargs['target_dir']) / 'remaining.mp3').write_bytes(b'not uploaded')
            raise PartialError('one upload failed')
        self.start()
        with patch.object(cli, 'run_leech_sync', side_effect=transfer):
            self.executor.run_next()
        job = self.registry.get(1, chat=7)
        self.assertIs(job.state, self.jobs.JobState.FAILED)
        self.assertEqual((job.sent, job.total, job.retained), (1, 2, ['remaining.mp3']))
        self.assertTrue(list(Path(self.temp.name).glob('downloads/*/remaining.mp3')))
        self.assertIn('1/2', '\n'.join(self.notify.call_args.args[2]))

    def test_runner_exception_cannot_leak_worker_or_block_following_job(self):
        from leech import cli
        self.start(runner=Mock(side_effect=RuntimeError('wrapper failed')))
        self.start(chat=8, url='https://example.org/b')
        with patch.object(cli, 'run_leech_sync', return_value=[]):
            self.executor.run_next()
            self.executor.run_next()
        self.assertEqual(self.service._workers, 0)
        self.assertEqual(self.registry.counts(), {})
        self.assertIs(self.registry.get(1, chat=7).state, self.jobs.JobState.FAILED)
        self.assertIs(self.registry.get(2, chat=8).state, self.jobs.JobState.SUCCEEDED)

    def test_service_runs_only_two_transfers_even_with_larger_injected_executor(self):
        from leech import cli
        gate, two_started = threading.Event(), threading.Event()
        lock = threading.Lock()
        running = maximum = started = 0
        def transfer(*args, **kwargs):
            nonlocal running, maximum, started
            with lock:
                running += 1
                started += 1
                maximum = max(maximum, running)
                if started == 2:
                    two_started.set()
            gate.wait(2)
            with lock:
                running -= 1
            return []
        with ThreadPoolExecutor(max_workers=8) as executor, \
                patch.object(cli, 'run_leech_sync', side_effect=transfer):
            self.executor = executor
            try:
                for index in range(10):
                    self.assertTrue(self.start(index, f'https://example.org/{index}')[1])
                self.assertTrue(two_started.wait(1))
                self.assertEqual(self.registry.counts(), {'downloading': 2, 'queued': 8})
                self.assertFalse(self.start(100, 'https://example.org/overflow')[1])
            finally:
                gate.set()
                executor.shutdown(wait=True)
        self.assertEqual(started, 10)
        self.assertEqual(maximum, 2)
        self.assertEqual(self.registry.counts(), {})

    def test_transfer_wrapped_cancellation_stays_cancelled_not_failed(self):
        from leech import cli, control
        def transfer(*args, **kwargs):
            self.service.cancel(1, chat=7)
            try:
                control.check_cancelled()
            except control.TransferCancelled as error:
                raise RuntimeError('upload stopped') from error
        self.start()
        with patch.object(cli, 'run_leech_sync', side_effect=transfer):
            self.executor.run_next()
        self.assertIs(self.registry.get(1, chat=7).state, self.jobs.JobState.CANCELLED)

    def test_legacy_finalization_is_also_idempotent(self):
        job = {'id': 10, 'token': 't', 'chat': 7, 'url': 'https://example.org/a',
               'lines': [], 'files': [], 'error': 'broken', 'note': '',
               'lock': threading.Lock(), 'pool': {10: 'active'}}
        self.service.finish(job)
        self.service.finish(job)
        self.notify.assert_called_once()

    def test_labels_never_expose_credentials_query_or_fragment(self):
        label = self.module.mask_url('https://name:password@example.org/file.mp3?token=secret#fragment')
        self.assertEqual(label, 'example.org/…/file.mp3')

    def test_final_notification_escapes_untrusted_markdown(self):
        self.assertEqual(self.module.escape_markdown('a`*_[]\\'), 'a\\`\\*\\_\\[\\]\\\\')

    def test_prune_keeps_nonempty_retained_run_directories(self):
        root = Path(self.temp.name) / 'downloads'
        (root / 'empty').mkdir(parents=True)
        (root / 'retained').mkdir()
        (root / 'retained' / 'file.mp3').write_bytes(b'unconfirmed')
        self.module.prune_downloads(self.temp.name)
        self.assertFalse((root / 'empty').exists())
        self.assertTrue((root / 'retained' / 'file.mp3').exists())


class DownloadWiringTests(unittest.TestCase):
    def setUp(self):
        from bot import app
        self.app = app
        self.api = Mock(return_value={'message_id': 5})
        self.executor = HeldExecutor()
        for name, value in [('_DL_POOL', {}), ('_LEECH_EXECUTOR', self.executor),
                            ('tg_call', self.api), ('send_long', Mock())]:
            patcher = patch.object(app, name, value)
            patcher.start()
            self.addCleanup(patcher.stop)

    def start(self, chat=7):
        return self.app.start_leech('test', chat, 'https://example.org/a',
                                    None, None, None, False, False)

    def update(self, text, chat=7):
        self.app._handle_update({'message': {'text': text, 'chat': {'id': chat}}},
                                Mock(), 'test')

    def tap(self, data, chat=7):
        self.app._handle_callback({'id': 'tap', 'data': data, 'message': {
            'message_id': 5, 'chat': {'id': chat}}}, Mock(), 'test')

    def test_cancel_callback_is_bound_to_chat_and_does_not_sleep(self):
        self.start()
        self.tap('cancel:1', chat=8)
        self.assertFalse(self.app._DL_POOL[1].cancel_event.is_set())
        with patch('time.sleep', side_effect=AssertionError('polling must not sleep')):
            self.tap('cancel:1')
        self.assertNotIn(1, self.app._DL_POOL)
        self.assertIn('cancelled', self.api.call_args.args[2]['text'])
        self.assertEqual(self.api.call_args.kwargs['tries'], 0)

    def test_status_and_cancel_commands_report_only_this_chat(self):
        self.start()
        self.update('/status 1', chat=8)
        self.assertIn('not found', self.api.call_args.args[2]['text'])
        self.update('/status 1')
        self.assertIn('queued', self.api.call_args.args[2]['text'])
        self.update('/cancel 1')
        self.assertNotIn(1, self.app._DL_POOL)
        self.update('/status 1')
        self.assertIn('cancelled', self.api.call_args.args[2]['text'])
        for text in ('/cancel', '/cancel nonsense', '/status 1 extra', '/status'):
            self.update(text)
            self.assertNotIn('parse_mode', self.api.call_args.args[2])
            self.assertEqual(self.api.call_args.kwargs['tries'], 0)

    def test_help_describes_control_commands_without_free_url_download(self):
        text = self.app.help_text()
        self.assertIn('/cancel', text)
        self.assertIn('/status', text)
        self.assertNotIn('/dl ', text)


if __name__ == '__main__':
    unittest.main()
