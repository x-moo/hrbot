"""Downloads and splitting work without system executables."""
import functools
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
import tempfile
import threading
import unittest

ROOT = Path(__file__).resolve().parents[1]
(ROOT / '.cache').mkdir(exist_ok=True)


class Downloads(unittest.TestCase):
    def test_http_download_uses_python_backend(self):
        from leech.downloader import download
        with tempfile.TemporaryDirectory(dir=ROOT / '.cache') as directory:
            base = Path(directory)
            payload = b'archive audio' * 10000
            (base / 'source.mp3').write_bytes(payload)
            handler = functools.partial(SimpleHTTPRequestHandler, directory=directory)
            server = ThreadingHTTPServer(('127.0.0.1', 0), handler)
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                files = download(f'http://127.0.0.1:{server.server_port}/source.mp3',
                                 base / 'downloads', 'file.mp3', [])
                self.assertEqual(Path(files[0]).read_bytes(), payload)
            finally:
                server.shutdown()
                server.server_close()
                thread.join()

    def test_split_uses_python(self):
        from leech.downloader import split_file
        with tempfile.TemporaryDirectory(dir=ROOT / '.cache') as directory:
            source = Path(directory) / 'file.bin'
            payload = b'0123456789abcdef'
            source.write_bytes(payload)
            parts = split_file(source, 6)
            self.assertEqual([Path(p).stat().st_size for p in parts], [6, 6, 4])
            self.assertEqual(b''.join(Path(p).read_bytes() for p in parts), payload)
            self.assertFalse(source.exists())
