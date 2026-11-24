"""Lifecycle admission counts queued and running jobs, never UI snapshots."""
import importlib
import unittest
from unittest.mock import patch


class JobRegistryTests(unittest.TestCase):
    def setUp(self):
        environment = patch.dict('os.environ', {'LEECH_QUEUE_SIZE': '10'})
        environment.start()
        self.addCleanup(environment.stop)
        self.jobs = importlib.import_module('bot.jobs')
        self.registry = self.jobs.JobRegistry()

    def add(self, chat=7, url='https://example.org/a'):
        return self.registry.add(token='test', chat=chat, url=url)

    def test_new_job_is_queued_until_a_worker_begins(self):
        job = self.add()
        self.assertIs(job.state, self.jobs.JobState.QUEUED)
        self.assertTrue(self.registry.begin(job))
        self.assertIs(job.state, self.jobs.JobState.DOWNLOADING)
        self.assertEqual(self.registry.counts(), {'downloading': 1})

    def test_admission_limits_count_queued_and_active(self):
        first = self.add()
        self.registry.begin(first)
        self.add(url='https://example.org/b')
        with self.assertRaisesRegex(self.jobs.JobRejected, 'chat'):
            self.add(url='https://example.org/c')
        for chat in range(8):
            self.add(chat=100 + chat, url=f'https://example.org/{chat}')
        self.assertEqual(sum(self.registry.counts().values()), 10)
        with self.assertRaisesRegex(self.jobs.JobRejected, 'full'):
            self.add(chat=200, url='https://example.org/overflow')

    def test_duplicate_url_is_rejected_queued_or_running(self):
        job = self.add()
        for state in ('queued', 'downloading'):
            with self.subTest(state=state):
                with self.assertRaisesRegex(self.jobs.JobRejected, 'already queued'):
                    self.add(chat=7)
                self.registry.begin(job)

    def test_capacity_is_configurable_with_default_ten(self):
        registry = self.jobs.JobRegistry(capacity=1)
        registry.add(token='t', chat=1, url='a')
        with self.assertRaises(self.jobs.JobRejected):
            registry.add(token='t', chat=2, url='b')
        registry = self.jobs.JobRegistry(capacity=12)
        for chat in range(12):
            registry.add(token='t', chat=chat, url=f'https://example.org/{chat}')
        self.assertEqual(registry.capacity, 12)
        with self.assertRaises(self.jobs.JobRejected):
            registry.add(token='t', chat=12, url='overflow')

    def test_same_recording_is_allowed_for_a_different_chat(self):
        self.add(chat=7)
        second = self.add(chat=8)
        self.assertEqual(second.chat, 8)
        self.assertEqual(self.registry.counts(), {'queued': 2})

    def test_job_repr_does_not_expose_token_or_signed_url(self):
        job = self.registry.add(token='SUPER-SECRET-TOKEN', chat=7,
                                url='https://example.org/a?secret=SIGNED-SECRET')
        self.assertNotIn('SUPER-SECRET', repr(job))
        self.assertNotIn('SIGNED-SECRET', repr(job))
        self.assertNotIn('https://', repr(job))

    def test_terminal_completion_is_idempotent_and_releases_admission(self):
        job = self.add()
        self.registry.begin(job)
        self.assertTrue(self.registry.complete(job, self.jobs.JobState.FAILED))
        self.assertFalse(self.registry.complete(job, self.jobs.JobState.SUCCEEDED))
        self.assertIs(job.state, self.jobs.JobState.FAILED)
        self.assertEqual(self.registry.counts(), {})
        self.assertIs(self.registry.get(job.id, chat=7), job)
        self.assertIsNone(self.registry.get(job.id, chat=8))
        self.assertTrue(self.registry.claim_finalization(job))
        self.assertFalse(self.registry.claim_finalization(job))
        self.assertNotEqual(self.add().id, job.id)

    def test_cancel_is_chat_bound_and_queued_jobs_never_begin(self):
        job = self.add()
        self.assertIsNone(self.registry.cancel(job.id, chat=8))
        self.assertFalse(job.cancel_event.is_set())
        self.assertIs(self.registry.cancel(job.id, chat=7), job)
        self.assertIs(job.state, self.jobs.JobState.CANCELLED)
        self.assertFalse(self.registry.begin(job))
        self.assertEqual(self.registry.counts(), {})

    def test_running_cancel_keeps_slot_until_worker_acknowledges(self):
        job = self.add()
        self.registry.begin(job)
        self.registry.cancel(job.id, chat=7)
        self.assertTrue(job.cancel_event.is_set())
        self.assertIs(job.state, self.jobs.JobState.DOWNLOADING)
        self.assertEqual(self.registry.counts(), {'downloading': 1})
        self.registry.complete(job, self.jobs.JobState.CANCELLED)
        self.assertEqual(self.registry.counts(), {})

    def test_counts_follow_real_transfer_stages_without_advancing_terminals(self):
        job = self.add()
        self.registry.begin(job)
        for stage, state in [('splitting', 'downloading'),
                             ('waiting_upload', 'waiting_upload'), ('uploading', 'uploading')]:
            self.registry.stage(job, stage)
            self.assertEqual(self.registry.counts(), {state: 1})
        self.registry.complete(job, self.jobs.JobState.SUCCEEDED)
        self.registry.stage(job, 'uploading')
        self.assertIs(job.state, self.jobs.JobState.SUCCEEDED)

    def test_terminal_history_is_bounded(self):
        for index in range(125):
            job = self.add(url=f'https://example.org/{index}')
            self.registry.complete(job, self.jobs.JobState.SUCCEEDED)
        self.assertLessEqual(len(self.registry.history), 100)

    def test_environment_capacity_is_safely_bounded(self):
        for value, expected in [('2', 2), ('1000', 1000), ('invalid', 10), ('0', 1)]:
            with self.subTest(value=value), patch.dict('os.environ', {'LEECH_QUEUE_SIZE': value}):
                self.assertEqual(self.jobs.JobRegistry().capacity, expected)

    def test_invalid_stage_or_completion_cannot_corrupt_lifecycle(self):
        job = self.add()
        with self.assertRaises(ValueError):
            self.registry.stage(job, 'succeeded')
        with self.assertRaises(ValueError):
            self.registry.complete(job, self.jobs.JobState.DOWNLOADING)
        self.assertIs(job.state, self.jobs.JobState.QUEUED)


if __name__ == '__main__':
    unittest.main()
