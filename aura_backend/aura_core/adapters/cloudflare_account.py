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

Two more reads, both read-only and both answering with a *named state* instead of raising (C3):

* ``usage`` -- Workers AI requests and neurons for the UTC day, from the GraphQL dataset
  ``aiInferenceAdaptiveGroups`` at ``{api_root}/graphql``. The name of its neuron field is not
  confirmed against a live token, so it is discovered by introspection (any sum field mentioning
  "neuron"); without one the result is requests only. Needs ``Account Analytics: Read``.
* ``r2_buckets`` -- ``GET /accounts/{id}/r2/buckets`` (``Workers R2 Storage: Read``), plus each
  bucket's lock rules as a best-effort extra. Error 10042 is the state ``not_enabled``.

The API root is ``config/external_inference.WORKERS_AI_API_ROOT`` (passed in by the container), so no
host is named here (ADR-030). The management token is read per call and never logged; vendor
messages are never passed through.

``InMemoryCloudflareAccount`` is the same contract with real state.
"""

from __future__ import annotations

import asyncio
import re
from dataclasses import replace
from datetime import date, datetime, timezone
from typing import Any, Awaitable, Callable, Mapping

from ..domain.errors import DependencyUnavailableError, NotFoundError, ValidationError
from ..ports.control_plane import (
    CloudflareBucket,
    CloudflareModel,
    CloudflareModelPage,
    CloudflareR2,
    CloudflareUsage,
)

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

    # -- usage and R2 (C3) -----------------------------------------------------------------------
    def _check(self, account_id: str, what: str) -> None:
        if not self._root:
            raise ValidationError(
                f"Cloudflare is not configured, so there is no {what} to read",
                details={"reason": "cloudflare_not_configured"},
            )
        if not isinstance(account_id, str) or not _ACCOUNT.fullmatch(account_id):
            raise ValidationError(
                "a Cloudflare account id is 32 lower-case hexadecimal characters",
                details={"reason": "bad_account"},
            )

    @staticmethod
    def _body(response: Any) -> Any:
        try:
            return response.json()
        except ValueError:
            return None

    async def _graphql(self, client: Any, headers: Mapping[str, str], query: str) -> tuple[int, Any]:
        response = await client.post(f"{self._root}/graphql", json={"query": query}, headers=dict(headers))
        return response.status_code, self._body(response)

    async def usage(self, account_id: str, *, day: date | None = None) -> CloudflareUsage:
        """Workers AI requests, and neurons when Cloudflare names a field for them, since 00:00 UTC.

        Two GraphQL calls: an introspection of ``AccountAiInferenceAdaptiveGroupsSum`` to find a
        field whose name mentions neurons (its name has not been confirmed against a live token),
        then the aggregate for the day. A refused token, a missing permission and every failure to
        read Cloudflare are named states of the result; vendor messages are never passed on.
        """
        self._check(account_id, "usage")
        day = day or datetime.now(timezone.utc).date()
        since = f"{day.isoformat()}T00:00:00Z"
        import httpx

        headers = {"Authorization": f"Bearer {await self._token_source()}"}
        try:
            async with httpx.AsyncClient(timeout=_TIMEOUT_S, transport=self._transport) as client:
                status, body = await self._graphql(client, headers, _SUM_FIELDS_QUERY)
                if status in (401, 403):
                    return CloudflareUsage(state="no_permission", day=day.isoformat())
                field = _neuron_field(body) if status == 200 else ""
                status, body = await self._graphql(client, headers, _usage_query(account_id, since, field))
                outcome = _read_usage(status, body, field)
                if outcome is _RETRY:
                    # The field was a guess that Cloudflare rejected: count requests alone.
                    status, body = await self._graphql(client, headers, _usage_query(account_id, since, ""))
                    outcome = _read_usage(status, body, "")
        except Exception:
            return CloudflareUsage(state="unavailable", day=day.isoformat(), reason="connection_failed")
        if isinstance(outcome, str):
            return CloudflareUsage(state="unavailable", day=day.isoformat(), reason=outcome)
        if outcome is _DENIED_RESULT:
            return CloudflareUsage(state="no_permission", day=day.isoformat())
        requests, neurons, used = outcome
        return CloudflareUsage(state="ok", day=day.isoformat(), requests=requests, neurons=neurons, neurons_field=used)

    async def r2_buckets(self, account_id: str) -> CloudflareR2:
        """The account's R2 buckets, with each bucket's lock rules where Cloudflare will say.

        Read-only. Error 10042 ("enable R2 through the Cloudflare Dashboard") is the state
        ``not_enabled``. The per-bucket lock lookup is best effort (its endpoint is unverified):
        any failure there means "unknown" for that bucket and never fails the list.
        """
        self._check(account_id, "R2 buckets")
        import httpx

        headers = {"Authorization": f"Bearer {await self._token_source()}"}
        base = f"{self._root}/accounts/{account_id}/r2/buckets"
        try:
            async with httpx.AsyncClient(timeout=_TIMEOUT_S, transport=self._transport) as client:
                response = await client.get(base, params={"per_page": _R2_PAGE}, headers=headers)
                body = self._body(response)
                failed = _r2_state(response.status_code, body)
                if failed is not None:
                    return CloudflareR2(state=failed[0], reason=failed[1])
                buckets = [b for b in (_bucket(item) for item in body["result"]["buckets"]) if b is not None]
                info = body.get("result_info")
                more = isinstance(info, dict) and bool(info.get("cursor")) and len(buckets) >= _R2_PAGE
                gate = asyncio.Semaphore(_LOCK_PARALLEL)

                async def locks(name: str) -> int | None:
                    async with gate:
                        return await self._lock_rules(client, f"{base}/{name}/lock", headers)

                looked_up = [b.name for b in buckets if _BUCKET.fullmatch(b.name)][:_LOCK_LOOKUPS]
                counts = dict(zip(looked_up, await asyncio.gather(*(locks(n) for n in looked_up))))
        except Exception:
            return CloudflareR2(state="unavailable", reason="connection_failed")
        return CloudflareR2(
            state="ok", more=more,
            buckets=tuple(replace(b, lock_rules=counts.get(b.name)) for b in buckets),
        )

    async def _lock_rules(self, client: Any, url: str, headers: Mapping[str, str]) -> int | None:
        try:
            response = await client.get(url, headers=dict(headers))
            if response.status_code != 200:
                return None
            body = self._body(response)
        except Exception:
            return None
        result = body.get("result") if isinstance(body, dict) else None
        rules = result.get("rules") if isinstance(result, dict) else None
        return len(rules) if isinstance(rules, list) else None


# -- usage: the GraphQL dataset ------------------------------------------------------------------

_SUM_FIELDS_QUERY = 'query { __type(name: "AccountAiInferenceAdaptiveGroupsSum") { fields { name } } }'
_NEURON = re.compile(r"neuron", re.IGNORECASE)
_IDENTIFIER = re.compile(r"[A-Za-z_][A-Za-z0-9_]{0,63}")
#: How GraphQL says the token may not read the dataset (``extensions.code`` is ``authz``).
_DENIED = re.compile(r"authz|authori[sz]|permission|forbidden|denied|does not have access", re.IGNORECASE)
_DENIED_RESULT = object()
_RETRY = object()


def _neuron_field(body: Any) -> str:
    """A sum field whose name mentions neurons, from the introspection answer, or ``""``."""
    try:
        fields = body["data"]["__type"]["fields"]
    except (KeyError, TypeError):
        return ""
    if not isinstance(fields, list):
        return ""
    for item in fields:
        name = item.get("name") if isinstance(item, dict) else None
        if isinstance(name, str) and _NEURON.search(name) and _IDENTIFIER.fullmatch(name):
            return name
    return ""


def _usage_query(account_id: str, since: str, field: str) -> str:
    """The day's aggregate. ``account_id`` is validated hexadecimal, ``since`` is built here and
    ``field`` is an identifier checked by :func:`_neuron_field`, so nothing is interpolated unchecked."""
    total = f" sum {{ {field} }}" if field else ""
    return (
        f'query {{ viewer {{ accounts(filter: {{accountTag: "{account_id}"}}) {{ '
        f'aiInferenceAdaptiveGroups(limit: 1, filter: {{datetimeHour_geq: "{since}"}}) {{ count{total} }} }} }} }}'
    )


def _denied(errors: list[Any]) -> bool:
    for error in errors:
        if not isinstance(error, dict):
            continue
        extensions = error.get("extensions")
        code = extensions.get("code") if isinstance(extensions, dict) else ""
        if _DENIED.search(f"{error.get('message') or ''} {code or ''}"):
            return True
    return False


def _read_usage(status: int, body: Any, field: str) -> Any:
    """``(requests, neurons, field)``, a reason string for ``unavailable``, ``_DENIED_RESULT``, or
    ``_RETRY`` when a guessed neuron field was rejected."""
    if status in (401, 403):
        return _DENIED_RESULT
    if status != 200:
        return f"http_{status}"
    if not isinstance(body, dict):
        return "unexpected_response"
    errors = body.get("errors")
    if isinstance(errors, list) and errors:
        if _denied(errors):
            return _DENIED_RESULT
        return _RETRY if field else "graphql_error"
    try:
        rows = body["data"]["viewer"]["accounts"][0]["aiInferenceAdaptiveGroups"]
    except (KeyError, IndexError, TypeError):
        return "unexpected_response"
    if not isinstance(rows, list):
        return "unexpected_response"
    requests = 0
    neurons = 0.0
    for row in rows:
        count = _whole(row.get("count")) if isinstance(row, dict) else None
        if count is None:
            return "unexpected_response"
        requests += count
        if field:
            sums = row.get("sum")
            value = sums.get(field) if isinstance(sums, dict) else None
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                return "unexpected_response"
            neurons += float(value)
    return requests, (neurons if field else None), field


# -- R2 ------------------------------------------------------------------------------------------

_R2_PAGE = 100
_LOCK_LOOKUPS = 50
_LOCK_PARALLEL = 5
#: R2 bucket names: 3-63 characters, lower-case letters, digits and hyphens, alphanumeric at both ends.
_BUCKET = re.compile(r"[a-z0-9][a-z0-9-]{1,61}[a-z0-9]")
#: "Please enable R2 through the Cloudflare Dashboard" -- the account has never turned R2 on.
_R2_NOT_ENABLED = 10042


def _error_codes(body: Any) -> set[int]:
    errors = body.get("errors") if isinstance(body, dict) else None
    codes: set[int] = set()
    for error in errors if isinstance(errors, list) else []:
        code = _whole(error.get("code")) if isinstance(error, dict) else None
        if code is not None:
            codes.add(code)
    return codes


def _r2_state(status: int, body: Any) -> tuple[str, str] | None:
    """``(state, reason)`` when the answer is not a bucket list, else ``None``."""
    if _R2_NOT_ENABLED in _error_codes(body):
        return "not_enabled", ""
    if status in (401, 403):
        return "no_permission", ""
    if status != 200:
        return "unavailable", f"http_{status}"
    if not isinstance(body, dict) or body.get("success") is False:
        return "unavailable", "unexpected_response"
    result = body.get("result")
    if not isinstance(result, dict) or not isinstance(result.get("buckets"), list):
        return "unavailable", "unexpected_response"
    return None


def _bucket(item: Any) -> CloudflareBucket | None:
    if not isinstance(item, dict):
        return None
    name = item.get("name")
    if not isinstance(name, str) or not name.strip():
        return None

    def text(key: str) -> str:
        value = item.get(key)
        return value.strip() if isinstance(value, str) else ""

    return CloudflareBucket(
        name=name.strip(), created=text("creation_date"), location=text("location"),
        storage_class=text("storage_class"),
    )


class InMemoryCloudflareAccount:
    """A catalog, an analytics record and R2 buckets with real state. ``calls`` records what each
    ``models`` call asked for, ``usage_calls`` and ``r2_calls`` the other two.

    The switches stand for what Cloudflare can answer: ``analytics_permission`` and
    ``r2_permission`` (the token's scopes), ``r2_enabled`` (R2 turned on in the dashboard),
    ``neurons_reported`` (Cloudflare names a neuron field) and ``available`` (Cloudflare reachable).
    """

    def __init__(self) -> None:
        self._models: list[CloudflareModel] = []
        self.calls: list[dict[str, Any]] = []
        self._usage: dict[date, tuple[int, float]] = {}
        self._buckets: list[CloudflareBucket] = []
        self.usage_calls: list[dict[str, Any]] = []
        self.r2_calls: list[dict[str, Any]] = []
        self.analytics_permission = True
        self.r2_permission = True
        self.r2_enabled = True
        self.neurons_reported = True
        self.available = True

    def add_model(self, model: CloudflareModel) -> None:
        self._models.append(model)

    def record_usage(self, day: date, *, requests: int, neurons: float = 0.0) -> None:
        before_requests, before_neurons = self._usage.get(day, (0, 0.0))
        self._usage[day] = (before_requests + requests, before_neurons + neurons)

    def add_bucket(self, bucket: CloudflareBucket) -> None:
        self._buckets.append(bucket)

    async def usage(self, account_id: str, *, day: date | None = None) -> CloudflareUsage:
        day = day or datetime.now(timezone.utc).date()
        self.usage_calls.append({"account_id": account_id, "day": day})
        if not self.available:
            return CloudflareUsage(state="unavailable", day=day.isoformat(), reason="connection_failed")
        if not self.analytics_permission:
            return CloudflareUsage(state="no_permission", day=day.isoformat())
        requests, neurons = self._usage.get(day, (0, 0.0))
        return CloudflareUsage(
            state="ok", day=day.isoformat(), requests=requests,
            neurons=neurons if self.neurons_reported else None,
        )

    async def r2_buckets(self, account_id: str) -> CloudflareR2:
        self.r2_calls.append({"account_id": account_id})
        if not self.available:
            return CloudflareR2(state="unavailable", reason="connection_failed")
        if not self.r2_enabled:
            return CloudflareR2(state="not_enabled")
        if not self.r2_permission:
            return CloudflareR2(state="no_permission")
        return CloudflareR2(state="ok", buckets=tuple(self._buckets))

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
