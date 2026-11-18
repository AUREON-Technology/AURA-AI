"""In-memory billing stores and a fake payment provider (SaaS P1-D1): the substrate for tests
and a local run. Production uses ``postgres_billing.py`` and ``fawry.py``."""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import secrets
from dataclasses import replace
from datetime import datetime

from ..domain.billing import Payment, PaymentStatus, StoreBinding, Subscription, SubscriptionStatus
from ..domain.errors import AuthenticationError, ValidationError
from ..ports.billing import ProviderCharge, ProviderNotification, ProviderStatus

__all__ = ["InMemoryBillingLocks", "InMemoryStoreBindingStore", "InMemoryStoreEventLog", "InMemorySubscriptionStore", "InMemoryPaymentStore", "FakePaymentProvider"]

_LIVE = (SubscriptionStatus.ACTIVE, SubscriptionStatus.PAST_DUE, SubscriptionStatus.GRACE, SubscriptionStatus.CANCELED)


class InMemorySubscriptionStore:
    def __init__(self) -> None:
        self._rows: dict[str, Subscription] = {}

    async def get(self, workspace_id: str) -> Subscription | None:
        return self._rows.get(workspace_id)

    async def put(self, sub: Subscription) -> None:
        self._rows[sub.workspace_id] = sub

    async def due(self, before: datetime) -> list[Subscription]:
        return sorted(
            (s for s in self._rows.values()
             if s.status in _LIVE and min(s.period_end, s.grace_until or s.period_end) < before),
            key=lambda s: (s.period_end, s.workspace_id),
        )

    async def list_page(self, limit: int, after: str | None) -> list[Subscription]:
        rows = sorted((s for s in self._rows.values() if after is None or s.workspace_id > after),
                      key=lambda s: s.workspace_id)
        return rows[:limit]


class InMemoryPaymentStore:
    def __init__(self) -> None:
        self._rows: dict[str, Payment] = {}
        self._lock = asyncio.Lock()

    async def put(self, payment: Payment) -> None:
        self._rows[payment.id] = payment

    async def get(self, payment_id: str) -> Payment | None:
        return self._rows.get(payment_id)

    async def pending(self, *, before: datetime | None) -> list[Payment]:
        rows = [p for p in self._rows.values() if p.status is PaymentStatus.PENDING
                and (before is None or p.created_at < before)]
        return sorted(rows, key=lambda p: (p.created_at, p.id))

    async def for_workspace(self, workspace_id: str, *, limit: int) -> list[Payment]:
        rows = [p for p in self._rows.values() if p.workspace_id == workspace_id]
        return sorted(rows, key=lambda p: (p.created_at, p.id), reverse=True)[:limit]

    async def mark(self, payment_id, status, *, provider_ref, paid_at, expected=(PaymentStatus.PENDING,)) -> Payment | None:
        async with self._lock:
            current = self._rows.get(payment_id)
            if current is None or current.status not in expected:
                return None
            updated = replace(current, status=status, provider_ref=provider_ref or current.provider_ref,
                              paid_at=paid_at if paid_at is not None else current.paid_at)
            self._rows[payment_id] = updated
            return updated

    async def received(self) -> list[Payment]:
        return sorted((p for p in self._rows.values() if p.status is PaymentStatus.RECEIVED), key=lambda p: p.created_at)


