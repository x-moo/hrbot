"""Per-request destination policy for untrusted archive downloads.

HTTP(S) connections are made to the validated numeric address, never by a
second hostname lookup. TLS continues to use the original hostname. No
process-global socket or resolver monkeypatches are used.

DNS uses /etc/hosts then the numeric nameservers in /etc/resolv.conf, with
bounded UDP/TCP transactions. It intentionally does not use blocking NSS,
mDNS, resolver search domains, or guessed public resolver fallbacks. This
Linux deployment convention also applies to trusted operator HTTP/FTP.
Limits: 3 resolvers, 2 seconds per DNS operation, 8 CNAME hops, 16 destination
addresses, 60 seconds per socket operation, and the shared download deadline.
"""
import http.client
import ftplib
import email.message
import ipaddress
import io
import secrets
import socket
import struct
import time
import urllib.request
from pathlib import Path
from urllib.parse import urlsplit, unquote


def public_address(address):
    """Conservatively reject local/special and IPv6 transition destinations."""
    ip = ipaddress.ip_address(address)
    if not ip.is_global or ip.is_multicast or ip.is_reserved:
        return False
    if isinstance(ip, ipaddress.IPv6Address):
        # Tunnels/translation may turn an apparently public IPv6 address into
        # a private IPv4 destination; downloads have no reason to use these.
        if ip.is_site_local or ip.ipv4_mapped or ip.sixtofour or ip.teredo:
            return False
        if ip in ipaddress.ip_network('64:ff9b::/96'):
            return False
    return True


def validate_url(url):
    parsed = urlsplit(url)
    if (parsed.scheme not in ('http', 'https') or not parsed.hostname
            or parsed.username is not None or parsed.password is not None
            or '%' in parsed.hostname):
        raise RuntimeError('unsafe download destination')
    # Force malformed ports to fail before touching the network.
    parsed.port
    try:
        ip = ipaddress.ip_address(parsed.hostname)
    except ValueError:
        return
    if not public_address(str(ip)):
        raise RuntimeError('unsafe download destination')


def hosts_addresses(host):
    """Honor operator-managed hosts entries without invoking blocking NSS."""
    result = []
    try:
        lines = Path('/etc/hosts').read_text().splitlines()
    except OSError:
        return result
    for line in lines:
        words = line.partition('#')[0].split()
        if len(words) > 1 and host.rstrip('.').lower() in [w.lower() for w in words[1:]]:
            result.append(str(ipaddress.ip_address(words[0])))
    return result


def configured_nameservers():
    """Use only configured numeric resolvers; never guess a public DNS server."""
    servers = []
    try:
        lines = Path('/etc/resolv.conf').read_text().splitlines()
    except OSError:
        lines = []
    for line in lines:
        words = line.partition('#')[0].split()
        if len(words) >= 2 and words[0] == 'nameserver':
            servers.append(str(ipaddress.ip_address(words[1])))
    if not servers:
        raise RuntimeError('no configured download DNS resolver')
    return servers[:3]


def dns_exchange(query, nameserver, deadline):
    """One bounded DNS transaction; connected UDP checks the reply peer.

    A truncated UDP reply is retried over TCP to the same configured server.
    No worker threads, child processes, search domains or external fallbacks.
    """
    ip = ipaddress.ip_address(nameserver)
    family = socket.AF_INET6 if ip.version == 6 else socket.AF_INET
    address = (str(ip), 53)
    with socket.socket(family, socket.SOCK_DGRAM) as sock:
        sock.settimeout(min(2, remaining(deadline)))
        sock.connect(address)
        sock.settimeout(min(2, remaining(deadline)))
        sock.send(query)
        sock.settimeout(min(2, remaining(deadline)))
        packet = sock.recv(65535)
    if len(packet) < 12 or packet[:2] != query[:2]:
        raise OSError('invalid DNS response')
    if not (struct.unpack_from('!H', packet, 2)[0] & 0x0200):
        return packet

    def read_exact(sock, count):
        data = bytearray()
        while len(data) < count:
            sock.settimeout(min(2, remaining(deadline)))
            part = sock.recv(count - len(data))
            if not part:
                raise OSError('incomplete DNS response')
            data.extend(part)
        return bytes(data)

    with socket.socket(family, socket.SOCK_STREAM) as sock:
        sock.settimeout(min(2, remaining(deadline)))
        sock.connect(address)
        sock.settimeout(min(2, remaining(deadline)))
        sock.sendall(struct.pack('!H', len(query)) + query)
        length = struct.unpack('!H', read_exact(sock, 2))[0]
        return read_exact(sock, length)


