"""Ports for subscriptions, payments and the payment provider (SaaS P1-D1). Stdlib only."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Protocol

from ..domain.billing import Payment, PaymentStatus, Subscription

__all__ = ["SubscriptionStore", "PaymentStore", "PaymentProvider", "ProviderCharge", "ProviderStatus", "ProviderNotification"]


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
        self, payment_id: str, status: PaymentStatus, *, provider_ref: str | None, paid_at: datetime | None
    ) -> Payment | None:
        """Move a payment out of ``pending``, atomically. Returns the updated payment, or None
        when it was not pending (already settled) or does not exist -- the idempotency lock that
        makes a duplicate notification a no-op."""


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

    def parse_notification(self, body: bytes) -> ProviderNotification:
        """Verify and parse a provider notification. AuthenticationError for a bad signature,
        ValidationError for a malformed body."""
