"""Limits and redirect credential regression tests."""
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch, Mock
import urllib.request
from leech import downloader as d


class DownloadSafetyTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(dir=Path(__file__).resolve().parents[1] / '.cache')
        self.directory = Path(self.temp.name)

    def tearDown(self):
        self.temp.cleanup()

    def response(self, length=None):
        response = Mock()
        response.headers = {} if length is None else {'Content-Length': str(length)}
        response.__enter__ = Mock(return_value=response)
        response.__exit__ = Mock(return_value=False)
        response.read.side_effect = [b'x' * 64, b'']
        return response

    def test_declared_and_stream_limits(self):
        for length in (64, None):
            with self.subTest(length=length), patch.dict(os.environ, {'LEECH_MAX_BYTES': '32'}), patch('urllib.request.OpenerDirector.open', return_value=self.response(length)):
                with self.assertRaisesRegex(RuntimeError, 'maximum download size'):
                    d.download_python('http://example/file', self.directory, 'file', [])
            self.assertFalse((self.directory / 'file.part').exists())

    def test_disk_reserve(self):
        with patch.object(d.shutil, 'disk_usage', return_value=(1000, 950, 50)), patch.dict(os.environ, {'LEECH_MIN_FREE_BYTES': '100'}):
            with self.assertRaisesRegex(RuntimeError, 'free space'):
                d.download_python('http://example/file', self.directory, 'file', [])

    def test_redirect_strips_credentials(self):
        request = urllib.request.Request('https://one.example/a', headers={'Authorization': 'secret', 'Cookie': 'secret', 'Proxy-Authorization': 'secret'})
        redirected = d.SafeRedirect().redirect_request(request, None, 302, '', {}, 'http://two.example/a')
        self.assertFalse(any(key.lower() in ('authorization', 'cookie', 'proxy-authorization') for key in redirected.headers))

    def test_cross_origin_redirect_forwards_only_safe_headers(self):
        headers = {'X-API-Key': 'secret', 'X-Auth-Token': 'secret',
                   'Private-Header': 'secret', 'Referer': 'https://one.example/?token=secret',
                   'Host': 'one.example', 'User-Agent': 'test-agent',
                   'Accept': 'audio/*', 'Range': 'bytes=0-100'}
        request = urllib.request.Request('https://one.example/a', headers=headers)
        for destination in ('https://two.example/a', 'http://one.example/a',
                            'https://one.example:444/a'):
            with self.subTest(destination=destination):
                redirected = d.SafeRedirect().redirect_request(
                    request, None, 302, '', {}, destination)
                self.assertEqual({key.lower(): value for key, value in redirected.header_items()},
                                 {'user-agent': 'test-agent', 'accept': 'audio/*',
                                  'range': 'bytes=0-100'})

    def test_same_origin_redirect_preserves_custom_headers(self):
        request = urllib.request.Request('https://one.example/a', headers={
            'X-API-Key': 'secret', 'Authorization': 'secret', 'Cookie': 'secret'})
        redirected = d.SafeRedirect().redirect_request(
            request, None, 302, '', {}, 'https://ONE.example:443/b')
        self.assertEqual(redirected.headers, request.headers)

    def test_same_origin_preserves_unredirected_headers(self):
        request = urllib.request.Request('https://one.example/a')
        request.add_unredirected_header('X-API-Key', 'secret')
        redirected = d.SafeRedirect().redirect_request(
            request, None, 302, '', {}, 'https://one.example/b')
        self.assertEqual(redirected.get_header('X-api-key'), 'secret')

    def test_redirect_closes_response_without_unbounded_body_drain(self):
        request = urllib.request.Request('https://one.example/a')
        request.timeout = 1
        handler = d.SafeRedirect()
        handler.parent = Mock()
        response = Mock()
        handler.http_error_302(request, response, 302, '', {'location': '/b'})
        response.read.assert_not_called()
        response.close.assert_called()

    def test_default_backend_is_bounded(self):
        with patch.object(d, 'download_python', return_value=[]) as backend:
            d.download('http://example/file', self.directory, 'file', [])
        backend.assert_called_once()
