"""The sandbox's egress proxy (guest/proxy.py), run with the standard library only.

ClientHello messages come from Python's own TLS stack, so the parser meets real ones; the
hostile cases are built by editing them.

    python -m unittest discover -s test -p "test_*.py"
"""

import asyncio
import shutil
import socket
import ssl
import struct
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "guest"))

import proxy  # noqa: E402


def real_hello(server_name: str | None) -> bytes:
    """The bytes a real TLS client sends first, with or without a server name."""
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    context.check_hostname = False
    context.verify_mode = ssl.CERT_NONE
    incoming, outgoing = ssl.MemoryBIO(), ssl.MemoryBIO()
    client = context.wrap_bio(incoming, outgoing, server_hostname=server_name)
    try:
        client.do_handshake()
    except ssl.SSLWantReadError:
        pass
    return outgoing.read()


def record(payload: bytes, kind: int = 22) -> bytes:
    return bytes([kind, 3, 3]) + struct.pack("!H", len(payload)) + payload


def message_of(records: bytes) -> bytes:
    """The handshake message carried by a run of handshake records."""
    message, at = b"", 0
    while at < len(records):
        length = struct.unpack("!H", records[at + 3:at + 5])[0]
        message += records[at + 5:at + 5 + length]
        at += 5 + length
    return message


def extensions_of(message: bytes) -> tuple[bytes, list[tuple[int, bytes]]]:
    """(everything before the extensions block, the extensions) of a ClientHello message."""
    body = message[4:]
    at = 34
    at += 1 + body[at]
    at += 2 + struct.unpack("!H", body[at:at + 2])[0]
    at += 1 + body[at]
    head, rest = body[:at], body[at + 2:]
    found = []
    while rest:
        kind, length = struct.unpack("!HH", rest[:4])
        found.append((kind, rest[4:4 + length]))
        rest = rest[4 + length:]
    return head, found


def build_hello(head: bytes, extensions: list[tuple[int, bytes]]) -> bytes:
    block = b"".join(struct.pack("!HH", kind, len(data)) + data for kind, data in extensions)
    body = head + struct.pack("!H", len(block)) + block
    return b"\x01" + len(body).to_bytes(3, "big") + body


def edited_hello(name: str, edit) -> bytes:
    head, extensions = extensions_of(message_of(real_hello(name)))
    return record(build_hello(head, edit(extensions)))


def server_names(*names: bytes) -> bytes:
    entries = b"".join(b"\x00" + struct.pack("!H", len(n)) + n for n in names)
    return struct.pack("!H", len(entries)) + entries


