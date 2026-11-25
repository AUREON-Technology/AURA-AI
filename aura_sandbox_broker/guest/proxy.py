#!/usr/bin/python3 -I
"""The sandbox's only way out: an HTTPS tunnel to an allowed host, and to nothing else.

    python3 -I proxy.py --allow pypi.org --allow github.com [--port 8888]

A host allowlist on the CONNECT line alone does not hold: many sites share a content-delivery
network's addresses, so a tunnel "to pypi.org" can ask the network for someone else's site by
naming it inside TLS. This proxy therefore reads the tunnel's TLS ClientHello and requires the
server name there to be the host the tunnel was opened to, before it connects anywhere. It does
the same for any later ClientHello on the connection, and refuses encrypted ClientHellos, which
would hide the real name.

Most tunnels are never decrypted. Some hosts sit on networks that also route by the HTTP `Host`
header inside TLS, which a pass-through proxy cannot see. For those (`--inspect`) the proxy is
the TLS server, with a certificate from an authority made for this boot and limited to those
names. It accepts only a body-less GET or HEAD naming that host, writes the request afresh, and
sends it over its own verified TLS connection.

Standard library only (and the `openssl` program to issue certificates). Runs unprivileged, as
its own user.
"""

import argparse
import asyncio
import ipaddress
import os
import socket
import ssl
import subprocess
import sys
import tempfile
from http import HTTPStatus
from pathlib import Path

CHANGE_CIPHER_SPEC = 20
HANDSHAKE = 22
#: change_cipher_spec, alert, handshake, application_data.
RECORD_TYPES = (CHANGE_CIPHER_SPEC, 21, HANDSHAKE, 23)
CLIENT_HELLO = 1
MAX_RECORD = 16384 + 2048
MAX_HELLO = 65536
#: Every field of a ClientHello at its protocol limit: version 2, random 32, session id 1+32,
#: cipher suites 2+65534, compression 1+255, extensions 2+65535.
LONGEST_POSSIBLE_HELLO = 131396
SERVER_NAME = 0

MAX_REQUEST = 8192
#: A request head on an inspected connection. Signed download addresses are long.
MAX_HEAD = 32768
HANDSHAKE_TIMEOUT_S = 30
CONNECT_TIMEOUT_S = 20
IDLE_TIMEOUT_S = 600
MAX_TUNNELS = 128


class Refused(Exception):
    pass


class Rejected(Refused):
    """A request on an inspected connection that is answered with a status instead."""

    def __init__(self, status: int) -> None:
        super().__init__(f"request rejected with {status}")
        self.status = status


#: The only request headers repeated upstream. Anything that could steer a network to another
#: site (Host, X-Forwarded-Host, Forwarded, vendor headers), carry credentials or frame a body
#: is left behind.
FORWARDED_HEADERS = frozenset((
    b"accept", b"accept-encoding", b"accept-language", b"cache-control", b"if-modified-since",
    b"if-none-match", b"if-range", b"range", b"user-agent",
))
_NAME_BYTES = frozenset(b"!#$%&'*+-.^_`|~0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz")


def rebuild_request(head: bytes, host: str) -> bytes:
    """The upstream request for one client request head on an inspected connection.

    Only body-less GET and HEAD, with exactly one Host header naming the tunnel's host. The
    result is written afresh from the parsed parts, so nothing ambiguous survives."""
    lines = head.split(b"\r\n")
    if any(byte in line for line in lines for byte in (b"\r", b"\n", b"\x00")):
        raise Rejected(400)
    words = lines[0].split(b" ")
    if len(words) != 3:
        raise Rejected(400)
    method, target, version = words
    if method not in (b"GET", b"HEAD"):
        raise Rejected(405)
    if (version not in (b"HTTP/1.1", b"HTTP/1.0") or not target.startswith(b"/")
            or any(not 0x20 < byte < 0x7F for byte in target)):
        raise Rejected(400)
    hosts, kept = [], []
    for line in lines[1:]:
        name, colon, value = line.partition(b":")
        if not colon or not name or any(byte not in _NAME_BYTES for byte in name):
            raise Rejected(400)
        value = value.strip(b" \t")
        if any((byte < 0x20 and byte != 0x09) or byte == 0x7F for byte in value):
            raise Rejected(400)
        lower = name.lower()
        if lower == b"host":
            hosts.append(value.lower())
        elif lower == b"transfer-encoding" or (lower == b"content-length" and value != b"0"):
            raise Rejected(400)
        elif lower in FORWARDED_HEADERS:
            kept.append(name + b": " + value)
    expected = host.lower().encode("ascii")
    if len(hosts) != 1 or hosts[0] not in (expected, expected + b":443"):
        raise Rejected(421)
    out = [method + b" " + target + b" HTTP/1.1", b"Host: " + expected, *kept, b"Connection: close"]
    return b"\r\n".join(out) + b"\r\n\r\n"


