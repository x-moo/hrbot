"""Untrusted archive boundaries; no connections to the public Internet."""
import io
import socket
import os
import time
import threading
import struct
import socketserver
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, HTTPServer
import tempfile
import unittest
import urllib.request
from pathlib import Path
from unittest.mock import Mock, patch

from leech import downloader as d
from leech import network as n

ROOT = Path(__file__).resolve().parents[1]
(ROOT / '.cache').mkdir(exist_ok=True)


@contextmanager
def slow_server(mode):
    stop = threading.Event()

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def do_GET(self):
            try:
                if mode == 'body':
                    self.send_response(200)
                    self.send_header('Content-Length', '30')
                    self.end_headers()
                    pieces = [b'x'] * 30
                elif mode == 'headers':
                    pieces = [bytes([value]) for value in b'HTTP/1.0 200 OK\r\nContent-Length: 0\r\n\r\n']
                elif mode == 'redirects':
                    if stop.wait(0.3):
                        return
                    self.send_response(302)
                    self.send_header('Location', '/hop' + self.path)
                    self.send_header('Content-Length', '0')
                    self.end_headers()
                    return
                else:
                    self.send_response(200)
                    self.send_header('Transfer-Encoding', 'chunked')
                    self.end_headers()
                    pieces = [b'1\r\nx\r\n', b'0\r\n'] + [b'X-Trailer: x\r\n'] * 30 + [b'\r\n']
                for piece in pieces:
                    self.wfile.write(piece)
                    self.wfile.flush()
                    if stop.wait(0.09):
                        break
            except (BrokenPipeError, ConnectionResetError):
                pass

    server = HTTPServer(('127.0.0.1', 0), Handler)
    worker = threading.Thread(target=server.serve_forever)
    worker.start()
    try:
        yield f'http://127.0.0.1:{server.server_port}/file'
    finally:
        stop.set()
        server.shutdown()
        server.server_close()
        worker.join(timeout=2)
        if worker.is_alive():
            raise AssertionError('fixture leaked a server thread')


@contextmanager
def ftp_server(delay=0.09):
    stop = threading.Event()

    class Handler(socketserver.StreamRequestHandler):
        def handle(self):
            listener = None
            try:
                self.wfile.write(b'220 local FTP\r\n')
                while not stop.is_set():
                    command = self.rfile.readline().decode().strip().split(' ', 1)[0]
                    if not command:
                        break
                    if command == 'USER':
                        self.wfile.write(b'331 password\r\n')
                    elif command == 'PASS':
                        self.wfile.write(b'230 logged in\r\n')
                    elif command in ('TYPE', 'CWD'):
                        self.wfile.write(b'200 ok\r\n')
                    elif command == 'PASV':
                        listener = socket.socket()
                        listener.bind(('127.0.0.1', 0))
                        listener.listen(1)
                        listener.settimeout(2)
                        port = listener.getsockname()[1]
                        self.wfile.write(f'227 passive (127,0,0,1,{port // 256},{port % 256})\r\n'.encode())
                    elif command == 'RETR':
                        self.wfile.write(b'150 opening (30 bytes)\r\n')
                        stream, _ = listener.accept()
                        with stream:
                            for _ in range(30):
                                stream.sendall(b'x')
                                if stop.wait(delay):
                                    break
                        self.wfile.write(b'226 complete\r\n')
                    elif command == 'QUIT':
                        break
                    else:
                        self.wfile.write(b'500 unexpected\r\n')
            except (BrokenPipeError, ConnectionResetError):
                pass
            finally:
                if listener:
                    listener.close()

    server = socketserver.TCPServer(('127.0.0.1', 0), Handler)
    worker = threading.Thread(target=server.serve_forever)
    worker.start()
    try:
        yield f'ftp://127.0.0.1:{server.server_address[1]}/file'
    finally:
        stop.set()
        server.shutdown()
        server.server_close()
        worker.join(timeout=2)
        if worker.is_alive():
            raise AssertionError('fixture leaked FTP thread')


class NetworkSafetyTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(dir=ROOT / '.cache')
        self.addCleanup(self.temp.cleanup)
        self.directory = Path(self.temp.name)

    def test_untrusted_non_global_literals_never_reach_socket(self):
        hosts = ['127.0.0.1', '10.1.2.3', '169.254.169.254', '0.0.0.0',
                 '192.168.1.1', '100.64.0.1', '192.0.2.1', '224.0.0.1',
                 '255.255.255.255', '[::1]', '[::]', '[fc00::1]', '[fe80::1]',
                 '[::ffff:127.0.0.1]', '[2002:7f00:1::]', '[64:ff9b::7f00:1]',
                 '[fec0::1]']
        with patch('socket.socket.connect', side_effect=AssertionError('unsafe connection')):
            for host in hosts:
                with self.subTest(host=host), self.assertRaisesRegex(RuntimeError, 'destination'):
                    d.download_python(f'http://{host}/file', self.directory,
                                      'file', [], untrusted=True)
        self.assertFalse((self.directory / 'file.part').exists())

    def test_untrusted_context_flows_through_public_backend(self):
        with patch('socket.socket.connect', side_effect=AssertionError('unsafe connection')):
            with d.untrusted_downloads():
                with self.assertRaisesRegex(RuntimeError, 'destination'):
                    d.download('http://127.0.0.1/file', self.directory,
                               'file', [])
        with patch.object(d, 'download_python', return_value=[]) as backend:
            d.download('http://127.0.0.1/file', self.directory, 'file', [])
        self.assertIsNone(backend.call_args.kwargs.get('untrusted'))

    def test_untrusted_rejects_ftp_and_url_credentials(self):
        for url in ('ftp://example.org/file', 'http://user:secret@example.org/file'):
            with self.subTest(url=url), self.assertRaises(RuntimeError):
                d.download_python(url, self.directory, 'file', [], untrusted=True)

    def test_mixed_dns_answers_are_rejected_before_connect(self):
        answers = [(socket.AF_INET, socket.SOCK_STREAM, 6, '', (ip, 80))
                   for ip in ('93.184.216.34', '127.0.0.1')]
        with patch.object(n, 'resolve_addresses', return_value=answers), patch.object(n.socket, 'socket') as create:
            with self.assertRaisesRegex(RuntimeError, 'destination'):
                n.connect_public(('archive.example', 80), 1)
        create.assert_not_called()

    def test_connector_uses_one_dns_lookup_and_numeric_validated_address(self):
        answer = [(socket.AF_INET, socket.SOCK_STREAM, 6, '', ('93.184.216.34', 443))]
        sock = Mock()
        with patch.object(n, 'resolve_addresses', side_effect=[answer, AssertionError('DNS rebound')]) as dns, patch.object(n.socket, 'getaddrinfo', side_effect=AssertionError('unbounded DNS')), patch.object(n.socket, 'socket', return_value=sock):
            self.assertIs(n.connect_public(('archive.example', 443), 1), sock)
        dns.assert_called_once()
        sock.connect.assert_called_once_with(('93.184.216.34', 443))

    def test_each_connect_attempt_uses_remaining_deadline(self):
        answers = [(socket.AF_INET, socket.SOCK_STREAM, 6, '', (ip, 80))
                   for ip in ('93.184.216.34', '93.184.216.35')]
        first, second = Mock(), Mock()
        first.connect.side_effect = OSError('first address unavailable')
        with patch.object(n, 'resolve_addresses', return_value=answers), patch.object(n.socket, 'socket', side_effect=[first, second]), patch.object(n.time, 'monotonic', side_effect=[0, 0.7]):
            self.assertIs(n.connect_public(('archive.example', 80), 1, deadline=1), second)
        first.settimeout.assert_called_with(1)
        self.assertAlmostEqual(second.settimeout.call_args.args[0], 0.3)
        first.close.assert_called_once()

    def test_tls_uses_original_hostname_and_verified_default_context(self):
        import http.client
        import ssl
        conn = http.client.HTTPSConnection('archive.example', timeout=1)
        n.configure_connection(conn, time.monotonic() + 2, True)
        self.assertTrue(conn._context.check_hostname)
        self.assertEqual(conn._context.verify_mode, ssl.CERT_REQUIRED)
        context, sock = Mock(), Mock()
        conn._context = context
        with patch.object(n, 'resolve_addresses', return_value=[(socket.AF_INET, socket.SOCK_STREAM, 6, '', ('93.184.216.34', 443))]), patch.object(n.socket, 'socket', return_value=sock):
            conn.connect()
        context.wrap_socket.assert_called_once_with(sock, server_hostname='archive.example')
        conn.close()

    def test_deadline_resolution_uses_configured_dns_without_libc_block(self):
        def reply(query, *args):
            kind = struct.unpack('!H', query[-4:-2])[0]
            address = socket.inet_pton(socket.AF_INET if kind == 1 else socket.AF_INET6,
                                       '93.184.216.34' if kind == 1 else '2606:2800:220:1:248:1893:25c8:1946')
            return (query[:2] + struct.pack('!HHHHH', 0x8180, 1, 1, 0, 0) + query[12:] +
                    b'\xc0\x0c' + struct.pack('!HHIH', kind, 1, 60, len(address)) + address)
        with patch.object(n.socket, 'getaddrinfo', side_effect=AssertionError('unbounded libc DNS')), patch.object(n, 'dns_exchange', side_effect=reply) as exchange, patch.object(n, 'configured_nameservers', return_value=['192.0.2.53']), patch.object(n, 'hosts_addresses', return_value=[]):
            answers = n.resolve_addresses('archive.example', 443, time.monotonic() + 1)
        self.assertEqual({item[4][0] for item in answers},
                         {'93.184.216.34', '2606:2800:220:1:248:1893:25c8:1946'})
        self.assertEqual(exchange.call_count, 2)

    def test_expired_connector_closes_socket(self):
        answer = [(socket.AF_INET, socket.SOCK_STREAM, 6, '', ('93.184.216.34', 443))]
        sock = Mock()
        with patch.object(n, 'resolve_addresses', return_value=answer), patch.object(n.socket, 'socket', return_value=sock), patch.object(n.time, 'monotonic', return_value=2):
            with self.assertRaisesRegex(RuntimeError, 'timed out'):
                n.connect_public(('archive.example', 443), 1, deadline=1)
        sock.close.assert_called_once()

    def test_tls_handshake_and_request_send_use_remaining_budget(self):
        import http.client
        context, sock = Mock(), Mock()
        context.wrap_socket.return_value = sock
        conn = http.client.HTTPSConnection('archive.example', timeout=1, context=context)
        n.configure_connection(conn, 1, True)
        answer = [(socket.AF_INET, socket.SOCK_STREAM, 6, '', ('93.184.216.34', 443))]
        with patch.object(n, 'resolve_addresses', return_value=answer), patch.object(n.socket, 'socket', return_value=sock), patch.object(n.time, 'monotonic', side_effect=[0, 0, 0.7, 0.8]):
            conn.connect()
            conn.send(b'test')
        self.assertAlmostEqual(sock.settimeout.call_args_list[-2].args[0], 0.3)
        self.assertAlmostEqual(sock.settimeout.call_args_list[-1].args[0], 0.2)
        conn.close()

    def test_redirect_rechecks_literal_and_hostname_destinations(self):
        request = urllib.request.Request('https://archive.example/a')
        handler = d.SafeRedirect(untrusted=True)
        with self.assertRaisesRegex(RuntimeError, 'destination'):
            handler.redirect_request(request, None, 302, '', {}, 'http://169.254.169.254/a')
        redirected = handler.redirect_request(request, None, 302, '', {}, 'http://internal.example/a')
        redirected.timeout = 1
        answer = [(socket.AF_INET, socket.SOCK_STREAM, 6, '', ('10.0.0.1', 80))]
        with patch.object(n, 'resolve_addresses', return_value=answer), patch.object(n.socket, 'socket') as create:
            with self.assertRaisesRegex(RuntimeError, 'destination'):
                n.PublicHTTPHandler().http_open(redirected)
        create.assert_not_called()

    def test_restricted_opener_has_no_proxy_or_ftp_escape(self):
        def inspect_open(opener, request, **kwargs):
            self.assertFalse(any(isinstance(h, urllib.request.ProxyHandler)
                                 and h.proxies for h in opener.handlers))
            raise RuntimeError('inspected')
        with patch.dict('os.environ', {'http_proxy': 'http://127.0.0.1:1', 'https_proxy': 'http://127.0.0.1:1'}), patch('urllib.request.OpenerDirector.open', new=inspect_open):
            with self.assertRaisesRegex(RuntimeError, 'inspected'):
                d.download_python('https://archive.example/a', self.directory, 'file', [], untrusted=True)

    def test_configured_resolver_and_hosts_file_parsing(self):
        with patch.object(Path, 'read_text', return_value='nameserver 127.0.0.53\nnameserver ::1 # local\nsearch ignored.example\n'):
            self.assertEqual(n.configured_nameservers(), ['127.0.0.53', '::1'])
        with patch.object(Path, 'read_text', return_value='127.0.0.1 localhost LOCAL\n::1 local # ignored\n'):
            self.assertEqual(n.hosts_addresses('LOCAL.'), ['127.0.0.1', '::1'])

    def test_dns_alias_and_pointer_loop_are_bounded(self):
        question = b'\x07archive\x07example\x00' + struct.pack('!HH', 1, 1)
        query = b'\x00\x01' + struct.pack('!HHHHH', 0x0100, 1, 0, 0, 0) + question
        alias = b'\x03cdn\x07example\x00'
        answer = (b'\xc0\x0c' + struct.pack('!HHIH', 5, 1, 60, len(alias)) + alias +
                  alias + struct.pack('!HHIH', 1, 1, 60, 4) + socket.inet_aton('93.184.216.34'))
        packet = b'\x00\x01' + struct.pack('!HHHHH', 0x8180, 1, 2, 0, 0) + question + answer
        self.assertEqual(n.dns_answers(packet, query, 'archive.example', 1),
                         (['93.184.216.34'], 'cdn.example'))
        with self.assertRaises(OSError):
            n.dns_name(b'\xc0\x00', 0)
        with self.assertRaises(OSError):
            n.dns_answers(b'bad', query, 'archive.example', 1)

    def test_dns_udp_truncation_retries_tcp_under_same_deadline(self):
        query = b'\x00\x01' + struct.pack('!HHHHH', 0x0100, 1, 0, 0, 0)
        truncated = b'\x00\x01' + struct.pack('!HHHHH', 0x8380, 1, 0, 0, 0)
        reply = b'\x00\x01' + struct.pack('!HHHHH', 0x8180, 1, 0, 0, 0)
        udp, tcp = Mock(), Mock()
        udp.__enter__ = Mock(return_value=udp)
        udp.__exit__ = Mock(return_value=False)
        tcp.__enter__ = Mock(return_value=tcp)
        tcp.__exit__ = Mock(return_value=False)
        udp.recv.return_value = truncated
        tcp.recv.side_effect = [struct.pack('!H', len(reply)), reply]
        with patch.object(n.socket, 'socket', side_effect=[udp, tcp]):
            self.assertEqual(n.dns_exchange(query, '127.0.0.53', time.monotonic() + 1), reply)
        tcp.sendall.assert_called_once_with(struct.pack('!H', len(query)) + query)

    def test_dns_timeout_never_falls_back_to_unbounded_libc(self):
        with patch.object(n, 'hosts_addresses', return_value=[]), patch.object(n, 'configured_nameservers', return_value=['127.0.0.53']), patch.object(n, 'dns_exchange', side_effect=TimeoutError), patch.object(n.socket, 'getaddrinfo', side_effect=AssertionError('libc fallback')):
            with self.assertRaisesRegex(RuntimeError, 'DNS resolution failed'):
                n.resolve_addresses('archive.example', 80, time.monotonic() + 1)

    def test_restricted_download_roundtrip_pins_initial_and_redirect_hops(self):
        replies = [b'HTTP/1.0 302 Found\r\nLocation: https://cdn.example/file\r\n\r\n',
                   b'HTTP/1.0 200 OK\r\nContent-Length: 4\r\n\r\ndata']
        sockets = []
        for reply in replies:
            sock = Mock()
            sock.makefile.return_value = io.BufferedReader(io.BytesIO(reply))
            sockets.append(sock)
        answers = [(socket.AF_INET, socket.SOCK_STREAM, 6, '', ('93.184.216.34', 443))]
        with patch.object(n, 'resolve_addresses', return_value=answers) as resolve, patch.object(n.socket, 'socket', side_effect=sockets), patch('ssl.SSLContext.wrap_socket', side_effect=lambda stream, **kwargs: stream), patch.dict(os.environ, {'https_proxy': 'http://127.0.0.1:1'}):
            files = d.download_python('https://archive.example/file', self.directory,
                                      'file', ['X-API-Key: private'], untrusted=True)
        self.assertEqual(Path(files[0]).read_bytes(), b'data')
        self.assertEqual([call.args[:2] for call in resolve.call_args_list],
                         [('archive.example', 443), ('cdn.example', 443)])
        for sock in sockets:
            sock.connect.assert_called_once_with(('93.184.216.34', 443))
            sock.close.assert_called()
        self.assertIn(b'X-Api-Key: private', sockets[0].sendall.call_args.args[0])
        self.assertNotIn(b'private', sockets[1].sendall.call_args.args[0])

    def test_real_slow_drip_body_has_absolute_deadline(self):
        self.assert_slow_deadline('body')

    def test_real_slow_drip_headers_have_absolute_deadline(self):
        self.assert_slow_deadline('headers')

    def test_real_slow_drip_chunk_trailers_have_absolute_deadline(self):
        self.assert_slow_deadline('trailers')

    def test_redirect_chain_shares_one_download_deadline(self):
        self.assert_slow_deadline('redirects')

    def test_context_is_isolated_between_async_tasks_and_cannot_be_disabled(self):
        import asyncio

        async def child(untrusted):
            if untrusted:
                with d.untrusted_downloads():
                    await asyncio.sleep(0)
                    return d._UNTRUSTED.get()
            await asyncio.sleep(0)
            return d._UNTRUSTED.get()

        async def run():
            return await asyncio.gather(child(True), child(False))

        self.assertEqual(asyncio.run(run()), [True, False])
        with d.untrusted_downloads(), self.assertRaisesRegex(RuntimeError, 'destination'):
            d.download_python('http://127.0.0.1/file', self.directory, 'file', [], untrusted=False)

    def test_trusted_local_ftp_has_absolute_deadline(self):
        with ftp_server() as url, patch.dict(os.environ, {'LEECH_DOWNLOAD_TIMEOUT': '1'}):
            started = time.monotonic()
            with self.assertRaisesRegex(RuntimeError, 'timed out'):
                d.download_python(url, self.directory, 'file', [])
            self.assertLess(time.monotonic() - started, 1.8)
        self.assertFalse((self.directory / 'file.part').exists())

    def test_trusted_local_ftp_remains_usable(self):
        with ftp_server(delay=0.001) as url:
            files = d.download_python(url, self.directory, 'file', [])
        self.assertEqual(Path(files[0]).read_bytes(), b'x' * 30)

    def assert_slow_deadline(self, mode):
        with slow_server(mode) as url, patch.dict(os.environ, {'LEECH_DOWNLOAD_TIMEOUT': '1'}):
            started = time.monotonic()
            with self.assertRaisesRegex(RuntimeError, 'timed out'):
                d.download_python(url, self.directory, 'file', [])
            self.assertLess(time.monotonic() - started, 1.8)
        self.assertFalse((self.directory / 'file.part').exists())
        self.assertFalse((self.directory / 'file').exists())
