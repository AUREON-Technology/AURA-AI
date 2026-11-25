#!/usr/bin/python3 -I
"""The sandbox's only way out: an HTTPS tunnel to an allowed host, and to nothing else.

    python3 -I proxy.py --allow pypi.org --allow github.com [--port 8888]

A host allowlist on the CONNECT line alone does not hold: many sites share a content-delivery
network's addresses, so a tunnel "to pypi.org" can ask the network for someone else's site by
naming it inside TLS. This proxy therefore reads the tunnel's TLS ClientHello and requires the
server name there to be the host the tunnel was opened to, before it connects anywhere. It does
the same for any later ClientHello on the connection, and refuses encrypted ClientHellos, which
would hide the real name. It never decrypts: what a request says inside TLS (its HTTP `Host`
header) is not visible here.

Standard library only. Runs unprivileged, as its own user.
"""

import argparse
import asyncio
import ipaddress
import socket
import sys

HANDSHAKE = 22
#: change_cipher_spec, alert, handshake, application_data.
RECORD_TYPES = (20, 21, HANDSHAKE, 23)
CLIENT_HELLO = 1
MAX_RECORD = 16384 + 2048
MAX_HELLO = 65536
SERVER_NAME = 0

MAX_REQUEST = 8192
HANDSHAKE_TIMEOUT_S = 30
CONNECT_TIMEOUT_S = 20
IDLE_TIMEOUT_S = 600
MAX_TUNNELS = 128


class Refused(Exception):
    pass


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


def _opens_hello(payload: bytes) -> bool:
    """Whether a handshake record, after the first ClientHello, begins another one. A TLS 1.2
    encrypted message starts with random-looking bytes and fails the version test; a first
    fragment too short to test is collected and checked."""
    return payload[0] == CLIENT_HELLO and (len(payload) < 6 or payload[4:6] == b"\x03\x03")


class Inspector:
    """The client's side of one tunnel. `feed` returns the bytes that may be forwarded: whole
    records, with every ClientHello checked before any of it is released."""

    def __init__(self, host: str) -> None:
        self.host = host
        self.greeted = False
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
        elif kind == HANDSHAKE and _opens_hello(payload):
            self._hello = bytearray(payload)
        else:
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
    def __init__(self, allowed, resolve=_lookup, address_ok=public_address) -> None:
        self.allowed = frozenset(host.lower() for host in allowed)
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

    async def _dial(self, host: str):
        for address, port in await self._resolve(host):
            if not self._address_ok(address):
                continue
            try:
                return await asyncio.wait_for(asyncio.open_connection(address, port), CONNECT_TIMEOUT_S)
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


async def serve(allowed: list[str], port: int) -> None:
    server = await asyncio.start_server(Proxy(allowed).handle, "127.0.0.1", port)
    async with server:
        await server.serve_forever()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    parser.add_argument("--allow", action="append", required=True)
    parser.add_argument("--port", type=int, default=8888)
    options = parser.parse_args()
    asyncio.run(serve(options.allow, options.port))


if __name__ == "__main__":
    main()
