"""SSRF: the agent's ``fetch_url`` tool and the unattended knowledge-refresh fetcher cannot be
pointed at the deployment's own network.

``tests/unit/test_builtin_tools.py`` covers the basics (scheme, RFC 1918, loopback, link-local,
allowlist, a rebinding hostname). This adds what it did not:

* **Address classes ``is_private`` misses.** On CPython 3.11 ``ipaddress`` does not count
  100.64.0.0/10 (carrier-grade NAT -- Tailscale, and Alibaba Cloud's metadata service at
  100.100.100.200) or multicast as private, so both passed the guard. The check now refuses
  anything that is not globally routable.
* **Encodings of an internal address**: IPv4-mapped IPv6, NAT64, userinfo tricks, integer
  hosts resolved by DNS.
* **Redirects** end to end through the real tool: refused, and the target never contacted.
* **The refresh fetcher's DNS check.** Its docstring promised "the policy is identical to the
  ``fetch_url`` tool's", but it inspected only the literal URL -- an approved source whose DNS
  later pointed inside the network was fetched every night.

No network: DNS is replaced at ``socket.getaddrinfo`` and HTTP at ``httpx``'s transport.
"""

from __future__ import annotations

import socket

import httpx
import pytest

from aura_core.domain import UserId
from aura_core.domain.errors import AuraError, ToolExecutionError
from aura_core.ports.runtime import ToolContext
from aura_core.tools.builtin import _assert_public_url, _assert_resolves_publicly, build_http_tool

ALLOWED = frozenset({"example.com"})

INTERNAL_LITERALS = [
    "https://100.100.100.200/latest/meta-data/",   # Alibaba Cloud metadata (CGNAT range)
    "https://100.64.0.1/",                          # carrier-grade NAT / Tailscale
    "https://224.0.0.1/",                           # multicast
    "https://0.0.0.0/",                             # unspecified -> this host
    "https://[::1]/",
    "https://[::ffff:127.0.0.1]/",                  # IPv4-mapped loopback
    "https://[::ffff:169.254.169.254]/",            # IPv4-mapped metadata
    "https://[64:ff9b::a9fe:a9fe]/",                # NAT64 of 169.254.169.254
    "https://[fd00:ec2::254]/",                     # AWS IMDS over IPv6 (ULA)
    "https://[fe80::1]/",
    "https://[fec0::1]/",                           # deprecated site-local
    "https://example.com@169.254.169.254/",         # userinfo: the host is the metadata IP
]


def _resolver(*addresses: str):
    def resolve(host, port, *args, **kwargs):
        family = socket.AF_INET6 if ":" in addresses[0] else socket.AF_INET
        return [(family, socket.SOCK_STREAM, 6, "", (a, 0)) for a in addresses]

    return resolve


@pytest.fixture
def ctx() -> ToolContext:
    return ToolContext(user_id=UserId("u"), correlation_id="c", deadline_s=10.0)


class TestLiteralAddresses:
    @pytest.mark.parametrize("url", INTERNAL_LITERALS)
    def test_internal_literal_is_refused_even_if_allowlisted(self, url: str) -> None:
        from urllib.parse import urlparse

        host = urlparse(url).hostname or ""
        with pytest.raises(ToolExecutionError):
            _assert_public_url(url, ALLOWED | {host}, "t")

    @pytest.mark.parametrize("url", ["ftp://example.com/", "file:///etc/passwd",
                                     "gopher://example.com:70/", "data:text/plain,hi",
                                     "javascript:alert(1)", "//example.com/"])
    def test_non_https_schemes_are_refused(self, url: str) -> None:
        with pytest.raises(ToolExecutionError):
            _assert_public_url(url, ALLOWED, "t")

    def test_a_trailing_dot_does_not_slip_past_the_allowlist(self) -> None:
        with pytest.raises(ToolExecutionError):
            _assert_public_url("https://evil.test./", ALLOWED, "t")


class TestResolution:
    @pytest.mark.parametrize(
        "address",
        ["100.100.100.200", "100.64.0.1", "224.0.0.1", "0.0.0.0", "::1", "::ffff:10.0.0.1",
         "64:ff9b::a9fe:a9fe", "fd00:ec2::254", "fec0::1"],
    )
    async def test_a_hostname_resolving_internally_is_refused(
        self, monkeypatch, address: str
    ) -> None:
        monkeypatch.setattr(socket, "getaddrinfo", _resolver(address))
        with pytest.raises(ToolExecutionError):
            await _assert_resolves_publicly("example.com", "t")

    async def test_an_integer_host_is_judged_by_what_it_resolves_to(self, monkeypatch) -> None:
        """``https://2130706433/`` is 127.0.0.1 to a resolver; the literal check cannot parse
        it as an address, so the resolution check is what must catch it."""
        monkeypatch.setattr(socket, "getaddrinfo", _resolver("127.0.0.1"))
        with pytest.raises(ToolExecutionError):
            await _assert_resolves_publicly("2130706433", "t")

    async def test_a_public_address_still_passes(self, monkeypatch) -> None:
        monkeypatch.setattr(socket, "getaddrinfo", _resolver("93.184.216.34"))
        await _assert_resolves_publicly("example.com", "t")


