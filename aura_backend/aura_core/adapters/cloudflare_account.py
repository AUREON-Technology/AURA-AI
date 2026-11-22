"""The Cloudflare account behind the console's Cloudflare tab (console plan, stage 6, B3).

``HttpCloudflareAccount`` implements ``CloudflareAccount`` with the control center's stored
management token. Today that is one call, the Workers AI model catalog:

* ``GET /accounts/{account_id}/ai/models/search`` -- one page of models, optionally filtered by
  ``task`` and ``search``. Cloudflare's published docs do not document this endpoint; its shape was
  probed live on 2026-11-23 (each model has ``name`` = the id the gateway uses, a ``task``, a
  ``description`` and a ``properties`` list of ``{property_id, value}``). Because it is undocumented
  the parser is tolerant of values that arrive as strings, booleans or numbers and skips an entry it
  cannot read, but a body that is not a list at all is an *error*, never an empty catalog: showing
  "no models" for a changed API would be a lie.

The API root is ``config/external_inference.WORKERS_AI_API_ROOT`` (passed in by the container), so no
host is named here (ADR-030). The management token is read per call and never logged; vendor
messages are never passed through.

``InMemoryCloudflareAccount`` is the same contract with real state.
"""

from __future__ import annotations

import re
from typing import Any, Awaitable, Callable

from ..domain.errors import DependencyUnavailableError, NotFoundError, ValidationError
from ..ports.control_plane import CloudflareModel, CloudflareModelPage

__all__ = ["HttpCloudflareAccount", "InMemoryCloudflareAccount"]

_TIMEOUT_S = 15.0
_MAX_PER_PAGE = 100
_ACCOUNT = re.compile(r"[0-9a-f]{32}")


def _truthy(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() in {"true", "1", "yes"}
    if isinstance(value, (int, float)):
        return bool(value)
    return False


def _whole(value: Any) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, float) and value.is_integer():
        return int(value)
    if isinstance(value, str) and value.strip().isdigit():
        return int(value.strip())
    return None


def _model(item: Any) -> CloudflareModel | None:
    """One catalog entry, or ``None`` when it cannot be read."""
    if not isinstance(item, dict):
        return None
    name = item.get("name")
    if not isinstance(name, str) or not name.strip():
        return None
    task = item.get("task")
    task_name = task.get("name") if isinstance(task, dict) else task
    props: dict[str, Any] = {}
    for prop in item.get("properties") or []:
        if isinstance(prop, dict) and isinstance(prop.get("property_id"), str):
            props[prop["property_id"]] = prop.get("value")
    price = props.get("price")
    return CloudflareModel(
        model=name.strip(),
        task=task_name.strip() if isinstance(task_name, str) else "",
        description=item["description"].strip() if isinstance(item.get("description"), str) else "",
        context_window=_whole(props.get("context_window")),
        vision=_truthy(props.get("vision")),
        function_calling=_truthy(props.get("function_calling")),
        reasoning=_truthy(props.get("reasoning")),
        paid_only=_truthy(props.get("require_workers_paid")),
        price=tuple(p for p in price if isinstance(p, dict)) if isinstance(price, list) else (),
    )


class HttpCloudflareAccount:
    """``token_source`` returns the stored management token; ``api_root`` is
    ``https://<host>/client/v4``."""

    def __init__(self, *, token_source: Callable[[], Awaitable[str]], api_root: str, transport: Any = None) -> None:
        self._token_source = token_source
        self._root = api_root.rstrip("/")
        self._transport = transport

    async def models(
        self, account_id: str, *, task: str = "", search: str = "", page: int = 1, per_page: int = 100
    ) -> CloudflareModelPage:
        if not self._root:
            raise ValidationError(
                "Cloudflare is not configured, so there is no model catalog to read",
                details={"reason": "cloudflare_not_configured"},
            )
        if not isinstance(account_id, str) or not _ACCOUNT.fullmatch(account_id):
            raise ValidationError(
                "a Cloudflare account id is 32 lower-case hexadecimal characters",
                details={"reason": "bad_account"},
            )
        page = max(int(page), 1)
        per_page = min(max(int(per_page), 1), _MAX_PER_PAGE)
        params: dict[str, str | int] = {"page": page, "per_page": per_page}
        if task:
            params["task"] = task
        if search:
            params["search"] = search

        import httpx

        token = await self._token_source()
        try:
            async with httpx.AsyncClient(timeout=_TIMEOUT_S, transport=self._transport) as client:
                response = await client.request(
                    "GET", f"{self._root}/accounts/{account_id}/ai/models/search",
                    params=params, headers={"Authorization": f"Bearer {token}"},
                )
        except Exception as exc:
            raise DependencyUnavailableError(
                "Cloudflare could not be reached", dependency="cloudflare", details={"error": type(exc).__name__},
            ) from exc
        if response.status_code in (401, 403):
            raise ValidationError(
                "Cloudflare refused the token: it needs 'Workers AI: Read' on this account",
                details={"reason": "cloudflare_refused"},
            )
        if response.status_code == 404:
            raise NotFoundError("Cloudflare has no such account")
        try:
            body = response.json()
        except ValueError:
            body = None
        if response.status_code >= 400:
            raise DependencyUnavailableError(
                "Cloudflare answered with an error", dependency="cloudflare",
                details={"status": response.status_code},
            )
        if not isinstance(body, dict) or body.get("success") is False or not isinstance(body.get("result"), list):
            raise DependencyUnavailableError(
                "Cloudflare's model list was not in the expected shape", dependency="cloudflare",
                details={"reason": "unexpected_response", "status": response.status_code},
            )
        models = tuple(m for m in (_model(item) for item in body["result"]) if m is not None)
        info = body.get("result_info")
        info = info if isinstance(info, dict) else {}
        return CloudflareModelPage(
            models=models, total=_whole(info.get("total_count")),
            page=_whole(info.get("page")) or page, per_page=_whole(info.get("per_page")) or per_page,
        )


class InMemoryCloudflareAccount:
    """A catalog with real state. ``calls`` records what each ``models`` call asked for."""

    def __init__(self) -> None:
        self._models: list[CloudflareModel] = []
        self.calls: list[dict[str, Any]] = []

    def add_model(self, model: CloudflareModel) -> None:
        self._models.append(model)

    async def models(
        self, account_id: str, *, task: str = "", search: str = "", page: int = 1, per_page: int = 100
    ) -> CloudflareModelPage:
        self.calls.append({"account_id": account_id, "task": task, "search": search, "page": page, "per_page": per_page})
        needle = search.strip().lower()
        matching = [
            m for m in self._models
            if (not task or m.task == task) and (not needle or needle in m.model.lower() or needle in m.description.lower())
        ]
        page = max(page, 1)
        per_page = min(max(per_page, 1), _MAX_PER_PAGE)
        window = matching[(page - 1) * per_page: page * per_page]
        return CloudflareModelPage(models=tuple(window), total=len(matching), page=page, per_page=per_page)
