"""What a person has used, what they are allowed, and what plan they are on.

    GET /me/usage            this month at a glance: messages, storage, tokens, limits, subscription
    GET /me/usage/history    messages per month, and tokens and cost per day
    GET /plans               every plan and what it allows

``/me`` is the caller's **personal** workspace. A team's plan, usage and billing are still
``/workspaces/{id}/plan`` and ``/workspaces/{id}/billing`` (any member, and owner or admin): the
same numbers, for a workspace the caller names.

**Messages and tokens are two different measures.** The plan meters *messages* (and storage bytes)
per workspace per calendar month, in Postgres, so the message history reaches back as far as the
meter does. Tokens and cost are not metered by the plan: they are summed from the content-free turn
telemetry, **per person** across every workspace they chatted in, and only as far back as the
telemetry is kept (``retention_days`` says how long; the purge is a daily task, so older rows may
still be there). ``estimated_turns`` counts the turns whose token numbers were AURA's own estimate
rather than the provider's report.

Operators and developers are not metered (``UNMETERED_ROLES``): their plan, messages and storage
are ``null`` and ``metered`` is false; their tokens are still totalled. A deployment with no signing
secret has no owner reference to total tokens under (``execution``'s turn usage fails closed the
same way): ``tokens.available`` is false rather than a made-up zero.

No ``from __future__ import annotations``, like the other API modules.
"""

from datetime import datetime, timedelta, timezone
from typing import Any, Callable

from ..domain.errors import NotFoundError
from ..domain.plans import period_for, period_resets_at
from ..plans.service import MESSAGES, UNMETERED_ROLES

__all__ = ["register_usage_ops"]


def _month_periods(now: datetime, count: int) -> list[str]:
    """``count`` calendar-month periods ending with ``now``'s, newest first."""
    periods, year, month = [], now.year, now.month
    for _ in range(count):
        periods.append(f"{year:04d}-{month:02d}")
        year, month = (year - 1, 12) if month == 1 else (year, month - 1)
    return periods


def _totals(rows: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "turns": sum(r["turns"] for r in rows),
        "prompt_tokens": sum(r["prompt_tokens"] for r in rows),
        "completion_tokens": sum(r["completion_tokens"] for r in rows),
        "total_tokens": sum(r["prompt_tokens"] + r["completion_tokens"] for r in rows),
        "cost_usd": round(sum(r["cost_usd"] for r in rows), 6),
        "estimated_turns": sum(r["estimated_turns"] for r in rows),
    }


def register_usage_ops(
    router: Any,
    container: Any,
    *,
    principal: Callable,
    idempotent: list,
    Depends: Any,
    JSONResponse: Any,
    enforce_rate_limit: Callable,
    Request: Any,
) -> None:
    from fastapi import Query

    def now() -> datetime:
        return container.clock.now()

    def person(caller: Any) -> str:
        if caller.is_service:
            raise NotFoundError("a service key has no usage of its own")
        return str(caller.user_id)

    def metered(caller: Any) -> bool:
        return getattr(container, "plans", None) is not None and getattr(caller, "role", "") not in UNMETERED_ROLES

    async def tokens_between(caller: Any, since: datetime, until: datetime) -> list[dict[str, Any]] | None:
        """The caller's per-day token rows, or ``None`` when no owner reference can be made."""
        make_ref = getattr(container, "owner_ref", None)
        ref = make_ref(caller.user_id.value) if make_ref is not None else None
        if not ref:
            return None
        return list(await container.telemetry_sink.usage_by_day(ref, since=since, until=until))

    def retention_days() -> int:
        return int(container.settings.observability.telemetry_retention_days)

    @router.get("/me/usage")
    async def my_usage(request: Request, caller: Any = Depends(principal)) -> Any:
        await enforce_rate_limit(request, caller, "read")
        user = person(caller)
        current = now()
        period = period_for(current)
        month_start = datetime(current.year, current.month, 1, tzinfo=timezone.utc)
        body: dict[str, Any] = {
            "period": period, "resets_at": period_resets_at(period).isoformat(), "metered": metered(caller),
            "workspace": None, "plan": None, "messages": None, "storage": None,
            "subscription": None, "billing_enabled": False,
        }
        if body["metered"]:
            workspace = await container.tenancy.ensure_personal(user)
            usage = await container.plans.usage(workspace.id)
            body["workspace"] = {"id": workspace.id, "kind": "personal"}
            body["plan"] = {
                "id": usage["plan"], "seats": usage["seats"], "seat_limit": usage["seat_limit"],
                "engineer": usage["engineer"],
            }
            body["messages"] = {"used": usage["messages_used"], "allowed": usage["messages_allowed"]}
            body["storage"] = {"used_bytes": usage["storage_used"], "allowed_bytes": usage["storage_allowed"]}
            billing = getattr(container, "billing", None)
            body["billing_enabled"] = bool(billing is not None and billing.settings.enabled)
            if body["billing_enabled"]:
                sub = await billing.subscriptions.get(workspace.id)
                if sub is not None:
                    body["subscription"] = {
                        "plan": sub.plan_id, "seats": sub.seats, "status": sub.status.value,
                        "period_end": sub.period_end.isoformat(),
                        "grace_until": sub.grace_until.isoformat() if sub.grace_until else None,
                    }
        rows = await tokens_between(caller, month_start, period_resets_at(period))
        body["tokens"] = (
            {"available": False} if rows is None else {"available": True, "since": month_start.isoformat(), **_totals(rows)}
        )
        return JSONResponse(content=body)

    @router.get("/me/usage/history")
    async def my_usage_history(
        request: Request,
        caller: Any = Depends(principal),
        months: int = Query(default=6, ge=1, le=24, description="Months of message history, newest first."),
        days: int = Query(default=30, ge=1, le=366, description="Days of token history, newest first."),
    ) -> Any:
        await enforce_rate_limit(request, caller, "read")
        user = person(caller)
        current = now()
        messages: list[dict[str, Any]] = []
        is_metered = metered(caller)
        if is_metered:
            workspace = await container.tenancy.ensure_personal(user)
            for period in _month_periods(current, months):
                messages.append({
                    "period": period,
                    "messages_used": await container.plans.meter.get(workspace.id, MESSAGES, period),
                })
        today = datetime(current.year, current.month, current.day, tzinfo=timezone.utc)
        rows = await tokens_between(caller, today - timedelta(days=days - 1), today + timedelta(days=1))
        return JSONResponse(content={
            "metered": is_metered,
            "messages": messages,
            "tokens": {
                "available": rows is not None,
                "retention_days": retention_days(),
                "days": rows or [],
                **(_totals(rows) if rows is not None else {}),
            },
        })

    @router.get("/plans")
    async def list_plans(request: Request, caller: Any = Depends(principal)) -> Any:
        await enforce_rate_limit(request, caller, "read")
        plans = getattr(container, "plans", None)
        catalogue = plans.catalogue if plans is not None else {}
        return JSONResponse(content={"plans": [
            {
                "id": p.id,
                "messages_per_month": p.messages_per_month,
                "messages_pooled_per_seat": p.messages_pooled_per_seat,
                "engineer": p.engineer,
                "storage_bytes": p.storage_bytes,
                "min_seats": p.min_seats,
                "max_seats": p.max_seats,
                "workspace_kinds": sorted(k.value for k in p.workspace_kinds),
            }
            for p in catalogue.values()
        ]})
