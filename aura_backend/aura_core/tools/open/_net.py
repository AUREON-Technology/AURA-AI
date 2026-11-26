"""Outbound-URL checks for the open tools that call a URL they were handed.

These are thin wrappers over ``_ops_common``, which owns the one outbound policy for this folder
(``check_url``, ``open_guarded``, ``open_owned_local``). This module imports no network library:
``tests/unit/test_open_tools_no_gateway.py`` allows only the modules listed there to do network
I/O, and each of them must go through ``_ops_common``.

* :func:`http_url_error` -- only ``http``/``https`` with a host. Enough for probes of a dev server
  or a service on the caller's own network, which is their purpose.
* :func:`public_http_url_error` -- the full policy: the host must resolve only to public addresses,
  and the platform's own hosts and the gateway's paths are refused as well.

Stdlib and ``_ops_common`` only: this module sits in the ``tools`` engine layer.
"""

from __future__ import annotations

from typing import Any
from urllib.parse import urlsplit

from ._ops_common import UrlRefused, check_url

__all__ = ["http_url_error", "public_http_url_error"]


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
    """``None`` when ``url`` passes the outbound policy, else the reason it does not."""
    try:
        check_url(url)
    except UrlRefused as exc:
        message = str(exc)
        # The policy's own wording for a name it could not look up, and for a name it refused
        # without a lookup (localhost, *.internal, a single-label service name): both are "not a
        # public address" to a caller.
        if message.startswith("Cannot resolve host"):
            return message.replace("Cannot resolve host", "could not resolve host", 1)
        if "local or internal name" in message or "single-label name" in message:
            host = urlsplit(url.strip()).hostname or ""
            return f"Host {host!r} is not a public address (a local or internal name)."
        return message
    return None
