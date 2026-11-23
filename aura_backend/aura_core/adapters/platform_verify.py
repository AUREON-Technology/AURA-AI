"""``PlatformVerifier`` adapters: one cheap read call proving a pasted token works (ADR-037).

Every platform is verified: Railway (``projectToken``: the project and environment a project
token reaches), Hugging Face (``whoami``, then the Space itself), Cloudflare (its token-verify
endpoint), Qdrant (listing collections on the configured cluster) and Grafana Cloud (the stack
named with the token, through grafana.com's instances API).

Every vendor call is made inside a method, never at import, and never logs the token or the
vendor's response body (which can echo it).
"""

from __future__ import annotations

import asyncio
import re
from typing import Any
from urllib.parse import urlsplit

from ..domain.control import Platform
from ..domain.errors import DependencyUnavailableError, ValidationError

__all__ = ["HttpPlatformVerifier", "StaticPlatformVerifier"]

_TIMEOUT_S = 10.0
_CF_ACCOUNT = re.compile(r"[0-9a-f]{32}")


class HttpPlatformVerifier:
    """``target`` is what the token is for (the HF Space id, the Qdrant cluster URL). The
    gateway's own ``AURA_QDRANT_URL`` stands in when no Qdrant target is given.

    ``cloudflare_api_root`` comes from ``aura_core/control/wiring.py`` (the registry's Workers AI
    root, or the one a configured provider names), never written here: hosted inference hosts may
    not be named in source (ADR-030). A Cloudflare connection's ``target`` is the account id.

    ``transport`` is for tests (an in-process ``httpx`` transport); production passes none.
    """

    def __init__(
        self, *, default_qdrant_url: str = "", cloudflare_api_root: str = "", transport: Any = None,
    ) -> None:
        self._default_qdrant_url = default_qdrant_url.rstrip("/")
        self._cloudflare_api_root = cloudflare_api_root.rstrip("/")
        self._transport = transport

    async def verify(self, platform: Platform, token: str, *, target: str = "") -> str | None:
        if platform is Platform.RAILWAY:
            from .railway_platform import railway_scope

            scope = await railway_scope(token)
            return f"project {scope.project_name or scope.project_id}, environment {scope.environment_name or scope.environment_id}"
        if platform is Platform.HF_SPACE:
            return await asyncio.to_thread(self._verify_hf, token, target.strip().strip("/"))
        if platform is Platform.CLOUDFLARE:
            return await self._verify_cloudflare(token, target.strip())
        if platform is Platform.QDRANT:
            return await self._verify_qdrant(token, (target or self._default_qdrant_url).rstrip("/"))
        if platform is Platform.GRAFANA:
            from .grafana_cloud import grafana_stack, stack_slug

            stack = await grafana_stack(token, stack_slug(target))
            return f"stack {stack.slug} (Prometheus {stack.prometheus_id})"
        return None

    @staticmethod
    def _verify_hf(token: str, space_id: str) -> str:
        from huggingface_hub import HfApi

        if not space_id or "/" not in space_id:
            raise ValidationError("give the Space as owner/name, e.g. my-org/aura-inference")
        api = HfApi(token=token)
        try:
            who = api.whoami()
        except Exception as exc:
            raise ValidationError("Hugging Face refused the token") from exc
        try:
            api.space_info(space_id)
        except Exception as exc:
            raise ValidationError(f"the token works but cannot reach the Space {space_id}") from exc
        return f"Space {space_id} (account {who.get('name') or '?'})"

    async def _verify_cloudflare(self, token: str, account: str) -> str:
        """Two cheap reads: the token is a user-owned token the token API accepts (an account-owned
        ``cfat_`` token gets 401 on ``/user/tokens/verify``), and it can read Workers AI on the
        account it is connected for (the Cloudflare tab's model catalog)."""
        root = self._cloudflare_api_root
        if not root:
            raise ValidationError("Cloudflare is not configured, so there is nothing to connect")
        if not _CF_ACCOUNT.fullmatch(account):
            raise ValidationError(
                "give the Cloudflare account id: 32 lower-case hexadecimal characters, shown in the "
                "Cloudflare dashboard on the account's overview page"
            )
        headers = {"Authorization": f"Bearer {token}"}
        body = await self._get_json(
            f"{root}/user/tokens/verify", headers=headers,
            refused="Cloudflare refused the token: it must be a user-owned API token (My Profile -> API "
                    "Tokens; it starts with cfut_), not an account-owned token",
        )
        status = ((body or {}).get("result") or {}).get("status")
        if status != "active":
            raise ValidationError(f"the Cloudflare token is not active (status {status!r})")
        await self._get_json(
            f"{root}/accounts/{account}/ai/models/search?per_page=1", headers=headers,
            refused="the token cannot read Workers AI on this account: give it 'Workers AI: Read' on the account",
        )
        return f"Cloudflare token active; it can read Workers AI on account {account}"

    async def _verify_qdrant(self, token: str, url: str) -> str:
        # The gateway sends the key wherever this URL points, so it may only be Qdrant Cloud or
        # the cluster the gateway already uses -- never an internal host (no SSRF by URL).
        host = (urlsplit(url).hostname or "").lower() if url.startswith("https://") else ""
        allowed = host.endswith(".qdrant.io") or (
            bool(host) and host == (urlsplit(self._default_qdrant_url).hostname or "").lower()
        )
        if not allowed:
            raise ValidationError(
                "give the Qdrant Cloud cluster URL (https://<id>.<region>.cloud.qdrant.io)"
            )
        body = await self._get_json(
            f"{url}/collections", headers={"api-key": token},
            refused="Qdrant refused the key",
        )
        count = len(((body or {}).get("result") or {}).get("collections") or [])
        return f"Qdrant cluster reachable, {count} collection(s)"

    async def _get_json(self, url: str, *, headers: dict[str, str], refused: str) -> Any:
        import httpx

        try:
            async with httpx.AsyncClient(timeout=_TIMEOUT_S, transport=self._transport) as client:
                response = await client.get(url, headers=headers)
        except Exception as exc:
            raise DependencyUnavailableError(
                "the platform could not be reached to verify the token",
                dependency="control-platform", details={"error": type(exc).__name__},
            ) from exc
        if response.status_code in (401, 403):
            raise ValidationError(refused)
        if response.status_code >= 400:
            raise ValidationError(f"verification failed with HTTP {response.status_code}")
        try:
            return response.json()
        except ValueError:
            return None


class StaticPlatformVerifier:
    """Tokens are good when listed; anything else is refused like a vendor 401."""

    def __init__(self, good: dict[Platform, set[str]] | None = None) -> None:
        self._good = {k: set(v) for k, v in (good or {}).items()}

    def accept(self, platform: Platform, token: str) -> None:
        self._good.setdefault(platform, set()).add(token)

    async def verify(self, platform: Platform, token: str, *, target: str = "") -> str | None:
        if token in self._good.get(platform, set()):
            return f"{platform.value} verified"
        raise ValidationError(f"{platform.value} refused the token")
