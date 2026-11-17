"""AURA's side of billing (SaaS P1-D1, spec §5.4): AURA owns the subscription; the payment
provider only collects each payment.

* :meth:`checkout` -- the workspace Owner buys a plan; the provider issues a reference code.
* :meth:`handle_notification` -- the provider's signed notification. A payment leaves ``pending``
  once (``PaymentStore.mark``), so a duplicate or out-of-order notification changes nothing; a
  paid amount that differs from what was asked activates nothing and is audited.
* :meth:`run_cycle` -- renewals before the period ends (a saved card is charged, otherwise a new
  reference code), past due -> grace (full access) -> expired (back to Free; nothing deleted),
  and reconciliation of pending payments with the provider, so a lost notification cannot lock a
  payer out. A provider error during the cycle changes nothing that needed the provider's answer.
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

    # -- buying ---------------------------------------------------------------------------

    async def checkout(self, actor: str, workspace_id: str, plan_id: str, seats: int, *, email: str | None) -> dict:
        _ws, membership = await self.tenancy.resolve(actor, workspace_id)
        if membership.role is not WorkspaceRole.OWNER:
            raise AuthorizationError("only the workspace owner can buy a plan")
        await self.plans.validate_plan(workspace_id, plan_id, seats)
        amount = price_for(self.prices, plan_id, seats)
        current = await self.subscriptions.get(workspace_id)
        if (current is not None and current.status is SubscriptionStatus.ACTIVE
                and (current.plan_id, current.seats) == (plan_id, seats)):
            raise ConflictError("this workspace already has this plan")
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
        sub = await self.subscriptions.get(workspace_id)
        if sub is None or sub.status in (SubscriptionStatus.EXPIRED, SubscriptionStatus.CANCELED):
            raise ConflictError("there is no subscription to cancel")
        await self.subscriptions.put(replace(sub, status=SubscriptionStatus.CANCELED, updated_at=self.clock.now()))
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
        return await self._settle(payment, note.status, note.amount_piastres, note.provider_ref, note.card_token)

    async def _settle(self, payment: Payment, status: PaymentStatus, amount: int | None,
                      provider_ref: str | None, card_token: str | None) -> str:
        now = self.clock.now()
        if status is PaymentStatus.PAID:
            if amount != payment.amount_piastres:
                marked = await self.payments.mark(payment.id, PaymentStatus.MISMATCH, provider_ref=provider_ref, paid_at=now)
                if marked is not None:
                    await self._audit("billing.mismatch", "provider", payment.workspace_id,
                                      {"payment_id": payment.id, "expected": payment.amount_piastres, "paid": amount})
                return "mismatch"
            paid = await self.payments.mark(payment.id, PaymentStatus.PAID, provider_ref=provider_ref, paid_at=now)
            if paid is None:
                return "duplicate"
            sub = apply_payment(await self.subscriptions.get(payment.workspace_id), paid, now)
            if card_token:
                sub = replace(sub, card_token=card_token, customer_profile_id=payment.workspace_id)
            await self.subscriptions.put(sub)
            await self.plans.set_entitlement("billing", payment.workspace_id, paid.plan_id, paid.seats, source="subscription")
            await self._audit("billing.paid", "provider", payment.workspace_id,
                              {"payment_id": payment.id, "period_end": sub.period_end.isoformat()})
            return "applied"
        if status in (PaymentStatus.EXPIRED, PaymentStatus.FAILED, PaymentStatus.REFUNDED):
            marked = await self.payments.mark(payment.id, status, provider_ref=provider_ref, paid_at=None)
            return "recorded" if marked is not None else "duplicate"
        return "pending"

    # -- the scheduled cycle --------------------------------------------------------------

    async def run_cycle(self) -> dict:
        counts = {"renewals": 0, "advanced": 0, "expired": 0, "reconciled": 0, "errors": 0}
        now = self.clock.now()
        lead = timedelta(days=self.settings.renewal_lead_days)
        grace = timedelta(days=self.settings.grace_days)

        for sub in await self.subscriptions.due(now + lead):
            if sub.status is not SubscriptionStatus.CANCELED and not await self._renewal_pending(sub.workspace_id, now):
                try:
                    amount = price_for(self.prices, sub.plan_id, sub.seats)
                    await self._issue(sub.workspace_id, sub.plan_id, sub.seats, amount, "renewal",
                                      email=None, card_token=sub.card_token)
                    counts["renewals"] += 1
                except Exception:
                    counts["errors"] += 1
                    logger.warning("renewal payment could not be issued", exc_info=True,
                                   extra={"workspace_id": sub.workspace_id})
            moved = sub
            for _ in range(3):  # past due -> grace -> expired can all be due in one run
                step = advance(moved, now, grace)
                if step is moved:
                    break
                moved = step
            if moved is not sub:
                await self.subscriptions.put(moved)
                counts["advanced"] += 1
                if moved.status is SubscriptionStatus.EXPIRED:
                    await self.plans.set_entitlement("billing", sub.workspace_id, FREE_PLAN, 1, source="subscription")
                    await self._audit("billing.expired", "billing", sub.workspace_id, {"plan": sub.plan_id})
                    counts["expired"] += 1

        for payment in await self.payments.pending(before=now - RECONCILE_AFTER):
            try:
                answer = await self.provider.status(payment.id)
            except Exception:
                counts["errors"] += 1
                logger.warning("payment status could not be read", exc_info=True, extra={"payment_id": payment.id})
                continue
            outcome = await self._settle(payment, answer.status, answer.amount_piastres, answer.provider_ref, None)
            if outcome == "applied":
                counts["reconciled"] += 1
        return counts

    # -- helpers --------------------------------------------------------------------------

    async def _renewal_pending(self, workspace_id: str, now) -> bool:
        return any(
            p.workspace_id == workspace_id and p.purpose == "renewal" and (p.expires_at is None or p.expires_at > now)
            for p in await self.payments.pending(before=None)
        )

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
        if card_token:
            charge = await self.provider.charge_card(payment, customer_profile_id=workspace_id, card_token=card_token)
        else:
            charge = await self.provider.create_reference(payment, customer_profile_id=workspace_id, email=email)
        # Stored only once the provider accepted it: a provider error leaves no half-made payment.
        payment = replace(payment, reference_number=charge.reference_number, provider_ref=charge.provider_ref)
        await self.payments.put(payment)
        return payment

    async def _audit(self, action: str, actor: str, workspace_id: str, detail: dict) -> None:
        await self.audit.record(AuditEvent(
            action=action, actor=actor, outcome="ok", at=self.clock.now(),
            resource=f"workspace:{workspace_id}" if workspace_id else "billing", detail=detail,
        ))
