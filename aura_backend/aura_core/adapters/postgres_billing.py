"""Postgres billing stores (SaaS P1-D1). Tables owned by Alembic revision 0022; no DDL here.

``PaymentStore.mark`` is ``UPDATE ... WHERE id = $1 AND status = 'pending' RETURNING``: a payment
leaves pending exactly once across every worker, which is what makes a duplicate or concurrent
provider notification a no-op.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Mapping

from ..domain.billing import Payment, PaymentStatus, StoreBinding, Subscription, SubscriptionStatus
from .postgres_store import PostgresPool

__all__ = ["PostgresSubscriptionStore", "PostgresPaymentStore", "PostgresStoreBindingStore", "PostgresStoreEventLog"]

_SUB = ("workspace_id, plan_id, seats, status, provider, period_start, period_end, grace_until, "
        "card_token, customer_profile_id, updated_at")
_PAY = ("id, workspace_id, plan_id, seats, amount_piastres, method, purpose, status, provider_ref, "
        "reference_number, created_at, expires_at, paid_at")


def _utc(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value.astimezone(timezone.utc)


def _sub(row: Mapping[str, Any]) -> Subscription:
    return Subscription(
        workspace_id=row["workspace_id"], plan_id=row["plan_id"], seats=int(row["seats"]),
        status=SubscriptionStatus(row["status"]), provider=row["provider"],
        period_start=_utc(row["period_start"]), period_end=_utc(row["period_end"]),
        updated_at=_utc(row["updated_at"]), grace_until=_utc(row["grace_until"]),
        card_token=row["card_token"], customer_profile_id=row["customer_profile_id"],
    )


def _pay(row: Mapping[str, Any]) -> Payment:
    return Payment(
        id=row["id"], workspace_id=row["workspace_id"], plan_id=row["plan_id"], seats=int(row["seats"]),
        amount_piastres=int(row["amount_piastres"]), method=row["method"], purpose=row["purpose"],
        status=PaymentStatus(row["status"]), provider_ref=row["provider_ref"],
        reference_number=row["reference_number"], created_at=_utc(row["created_at"]),
        expires_at=_utc(row["expires_at"]), paid_at=_utc(row["paid_at"]),
    )


class PostgresSubscriptionStore:
    def __init__(self, url: str, *, pool: PostgresPool | None = None) -> None:
        self._pool = pool or PostgresPool(url)

    async def get(self, workspace_id: str) -> Subscription | None:
        pool = await self._pool._get_pool()
        async with pool.acquire() as conn:
            row = await conn.fetchrow(f"SELECT {_SUB} FROM aura_subscriptions WHERE workspace_id = $1", workspace_id)
        return _sub(row) if row else None

    async def put(self, s: Subscription) -> None:
        pool = await self._pool._get_pool()
        async with pool.acquire() as conn:
            await conn.execute(
                f"INSERT INTO aura_subscriptions ({_SUB}) VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11) "
                "ON CONFLICT (workspace_id) DO UPDATE SET plan_id = EXCLUDED.plan_id, seats = EXCLUDED.seats, "
                "status = EXCLUDED.status, provider = EXCLUDED.provider, period_start = EXCLUDED.period_start, "
                "period_end = EXCLUDED.period_end, grace_until = EXCLUDED.grace_until, "
                "card_token = EXCLUDED.card_token, customer_profile_id = EXCLUDED.customer_profile_id, "
                "updated_at = EXCLUDED.updated_at",
                s.workspace_id, s.plan_id, s.seats, s.status.value, s.provider, s.period_start, s.period_end,
                s.grace_until, s.card_token, s.customer_profile_id, s.updated_at,
            )

    async def due(self, before: datetime) -> list[Subscription]:
        pool = await self._pool._get_pool()
        async with pool.acquire() as conn:
            rows = await conn.fetch(
                f"SELECT {_SUB} FROM aura_subscriptions WHERE status IN ('active','past_due','grace','canceled') "
                "AND LEAST(period_end, COALESCE(grace_until, period_end)) < $1 ORDER BY period_end, workspace_id",
                before,
            )
        return [_sub(r) for r in rows]


class PostgresPaymentStore:
    def __init__(self, url: str, *, pool: PostgresPool | None = None) -> None:
        self._pool = pool or PostgresPool(url)

    async def put(self, p: Payment) -> None:
        pool = await self._pool._get_pool()
        async with pool.acquire() as conn:
            await conn.execute(
                f"INSERT INTO aura_payments ({_PAY}) VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,$13) "
                "ON CONFLICT (id) DO UPDATE SET reference_number = EXCLUDED.reference_number, "
                "provider_ref = EXCLUDED.provider_ref",
                p.id, p.workspace_id, p.plan_id, p.seats, p.amount_piastres, p.method, p.purpose, p.status.value,
                p.provider_ref, p.reference_number, p.created_at, p.expires_at, p.paid_at,
            )

    async def get(self, payment_id: str) -> Payment | None:
        pool = await self._pool._get_pool()
        async with pool.acquire() as conn:
            row = await conn.fetchrow(f"SELECT {_PAY} FROM aura_payments WHERE id = $1", payment_id)
        return _pay(row) if row else None

    async def pending(self, *, before: datetime | None) -> list[Payment]:
        pool = await self._pool._get_pool()
        async with pool.acquire() as conn:
            rows = await conn.fetch(
                f"SELECT {_PAY} FROM aura_payments WHERE status = 'pending' "
                "AND ($1::timestamptz IS NULL OR created_at < $1) ORDER BY created_at, id",
                before,
            )
        return [_pay(r) for r in rows]

    async def for_workspace(self, workspace_id: str, *, limit: int) -> list[Payment]:
        pool = await self._pool._get_pool()
        async with pool.acquire() as conn:
            rows = await conn.fetch(
                f"SELECT {_PAY} FROM aura_payments WHERE workspace_id = $1 ORDER BY created_at DESC, id DESC LIMIT $2",
                workspace_id, limit,
            )
        return [_pay(r) for r in rows]

    async def mark(self, payment_id, status, *, provider_ref, paid_at) -> Payment | None:
        pool = await self._pool._get_pool()
        async with pool.acquire() as conn:
            row = await conn.fetchrow(
                f"UPDATE aura_payments SET status = $2, provider_ref = COALESCE($3, provider_ref), paid_at = $4 "
                f"WHERE id = $1 AND status = 'pending' RETURNING {_PAY}",
                payment_id, status.value, provider_ref, paid_at,
            )
        return _pay(row) if row else None


class PostgresStoreBindingStore:
    """Tables owned by Alembic revision 0023."""

    def __init__(self, url: str, *, pool: PostgresPool | None = None) -> None:
        self._pool = pool or PostgresPool(url)

    async def put(self, b: StoreBinding) -> None:
        pool = await self._pool._get_pool()
        async with pool.acquire() as conn:
            await conn.execute(
                "INSERT INTO aura_store_bindings (token, workspace_id, created_by, created_at) VALUES ($1,$2,$3,$4) "
                "ON CONFLICT (token) DO NOTHING",
                b.token, b.workspace_id, b.created_by, b.created_at,
            )

    async def get(self, token: str) -> StoreBinding | None:
        pool = await self._pool._get_pool()
        async with pool.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT token, workspace_id, created_by, created_at FROM aura_store_bindings WHERE token = $1", token
            )
        return StoreBinding(row["token"], row["workspace_id"], row["created_by"], _utc(row["created_at"])) if row else None


class PostgresStoreEventLog:
    def __init__(self, url: str, *, pool: PostgresPool | None = None) -> None:
        self._pool = pool or PostgresPool(url)

    async def record(self, provider: str, event_id: str, at: datetime) -> bool:
        pool = await self._pool._get_pool()
        async with pool.acquire() as conn:
            status = await conn.execute(
                "INSERT INTO aura_store_events (provider, event_id, received_at) VALUES ($1,$2,$3) "
                "ON CONFLICT (provider, event_id) DO NOTHING",
                provider, event_id, at,
            )
        return status.rsplit(" ", 1)[-1] == "1"
