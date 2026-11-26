import unittest

from pipeline.common import extract_media, extract_videos, classify


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

    def test_excludes_google_media_and_country_domains_by_hostname(self):
        for host in ('rr1.googlevideo.com', 'blogger.googleusercontent.com',
                     'lh3.ggpht.com', 'drive.google.co.uk', 'Google.DE.:443',
                     'www.google.com.au'):
            with self.subTest(host=host):
                self.assertEqual(extract_media(f'https://{host}/clip.mp4'), ([], []))

    def test_keeps_urls_that_only_mention_google_media_domains(self):
        urls = [
            'https://media.example/googlevideo.com.mp3?next=https://google.co.uk/',
            'https://notgooglevideo.com/clip.mp3',
            'https://google.co.uk.media.example/clip.mp3',
        ]
        self.assertEqual(extract_media(' '.join(urls)), (urls, []))


class YouTubeParsingTests(unittest.TestCase):
    def test_normalizes_every_embed_form_to_one_watch_url(self):
        body = " ".join([
            'https://www.youtube.com/embed/abcdefghijk?rel=0',
            'https://youtube.com/watch?v=abcdefghijk&t=30',
            'https://youtu.be/abcdefghijk',
            'https://m.youtube.com/shorts/abcdefghijk',
            'https://www.youtube-nocookie.com/embed/abcdefghijk',
        ])
        self.assertEqual(extract_videos(body),
                         ['https://www.youtube.com/watch?v=abcdefghijk'])

    def test_keeps_order_and_distinct_ids(self):
        body = ('https://youtu.be/aaaaaaaaaaa '
                'https://www.youtube.com/embed/bbbbbbbbbbb '
                'https://youtu.be/aaaaaaaaaaa')
        self.assertEqual(extract_videos(body), [
            'https://www.youtube.com/watch?v=aaaaaaaaaaa',
            'https://www.youtube.com/watch?v=bbbbbbbbbbb',
        ])

    def test_ignores_lookalike_hosts_and_bad_ids(self):
        body = " ".join([
            'https://youtube.com.evil.example/embed/abcdefghijk',
            'https://notyoutube.com/watch?v=abcdefghijk',
            'https://www.youtube.com/embed/too-short',
            'https://www.youtube.com/embed/abcdefghijkl',
        ])
        self.assertEqual(extract_videos(body), [])

    def test_youtube_links_are_not_downloadable_media(self):
        body = 'https://www.youtube.com/watch?v=abcdefghijk'
        self.assertEqual(extract_media(body), ([], []))


if __name__ == "__main__":
    unittest.main()