class _Transport:
    """Replaces the network under the tool's own ``httpx.AsyncClient``."""

    def __init__(self, responses: dict[str, httpx.Response]) -> None:
        self.responses = responses
        self.seen: list[str] = []

    def install(self, monkeypatch) -> None:
        original = httpx.AsyncClient
        transport = httpx.MockTransport(self)

        def factory(*args, **kwargs):
            kwargs["transport"] = transport
            return original(*args, **kwargs)

        monkeypatch.setattr(httpx, "AsyncClient", factory)

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.seen.append(str(request.url))
        return self.responses.get(str(request.url), httpx.Response(404))


class TestFetchUrlTool:
    @pytest.mark.parametrize("location", ["http://169.254.169.254/latest/meta-data/",
                                          "https://example.com/elsewhere",
                                          "https://127.0.0.1/admin"])
    async def test_a_redirect_is_refused_and_never_followed(
        self, monkeypatch, ctx, location: str
    ) -> None:
        monkeypatch.setattr(socket, "getaddrinfo", _resolver("93.184.216.34"))
        transport = _Transport({
            "https://example.com/start": httpx.Response(302, headers={"location": location}),
        })
        transport.install(monkeypatch)
        (fetch,) = build_http_tool(allowed_hosts=["example.com"])

        with pytest.raises(ToolExecutionError):
            await fetch.execute({"url": "https://example.com/start"}, ctx)
        assert transport.seen == ["https://example.com/start"]

    async def test_a_rebinding_host_is_refused_before_any_request(self, monkeypatch, ctx) -> None:
        monkeypatch.setattr(socket, "getaddrinfo", _resolver("100.100.100.200"))
        transport = _Transport({})
        transport.install(monkeypatch)
        (fetch,) = build_http_tool(allowed_hosts=["example.com"])
        with pytest.raises(ToolExecutionError):
            await fetch.execute({"url": "https://example.com/"}, ctx)
        assert transport.seen == []

    async def test_a_public_page_is_fetched(self, monkeypatch, ctx) -> None:
        monkeypatch.setattr(socket, "getaddrinfo", _resolver("93.184.216.34"))
        transport = _Transport({"https://example.com/": httpx.Response(
            200, headers={"content-type": "text/html"}, text="<p>hello</p>")})
        transport.install(monkeypatch)
        (fetch,) = build_http_tool(allowed_hosts=["example.com"])
        assert await fetch.execute({"url": "https://example.com/"}, ctx) == "hello"


class TestKnowledgeRefreshFetcher:
    @staticmethod
    def _source(uri: str):
        from aura_core.domain.knowledge import SourceType
        from aura_core.domain.knowledge import KnowledgeSource

        return KnowledgeSource(id="s1", type=SourceType.WEB, uri=uri)

    @pytest.mark.parametrize("url", INTERNAL_LITERALS[:4])
    def test_internal_literals_are_refused(self, url: str) -> None:
        from aura_core.learning.sources import HttpSourceFetcher

        with pytest.raises(AuraError):
            HttpSourceFetcher._assert_fetchable(url)

    async def test_an_approved_source_rebound_to_an_internal_address_is_not_fetched(
        self, monkeypatch
    ) -> None:
        from aura_core.learning.sources import HttpSourceFetcher

        monkeypatch.setattr(socket, "getaddrinfo", _resolver("169.254.169.254"))
        transport = _Transport({})
        transport.install(monkeypatch)
        with pytest.raises(AuraError):
            await HttpSourceFetcher().fetch(self._source("https://docs.example.com/handbook"))
        assert transport.seen == [], "the nightly fetcher contacted an internal address"

    async def test_a_public_source_is_fetched(self, monkeypatch) -> None:
        from aura_core.learning.sources import HttpSourceFetcher

        monkeypatch.setattr(socket, "getaddrinfo", _resolver("93.184.216.34"))
        transport = _Transport({"https://docs.example.com/handbook": httpx.Response(
            200, headers={"content-type": "text/html"},
            text="<title>Handbook</title><p>Leave is 21 days.</p>")})
        transport.install(monkeypatch)
        (document,) = await HttpSourceFetcher().fetch(
            self._source("https://docs.example.com/handbook")
        )
        assert "21 days" in document.content