def issue_certificates(directory, hosts) -> None:
    """A certificate authority limited to `hosts`, and the one certificate the proxy serves.

    Writes `ca.crt`, `leaf.crt` and `leaf.key` into `directory`. The authority's own key lives
    only in a temporary folder and is gone when this returns, so nothing else can ever be
    signed by it."""
    directory = Path(directory)
    names = [f"DNS:{host}" for host in hosts]
    with tempfile.TemporaryDirectory(dir=directory.parent) as scratch:
        work = Path(scratch)

        def openssl(*args: str) -> None:
            subprocess.run(["openssl", *args], check=True, capture_output=True, timeout=60)

        key = ("-newkey", "ec", "-pkeyopt", "ec_paramgen_curve:P-256", "-nodes")
        constraints = ",".join(f"permitted;{name}" for name in names)
        openssl("req", "-x509", *key, "-days", "3650", "-subj", "/CN=AURA sandbox authority",
                "-keyout", str(work / "ca.key"), "-out", str(directory / "ca.crt"),
                "-addext", "basicConstraints=critical,CA:TRUE,pathlen:0",
                "-addext", "keyUsage=critical,keyCertSign",
                "-addext", f"nameConstraints=critical,{constraints}")
        openssl("req", *key, "-subj", f"/CN={hosts[0]}",
                "-keyout", str(directory / "leaf.key"), "-out", str(work / "leaf.csr"))
        (work / "leaf.ext").write_text(
            f"subjectAltName={','.join(names)}\nbasicConstraints=critical,CA:FALSE\n"
            "keyUsage=critical,digitalSignature\nextendedKeyUsage=serverAuth\n"
            "subjectKeyIdentifier=hash\nauthorityKeyIdentifier=keyid\n",
            encoding="ascii",
        )
        openssl("x509", "-req", "-in", str(work / "leaf.csr"), "-days", "3650",
                "-CA", str(directory / "ca.crt"), "-CAkey", str(work / "ca.key"),
                "-set_serial", str(int.from_bytes(os.urandom(16), "big") >> 1),
                "-extfile", str(work / "leaf.ext"), "-out", str(directory / "leaf.crt"))


def server_context(certificate, key, host: str) -> ssl.SSLContext:
    """The TLS server the proxy is for one inspected host: HTTP/1.1 only, that name only."""
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    context.minimum_version = ssl.TLSVersion.TLSv1_2
    context.load_cert_chain(certificate, key)
    context.set_alpn_protocols(["http/1.1"])

    def only_this_name(_connection, name, _context):
        if name is None or name.lower() != host.lower():
            return ssl.ALERT_DESCRIPTION_UNRECOGNIZED_NAME
        return None

    context.sni_callback = only_this_name
    return context


def public_address(address: str) -> bool:
    """True only for an address on the public internet, whatever name led to it."""
    try:
        ip = ipaddress.ip_address(address)
    except ValueError:
        return False
    ip = getattr(ip, "ipv4_mapped", None) or ip
    return ip.is_global and not ip.is_multicast


class _Fields:
    """Bounds-checked reading of a TLS structure."""

    def __init__(self, data: bytes) -> None:
        self.data = data
        self.at = 0

    @property
    def remaining(self) -> int:
        return len(self.data) - self.at

    def take(self, count: int) -> bytes:
        if count > self.remaining:
            raise Refused("truncated ClientHello")
        part = self.data[self.at:self.at + count]
        self.at += count
        return part

    def number(self, size: int) -> int:
        return int.from_bytes(self.take(size), "big")

    def block(self, size: int) -> "_Fields":
        return _Fields(self.take(self.number(size)))


