"""SaaS P1-D1: SubscriptionStore and PaymentStore, run against every adapter."""

from __future__ import annotations

import os
from dataclasses import replace
from datetime import datetime, timedelta, timezone

import pytest

from aura_core.adapters.billing import InMemoryPaymentStore, InMemorySubscriptionStore
from aura_core.domain.billing import Payment, PaymentStatus, Subscription, SubscriptionStatus
from aura_core.domain.tenancy import Membership, Workspace, WorkspaceKind, WorkspaceRole, new_workspace_id

T0 = datetime(2026, 11, 17, 12, tzinfo=timezone.utc)
_PG_URL = os.environ.get("AURA_TEST_DATABASE_URL", "")


class _Memory:
    async def __aenter__(self):
        self.workspaces = [new_workspace_id(), new_workspace_id()]
        return InMemorySubscriptionStore(), InMemoryPaymentStore()

    async def __aexit__(self, *exc):
        return None


class _Postgres:
    async def __aenter__(self):
        from aura_core.adapters.postgres_billing import PostgresPaymentStore, PostgresSubscriptionStore
        from aura_core.adapters.postgres_store import PostgresPool
        from aura_core.adapters.postgres_tenancy import PostgresTenancyStore
        from tests.postgres_migrations import migrated_database

        if not _PG_URL:
            pytest.skip("AURA_TEST_DATABASE_URL is not set")
        self._db = migrated_database(_PG_URL, "billing")
        url = await self._db.__aenter__()
        self._pool = PostgresPool(url, min_size=1, max_size=4)
        tenancy = PostgresTenancyStore(url, pool=self._pool)
        self.workspaces = []
        for owner in ("u1", "u2"):
            ws = Workspace(new_workspace_id(), WorkspaceKind.PERSONAL, "P", owner, T0)
            await tenancy.create_workspace(ws, Membership(ws.id, owner, WorkspaceRole.OWNER, T0))
            self.workspaces.append(ws.id)
        return PostgresSubscriptionStore(url, pool=self._pool), PostgresPaymentStore(url, pool=self._pool)

    async def __aexit__(self, *exc):
        await self._pool.close()
        await self._db.__aexit__(*exc)


@pytest.fixture(params=[pytest.param(_Memory, id="memory"), pytest.param(_Postgres, id="postgres", marks=pytest.mark.load)])
async def stores(request):
    backend = request.param()
    async with backend as (subs, payments):
        yield subs, payments, backend.workspaces


def _sub(ws: str, **overrides) -> Subscription:
    fields = dict(workspace_id=ws, plan_id="pro", seats=1, status=SubscriptionStatus.ACTIVE, provider="fawry",
                  period_start=T0, period_end=T0 + timedelta(days=30), updated_at=T0)
    fields.update(overrides)
    return Subscription(**fields)


def _payment(ws: str, pid: str, **overrides) -> Payment:
    fields = dict(id=pid, workspace_id=ws, plan_id="pro", seats=1, amount_piastres=15000, method="PAYATFAWRY",
                  purpose="initial", status=PaymentStatus.PENDING, provider_ref=None, reference_number="9988",
                  created_at=T0, expires_at=T0 + timedelta(hours=72))
    fields.update(overrides)
    return Payment(**fields)


async def test_subscription_roundtrip_and_replace(stores) -> None:
    subs, _p, (ws, _) = stores
    assert await subs.get(ws) is None
    await subs.put(_sub(ws, card_token="tok-1", customer_profile_id="cp-1"))
    await subs.put(_sub(ws, status=SubscriptionStatus.GRACE, grace_until=T0 + timedelta(days=37), card_token="tok-1"))
    got = await subs.get(ws)
    assert got.status is SubscriptionStatus.GRACE and got.grace_until == T0 + timedelta(days=37)
    assert got.card_token == "tok-1" and got.period_end == T0 + timedelta(days=30)


async def test_due_lists_what_needs_the_cycle(stores) -> None:
    subs, _p, (ws1, ws2) = stores
    await subs.put(_sub(ws1, period_end=T0 + timedelta(days=2)))
    await subs.put(_sub(ws2, period_end=T0 + timedelta(days=20)))
    due = await subs.due(T0 + timedelta(days=3))
    assert [s.workspace_id for s in due] == [ws1]
    await subs.put(_sub(ws1, status=SubscriptionStatus.EXPIRED, period_end=T0))
    assert await subs.due(T0 + timedelta(days=3)) == []


async def test_mark_from_pending_is_once_only(stores) -> None:
    _s, payments, (ws, _) = stores
    await payments.put(_payment(ws, "pay-1"))
    paid = await payments.mark("pay-1", PaymentStatus.PAID, provider_ref="F-1", paid_at=T0)
    assert paid.status is PaymentStatus.PAID and paid.provider_ref == "F-1" and paid.paid_at == T0
    assert await payments.mark("pay-1", PaymentStatus.PAID, provider_ref="F-1", paid_at=T0) is None
    assert await payments.mark("pay-1", PaymentStatus.EXPIRED, provider_ref=None, paid_at=None) is None
    assert (await payments.get("pay-1")).status is PaymentStatus.PAID
    assert await payments.mark("nope", PaymentStatus.PAID, provider_ref=None, paid_at=T0) is None


async def test_pending_and_recent(stores) -> None:
    _s, payments, (ws, _) = stores
    await payments.put(_payment(ws, "old", created_at=T0 - timedelta(hours=1)))
    await payments.put(_payment(ws, "new", created_at=T0))
    await payments.put(_payment(ws, "done", status=PaymentStatus.PAID, created_at=T0))
    assert [p.id for p in await payments.pending(before=T0 - timedelta(minutes=10))] == ["old"]
    assert {p.id for p in await payments.pending(before=None)} == {"old", "new"}
    assert [p.id for p in await payments.for_workspace(ws, limit=2)] == ["new", "done"] or \
        [p.id for p in await payments.for_workspace(ws, limit=2)] == ["done", "new"]