class InspectorTests(unittest.TestCase):
    def accept(self, data: bytes, host: str = "pypi.org") -> bytes:
        return proxy.Inspector(host).feed(data)

    def refuse(self, data: bytes, host: str = "pypi.org") -> None:
        with self.assertRaises(proxy.Refused):
            proxy.Inspector(host).feed(data)

    def test_a_real_hello_for_the_tunnel_host_passes_unchanged(self):
        hello = real_hello("pypi.org")
        inspector = proxy.Inspector("pypi.org")
        self.assertEqual(inspector.feed(hello), hello)
        self.assertTrue(inspector.greeted)

    def test_the_name_is_compared_without_case(self):
        self.assertTrue(self.accept(real_hello("PyPI.org")))

    def test_another_name_no_name_and_a_trailing_dot_are_refused(self):
        for name in ("www.fastly.com", "pypi.org.evil.example", "evil.pypi.org", "pypi.org.", None):
            with self.subTest(name=name):
                self.refuse(real_hello(name) if name != "pypi.org." else edited_hello(
                    "pypi.org", lambda e: [(k, server_names(b"pypi.org.") if k == 0 else d) for k, d in e]))

    def test_nothing_is_released_until_the_whole_hello_has_arrived(self):
        hello = real_hello("pypi.org")
        inspector = proxy.Inspector("pypi.org")
        for index in range(len(hello) - 1):
            self.assertEqual(inspector.feed(hello[index:index + 1]), b"")
        self.assertFalse(inspector.greeted)
        self.assertEqual(inspector.feed(hello[-1:]), hello)

    def test_a_hello_split_over_many_records_is_still_checked(self):
        for name, ok in (("pypi.org", True), ("www.fastly.com", False)):
            message = message_of(real_hello(name))
            for size in (1, 3, 100):
                split = b"".join(record(message[i:i + size]) for i in range(0, len(message), size))
                with self.subTest(name=name, size=size):
                    if ok:
                        self.assertEqual(self.accept(split), split)
                    else:
                        self.refuse(split)

    def test_a_second_message_cannot_ride_in_the_hello_record(self):
        message = message_of(real_hello("pypi.org"))
        self.refuse(record(message + message_of(real_hello("www.fastly.com"))))

    def test_encrypted_hello_extensions_are_refused(self):
        for kind in (0xFE0D, 0xFE0A, 0xFD00, 0xFFCE):
            with self.subTest(kind=hex(kind)):
                self.refuse(edited_hello("pypi.org", lambda e, k=kind: e + [(k, b"\x00" * 8)]))

    def test_an_ordinary_unknown_extension_is_allowed(self):
        self.assertTrue(self.accept(edited_hello("pypi.org", lambda e: e + [(0x0A0A, b"")])))

    def test_ambiguous_server_names_are_refused(self):
        evil = server_names(b"www.fastly.com")
        cases = {
            "two extensions": lambda e: e + [(0, evil)],
            "two names": lambda e: [(k, server_names(b"pypi.org", b"www.fastly.com") if k == 0 else d) for k, d in e],
            "other type": lambda e: [(k, b"\x00\x0b\x01\x00\x08pypi.org" if k == 0 else d) for k, d in e],
            "short list": lambda e: [(k, d + b"x" if k == 0 else d) for k, d in e],
            "null byte": lambda e: [(k, server_names(b"pypi.org\x00.evil") if k == 0 else d) for k, d in e],
        }
        for label, edit in cases.items():
            with self.subTest(label):
                self.refuse(edited_hello("pypi.org", edit))

    def test_trailing_bytes_inside_the_hello_are_refused(self):
        message = message_of(real_hello("pypi.org"))
        longer = b"\x01" + (len(message) - 4 + 3).to_bytes(3, "big") + message[4:] + b"abc"
        self.refuse(record(longer))

    def test_anything_that_is_not_a_tls_hello_is_refused(self):
        for label, data in (
            ("ssh", b"SSH-2.0-OpenSSH_9\r\n"),
            ("http", b"GET / HTTP/1.1\r\nHost: pypi.org\r\n\r\n"),
            ("data first", record(b"x" * 20, kind=23)),
            ("alert first", record(b"\x02\x28", kind=21)),
            ("other handshake", record(b"\x10\x00\x00\x01x")),
            ("old version", b"\x80\x2e\x01\x03\x01" + b"\x00" * 40),
            ("empty record", record(b"")),
            ("huge record", b"\x16\x03\x03\xff\xff" + b"\x00" * 100),
            ("huge hello", record(b"\x01\xff\xff\xff\x03\x03")),
        ):
            with self.subTest(label):
                self.refuse(data)

    def greeted(self) -> "proxy.Inspector":
        inspector = proxy.Inspector("pypi.org")
        inspector.feed(real_hello("pypi.org"))
        return inspector

    def test_later_traffic_passes(self):
        inspector = self.greeted()
        for data in (
            record(b"\x01", kind=20),                       # change_cipher_spec
            record(b"\x10\x00\x00\x21" + b"k" * 33),        # a TLS 1.2 key exchange
            record(b"\xd4\x9a" * 20),                       # a TLS 1.2 encrypted finish
            record(b"\x00" * 8 + b"z" * 32),                # explicit nonce starting at zero
            record(b"application data" * 10, kind=23),
            record(b"\x00" * 30, kind=21),
        ):
            self.assertEqual(inspector.feed(data), data)

    def test_a_second_hello_must_name_the_same_host_even_after_other_records(self):
        evil = message_of(real_hello("www.fastly.com"))
        good = message_of(real_hello("pypi.org"))
        for before in (b"", record(b"\x01", kind=20), record(b"early data", kind=23)):
            with self.subTest(before=before[:1]):
                inspector = self.greeted()
                inspector.feed(before)
                with self.assertRaises(proxy.Refused):
                    inspector.feed(record(evil))
        inspector = self.greeted()
        self.assertEqual(inspector.feed(record(good)), record(good))

    def test_a_second_hello_split_after_its_first_byte_is_still_checked(self):
        evil = message_of(real_hello("www.fastly.com"))
        for cut in (1, 4, 5, 40):
            with self.subTest(cut=cut):
                inspector = self.greeted()
                self.assertEqual(inspector.feed(record(evil[:cut])), b"")
                with self.assertRaises(proxy.Refused):
                    inspector.feed(record(evil[cut:]))

    def test_a_second_hello_is_checked_whatever_version_it_claims(self):
        evil = message_of(real_hello("www.fastly.com"))
        for version in (b"\x03\x01", b"\x03\x04", b"\x00\x00", b"\x7f\x17"):
            odd = evil[:4] + version + evil[6:]
            for before in (b"", record(b"\x01", kind=20)):
                with self.subTest(version=version, after_ccs=bool(before)):
                    inspector = self.greeted()
                    inspector.feed(before)
                    with self.assertRaises(proxy.Refused):
                        inspector.feed(record(odd))

    def test_before_a_cipher_change_every_hello_shaped_record_is_checked(self):
        for payload in (b"\x01\xff\xff\xff", b"\x01\x05" + b"q" * 38):
            with self.subTest(payload=payload[:4]):
                with self.assertRaises(proxy.Refused):
                    inspector = self.greeted()
                    inspector.feed(record(payload))
                    inspector.feed(record(b"\x01", kind=20))

    def test_no_possible_hello_length_slips_past_after_a_cipher_change(self):
        # 131,396 bytes is the longest a ClientHello can be: every field at its limit.
        for claimed in (65_537, 131_071, 131_072, 131_396):
            with self.subTest(claimed=claimed):
                inspector = self.greeted()
                inspector.feed(record(b"\x01", kind=20))
                with self.assertRaises(proxy.Refused):
                    inspector.feed(record(b"\x01" + claimed.to_bytes(3, "big") + b"q" * 38))

    def test_a_length_no_hello_can_have_is_ciphertext(self):
        inspector = self.greeted()
        inspector.feed(record(b"\x01", kind=20))
        encrypted = record(b"\x01" + (131_397).to_bytes(3, "big") + b"q" * 38)
        self.assertEqual(inspector.feed(encrypted), encrypted)

    def test_after_a_cipher_change_an_encrypted_record_is_not_mistaken_for_a_hello(self):
        inspector = self.greeted()
        inspector.feed(record(b"\x01", kind=20))
        encrypted = record(b"\x01\x9a" + b"q" * 38)  # no hello is that long
        self.assertEqual(inspector.feed(encrypted), encrypted)

    def test_nothing_may_interrupt_a_split_hello(self):
        message = message_of(real_hello("pypi.org"))
        self.refuse(record(message[:40]) + record(b"\x01", kind=20) + record(message[40:]))


class ProxyTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.received = asyncio.Queue()
        self.upstreams = 0

        async def upstream(reader, writer):
            self.upstreams += 1
            while data := await reader.read(65536):
                await self.received.put(data)
                writer.write(b"reply:" + data[:5])
                await writer.drain()
            writer.close()

        self.upstream = await asyncio.start_server(upstream, "127.0.0.1", 0)
        port = self.upstream.sockets[0].getsockname()[1]

        async def resolve(host):
            return [("127.0.0.1", port)]

        self.resolved = resolve
        self.server = await self.start(resolve, lambda address: True)

    async def start(self, resolve, address_ok=None):
        options = {} if address_ok is None else {"address_ok": address_ok}
        service = proxy.Proxy(["pypi.org", "github.com"], resolve=resolve, **options)
        server = await asyncio.start_server(service.handle, "127.0.0.1", 0)
        self.addAsyncCleanup(self.stop, server)
        return server

    async def stop(self, server):
        server.close()

    async def asyncTearDown(self):
        self.upstream.close()

    async def connect(self, request: bytes, server=None):
        port = (server or self.server).sockets[0].getsockname()[1]
        reader, writer = await asyncio.open_connection("127.0.0.1", port)
        self.addCleanup(writer.close)
        writer.write(request)
        await writer.drain()
        status = await asyncio.wait_for(reader.readline(), 5)
        return reader, writer, status

    async def tunnel(self, target: str, server=None):
        request = f"CONNECT {target} HTTP/1.1\r\nHost: {target}\r\n\r\n".encode()
        reader, writer, status = await self.connect(request, server)
        if b" 200 " in status:
            await asyncio.wait_for(reader.readuntil(b"\r\n"), 5)
        return reader, writer, status

    async def closed(self, reader) -> bool:
        try:
            return await asyncio.wait_for(reader.read(), 5) == b""
        except ConnectionError:
            return True

    async def test_a_matching_hello_is_forwarded_and_the_tunnel_carries_both_ways(self):
        reader, writer, status = await self.tunnel("pypi.org:443")
        self.assertIn(b" 200 ", status)
        hello = real_hello("pypi.org")
        writer.write(hello)
        self.assertEqual(await asyncio.wait_for(self.received.get(), 5), hello)
        self.assertEqual(await asyncio.wait_for(reader.readexactly(11), 5), b"reply:" + hello[:5])
        writer.write(record(b"payload", kind=23))
        self.assertEqual(await asyncio.wait_for(self.received.get(), 5), record(b"payload", kind=23))

    async def test_a_mismatched_name_never_reaches_the_upstream(self):
        reader, writer, _ = await self.tunnel("pypi.org:443")
        writer.write(real_hello("www.fastly.com"))
        self.assertTrue(await self.closed(reader))
        self.assertEqual(self.upstreams, 0)

    async def test_a_later_mismatched_hello_closes_the_tunnel(self):
        reader, writer, _ = await self.tunnel("pypi.org:443")
        writer.write(real_hello("pypi.org"))
        await asyncio.wait_for(self.received.get(), 5)
        await asyncio.wait_for(reader.readexactly(11), 5)
        writer.write(real_hello("www.fastly.com"))
        self.assertTrue(await self.closed(reader))
        self.assertTrue(self.received.empty())

    async def test_other_hosts_ports_and_methods_are_refused(self):
        for target in ("evil.example:443", "pypi.org:22", "pypi.org:80", "1.1.1.1:443", "pypi.org.:443",
                       "pypi.org", "[::1]:443", "pypi.org:443@evil.example:443"):
            with self.subTest(target=target):
                _, _, status = await self.tunnel(target)
                self.assertIn(b" 403 ", status)
        for request in (b"GET http://pypi.org/ HTTP/1.1\r\n\r\n", b"POST / HTTP/1.1\r\n\r\n", b"nonsense\r\n\r\n"):
            with self.subTest(request=request):
                _, _, status = await self.connect(request)
                self.assertIn(b" 405 ", status)
        self.assertEqual(self.upstreams, 0)

    async def test_an_oversized_request_is_dropped(self):
        reader, writer, status = await self.connect(b"CONNECT pypi.org:443 HTTP/1.1\r\nX: " + b"a" * 20000)
        self.assertEqual(status, b"") if status == b"" else self.assertNotIn(b" 200 ", status)

    async def test_bytes_sent_with_the_request_are_checked_too(self):
        request = b"CONNECT pypi.org:443 HTTP/1.1\r\n\r\n" + real_hello("www.fastly.com")
        reader, _, status = await self.connect(request)
        self.assertIn(b" 200 ", status)
        await asyncio.wait_for(reader.readuntil(b"\r\n"), 5)
        self.assertTrue(await self.closed(reader))
        self.assertEqual(self.upstreams, 0)

    async def test_a_name_resolving_to_a_private_address_is_not_dialled(self):
        guarded = await self.start(self.resolved)  # the default address rule
        reader, writer, _ = await self.tunnel("pypi.org:443", guarded)
        writer.write(real_hello("pypi.org"))
        self.assertTrue(await self.closed(reader))
        self.assertEqual(self.upstreams, 0)

    async def test_ipv4_addresses_are_tried_first(self):
        async def answer(host, port, **options):
            return [(socket.AF_INET6, 1, 6, "", ("2606:4700::1", 443, 0, 0)),
                    (socket.AF_INET, 1, 6, "", ("151.101.0.223", 443))]

        loop = asyncio.get_running_loop()
        original, loop.getaddrinfo = loop.getaddrinfo, answer
        try:
            found = await proxy._lookup("pypi.org")
        finally:
            loop.getaddrinfo = original
        self.assertEqual(found, [("151.101.0.223", 443), ("2606:4700::1", 443)])

    def test_the_default_address_rule(self):
        for address in ("127.0.0.1", "10.1.2.3", "172.16.0.9", "192.168.1.1", "169.254.169.254",
                        "100.64.0.1", "::1", "fd12::1", "fe80::1", "0.0.0.0", "224.0.0.1", "::ffff:10.0.0.1"):
            self.assertFalse(proxy.public_address(address), address)
        for address in ("151.101.0.223", "140.82.121.4", "2606:4700:4700::1111"):
            self.assertTrue(proxy.public_address(address), address)