def _hides_the_name(extension: int) -> bool:
    # Encrypted ClientHello (final and draft code points) and its predecessor, ESNI.
    return extension in (0xFD00, 0xFFCE) or 0xFE00 <= extension <= 0xFEFF


def _server_name(extension: _Fields) -> bytes:
    entries = extension.block(2)
    if extension.remaining or entries.number(1) != 0:
        raise Refused("malformed server name")
    name = entries.block(2).data
    if entries.remaining:
        raise Refused("more than one server name")
    return name


def check_hello(message: bytes, host: str) -> None:
    """Refuse a ClientHello that is not unambiguously for `host`. Strict on purpose: anything a
    server might read differently from this parser is refused."""
    fields = _Fields(message)
    if fields.number(1) != CLIENT_HELLO:
        raise Refused("not a ClientHello")
    body = fields.block(3)
    if fields.remaining:
        raise Refused("another message follows the ClientHello")
    body.take(2 + 32)  # version, random
    body.block(1)  # session id
    body.block(2)  # cipher suites
    body.block(1)  # compression methods
    extensions = body.block(2)
    if body.remaining:
        raise Refused("bytes after the extensions")
    seen: set[int] = set()
    name = None
    while extensions.remaining:
        kind = extensions.number(2)
        data = extensions.block(2)
        if kind in seen:
            raise Refused("repeated extension")
        seen.add(kind)
        if _hides_the_name(kind):
            raise Refused("encrypted ClientHello")
        if kind == SERVER_NAME:
            name = _server_name(data)
    if name is None:
        raise Refused("no server name")
    if name.lower() != host.lower().encode("ascii"):
        raise Refused("the server name is not the tunnel's host")


def _opens_hello(payload: bytes, cipher_changed: bool) -> bool:
    """Whether a handshake record, after the first ClientHello, must be checked as another one.

    Until the client changes cipher, every handshake record is plaintext, so any that starts
    like a ClientHello is one -- whatever version it claims. Afterwards a TLS 1.2 client sends
    encrypted records here, while a TLS 1.3 client may still send its second ClientHello in the
    clear. The client controls that cipher change, so it switches nothing off: a record is
    taken for ciphertext only when it declares a length no ClientHello can have. Anything else
    is checked, and refused if it is larger than this proxy accepts."""
    if payload[0] != CLIENT_HELLO:
        return False
    if not cipher_changed or len(payload) < 4:
        return True
    return int.from_bytes(payload[1:4], "big") <= LONGEST_POSSIBLE_HELLO


class Inspector:
    """The client's side of one tunnel. `feed` returns the bytes that may be forwarded: whole
    records, with every ClientHello checked before any of it is released."""

    def __init__(self, host: str) -> None:
        self.host = host
        self.greeted = False
        self._cipher_changed = False
        self._pending = bytearray()
        self._hello: bytearray | None = None
        self._held = bytearray()

    def feed(self, data: bytes) -> bytes:
        self._pending += data
        released = bytearray()
        while self._pending:
            if not self.greeted and self._pending[0] != HANDSHAKE:
                raise Refused("not a TLS handshake")
            if len(self._pending) < 5:
                break
            kind, major = self._pending[0], self._pending[1]
            length = int.from_bytes(self._pending[3:5], "big")
            if kind not in RECORD_TYPES or major != 3 or not 0 < length <= MAX_RECORD:
                raise Refused("not a TLS record")
            if len(self._pending) < 5 + length:
                break
            record = bytes(self._pending[:5 + length])
            del self._pending[:5 + length]
            released += self._record(kind, record)
        return bytes(released)

    def _record(self, kind: int, record: bytes) -> bytes:
        payload = record[5:]
        if self._hello is not None:
            if kind != HANDSHAKE:
                raise Refused("a ClientHello was interrupted")
            self._hello += payload
        elif not self.greeted:
            if kind != HANDSHAKE or payload[0] != CLIENT_HELLO:
                raise Refused("the first message is not a ClientHello")
            self._hello = bytearray(payload)
        elif kind == HANDSHAKE and _opens_hello(payload, self._cipher_changed):
            self._hello = bytearray(payload)
        else:
            self._cipher_changed = self._cipher_changed or kind == CHANGE_CIPHER_SPEC
            return record

        self._held += record
        if len(self._hello) >= 4:
            length = int.from_bytes(self._hello[1:4], "big")
            if length > MAX_HELLO:
                raise Refused("ClientHello too large")
            if len(self._hello) >= 4 + length:
                check_hello(bytes(self._hello), self.host)
                released = bytes(self._held)
                self._hello = None
                self._held.clear()
                self.greeted = True
                return released
        if len(self._held) > 2 * MAX_HELLO:
            raise Refused("ClientHello too large")
        return b""