def dns_name(packet, offset):
    """Decode bounded DNS compression pointers and return (name, next offset)."""
    labels, seen, end = [], set(), None
    while True:
        if offset in seen or len(seen) > 128 or offset >= len(packet):
            raise OSError('invalid DNS name')
        seen.add(offset)
        size = packet[offset]
        if size & 0xc0 == 0xc0:
            if offset + 1 >= len(packet):
                raise OSError('invalid DNS pointer')
            if end is None:
                end = offset + 2
            offset = ((size & 0x3f) << 8) | packet[offset + 1]
            continue
        offset += 1
        if size == 0:
            return '.'.join(labels).lower(), end if end is not None else offset
        if size > 63 or offset + size > len(packet):
            raise OSError('invalid DNS label')
        try:
            labels.append(packet[offset:offset + size].decode('ascii'))
        except UnicodeError:
            raise OSError('invalid DNS label') from None
        offset += size


def dns_answers(packet, query, host, kind):
    """Accept only answers bound to the exact query or its bounded CNAME chain."""
    if len(packet) < 12 or packet[:2] != query[:2]:
        raise OSError('invalid DNS response')
    flags, qd, an, _, _ = struct.unpack_from('!HHHHH', packet, 2)
    if flags & 0xfa00 != 0x8000 or qd != 1 or an > 256:
        raise OSError('invalid DNS response')
    question, offset = dns_name(packet, 12)
    if question != host or packet[offset:offset + 4] != struct.pack('!HH', kind, 1):
        raise OSError('unrelated DNS response')
    if flags & 0xf == 3:  # NXDOMAIN, not a transient server error.
        return [], None
    if flags & 0xf:
        raise OSError('DNS resolver failed')
    offset += 4
    records, aliases = {}, {}
    for _ in range(an):
        owner, offset = dns_name(packet, offset)
        if offset + 10 > len(packet):
            raise OSError('truncated DNS answer')
        rtype, rclass, _, size = struct.unpack_from('!HHIH', packet, offset)
        offset += 10
        end = offset + size
        if end > len(packet):
            raise OSError('truncated DNS answer')
        if rclass == 1 and rtype == kind and size == (4 if kind == 1 else 16):
            records.setdefault(owner, []).append(str(ipaddress.ip_address(packet[offset:end])))
        elif rclass == 1 and rtype == 5:
            alias, after = dns_name(packet, offset)
            if after != end:
                raise OSError('invalid DNS alias')
            aliases[owner] = alias
        offset = end
    owner, seen = host, set()
    while owner in aliases:
        if owner in seen or len(seen) >= 8:
            raise OSError('DNS alias loop')
        seen.add(owner)
        owner = aliases[owner]
    return records.get(owner, []), owner if owner != host else None


