"""AURA's side of billing (SaaS P1-D1, spec §5.4): AURA owns the subscription; the payment
provider only collects each payment.

Money rules this module keeps (several were review findings, D1#1-#8):

* **A payment is recorded before the provider is asked to charge it.** A card charge that times
  out stays ``pending`` and is *asked about* (reconcile), never charged again; only a definitive
  refusal marks it failed.
* **Settling is serialised per workspace** (``BillingLocks``) and the subscription is re-read inside
  the lock, so neither a concurrent notification nor the cycle can overwrite a payment with a stale
  snapshot. **One cycle at a time** across every worker and the cron.
* **Money received is never dropped.** A payment goes ``pending -> received -> paid``: ``received``
  is set once (the idempotency lock), the subscription is updated (re-applying the same payment is a
  no-op via ``last_payment_id``), then ``paid``. A crash in between is re-driven by the cycle. A PAID
  for a reference that had expired is applied and audited; a PAID for a voided renewal is recorded
  for a person to review; a refund of the payment that bought the current period ends it.
* **Checkout voids pending reference-code renewals**, so an old renewal can't roll an upgrade back.
* **Expiry leaves an operator's later hand-set plan alone.**
"""

from __future__ import annotations

import logging
import secrets
from dataclasses import dataclass, replace
from datetime import timedelta
from typing import Any, Mapping

from ..domain.billing import (
    Payment,
    PaymentStatus,
    SubscriptionStatus,
    advance,
    apply_payment,
    price_for,
)
from ..domain.errors import AuthorizationError, ConflictError
from ..domain.tenancy import WorkspaceRole
from ..ports.runtime import AuditEvent

__all__ = ["BillingService"]

logger = logging.getLogger("aura.billing")
RECONCILE_AFTER = timedelta(minutes=10)
FREE_PLAN = "free"


