"""Outbound-URL checks for the open tools that call a URL they were handed.

``builtin.py``'s ``fetch_url`` guards its URLs with an allowlist and ``_assert_public_url``; these
helpers are the lighter version for classes that take a free-form URL, and use the same address
test (``domain/network.is_internal_address``) so the two cannot disagree about what is internal.

* :func:`http_url_error` -- only ``http``/``https`` with a host. Enough for probes of a dev server
  or a service on the caller's own network, which is their purpose.
* :func:`public_http_url_error` -- additionally the host must resolve only to public addresses.
  For anything that sends data to a URL a model may have chosen (a webhook).
* :func:`open_no_redirect` -- ``urlopen`` that does not follow redirects, since a redirect from a
  public host to ``http://169.254.169.254/`` would walk straight past the check above.

Resolving before connecting leaves a DNS-rebinding window (the name can resolve differently a
moment later), as ``builtin._assert_resolves_publicly`` documents; a full fix pins the address.

Stdlib and ``domain`` only: this module sits in the ``tools`` engine layer.
"""

from __future__ import annotations

import ipaddress
import socket
import urllib.request
from typing import Any
from urllib.parse import urlsplit

from ...domain.network import is_internal_address

__all__ = ["http_url_error", "public_http_url_error", "open_no_redirect"]


def http_url_error(url: Any) -> str | None:
    """Why ``url`` is not an ``http(s)`` URL with a host, or ``None`` when it is one."""
    if not isinstance(url, str) or not url.strip():
        return "url is empty"
    try:
        parts = urlsplit(url.strip())
        host = parts.hostname
    except ValueError as exc:
        return f"url is malformed: {exc}"
    if parts.scheme.lower() not in ("http", "https"):
        return f"url scheme {parts.scheme!r} is not allowed; only http and https are"
    if not host:
        return "url has no host"
    return None


def public_http_url_error(url: Any) -> str | None:
    """Like :func:`http_url_error`, and the host must resolve only to public addresses."""
    problem = http_url_error(url)
    if problem:
        return problem
    host = urlsplit(url.strip()).hostname or ""
    try:
        infos = socket.getaddrinfo(host, None)
    except OSError:
        return f"could not resolve host {host!r}"
    if not infos:
        return f"could not resolve host {host!r}"
    for info in infos:
        address = str(info[4][0]).split("%", 1)[0]
        try:
            ip = ipaddress.ip_address(address)
        except ValueError:
            return f"host {host!r} resolved to {address!r}, which is not a public address"
        if is_internal_address(ip):
            return f"host {host!r} resolves to {address}, which is not a public address"
    return None


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):  # noqa: D401
        return None  # the 3xx surfaces as an HTTPError instead of being followed


def open_no_redirect(request: urllib.request.Request, timeout: float):
    """``urlopen`` for ``request`` without following redirects."""
    return urllib.request.build_opener(_NoRedirect()).open(request, timeout=timeout)