def request(host: bytes = b"pypi.org", line: bytes = b"GET /simple/pip/ HTTP/1.1", *extra: bytes) -> bytes:
    headers = [b"Host: " + host] if host is not None else []
    return b"\r\n".join([line, *headers, *extra])


class RequestTests(unittest.TestCase):
    """What the proxy will repeat upstream for an inspected host."""

    def status(self, head: bytes) -> int:
        with self.assertRaises(proxy.Rejected) as caught:
            proxy.rebuild_request(head, "pypi.org")
        return caught.exception.status

    def test_a_request_is_written_again_from_its_parts(self):
        head = request(
            b"PyPI.org:443", b"GET /simple/pip/?a=1 HTTP/1.1", b"User-Agent: pip/1", b"Accept:  text/html ",
            b"Range: bytes=0-9", b"Connection: keep-alive", b"X-Forwarded-Host: www.fastly.com",
            b"Forwarded: host=www.fastly.com", b"Fastly-SSL: 1", b"Authorization: Basic eA==", b"Cookie: a=1",
        )
        self.assertEqual(
            proxy.rebuild_request(head, "pypi.org"),
            b"GET /simple/pip/?a=1 HTTP/1.1\r\nHost: pypi.org\r\nUser-Agent: pip/1\r\nAccept: text/html\r\n"
            b"Range: bytes=0-9\r\nConnection: close\r\n\r\n",
        )

    def test_head_and_an_empty_body_are_allowed(self):
        head = request(b"pypi.org", b"HEAD / HTTP/1.1", b"Content-Length: 0")
        self.assertTrue(proxy.rebuild_request(head, "pypi.org").startswith(b"HEAD / HTTP/1.1\r\n"))

    def test_another_missing_or_repeated_host_is_misdirected(self):
        for head in (
            request(b"www.fastly.com"), request(b"pypi.org:80"), request(b"pypi.org."), request(None),
            request(b"pypi.org", b"GET / HTTP/1.1", b"Host: www.fastly.com"),
            request(b"pypi.org", b"GET / HTTP/1.1", b"host: pypi.org"),
        ):
            with self.subTest(head=head):
                self.assertEqual(self.status(head), 421)

    def test_only_reading_methods(self):
        for method in (b"POST", b"PUT", b"DELETE", b"CONNECT", b"OPTIONS", b"get"):
            with self.subTest(method=method):
                self.assertEqual(self.status(request(b"pypi.org", method + b" / HTTP/1.1")), 405)

    def test_nothing_that_two_servers_could_read_differently(self):
        for label, head in (
            ("absolute target", request(b"pypi.org", b"GET https://www.fastly.com/ HTTP/1.1")),
            ("authority target", request(b"pypi.org", b"GET www.fastly.com HTTP/1.1")),
            ("space in target", request(b"pypi.org", b"GET /a b HTTP/1.1")),
            ("control in target", request(b"pypi.org", b"GET /a\x00 HTTP/1.1")),
            ("version", request(b"pypi.org", b"GET / HTTP/2")),
            ("a body", request(b"pypi.org", b"GET / HTTP/1.1", b"Content-Length: 5")),
            ("odd length", request(b"pypi.org", b"GET / HTTP/1.1", b"Content-Length: 00")),
            ("chunked", request(b"pypi.org", b"GET / HTTP/1.1", b"Transfer-Encoding: chunked")),
            ("folded line", request(b"pypi.org", b"GET / HTTP/1.1", b" Host: www.fastly.com")),
            ("bare newline", request(b"pypi.org", b"GET / HTTP/1.1", b"Accept: a\nHost: www.fastly.com")),
            ("bare return", request(b"pypi.org", b"GET / HTTP/1.1", b"Accept: a\rHost: www.fastly.com")),
            ("space before colon", request(b"pypi.org", b"GET / HTTP/1.1", b"Accept : a")),
            ("no colon", request(b"pypi.org", b"GET / HTTP/1.1", b"Accept")),
            ("empty line", request(b"pypi.org", b"GET / HTTP/1.1", b"")),
        ):
            with self.subTest(label):
                self.assertEqual(self.status(head), 400)


