"""Cloudflare API tokens for the Workers AI slot (ADR-037, stage 4).

``HttpCloudflareTokens`` implements ``CloudflareTokens`` against Cloudflare's user-token API with
the management token stored in the control center (it needs "API Tokens: Edit"). The API root is
derived from the configured Workers AI provider's base URL (``control/wiring.py``), never written
here: hosted-inference hosts may not be named in source (ADR-030).

* ``GET  /user/tokens/verify`` -- with a key as its own bearer: which token a key is.
* ``GET  /user/tokens`` -- the management token's user's tokens.
* ``GET  /user/tokens/permission_groups`` -- to find the Workers AI groups by name.
* ``POST /user/tokens`` -- a new token scoped to the one account, with the Workers AI read and
  write groups (what Cloudflare's own Workers AI template grants).
* ``DELETE /user/tokens/{id}``.

A new token's value is returned to the engine once, which writes it to the gateway's variable;
it is never logged or returned to a browser. Vendor messages are never passed through.

``InMemoryCloudflareTokens`` is the same contract with real state.
"""

from __future__ import annotations

import itertools
import secrets
from datetime import datetime, timezone
from typing import Any, Awaitable, Callable, Mapping, Sequence

from ..domain.errors import DependencyUnavailableError, NotFoundError, ValidationError
from ..ports.control_plane import CloudflareToken

__all__ = ["HttpCloudflareTokens", "InMemoryCloudflareTokens", "WORKERS_AI_GROUPS"]

#: Permission groups a new Workers AI key gets, by the names Cloudflare lists them under.
WORKERS_AI_GROUPS = ("Workers AI Read", "Workers AI Write")
_TIMEOUT_S = 15.0


def _parse_time(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


class HttpCloudflareTokens:
    """``token_source`` returns the stored management token; ``api_root`` is
    ``https://<host>/client/v4`` (``cloudflare_api_root``)."""

    def __init__(self, *, token_source: Callable[[], Awaitable[str]], api_root: str, transport: Any = None) -> None:
        self._token_source = token_source
        self._root = api_root.rstrip("/")
        self._transport = transport

    async def _request(self, method: str, path: str, *, bearer: str | None = None,
                       json: Mapping[str, Any] | None = None, refused: str = "Cloudflare refused the request") -> Any:
        if not self._root:
            raise ValidationError(
                "no Cloudflare Workers AI provider is configured, so there is no Cloudflare key to manage",
                details={"reason": "cloudflare_not_configured"},
            )
        import httpx

        token = bearer if bearer is not None else await self._token_source()
        try:
            async with httpx.AsyncClient(timeout=_TIMEOUT_S, transport=self._transport) as client:
                response = await client.request(
                    method, f"{self._root}{path}", json=json, headers={"Authorization": f"Bearer {token}"},
                )
        except Exception as exc:
            raise DependencyUnavailableError(
                "Cloudflare could not be reached", dependency="cloudflare", details={"error": type(exc).__name__},
            ) from exc
        if response.status_code in (401, 403):
            raise ValidationError(refused, details={"reason": "cloudflare_refused"})
        if response.status_code == 404:
            raise NotFoundError("Cloudflare has no such token")
        try:
            body = response.json()
        except ValueError:
            body = None
        if response.status_code >= 400 or not isinstance(body, dict) or body.get("success") is False:
            raise DependencyUnavailableError(
                "Cloudflare answered with an error", dependency="cloudflare",
                details={"status": response.status_code},
            )
        return body.get("result")

    async def identify(self, token: str) -> str | None:
        if not token:
            return None
        try:
            result = await self._request("GET", "/user/tokens/verify", bearer=token)
        except (ValidationError, NotFoundError):
            return None  # not a user token this API knows (or no longer valid)
        return str((result or {}).get("id") or "") or None

    async def list(self) -> Sequence[CloudflareToken]:
        result = await self._request(
            "GET", "/user/tokens?per_page=50", refused="Cloudflare refused the management token",
        )
        return [
            CloudflareToken(token_id=str(t.get("id") or ""), name=str(t.get("name") or ""),
                            status=str(t.get("status") or ""), issued_on=_parse_time(t.get("issued_on")))
            for t in (result or []) if isinstance(t, dict)
        ]

    async def _groups(self) -> list[dict[str, str]]:
        result = await self._request(
            "GET", "/user/tokens/permission_groups", refused="Cloudflare refused the management token",
        )
        groups = [{"id": str(g["id"])} for g in (result or [])
                  if isinstance(g, dict) and g.get("name") in WORKERS_AI_GROUPS and g.get("id")]
        if not groups:
            raise DependencyUnavailableError(
                "Cloudflare did not list the Workers AI permission groups", dependency="cloudflare",
            )
        return groups

    async def create(self, *, name: str, account_id: str) -> tuple[str, str]:
        if not account_id:
            raise ValidationError("the Workers AI provider's URL names no account", details={"reason": "no_account"})
        body = {
            "name": name,
            "policies": [{
                "effect": "allow",
                "resources": {f"com.cloudflare.api.account.{account_id}": "*"},
                "permission_groups": await self._groups(),
            }],
        }
        result = await self._request("POST", "/user/tokens", json=body,
                                     refused="the management token may not create tokens (it needs API Tokens: Edit)")
        token_id, value = str((result or {}).get("id") or ""), str((result or {}).get("value") or "")
        if not token_id or not value:
            raise DependencyUnavailableError("Cloudflare did not return the new token", dependency="cloudflare")
        return token_id, value

    async def revoke(self, token_id: str) -> None:
        await self._request("DELETE", f"/user/tokens/{token_id}",
                            refused="the management token may not revoke tokens (it needs API Tokens: Edit)")


class InMemoryCloudflareTokens:
    """Tokens with real state: ``values`` maps a key to its token id."""

    def __init__(self) -> None:
        self.tokens: dict[str, CloudflareToken] = {}
        self.values: dict[str, str] = {}
        self.created: list[tuple[str, str]] = []
        self.revoked: list[str] = []
        self._ids = itertools.count(1)

    def add(self, name: str, value: str) -> str:
        token_id = f"cf-{next(self._ids)}"
        self.tokens[token_id] = CloudflareToken(token_id, name, "active", datetime.now(timezone.utc))
        self.values[value] = token_id
        return token_id

    async def identify(self, token: str) -> str | None:
        token_id = self.values.get(token)
        return token_id if token_id in self.tokens else None

    async def list(self) -> Sequence[CloudflareToken]:
        return sorted(self.tokens.values(), key=lambda t: t.token_id)

    async def create(self, *, name: str, account_id: str) -> tuple[str, str]:
        if not account_id:
            raise ValidationError("the Workers AI provider's URL names no account", details={"reason": "no_account"})
        value = f"cfv-{secrets.token_hex(8)}"
        token_id = self.add(name, value)
        self.created.append((token_id, account_id))
        return token_id, value

    async def revoke(self, token_id: str) -> None:
        if token_id not in self.tokens:
            raise NotFoundError("Cloudflare has no such token")
        del self.tokens[token_id]
        self.revoked.append(token_id)
