"""Small derivations the container needs to wire the control center (no I/O)."""

from __future__ import annotations

from typing import Any
from urllib.parse import urlsplit

from ..config.external_inference import is_hosted_open_weight_host

__all__ = ["cloudflare_api_root"]


def cloudflare_api_root(providers: Any) -> str:
    """``https://<host>/client/v4`` of the first configured Workers AI provider, or ``""``.

    A Workers AI slot's base URL is ``https://<host>/client/v4/accounts/<id>/ai/v1``; the token
    API lives at the same root. Only a host on ADR-030's sanctioned list qualifies, so the
    control center never sends a Cloudflare token anywhere the inference policy has not admitted.
    """
    for provider in providers:
        base = getattr(provider, "base_url", "") or ""
        parts = urlsplit(base)
        marker = "/client/v4/"
        if parts.scheme == "https" and marker in parts.path and is_hosted_open_weight_host(parts.hostname or ""):
            return f"https://{parts.hostname}{parts.path.split(marker, 1)[0]}/client/v4"
    return ""