@unittest.skipUnless(shutil.which("openssl"), "openssl is not installed")
class InspectionTests(unittest.IsolatedAsyncioTestCase):
    """A real TLS client and a real TLS upstream, with certificates the proxy itself issues."""

    @classmethod
    def setUpClass(cls):
        cls.directory = tempfile.TemporaryDirectory()
        cls.certs = Path(cls.directory.name)
        proxy.issue_certificates(cls.certs, ["pypi.org", "files.pythonhosted.org"])

    @classmethod
    def tearDownClass(cls):
        cls.directory.cleanup()

    def client_context(self, trusted: bool = True) -> ssl.SSLContext:
        context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)  # strict by default
        if trusted:
            context.load_verify_locations(self.certs / "ca.crt")
        else:
            context.load_default_certs()
        return context

    async def asyncSetUp(self):
        self.requests: list[bytes] = []

        async def upstream(reader, writer):
            head = await reader.readuntil(b"\r\n\r\n")
            self.requests.append(head)
            body = b"y" * 200_000
            writer.write(b"HTTP/1.1 200 OK\r\nContent-Length: %d\r\nConnection: close\r\n\r\n" % len(body) + body)
            await writer.drain()
            writer.close()

        server = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        server.load_cert_chain(self.certs / "leaf.crt", self.certs / "leaf.key")
        self.upstream = await asyncio.start_server(upstream, "127.0.0.1", 0, ssl=server)
        port = self.upstream.sockets[0].getsockname()[1]

        async def resolve(host):
            return [("127.0.0.1", port)]

        service = proxy.Proxy(
            ["pypi.org", "github.com"],
            inspected={"pypi.org": proxy.server_context(self.certs / "leaf.crt", self.certs / "leaf.key", "pypi.org")},
            upstream_context=self.client_context(),
            resolve=resolve,
            address_ok=lambda address: True,
        )
        self.server = await asyncio.start_server(service.handle, "127.0.0.1", 0)

    async def asyncTearDown(self):
        self.server.close()
        self.upstream.close()

    async def tunnel(self, early: bytes = b""):
        port = self.server.sockets[0].getsockname()[1]
        reader, writer = await asyncio.open_connection("127.0.0.1", port)
        self.addCleanup(writer.close)
        writer.write(b"CONNECT pypi.org:443 HTTP/1.1\r\n\r\n" + early)
        status = await asyncio.wait_for(reader.readuntil(b"\r\n\r\n"), 5) if not early else b""
        return reader, writer, status

    async def tls(self, name: str = "pypi.org", context: ssl.SSLContext | None = None):
        reader, writer, status = await self.tunnel()
        self.assertIn(b" 200 ", status)
        await asyncio.wait_for(writer.start_tls(context or self.client_context(), server_hostname=name), 5)
        return reader, writer

    async def exchange(self, data: bytes) -> bytes:
        reader, writer = await self.tls()
        writer.write(data)
        return await asyncio.wait_for(reader.read(), 10)

    async def test_a_matching_request_is_answered_whole_and_the_connection_ends(self):
        answer = await self.exchange(request() + b"\r\n\r\n")
        self.assertTrue(answer.startswith(b"HTTP/1.1 200 OK"))
        self.assertTrue(answer.endswith(b"y" * 1000))
        self.assertGreater(len(answer), 200_000)
        self.assertEqual(self.requests, [b"GET /simple/pip/ HTTP/1.1\r\nHost: pypi.org\r\nConnection: close\r\n\r\n"])

    async def test_another_host_header_never_reaches_the_upstream(self):
        answer = await self.exchange(request(b"www.fastly.com") + b"\r\n\r\n")
        self.assertTrue(answer.startswith(b"HTTP/1.1 421 "))
        self.assertEqual(self.requests, [])

    async def test_a_second_request_on_the_connection_is_never_forwarded(self):
        answer = await self.exchange(request() + b"\r\n\r\n" + request(b"www.fastly.com") + b"\r\n\r\n")
        self.assertTrue(answer.startswith(b"HTTP/1.1 200 OK"))
        self.assertEqual(len(self.requests), 1)
        self.assertNotIn(b"fastly", self.requests[0])

    async def test_an_endless_request_head_is_dropped(self):
        reader, writer = await self.tls()
        writer.write(b"GET / HTTP/1.1\r\nHost: pypi.org\r\nAccept: " + b"a" * 100_000)
        try:
            answer = await asyncio.wait_for(reader.read(), 5)
        except (ConnectionError, ssl.SSLError):
            answer = b""
        self.assertNotIn(b" 200 ", answer)
        self.assertEqual(self.requests, [])

    async def test_another_tls_name_fails_the_handshake(self):
        context = self.client_context()
        context.check_hostname = False
        with self.assertRaises((ssl.SSLError, ConnectionError, asyncio.IncompleteReadError)):
            await self.tls("files.pythonhosted.org", context)
        self.assertEqual(self.requests, [])

    async def test_control_a_client_that_does_not_trust_the_sandbox_authority_fails(self):
        with self.assertRaises(ssl.SSLCertVerificationError):
            await self.tls(context=self.client_context(trusted=False))

    async def test_bytes_sent_before_the_tunnel_opens_end_it(self):
        reader, _, _ = await self.tunnel(early=real_hello("pypi.org"))
        try:
            answer = await asyncio.wait_for(reader.read(), 5)
        except ConnectionError:
            answer = b""
        self.assertNotIn(b" 200 ", answer)

    def test_only_what_is_needed_is_left_on_disk(self):
        self.assertEqual(sorted(p.name for p in self.certs.iterdir()), ["ca.crt", "leaf.crt", "leaf.key"])

    def test_the_authority_is_limited_to_the_inspected_names(self):
        text = subprocess.run(
            ["openssl", "x509", "-in", str(self.certs / "ca.crt"), "-noout", "-text"],
            capture_output=True, text=True, check=True,
        ).stdout
        self.assertIn("Name Constraints: critical", text)
        self.assertIn("DNS:pypi.org", text)
        self.assertIn("pathlen:0", text)


if __name__ == "__main__":
    unittest.main()