@dataclass
class BillingService:
    subscriptions: Any
    payments: Any
    provider: Any
    plans: Any
    tenancy: Any
    clock: Any
    audit: Any
    settings: Any
    prices: Mapping[str, int]
    #: ``BillingLocks``: per-workspace settle lock and the single-cycle lock (review D1#2/#6).
    locks: Any

    # -- buying ---------------------------------------------------------------------------

    async def checkout(self, actor: str, workspace_id: str, plan_id: str, seats: int, *, email: str | None) -> dict:
        _ws, membership = await self.tenancy.resolve(actor, workspace_id)
        if membership.role is not WorkspaceRole.OWNER:
            raise AuthorizationError("only the workspace owner can buy a plan")
        await self.plans.validate_plan(workspace_id, plan_id, seats)
        amount = price_for(self.prices, plan_id, seats)
        lock = await self.locks.lock_workspace(workspace_id)
        try:
            current = await self.subscriptions.get(workspace_id)
            if (current is not None and current.status is SubscriptionStatus.ACTIVE
                    and (current.plan_id, current.seats) == (plan_id, seats)):
                raise ConflictError("this workspace already has this plan")
            await self._void_pending_renewals(workspace_id)
        finally:
            await lock.release()
        payment = await self._issue(workspace_id, plan_id, seats, amount, "initial", email=email, card_token=None)
        await self._audit("billing.checkout", actor, workspace_id, {"payment_id": payment.id, "plan": plan_id, "seats": seats})
        return {
            "payment_id": payment.id,
            "reference_number": payment.reference_number,
            "amount_piastres": payment.amount_piastres,
            "expires_at": payment.expires_at.isoformat() if payment.expires_at else None,
        }

    async def cancel(self, actor: str, workspace_id: str) -> None:
        _ws, membership = await self.tenancy.resolve(actor, workspace_id)
        if membership.role is not WorkspaceRole.OWNER:
            raise AuthorizationError("only the workspace owner can cancel the plan")
        lock = await self.locks.lock_workspace(workspace_id)
        try:
            sub = await self.subscriptions.get(workspace_id)
            if sub is None or sub.status in (SubscriptionStatus.EXPIRED, SubscriptionStatus.CANCELED):
                raise ConflictError("there is no subscription to cancel")
            await self.subscriptions.put(replace(sub, status=SubscriptionStatus.CANCELED, updated_at=self.clock.now()))
        finally:
            await lock.release()
        await self._audit("billing.cancel", actor, workspace_id, {})

    async def status(self, workspace_id: str) -> dict:
        sub = await self.subscriptions.get(workspace_id)
        recent = await self.payments.for_workspace(workspace_id, limit=10)
        return {
            "subscription": None if sub is None else {
                "plan": sub.plan_id, "seats": sub.seats, "status": sub.status.value,
                "period_start": sub.period_start.isoformat(), "period_end": sub.period_end.isoformat(),
                "grace_until": sub.grace_until.isoformat() if sub.grace_until else None,
                "card_on_file": sub.card_token is not None,
            },
            "payments": [{
                "id": p.id, "plan": p.plan_id, "seats": p.seats, "amount_piastres": p.amount_piastres,
                "purpose": p.purpose, "status": p.status.value, "reference_number": p.reference_number,
                "created_at": p.created_at.isoformat(),
                "expires_at": p.expires_at.isoformat() if p.expires_at else None,
                "paid_at": p.paid_at.isoformat() if p.paid_at else None,
            } for p in recent],
        }

    # -- the provider's answer ------------------------------------------------------------

    async def handle_notification(self, body: bytes) -> str:
        note = await self.provider.parse_notification(body)
        payment = await self.payments.get(note.payment_id)
        if payment is None:
            logger.warning("payment notification for an unknown payment", extra={"payment_id": note.payment_id[:64]})
            await self._audit("billing.unknown_payment", "provider", "", {"payment_id": note.payment_id[:64]})
            return "ignored"
        lock = await self.locks.lock_workspace(payment.workspace_id)
        try:
            return await self._settle(payment.id, note.status, note.amount_piastres, note.provider_ref, note.card_token)
        finally:
            await lock.release()

    async def _settle(self, payment_id: str, status: PaymentStatus, amount: int | None,
                      provider_ref: str | None, card_token: str | None) -> str:
        """Caller holds the workspace lock."""
        now = self.clock.now()
        payment = await self.payments.get(payment_id)
        if payment is None:
            return "ignored"
        if status is PaymentStatus.PAID:
            if amount is None:
                return "pending"  # no amount to check yet: ask again later (review D1#5)
            if amount != payment.amount_piastres:
                marked = await self.payments.mark(
                    payment.id, PaymentStatus.MISMATCH, provider_ref=provider_ref, paid_at=now,
                    expected=(PaymentStatus.PENDING, PaymentStatus.EXPIRED, PaymentStatus.FAILED),
                )
                if marked is not None:
                    await self._audit("billing.mismatch", "provider", payment.workspace_id,
                                      {"payment_id": payment.id, "expected": payment.amount_piastres, "paid": amount})
                return "mismatch" if marked is not None else "duplicate"
            received = await self.payments.mark(payment.id, PaymentStatus.RECEIVED, provider_ref=provider_ref, paid_at=now)
            if received is None:
                if payment.status is PaymentStatus.RECEIVED:
                    received = payment  # a retry of an application that did not finish
                elif payment.status is PaymentStatus.EXPIRED or (
                        payment.status is PaymentStatus.FAILED and payment.purpose == "initial"):
                    received = await self.payments.mark(
                        payment.id, PaymentStatus.RECEIVED, provider_ref=provider_ref, paid_at=now,
                        expected=(PaymentStatus.EXPIRED, PaymentStatus.FAILED),
                    )
                    await self._audit("billing.late_payment", "provider", payment.workspace_id,
                                      {"payment_id": payment.id, "was": payment.status.value})
                elif payment.status is PaymentStatus.FAILED:
                    # A renewal voided by a newer checkout, paid anyway: money a person must look at.
                    await self._audit("billing.needs_review", "provider", payment.workspace_id,
                                      {"payment_id": payment.id, "reason": "voided renewal paid"})
                    return "needs_review"
                else:
                    return "duplicate"
                if received is None:
                    return "duplicate"
            return await self._apply(received, card_token)
        if status is PaymentStatus.REFUNDED:
            refunded = await self.payments.mark(payment.id, PaymentStatus.REFUNDED, provider_ref=provider_ref,
                                                paid_at=None, expected=(PaymentStatus.PAID, PaymentStatus.RECEIVED))
            if refunded is None:
                return "duplicate"
            sub = await self.subscriptions.get(payment.workspace_id)
            if sub is not None and sub.last_payment_id == payment.id:
                await self.subscriptions.put(replace(sub, status=SubscriptionStatus.EXPIRED, updated_at=now))
                await self._downgrade(payment.workspace_id)
            await self._audit("billing.refund", "provider", payment.workspace_id, {"payment_id": payment.id})
            return "refunded"
        if status in (PaymentStatus.EXPIRED, PaymentStatus.FAILED):
            marked = await self.payments.mark(payment.id, status, provider_ref=provider_ref, paid_at=None)
            return "recorded" if marked is not None else "duplicate"
        return "pending"

    async def _apply(self, payment: Payment, card_token: str | None) -> str:
        """Apply received money to the subscription, then mark it paid. Re-applying the same
        payment is a no-op (``last_payment_id``), so a retry after a crash extends once."""
        now = self.clock.now()
        sub = apply_payment(await self.subscriptions.get(payment.workspace_id), payment, now)
        if card_token:
            sub = replace(sub, card_token=card_token, customer_profile_id=payment.workspace_id)
        await self.subscriptions.put(sub)
        await self.plans.set_entitlement("billing", payment.workspace_id, payment.plan_id, payment.seats, source="subscription")
        await self.payments.mark(payment.id, PaymentStatus.PAID, provider_ref=None, paid_at=None,
                                 expected=(PaymentStatus.RECEIVED,))
        await self._audit("billing.paid", "provider", payment.workspace_id,
                          {"payment_id": payment.id, "period_end": sub.period_end.isoformat()})
        return "applied"

    # -- the scheduled cycle --------------------------------------------------------------

    async def run_cycle(self) -> dict:
        counts = {"renewals": 0, "advanced": 0, "expired": 0, "reconciled": 0, "errors": 0}
        cycle_lock = await self.locks.try_lock_cycle()
        if cycle_lock is None:
            return {**counts, "skipped": True}
        try:
            now = self.clock.now()
            lead = timedelta(days=self.settings.renewal_lead_days)
            for due in await self.subscriptions.due(now + lead):
                lock = await self.locks.lock_workspace(due.workspace_id)
                try:
                    await self._cycle_one(due.workspace_id, counts)
                finally:
                    await lock.release()
            for payment in await self.payments.received():
                lock = await self.locks.lock_workspace(payment.workspace_id)
                try:
                    if await self._settle(payment.id, PaymentStatus.PAID, payment.amount_piastres, None, None) == "applied":
                        counts["reconciled"] += 1
                except Exception:
                    counts["errors"] += 1
                    logger.warning("received payment could not be applied", exc_info=True, extra={"payment_id": payment.id})
                finally:
                    await lock.release()
            for payment in await self.payments.pending(before=now - RECONCILE_AFTER):
                try:
                    answer = await self.provider.status(payment.id)
                except Exception:
                    counts["errors"] += 1
                    logger.warning("payment status could not be read", exc_info=True, extra={"payment_id": payment.id})
                    continue
                lock = await self.locks.lock_workspace(payment.workspace_id)
                try:
                    outcome = await self._settle(payment.id, answer.status, answer.amount_piastres, answer.provider_ref, None)
                finally:
                    await lock.release()
                if outcome == "applied":
                    counts["reconciled"] += 1
        finally:
            await cycle_lock.release()
        return counts

    async def _cycle_one(self, workspace_id: str, counts: dict) -> None:
        """Caller holds the workspace lock. Works on a fresh read, never the ``due`` snapshot."""
        now = self.clock.now()
        sub = await self.subscriptions.get(workspace_id)
        if sub is None:
            return
        lead = timedelta(days=self.settings.renewal_lead_days)
        if (sub.status is not SubscriptionStatus.CANCELED and sub.status is not SubscriptionStatus.EXPIRED
                and sub.period_end - lead <= now and sub.provider not in ("apple", "google")
                and not await self._renewal_pending(workspace_id, now)):
            try:
                amount = price_for(self.prices, sub.plan_id, sub.seats)
                await self._issue(workspace_id, sub.plan_id, sub.seats, amount, "renewal",
                                  email=None, card_token=sub.card_token)
                counts["renewals"] += 1
            except Exception:
                counts["errors"] += 1
                logger.warning("renewal payment could not be issued", exc_info=True, extra={"workspace_id": workspace_id})
        moved = sub
        grace = timedelta(days=self.settings.grace_days)
        for _ in range(3):  # past due -> grace -> expired can all be due in one run
            step = advance(moved, now, grace)
            if step is moved:
                break
            moved = step
        if moved is not sub:
            await self.subscriptions.put(moved)
            counts["advanced"] += 1
            if moved.status is SubscriptionStatus.EXPIRED:
                await self._downgrade(workspace_id)
                await self._audit("billing.expired", "billing", workspace_id, {"plan": sub.plan_id})
                counts["expired"] += 1

    # -- helpers --------------------------------------------------------------------------

    async def _downgrade(self, workspace_id: str) -> None:
        """Back to Free -- unless an operator set a plan by hand since (review D1#8)."""
        current = await self.plans.entitlements.get(workspace_id)
        if current is not None and current.source == "manual":
            return
        await self.plans.set_entitlement("billing", workspace_id, FREE_PLAN, 1, source="subscription")

    async def _renewal_pending(self, workspace_id: str, now) -> bool:
        return any(
            p.workspace_id == workspace_id and p.purpose == "renewal" and (p.expires_at is None or p.expires_at > now)
            for p in await self.payments.pending(before=None)
        )

    async def _void_pending_renewals(self, workspace_id: str) -> None:
        """A checkout supersedes pending reference-code renewals (review D1#7). A card renewal in
        flight is left alone: it may already have moved money."""
        for p in await self.payments.pending(before=None):
            if p.workspace_id == workspace_id and p.purpose == "renewal" and p.method == "PAYATFAWRY":
                if await self.payments.mark(p.id, PaymentStatus.FAILED, provider_ref=None, paid_at=None) is not None:
                    await self._audit("billing.renewal_voided", "billing", workspace_id, {"payment_id": p.id})

    async def _issue(self, workspace_id: str, plan_id: str, seats: int, amount: int, purpose: str, *,
                     email: str | None, card_token: str | None) -> Payment:
        now = self.clock.now()
        method = "CARD" if card_token else "PAYATFAWRY"
        payment = Payment(
            id=secrets.token_hex(12), workspace_id=workspace_id, plan_id=plan_id, seats=seats,
            amount_piastres=amount, method=method, purpose=purpose, status=PaymentStatus.PENDING,
            provider_ref=None, reference_number=None, created_at=now,
            expires_at=None if card_token else now + timedelta(hours=self.settings.reference_expiry_hours),
        )
        # Recorded *before* the provider is asked (review D1#1): if the answer never arrives, the
        # payment stays pending and reconcile asks the provider about it -- a card is never charged
        # twice for one renewal.
        await self.payments.put(payment)
        try:
            if card_token:
                charge = await self.provider.charge_card(payment, customer_profile_id=workspace_id, card_token=card_token)
            else:
                charge = await self.provider.create_reference(payment, customer_profile_id=workspace_id, email=email)
        except Exception as exc:
            if getattr(exc, "details", {}).get("refused"):
                await self.payments.mark(payment.id, PaymentStatus.FAILED, provider_ref=None, paid_at=None)
            raise
        payment = replace(payment, reference_number=charge.reference_number, provider_ref=charge.provider_ref)
        await self.payments.put(payment)
        return payment

    async def _audit(self, action: str, actor: str, workspace_id: str, detail: dict) -> None:
        await self.audit.record(AuditEvent(
            action=action, actor=actor, outcome="ok", at=self.clock.now(),
            resource=f"workspace:{workspace_id}" if workspace_id else "billing", detail=detail,
        ))
