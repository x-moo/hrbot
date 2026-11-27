"""Completion card: what was saved, plus the direct link as a clickable label."""

import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock

from bot.downloads import DownloadService
from bot.i18n import language
from bot.jobs import JobRegistry, JobState

URL = "https://mirrors.example/Sheikh_Test_(1)-2026.mp3"


class DownloadCardTests(unittest.TestCase):
    def setUp(self):
        context = language.set('id')
        self.addCleanup(language.reset, context)
        self.registry = JobRegistry()
        self.api = Mock(return_value={"message_id": 3})
        self.notify = Mock()
        self.card = Mock()
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.service = DownloadService(
            registry=self.registry,
            executor=lambda: Mock(),
            api=lambda *args, **kwargs: self.api(*args, **kwargs),
            notify=self.notify,
            card=self.card,
            root=lambda: Path(self.temp.name),
        )
        self.job = self.registry.add(
            token="token", chat=7, url=URL, name=None, split_size=None,
            thumb=None, as_doc=False, to_self=False,
        )

    def _text(self):
        return self.service.card_text(self.job)

    def test_url_is_a_link_target_not_visible_text(self):
        text = self._text()
        visible = text.split("](", 1)[0]
        self.assertNotIn("https://", visible)
        self.assertNotIn("mirrors.example/Sheikh", visible)
        self.assertIn("[Buka link langsung](", text)
        self.assertIn("%281%29", text)  # parentheses stay inside the markdown target

    def test_card_names_the_file_kind_size_and_host(self):
        self.job.files = [str(Path(self.temp.name) / "SheikhDosary_Isha-2026-11-25.mp3")]
        self.job.lines.append("downloaded 3.4 MB in 2.1s")
        text = self._text()
        self.assertIn("🎙️ Audio", text)
        self.assertIn("3.4 MB", text)
        self.assertIn("mirrors.example", text)
        self.assertIn("Selesai", text)

    def test_file_kinds_follow_the_uploader_classification(self):
        for name, label in [('track.opus', '🎙️ Audio'), ('video.webm', '🎬 Video'),
                            ('video.mov', '🎬 Video'), ('photo.jpg', '🖼️ Gambar'),
                            ('archive.bin', '📦 Berkas')]:
            with self.subTest(name=name):
                self.job.files = [str(Path(self.temp.name) / name)]
                self.assertIn(label, self._text())

    def test_filename_words_are_not_localized(self):
        self.job.files = ['Latest.mp3', 'Download.mp3']
        for code in ('id', 'en'):
            token = language.set(code)
            try:
                text = self._text()
                self.assertIn('Latest.mp3', text)
                self.assertIn('Download.mp3', text)
                self.assertNotIn('Terbaru.mp3', text)
                self.assertNotIn('Unduh.mp3', text)
            finally:
                language.reset(token)

    def test_filename_markdown_is_escaped(self):
        self.job.files = [str(Path(self.temp.name) / "Sheikh_Test_[1].mp3")]
        text = self._text()
        self.assertIn("Sheikh\\_Test\\_\\[1\\].mp3", text)

    def test_size_falls_back_to_the_file_on_disk(self):
        path = Path(self.temp.name) / "part.mp3"
        path.write_bytes(b"x" * 2048)
        self.job.files = [str(path)]
        self.assertIn("2.0 KB", self._text())

    def test_many_parts_are_truncated(self):
        self.job.files = [str(Path(self.temp.name) / f"part{n}.mp3") for n in range(4)]
        text = self._text()
        self.assertIn("… (+1)", text)

    def test_finish_uses_the_card_only_for_success(self):
        self.job.files = [str(Path(self.temp.name) / "a.mp3")]
        self.registry.complete(self.job, JobState.SUCCEEDED)
        self.service.finish(self.job)
        self.card.assert_called_once()
        self.notify.assert_not_called()

    def test_failure_keeps_the_plain_summary(self):
        self.registry.complete(self.job, JobState.FAILED)
        self.service.finish(self.job)
        self.card.assert_not_called()
        self.notify.assert_called_once()

    def test_card_failure_still_sends_the_plain_summary(self):
        self.card.side_effect = RuntimeError("formatting exploded")
        self.job.files = [str(Path(self.temp.name) / "a.mp3")]
        self.registry.complete(self.job, JobState.SUCCEEDED)
        self.service.finish(self.job)
        self.notify.assert_called_once()

    def test_label_follows_the_chat_language(self):
        token = language.set("en")
        try:
            self.assertIn("[Open direct link](", self._text())
        finally:
            language.reset(token)


if __name__ == "__main__":
    unittest.main()
