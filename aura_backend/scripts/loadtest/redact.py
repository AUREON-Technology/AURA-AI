"""Credential redaction for anything that ends up in a results file or report."""

from __future__ import annotations

import re
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

_SENSITIVE_PARAM = re.compile(r"(token|key|secret|pass|auth|sig|credential|session)", re.I)


def redact_url(url: str) -> str:
    """Strip userinfo and blank out credential-looking query parameters.

    ``https://user:pw@host/x?api_key=abc&q=1`` -> ``https://host/x?api_key=REDACTED&q=1``.
    """
    if not url:
        return url
    parts = urlsplit(url)
    netloc = parts.hostname or ""
    if parts.port:
        netloc = f"{netloc}:{parts.port}"
    if parts.username or parts.password:
        netloc = f"REDACTED@{netloc}"
    query = urlencode(
        [
            (k, "REDACTED" if _SENSITIVE_PARAM.search(k) else v)
            for k, v in parse_qsl(parts.query, keep_blank_values=True)
        ]
    )
    return urlunsplit((parts.scheme, netloc, parts.path, query, ""))
