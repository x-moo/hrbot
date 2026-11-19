import unittest

from pipeline.common import extract_media, classify


class MediaParsingTests(unittest.TestCase):
    def test_only_accepts_complete_media_path_extensions(self):
        body = " ".join([
            "https://media.example/lecture.ram",
            "https://media.example/session.ramadan.mp3",
            "https://media.example/audio.mp3.backup",
            "https://media.example/real.mp3",
        ])
        audio, video = extract_media(body)
        self.assertIn("https://media.example/lecture.ram", audio)
        self.assertIn("https://media.example/session.ramadan.mp3", audio)
        self.assertIn("https://media.example/real.mp3", audio)
        self.assertNotIn("https://media.example/audio.mp3", audio)

    def test_excludes_google_domains_not_matching_media_path_or_query(self):
        audio, video = extract_media(
            "https://media.example/talk-google.mp3 "
            "https://media.example/ok.mp3?next=https://google.com/"
        )
        self.assertEqual(audio, [
            "https://media.example/talk-google.mp3",
            "https://media.example/ok.mp3?next=https://google.com/",
        ])
        self.assertEqual(video, [])

    def test_excludes_google_subdomains(self):
        audio, video = [], []
        classify("https://drive.google.com/file.mp4", audio, video, set(), set())
        self.assertEqual((audio, video), ([], []))


if __name__ == "__main__":
    unittest.main()
