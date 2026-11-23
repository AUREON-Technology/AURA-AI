"""``verify_release_health``: judge a deployed service's health endpoints after a release (ADR-044).

``plan_rollout`` (``tools/analysis.py``) plans; this is the verification half. It GETs health URLs the
operator has allowed and decides from the response *body* whether the release is serving: a 200 that
says ``"healthy": false`` or reports the wrong ``version`` fails. It can retry for a bounded time so a
service that is still starting is not condemned on the first probe.

Boundaries, each pinned by ``tests/unit/test_open_partial_tools_completed.py``:

* **Only the operator's hosts.** Registered only when ``AURA_TOOLS_HTTP_ALLOWED_HOSTS`` is set (the same
  list ``fetch_url`` uses), https on the default port only, and the host must be on that list.
* **Never the gateway.** The request goes through ``tools/open/_ops_common.open_guarded``: private and
  internal hosts, the platform's own hosts and the ``/api/v3`` and ``/health`` paths are refused
  there, with no opt-out. That means a service whose health path is literally ``/health`` cannot be
  probed by this tool -- use ``/healthz``, ``/ready``, ``/status`` or ``/ping``.
* **Read only.** GET only, redirects not followed, body read to 64 KiB. It cannot deploy, restart or
  roll back anything; those stay in the operator's pipeline.
* **Untrusted output.** Health bodies are another system's text, so the tool is tagged
  ``untrusted_content`` and the Engineer persona (not the coding agent) holds it.
"""

from __future__ import annotations

import asyncio
import ipaddress
import json
from typing import Any
from urllib.parse import urlparse

from ..domain.errors import ToolExecutionError
from ..domain.tools import NETWORK_TAG, ToolCategory, ToolSensitivity
from ..ports.runtime import Tool
from .base import param, tool
from .open.release_ext import probe_health

__all__ = ["build_release_tools", "RELEASE_TOOL_NAMES"]

RELEASE_TOOL_NAMES: frozenset[str] = frozenset({"verify_release_health"})
_MAX_URLS = 5
_MAX_ATTEMPTS = 10


def _check_host(url: str, allowed: frozenset[str]) -> str:
    name = "verify_release_health"
    if any(c.isspace() or ord(c) < 32 or ord(c) == 127 or c == "\\" for c in url):
        raise ToolExecutionError("the URL contains characters that are not permitted", tool=name)
    try:
        parsed = urlparse(url)
        port = parsed.port
    except ValueError as exc:
        raise ToolExecutionError("the URL is not valid", tool=name) from exc
    if parsed.scheme != "https" or "@" in parsed.netloc or port not in (None, 443):
        raise ToolExecutionError("only https URLs on the default port, without credentials, are permitted", tool=name)
    host = (parsed.hostname or "").lower()
    try:
        ipaddress.ip_address(host)
        raise ToolExecutionError("IP-literal hosts are not permitted", tool=name)
    except ValueError:
        pass
    if not host or not any(host == h or host.endswith(f".{h}") for h in allowed):
        raise ToolExecutionError(
            f"host {host!r} is not in the configured allowlist", tool=name, details={"allowlist": sorted(allowed)})
    return url


def build_release_tools(allowed_hosts: frozenset[str] | set[str] | tuple[str, ...]) -> list[Tool]:
    """The verification tool, or nothing when the operator has allowed no hosts."""
    allowed = frozenset(h.lower() for h in allowed_hosts if h)
    if not allowed:
        return []

    @tool(
        "verify_release_health",
        "After a release, GET health URLs on operator-allowed hosts and judge the body: 2xx, not "
        "healthy:false, expected version, required fields. Retries while the service starts. Read "
        "only; cannot deploy or roll back. Paths /health and /api/v3 are refused by policy (use "
        "/healthz, /ready, /status). Hosts: " + ", ".join(sorted(allowed)),
        param("urls", "array", "https health URLs (1-5).", item_type="string"),
        param("expected_version", "string", "Version (or commit prefix) the body must report.",
              required=False, max_length=100),
        param("version_field", "string", "Dotted path of the version in the JSON (default: search "
              "version/release/commit/sha).", required=False, max_length=100),
        param("required_fields", "array", "Dotted JSON paths that must exist, e.g. schema.revision.",
              required=False, item_type="string"),
        param("attempts", "integer", "Tries per URL until healthy (default 3).", required=False, default=3,
              minimum=1, maximum=_MAX_ATTEMPTS),
        param("interval_s", "number", "Seconds between tries (default 5).", required=False, default=5,
              minimum=0, maximum=30),
        param("timeout_s", "number", "Seconds per request (default 10).", required=False, default=10,
              minimum=1, maximum=20),
        category=ToolCategory.DEVELOPMENT, sensitivity=ToolSensitivity.PUBLIC, timeout_s=330.0,
        max_result_chars=16_000, tags=("untrusted_content", NETWORK_TAG),
    )
    async def verify_release_health(
        urls: list, expected_version: str | None = None, version_field: str | None = None,
        required_fields: list | None = None, attempts: int = 3, interval_s: float = 5.0, timeout_s: float = 10.0,
    ) -> str:
        if not urls or len(urls) > _MAX_URLS:
            raise ToolExecutionError(f"give 1-{_MAX_URLS} URLs", tool="verify_release_health")
        checked = [_check_host(str(u), allowed) for u in urls]
        fields = [str(f)[:100] for f in (required_fields or [])][:10]
        history: dict[str, list[dict[str, Any]]] = {u: [] for u in checked}
        pending = list(checked)
        for attempt in range(1, attempts + 1):
            results = await asyncio.gather(*(asyncio.to_thread(
                probe_health, u, timeout_s=timeout_s, expected_version=expected_version,
                version_field=version_field, required_fields=fields) for u in pending))
            still = []
            for url, outcome in zip(pending, results):
                history[url].append({"attempt": attempt, **{k: v for k, v in outcome.items() if k != "url"}})
                if not outcome.get("ok") and "URL refused" not in str(outcome.get("error", "")):
                    still.append(url)
            pending = still
            if not pending or attempt == attempts:
                break
            await asyncio.sleep(interval_s)
        endpoints = [{"url": u, "healthy": bool(h and h[-1].get("ok")), "attempts": len(h), "last": h[-1], "history": h[:_MAX_ATTEMPTS]}
                     for u, h in history.items()]
        healthy = all(e["healthy"] for e in endpoints)
        return json.dumps({
            "all_healthy": healthy, "endpoints": endpoints, "expected_version": expected_version,
            "verdict": "release is serving" if healthy else "release NOT verified: " + "; ".join(
                f"{e['url']}: {e['last'].get('error') or ', '.join(e['last'].get('problems', [])) or 'unhealthy'}"
                for e in endpoints if not e["healthy"]),
            "executed": False, "read_only": True,
        }, ensure_ascii=False, default=str)

    return [verify_release_health]