def resolve_addresses(host, port, deadline):
    try:
        values = [str(ipaddress.ip_address(host))]
    except ValueError:
        values = hosts_addresses(host)
    if not values:
        # System getaddrinfo has no cancellable deadline. Use a small DNS stub
        # against configured resolvers instead of leaking a resolver thread.
        host = host.rstrip('.').encode('idna').decode('ascii').lower()
        nameservers = configured_nameservers()
        for kind in (1, 28):
            owner = host
            for _ in range(8):
                labels = owner.split('.')
                if not all(0 < len(label) <= 63 for label in labels) or len(owner) > 253:
                    raise OSError('invalid DNS hostname')
                name = b''.join(bytes([len(label)]) + label.encode('ascii') for label in labels) + b'\x00'
                query = secrets.token_bytes(2) + struct.pack('!HHHHH', 0x0100, 1, 0, 0, 0) + name + struct.pack('!HH', kind, 1)
                for server in nameservers:
                    try:
                        packet = dns_exchange(query, server, deadline)
                        addresses, alias = dns_answers(packet, query, owner, kind)
                        break
                    except OSError:
                        remaining(deadline)
                else:
                    raise RuntimeError('download DNS resolution failed')
                if addresses:
                    values.extend(addresses)
                    break
                if not alias:
                    break
                owner = alias
            else:
                raise RuntimeError('download DNS alias limit exceeded')
    result = []
    for value in dict.fromkeys(values):
        ip = ipaddress.ip_address(value)
        family = socket.AF_INET6 if ip.version == 6 else socket.AF_INET
        sockaddr = (value, port, 0, 0) if ip.version == 6 else (value, port)
        result.append((family, socket.SOCK_STREAM, socket.IPPROTO_TCP, '', sockaddr))
    return result


def connect_public(address, timeout, source_address=None, *, deadline=None,
                   restricted=True):
    host, port = address
    # All answers must be safe: a mixed public/private set is not a safe host.
    # Low-level callers get a bound too; production supplies the shared
    # download deadline instead of restarting it for each redirect.
    if deadline is None:
        deadline = time.monotonic() + min(60, timeout)
    addresses = resolve_addresses(host, port, deadline)
    if not addresses or len(addresses) > 16:
        raise RuntimeError('unsafe download destination')
    if restricted and any(not public_address(item[4][0]) for item in addresses):
        raise RuntimeError('unsafe download destination')
    last_error = None
    for family, kind, proto, _, sockaddr in addresses:
        sock = socket.socket(family, kind, proto)
        try:
            sock.settimeout(remaining(deadline) if deadline is not None else timeout)
            if source_address:
                sock.bind(source_address)
            # Numeric sockaddr avoids DNS re-resolution / rebinding here.
            sock.connect(sockaddr)
            return sock
        except BaseException as error:
            sock.close()
            if not isinstance(error, OSError):
                raise
            last_error = error
    raise last_error


class PublicHTTPHandler(urllib.request.HTTPHandler):
    def __init__(self, *, deadline=None, untrusted=True):
        super().__init__()
        self.deadline = deadline
        self.untrusted = untrusted

    def http_open(self, request):
        if self.untrusted:
            validate_url(request.full_url)
        def connection(host, **kwargs):
            conn = http.client.HTTPConnection(host, **kwargs)
            configure_connection(conn, self.deadline, self.untrusted)
            return conn
        return self.do_open(connection, request)


class PublicHTTPSHandler(urllib.request.HTTPSHandler):
    def __init__(self, *, deadline=None, untrusted=True):
        super().__init__()
        self.deadline = deadline
        self.untrusted = untrusted

    def https_open(self, request):
        if self.untrusted:
            validate_url(request.full_url)
        def connection(host, **kwargs):
            conn = http.client.HTTPSConnection(host, **kwargs)
            configure_connection(conn, self.deadline, self.untrusted)
            return conn
        # HTTPSConnection retains the URL hostname for SNI and verification;
        # only its TCP connector is replaced with the validated-IP connector.
        return self.do_open(connection, request, context=self._context)


def remaining(deadline):
    budget = deadline - time.monotonic()
    if budget <= 0:
        raise RuntimeError('download timed out')
    return min(60, budget)


