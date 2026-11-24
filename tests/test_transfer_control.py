"""Transfer-scoped control without credentials or network access."""
import importlib.util
import threading
import unittest


class TransferControlTests(unittest.TestCase):
    def test_nested_scope_cannot_disable_outer_cancellation(self):
        self.assertIsNotNone(importlib.util.find_spec('leech.control'),
                             'transfer control context is missing')
        from leech.control import TransferCancelled, check_cancelled, transfer_control

        outer = threading.Event()
        inner = threading.Event()
        with transfer_control(cancel_event=outer):
            with transfer_control(cancel_event=inner):
                check_cancelled()
                outer.set()
                with self.assertRaises(TransferCancelled):
                    check_cancelled()
            with transfer_control():
                with self.assertRaises(TransferCancelled):
                    check_cancelled()
        check_cancelled()  # Context restoration must not cancel the next job.

    def test_cli_help_describes_python_streaming_and_ignored_jobs(self):
        from leech.cli import build_parser
        help_text = build_parser().format_help()
        self.assertIn('Python streaming', help_text)
        self.assertIn('compatibility', help_text)
        self.assertIn('ignored', help_text)
        self.assertNotIn('aria2c connections', help_text)

    def test_stage_callback_is_inherited_normalized_and_restored(self):
        from leech import control
        self.assertTrue(callable(getattr(control, 'report_stage', None)),
                        'stage progress hook is missing')
        stages = []
        nested = []
        with control.transfer_control(on_stage=stages.append):
            control.report_stage('DOWNLOADING')
            with control.transfer_control():
                control.report_stage('splitting')
            with control.transfer_control(on_stage=nested.append):
                control.report_stage('waiting_upload')
            control.report_stage('uploading')
        control.report_stage('downloading')
        self.assertEqual(stages, ['downloading', 'splitting', 'uploading'])
        self.assertEqual(nested, ['waiting_upload'])
