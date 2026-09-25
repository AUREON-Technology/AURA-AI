"""Which network addresses an outbound fetch may reach.

One predicate for every component that fetches a URL on the deployment's behalf (the agent's
``fetch_url`` tool, the nightly knowledge-refresh fetcher), so their SSRF policy cannot drift
apart. Stdlib only.
"""

from __future__ import annotations

import ipaddress

__all__ = ["is_internal_address"]


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