def configure_connection(connection, deadline, untrusted):
    connector = connect_public if untrusted else socket.create_connection
    if deadline is None:
        connection._create_connection = connector
        return

    def connect(address, timeout, source_address=None):
        if untrusted:
            stream = connector(address, remaining(deadline), source_address, deadline=deadline)
        else:
            stream = connect_public(address, remaining(deadline), source_address,
                                    deadline=deadline, restricted=False)
        try:
            # HTTPSConnection immediately wraps this socket; the handshake
            # must not receive a fresh copy of the original connect timeout.
            stream.settimeout(remaining(deadline))
            return stream
        except BaseException:
            stream.close()
            raise

    original_send = connection.send
    def send(data):
        if connection.sock is None:
            connection.connect()
        connection.sock.settimeout(remaining(deadline))
        return original_send(data)

    class Response(http.client.HTTPResponse):
        def __init__(self, stream, *args, **kwargs):
            # socket.dup() is not supported for SSLSocket. Instead the reader
            # retains a native unbuffered makefile, which owns its socket ref.
            super().__init__(stream, *args, **kwargs)
            raw = self.fp.detach()
            self.fp = io.BufferedReader(DeadlineFile(raw, stream, deadline))

    connection._create_connection = connect
    connection.send = send
    connection.response_class = Response


class DeadlineFile(io.RawIOBase):
    """Check an absolute deadline below all HTTP parser buffering/loops."""
    def __init__(self, raw, stream, deadline):
        super().__init__()
        self.raw = raw
        self.stream = stream
        self.deadline = deadline

    def readable(self):
        return True

    def readinto(self, buffer):
        self.stream.settimeout(remaining(self.deadline))
        try:
            count = self.raw.readinto(buffer)
        except TimeoutError:
            raise RuntimeError('download timed out') from None
        remaining(self.deadline)
        return count

    def close(self):
        if not self.closed:
            try:
                self.raw.close()
            finally:
                super().close()


class DeadlineFTPHandler(urllib.request.FTPHandler):
    """Trusted operator FTP only, with bounded control and data channels."""
    def __init__(self, deadline, *, untrusted=False):
        self.deadline = deadline
        self.untrusted = untrusted

    def ftp_open(self, request):
        if self.untrusted:
            raise RuntimeError('unsafe download destination')
        parsed = urlsplit(request.full_url)
        ftp = ftplib.FTP()
        data = None
        try:
            ftp.sock = connect_public((parsed.hostname, parsed.port or 21),
                                      remaining(self.deadline), deadline=self.deadline,
                                      restricted=False)
            ftp.af = ftp.sock.family
            raw = ftp.sock.makefile('rb', buffering=0)
            ftp.file = io.TextIOWrapper(io.BufferedReader(
                DeadlineFile(raw, ftp.sock, self.deadline)), encoding=ftp.encoding)
            # Every command send shares the remaining budget; replies are
            # bounded at the raw-reader level, including multiline greetings.
            putline = ftp.putline
            def sendline(line):
                ftp.sock.settimeout(remaining(self.deadline))
                return putline(line)
            ftp.putline = sendline
            ftp.welcome = ftp.getresp()
            ftp.login(unquote(parsed.username or 'anonymous'),
                      unquote(parsed.password or 'anonymous@'))
            path = unquote(parsed.path)
            if path.endswith(';type=i'):
                path = path[:-7]
            directory, _, filename = path.rpartition('/')
            if not filename:
                raise RuntimeError('FTP download requires a file')
            if directory:
                ftp.cwd(directory)
            ftp.voidcmd('TYPE I')
            host, port = ftp.makepasv()
            data = connect_public((host, port), remaining(self.deadline),
                                  deadline=self.deadline, restricted=False)
            reply = ftp.sendcmd('RETR ' + filename)
            if reply[0] != '1':
                raise ftplib.error_reply(reply)
            length = ftplib.parse150(reply) if reply[:3] == '150' else None
            raw = data.makefile('rb', buffering=0)
            stream = io.BufferedReader(DeadlineFile(raw, data, self.deadline))
            data.close()  # makefile owns the remaining socket reference.
            data = None
            headers = email.message.Message()
            if length is not None:
                headers['Content-Length'] = str(length)

            class Response(urllib.response.addinfourl):
                def close(self):
                    try:
                        super().close()
                    finally:
                        ftp.close()  # No blocking QUIT/voidresp after timeout.

            return Response(stream, headers, request.full_url)
        except BaseException:
            if data is not None:
                data.close()
            ftp.close()
            raise
