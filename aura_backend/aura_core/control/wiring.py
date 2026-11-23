"""Small derivations the container needs to wire the control center (no I/O)."""

from __future__ import annotations

from typing import Any
from urllib.parse import urlsplit

from ..config.external_inference import (
    WORKERS_AI_API_ROOT,
    is_hosted_open_weight_host,
    is_remote_inference_host,
)
from ..domain.control import KeySlot

__all__ = ["cloudflare_api_root", "key_slots"]


def cloudflare_api_root(providers: Any) -> str:
    """``https://<host>/client/v4`` of the first configured Workers AI provider, else the
    registry's own Workers AI root.

    A Workers AI slot's base URL is ``https://<host>/client/v4/accounts/<id>/ai/v1``; the token
    API lives at the same root. Only a host on ADR-030's sanctioned list qualifies, so the
    control center never sends a Cloudflare token anywhere the inference policy has not admitted.

    With no provider configured the root is :data:`WORKERS_AI_API_ROOT`, so the *first* Workers AI
    provider can be added from the console. Before the Cloudflare tab, it was empty here and
    nothing could connect until a provider already existed.
    """
    for provider in providers:
        base = getattr(provider, "base_url", "") or ""
        parts = urlsplit(base)
        marker = "/client/v4/"
        if parts.scheme == "https" and marker in parts.path and is_hosted_open_weight_host(parts.hostname or ""):
            return f"https://{parts.hostname}{parts.path.split(marker, 1)[0]}/client/v4"
    return WORKERS_AI_API_ROOT


def key_slots(providers: Any) -> list[KeySlot]:
    """One slot per configured provider whose key is read from a variable (``api_key_env``).

    A Workers AI slot is recognised by its base URL (``.../client/v4/accounts/<id>/...`` on a
    sanctioned host), which also names the account a new token must be scoped to; a Space by its
    host being a gated remote-inference host. No host is named here (ADR-030).
    """
    slots: list[KeySlot] = []
    for provider in providers:
        env = getattr(provider, "api_key_env", "") or ""
        if not env:
            continue
        parts = urlsplit(getattr(provider, "base_url", "") or "")
        host = (parts.hostname or "").lower()
        account = ""
        marker = "/client/v4/accounts/"
        if parts.scheme == "https" and marker in parts.path and is_hosted_open_weight_host(host):
            account = parts.path.split(marker, 1)[1].split("/", 1)[0]
        slots.append(KeySlot(
            name=provider.name, kind=getattr(provider, "kind", ""), api_key_env=env, host=host,
            remote_host=is_remote_inference_host(host) is not None, account_id=account,
        ))
    return slots