class FakePaymentProvider:
    """Stands in for Fawry: issues reference codes, records card charges, and builds/verifies
    notifications signed with an HMAC of a test secret -- so the service's handling of a real
    signed notification is exercised without the network."""

    name = "fake"

    def __init__(self, secret: str | None = None) -> None:
        # Random per instance: the fake can never accept a notification forged with a known key.
        self._secret = (secret or secrets.token_hex(16)).encode()
        self.statuses: dict[str, ProviderStatus] = {}
        self.calls: list[tuple[str, str]] = []
        self.fail_with: Exception | None = None

    async def create_reference(self, payment: Payment, *, customer_profile_id: str, email: str | None) -> ProviderCharge:
        self.calls.append(("reference", payment.id))  # recorded first: a timeout may still have reached it
        self._maybe_fail()
        return ProviderCharge(reference_number=f"9{abs(hash(payment.id)) % 10**9:09d}", provider_ref=None)

    async def charge_card(self, payment: Payment, *, customer_profile_id: str, card_token: str) -> ProviderCharge:
        self.calls.append(("card", payment.id))  # recorded first: a timeout may still have reached it
        self._maybe_fail()
        return ProviderCharge(reference_number=None, provider_ref=f"card-{payment.id}")

    async def status(self, payment_id: str) -> ProviderStatus:
        self._maybe_fail()
        return self.statuses.get(payment_id, ProviderStatus(PaymentStatus.PENDING, None, None))

    def _maybe_fail(self) -> None:
        if self.fail_with is not None:
            raise self.fail_with

    def notification(self, payment_id: str, status: str, amount_piastres: int, *, provider_ref: str = "FR-1",
                     card_token: str | None = None, sign: bool = True) -> bytes:
        body = {"payment_id": payment_id, "status": status, "amount_piastres": amount_piastres,
                "provider_ref": provider_ref, "card_token": card_token}
        payload = json.dumps(body, sort_keys=True)
        body["signature"] = hmac.new(self._secret, payload.encode(), hashlib.sha256).hexdigest() if sign else "x"
        return json.dumps(body).encode()

    async def parse_notification(self, body: bytes) -> ProviderNotification:
        try:
            data = json.loads(body)
            signature = data.pop("signature")
            status = PaymentStatus(data["status"])
            amount = int(data["amount_piastres"])
            payment_id = str(data["payment_id"])
        except (ValueError, KeyError, TypeError, AttributeError) as exc:
            raise ValidationError("malformed notification") from exc
        expected = hmac.new(self._secret, json.dumps(data, sort_keys=True).encode(), hashlib.sha256).hexdigest()
        if not hmac.compare_digest(expected, str(signature)):
            raise AuthenticationError("notification signature does not match")
        return ProviderNotification(payment_id, status, amount, data.get("provider_ref"), data.get("card_token"))


class InMemoryBillingContactStore:
    def __init__(self) -> None:
        self._rows: dict[str, tuple[str, str]] = {}

    async def put(self, workspace_id: str, email: str, at: datetime, set_by: str = "") -> None:
        self._rows[workspace_id] = (email, set_by)

    async def get(self, workspace_id: str) -> tuple[str, str] | None:
        return self._rows.get(workspace_id)


class InMemoryStoreBindingStore:
    def __init__(self) -> None:
        self._rows: dict[str, StoreBinding] = {}

    async def put(self, binding: StoreBinding) -> None:
        self._rows[binding.token] = binding

    async def get(self, token: str) -> StoreBinding | None:
        return self._rows.get(token)


class InMemoryStoreEventLog:
    def __init__(self) -> None:
        self._seen: set[tuple[str, str]] = set()
        self._lock = asyncio.Lock()

    async def seen(self, provider: str, event_id: str) -> bool:
        return (provider, event_id) in self._seen

    async def record(self, provider: str, event_id: str, at: datetime) -> bool:
        async with self._lock:
            key = (provider, event_id)
            if key in self._seen:
                return False
            self._seen.add(key)
            return True


class _Release:
    def __init__(self, lock: asyncio.Lock) -> None:
        self._lock = lock

    async def release(self) -> None:
        if self._lock.locked():
            self._lock.release()


class InMemoryBillingLocks:
    """Process-local locks; production uses Postgres advisory locks (``postgres_billing.py``)."""

    def __init__(self) -> None:
        self._workspaces: dict[str, asyncio.Lock] = {}
        self._cycle = asyncio.Lock()

    async def lock_workspace(self, workspace_id: str) -> _Release:
        lock = self._workspaces.setdefault(workspace_id, asyncio.Lock())
        await lock.acquire()
        return _Release(lock)

    async def try_lock_cycle(self) -> _Release | None:
        if self._cycle.locked():
            return None
        await self._cycle.acquire()
        return _Release(self._cycle)