async def _lookup(host: str) -> list[tuple[str, int]]:
    found = await asyncio.get_running_loop().getaddrinfo(host, 443, type=socket.SOCK_STREAM)
    # IPv4 first: where the VM has no IPv6 route out, an IPv6 attempt can only waste time.
    found = sorted(found, key=lambda entry: entry[0] != socket.AF_INET)
    return [(entry[4][0], 443) for entry in found]


class Proxy:
    def __init__(self, allowed, resolve=_lookup, address_ok=public_address, inspected=None,
                 upstream_context=None) -> None:
        self.allowed = frozenset(host.lower() for host in allowed)
        #: host -> the server context its tunnels are terminated with. Others are passed through.
        self.inspected = {host.lower(): context for host, context in (inspected or {}).items()}
        if not set(self.inspected) <= self.allowed:
            raise ValueError("every inspected host must be an allowed host")
        self._upstream_context = upstream_context or ssl.create_default_context()
        self._resolve = resolve
        self._address_ok = address_ok
        self._open = 0

    async def handle(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        self._open += 1
        try:
            if self._open > MAX_TUNNELS:
                await self._answer(writer, "503 Service Unavailable")
            else:
                await self._serve(reader, writer)
        except Refused as why:
            print(f"refused: {why}", file=sys.stderr, flush=True)
        except (OSError, asyncio.TimeoutError):
            pass
        finally:
            self._open -= 1
            writer.close()

    @staticmethod
    async def _answer(writer: asyncio.StreamWriter, status: str) -> None:
        writer.write(f"HTTP/1.1 {status}\r\nContent-Length: 0\r\nConnection: close\r\n\r\n".encode("ascii"))
        await writer.drain()

    async def _serve(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        request = b""
        while b"\r\n\r\n" not in request:
            if len(request) > MAX_REQUEST:
                return
            chunk = await asyncio.wait_for(reader.read(4096), HANDSHAKE_TIMEOUT_S)
            if not chunk:
                return
            request += chunk
        head, _, early = request.partition(b"\r\n\r\n")
        if len(head) > MAX_REQUEST:
            return
        words = head.split(b"\r\n", 1)[0].split(b" ")
        if len(words) != 3 or words[0] != b"CONNECT" or not words[2].startswith(b"HTTP/1."):
            await self._answer(writer, "405 Method Not Allowed")
            return
        host, colon, port = words[1].decode("ascii", "replace").rpartition(":")
        if not colon or port != "443" or host.lower() not in self.allowed:
            print("refused: host or port not allowed", file=sys.stderr, flush=True)
            await self._answer(writer, "403 Forbidden")
            return
        context = self.inspected.get(host.lower())
        if context is not None:
            if early:
                raise Refused("bytes sent before the tunnel opened")
            await self._inspect(host.lower(), context, reader, writer)
            return
        writer.write(b"HTTP/1.1 200 Connection established\r\n\r\n")
        await writer.drain()

        # Nothing is dialled until the client has said, inside TLS, who it wants.
        inspector = Inspector(host)
        first = inspector.feed(early)
        while not first:
            chunk = await asyncio.wait_for(reader.read(65536), HANDSHAKE_TIMEOUT_S)
            if not chunk:
                return
            first = inspector.feed(chunk)
        upstream_reader, upstream_writer = await self._dial(host.lower())
        try:
            upstream_writer.write(first)
            await upstream_writer.drain()
            await self._relay(inspector, reader, writer, upstream_reader, upstream_writer)
        finally:
            upstream_writer.close()

    async def _inspect(self, host: str, context, reader, writer) -> None:
        """Terminate TLS, accept one checked request, and answer it from the real host.

        One request per connection: the client is told to close, so a second request, however it
        is framed, is never read."""
        # Paused first, so no byte of the client's TLS is taken by the plaintext reader.
        writer.transport.pause_reading()
        writer.write(b"HTTP/1.1 200 Connection established\r\n\r\n")
        await writer.drain()
        await asyncio.wait_for(writer.start_tls(context), HANDSHAKE_TIMEOUT_S)
        head = b""
        while b"\r\n\r\n" not in head:
            if len(head) > MAX_HEAD:
                return
            chunk = await asyncio.wait_for(reader.read(4096), HANDSHAKE_TIMEOUT_S)
            if not chunk:
                return
            head += chunk
        head = head.partition(b"\r\n\r\n")[0]
        if len(head) > MAX_HEAD:
            return
        try:
            request = rebuild_request(head, host)
        except Rejected as refusal:
            await self._answer(writer, f"{refusal.status} {HTTPStatus(refusal.status).phrase}")
            raise
        upstream_reader, upstream_writer = await self._dial(host, ssl=self._upstream_context, server_hostname=host)
        try:
            upstream_writer.write(request)
            await upstream_writer.drain()
            while chunk := await asyncio.wait_for(upstream_reader.read(65536), IDLE_TIMEOUT_S):
                writer.write(chunk)
                await writer.drain()
        finally:
            upstream_writer.close()

    async def _dial(self, host: str, **tls):
        for address, port in await self._resolve(host):
            if not self._address_ok(address):
                continue
            try:
                return await asyncio.wait_for(asyncio.open_connection(address, port, **tls), CONNECT_TIMEOUT_S)
            except (OSError, asyncio.TimeoutError):
                continue
        raise Refused("no public address of the host is reachable")

    @staticmethod
    async def _relay(inspector, reader, writer, upstream_reader, upstream_writer) -> None:
        async def outward() -> None:
            while chunk := await reader.read(65536):
                if data := inspector.feed(chunk):
                    upstream_writer.write(data)
                    await upstream_writer.drain()
            upstream_writer.write_eof()

        async def inward() -> None:
            while chunk := await asyncio.wait_for(upstream_reader.read(65536), IDLE_TIMEOUT_S):
                writer.write(chunk)
                await writer.drain()

        out, back = asyncio.create_task(outward()), asyncio.create_task(inward())
        try:
            await asyncio.wait({out, back}, return_when=asyncio.FIRST_COMPLETED)
            # The client may finish sending while the answer is still arriving.
            if out.done() and out.exception() is None:
                await asyncio.wait({back})
        finally:
            for task in (out, back):
                task.cancel()
            results = await asyncio.gather(out, back, return_exceptions=True)
        for result in results:
            if isinstance(result, Refused):
                raise result


async def serve(proxy: "Proxy", port: int) -> None:
    server = await asyncio.start_server(proxy.handle, "127.0.0.1", port)
    async with server:
        await server.serve_forever()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    parser.add_argument("--allow", action="append", default=[])
    parser.add_argument("--inspect", action="append", default=[],
                        help="an allowed host whose TLS the proxy terminates to check requests")
    parser.add_argument("--certificates", help="folder holding leaf.crt and leaf.key")
    parser.add_argument("--issue", metavar="FOLDER",
                        help="issue the certificates for the --inspect hosts into FOLDER and exit")
    parser.add_argument("--port", type=int, default=8888)
    options = parser.parse_args()
    if options.issue:
        if not options.inspect:
            parser.error("--issue needs at least one --inspect host")
        issue_certificates(options.issue, options.inspect)
        return
    if not options.allow or (options.inspect and not options.certificates):
        parser.error("--allow is required, and --inspect needs --certificates")
    inspected = {
        host: server_context(Path(options.certificates, "leaf.crt"), Path(options.certificates, "leaf.key"), host)
        for host in options.inspect
    }
    asyncio.run(serve(Proxy(options.allow, inspected=inspected), options.port))


if __name__ == "__main__":
    main()
