"""Ports for subscriptions, payments and the payment provider (SaaS P1-D1). Stdlib only."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Protocol

from ..domain.billing import Payment, PaymentStatus, StoreBinding, Subscription

__all__ = ["BillingLock", "BillingLocks", "StoreBindingStore", "StoreEventLog", "SubscriptionStore", "PaymentStore", "PaymentProvider", "ProviderCharge", "ProviderStatus", "ProviderNotification"]


class SubscriptionStore(Protocol):
    async def get(self, workspace_id: str) -> Subscription | None: ...

    async def put(self, sub: Subscription) -> None:
        """Insert or replace (one subscription per workspace)."""

    async def due(self, before: datetime) -> list[Subscription]:
        """Subscriptions the cycle must look at: still live (active, past due, in grace or
        canceled) and whose period end -- or grace end -- is before ``before``."""


class PaymentStore(Protocol):
    async def put(self, payment: Payment) -> None: ...

    async def get(self, payment_id: str) -> Payment | None: ...

    async def pending(self, *, before: datetime | None) -> list[Payment]:
        """Pending payments, oldest first; only those created before ``before`` when given."""

    async def for_workspace(self, workspace_id: str, *, limit: int) -> list[Payment]:
        """Newest first."""

    async def mark(
        self, payment_id: str, status: PaymentStatus, *, provider_ref: str | None, paid_at: datetime | None,
        expected: tuple[PaymentStatus, ...] = (PaymentStatus.PENDING,),
    ) -> Payment | None:
        """Move a payment from one of ``expected`` to ``status``, atomically. Returns the updated
        payment, or None when it was in another status or does not exist -- the idempotency lock
        that makes a duplicate notification a no-op."""

    async def received(self) -> list[Payment]:
        """Payments whose money arrived but whose application did not finish (re-driven)."""


@dataclass(frozen=True, slots=True)
class ProviderCharge:
    reference_number: str | None
    provider_ref: str | None


@dataclass(frozen=True, slots=True)
class ProviderStatus:
    status: PaymentStatus
    amount_piastres: int | None
    provider_ref: str | None


@dataclass(frozen=True, slots=True)
class ProviderNotification:
    payment_id: str
    status: PaymentStatus
    amount_piastres: int
    provider_ref: str | None
    card_token: str | None = None


class PaymentProvider(Protocol):
    name: str

    async def create_reference(self, payment: Payment, *, customer_profile_id: str, email: str | None) -> ProviderCharge:
        """A reference code to pay with cash at an outlet, a wallet or the provider's app."""

    async def charge_card(self, payment: Payment, *, customer_profile_id: str, card_token: str) -> ProviderCharge: ...

    async def status(self, payment_id: str) -> ProviderStatus: ...

    async def parse_notification(self, body: bytes) -> ProviderNotification:
        """Verify and parse a provider notification. AuthenticationError for a bad signature,
        ValidationError for a malformed body."""


class BillingLock(Protocol):
    async def release(self) -> None: ...


class BillingLocks(Protocol):
    """Serialises billing work across every worker: one cycle at a time, and one settlement or
    cycle step per workspace at a time, so no stale read overwrites a payment (review D1#2/#6)."""

    async def lock_workspace(self, workspace_id: str) -> BillingLock:
        """Wait for the workspace's lock."""

    async def try_lock_cycle(self) -> BillingLock | None:
        """The cycle's lock, or None when another cycle holds it."""


class BillingContactStore(Protocol):
    """Where a workspace's billing email goes (SaaS P1-F1)."""

    async def put(self, workspace_id: str, email: str, at: datetime) -> None: ...

    async def get(self, workspace_id: str) -> str | None: ...


class StoreBindingStore(Protocol):
    async def put(self, binding: StoreBinding) -> None: ...

    async def get(self, token: str) -> StoreBinding | None: ...


class StoreEventLog(Protocol):
    async def seen(self, provider: str, event_id: str) -> bool:
        """Whether ``(provider, event_id)`` was recorded. Checked under the workspace lock, before
        the event is applied; ``record`` follows the application, so a crash in between is
        retried by the store rather than lost."""
        ...

    async def record(self, provider: str, event_id: str, at: datetime) -> bool:
        """True the first time ``(provider, event_id)`` is seen, False for a replay. Atomic."""
