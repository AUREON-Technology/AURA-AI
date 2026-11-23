"""Which network addresses an outbound fetch may reach.

One predicate for every component that fetches a URL on the deployment's behalf (the agent's
``fetch_url`` tool, the nightly knowledge-refresh fetcher), so their SSRF policy cannot drift
apart. Stdlib only.
"""

from __future__ import annotations

import ipaddress
from urllib.parse import urlparse

__all__ = ["is_internal_address", "validate_search_url"]


def is_internal_address(address: ipaddress.IPv4Address | ipaddress.IPv6Address) -> bool:
    """True for anything that is not a globally routable unicast address.

    ``is_private`` alone is not enough on CPython 3.11: it does not cover 100.64.0.0/10
    (carrier-grade NAT -- Tailscale, and Alibaba Cloud's metadata service at 100.100.100.200),
    multicast, or the deprecated IPv6 site-local range, all of which it reports as not private.
    ``not is_global`` catches the first; the explicit flags stay because some internal forms
    (NAT64 of a link-local address, for one) are classified global by ``is_global`` alone.
    IPv4-mapped IPv6 is judged by the IPv4 address it carries.
    """
    if isinstance(address, ipaddress.IPv6Address):
        if address.ipv4_mapped is not None:
            return is_internal_address(address.ipv4_mapped)
        if address.is_site_local:
            return True
    return (
        not address.is_global
        or address.is_private
        or address.is_loopback
        or address.is_link_local
        or address.is_reserved
        or address.is_multicast
        or address.is_unspecified
    )


def validate_search_url(value: str) -> str:
    """The configured SearXNG base URL, normalised (no trailing slash), or ``ValueError``.

    Must be ``http`` or ``https`` with a host, and nothing a request could be smuggled through:
    no credentials, query, fragment, whitespace or backslash.
    """
    text = (value or "").strip()
    if not text:
        raise ValueError("AURA_WEB_SEARCH_URL is empty")
    if any(c.isspace() or ord(c) < 32 or ord(c) == 127 or c == "\\" for c in text):
        raise ValueError("AURA_WEB_SEARCH_URL contains characters that are not permitted")
    try:
        parsed = urlparse(text)
        parsed.port  # noqa: B018  (raises ValueError on a malformed port)
    except ValueError as exc:
        raise ValueError("AURA_WEB_SEARCH_URL is not a valid URL") from exc
    if parsed.scheme not in ("http", "https") or not parsed.hostname:
        raise ValueError("AURA_WEB_SEARCH_URL must be an http(s) URL with a host")
    if parsed.username or parsed.password or "@" in parsed.netloc:
        raise ValueError("AURA_WEB_SEARCH_URL must not embed credentials")
    if parsed.query or parsed.fragment or "?" in text or "#" in text:
        raise ValueError("AURA_WEB_SEARCH_URL must not carry a query or fragment")
    return text.rstrip("/")
