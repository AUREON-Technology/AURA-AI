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

import json
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
    proration_for,
)
from ..domain.errors import AuthorizationError, ConflictError, ValidationError
from ..domain.tenancy import WorkspaceRole
from ..ports.runtime import AuditEvent

__all__ = ["BillingService"]

logger = logging.getLogger("aura.billing")
RECONCILE_AFTER = timedelta(minutes=10)
#: Where the cycle leaves its heartbeat for the gateway's scrape gauges (SaaS P1-H).
BILLING_HEARTBEAT_KEY = "billing:cycle:heartbeat"
#: Kept a week: long enough that "stale" is read from its age, not from a vanished key.
HEARTBEAT_TTL_S = 7 * 24 * 3600
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
    #: ``BillingContactStore`` and ``Notifier`` (SaaS P1-F1): receipts, renewal reminders and
    #: past-due notices go to the Owner's address stored at checkout. Either unset: no email.
    contacts: Any = None
    notifier: Any = None
    #: A Cache shared with the gateway (Redis on Railway). After each cycle that ran, a small JSON
    #: heartbeat goes to ``BILLING_HEARTBEAT_KEY`` for the gateway's scrape gauges (SaaS P1-H):
    #: the cycle is a cron process nothing scrapes.
    heartbeat: Any = None

    # -- buying ---------------------------------------------------------------------------

    async def checkout(self, actor: str, workspace_id: str, plan_id: str, seats: int, *, email: str | None) -> dict:
        _ws, membership = await self.tenancy.resolve(actor, workspace_id)
        if membership.role is not WorkspaceRole.OWNER:
            raise AuthorizationError("only the workspace owner can buy a plan")
        await self.plans.validate_plan(workspace_id, plan_id, seats)
        price = price_for(self.prices, plan_id, seats)
        if email and self.contacts is not None:
            await self.contacts.put(workspace_id, email, self.clock.now(), set_by=actor)
        lock = await self.locks.lock_workspace(workspace_id)
        try:
            current = await self.subscriptions.get(workspace_id)
            if (current is not None and current.status is SubscriptionStatus.ACTIVE
                    and (current.plan_id, current.seats) == (plan_id, seats)):
                raise ConflictError("this workspace already has this plan")
            if current is not None and current.provider in ("apple", "google") and current.status in (
                    SubscriptionStatus.ACTIVE, SubscriptionStatus.PAST_DUE, SubscriptionStatus.GRACE):
                # The store would keep renewing too: cancel there first (D2 review #7).
                raise ConflictError("cancel the subscription in the app store before paying here")
            # The new plan applies as soon as this is paid, the month it buys after the current one:
            # the rest of the current period at the higher price is owed now (F42).
            proration = proration_for(self.prices, current, plan_id, seats, self.clock.now())
            await self._void_pending_renewals(workspace_id)
        finally:
            await lock.release()
        payment = await self._issue(workspace_id, plan_id, seats, price + proration, "initial", email=email, card_token=None)
        await self._audit("billing.checkout", actor, workspace_id,
                          {"payment_id": payment.id, "plan": plan_id, "seats": seats, "proration_piastres": proration})
        return {
            "payment_id": payment.id,
            "reference_number": payment.reference_number,
            "amount_piastres": payment.amount_piastres,
            "proration_piastres": proration,
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
            if sub.provider != "fawry":
                # Only the store can stop its own renewals: marking it canceled here would leave
                # the person charged while AURA said otherwise (SaaS P1-F2b).
                raise ConflictError("this subscription is managed in the App Store or Google Play; cancel it there")
            await self.subscriptions.put(replace(sub, status=SubscriptionStatus.CANCELED, updated_at=self.clock.now()))
        finally:
            await lock.release()
        await self._audit("billing.cancel", actor, workspace_id, {})

    async def forget_card(self, workspace_id: str) -> None:
        """The workspace's Owner is about to change: stop charging the saved card, which is the
        outgoing Owner's (F41). Renewals fall back to a reference code until the new Owner pays
        and saves a card of their own. Wired as ``TenancyService.owner_changing``; it runs before
        the transfer, so a failure here stops the transfer rather than leaving the card charged."""
        lock = await self.locks.lock_workspace(workspace_id)
        try:
            sub = await self.subscriptions.get(workspace_id)
            if sub is None or sub.card_token is None:
                return
            await self.subscriptions.put(replace(sub, card_token=None, updated_at=self.clock.now()))
        finally:
            await lock.release()
        await self._audit("billing.card_forgotten", "billing", workspace_id, {"reason": "owner_transfer"})

    async def status(self, workspace_id: str) -> dict:
        sub = await self.subscriptions.get(workspace_id)
        recent = await self.payments.for_workspace(workspace_id, limit=10)
        return {
            "subscription": None if sub is None else {
                "plan": sub.plan_id, "seats": sub.seats, "status": sub.status.value,
                "period_start": sub.period_start.isoformat(), "period_end": sub.period_end.isoformat(),
                "grace_until": sub.grace_until.isoformat() if sub.grace_until else None,
                "card_on_file": sub.card_token is not None,
                "provider": sub.provider,
            },
            "payments": [{
                "id": p.id, "plan": p.plan_id, "seats": p.seats, "amount_piastres": p.amount_piastres,
                "purpose": p.purpose, "status": p.status.value, "reference_number": p.reference_number,
                "created_at": p.created_at.isoformat(),
                "expires_at": p.expires_at.isoformat() if p.expires_at else None,
                "paid_at": p.paid_at.isoformat() if p.paid_at else None,
            } for p in recent],
        }

    # -- operator tooling (SaaS P1-F1, spec 5.4 "Owner tooling") ----------------------------

    async def customers(self, limit: int, after: str | None) -> dict:
        """Read-only: every subscribed workspace, its plan and state, and its last payment."""
        rows = []
        page = await self.subscriptions.list_page(limit, after)
        for sub in page:
            workspace = await self.tenancy.store.get_workspace(sub.workspace_id)
            last = (await self.payments.for_workspace(sub.workspace_id, limit=1) or [None])[0]
            rows.append({
                "workspace_id": sub.workspace_id,
                "workspace_name": workspace.name if workspace else None,
                "kind": workspace.kind.value if workspace else None,
                "plan": sub.plan_id, "seats": sub.seats, "status": sub.status.value, "provider": sub.provider,
                "period_end": sub.period_end.isoformat(),
                "grace_until": sub.grace_until.isoformat() if sub.grace_until else None,
                "last_payment": None if last is None else {
                    "status": last.status.value, "amount_piastres": last.amount_piastres,
                    "at": (last.paid_at or last.created_at).isoformat(),
                },
            })
        return {"customers": rows, "next": page[-1].workspace_id if len(page) == limit else None}

    async def extend_grace(self, actor: str, workspace_id: str, days: int) -> dict:
        """The operator's one write: more grace for a Fawry subscription that is behind on
        payment -- or already expired, which brings its plan back. A store owns its own
        subscriptions, so theirs are refused. Audited."""
        if isinstance(days, bool) or not isinstance(days, int) or not 1 <= days <= 30:
            raise ValidationError("days must be between 1 and 30")
        lock = await self.locks.lock_workspace(workspace_id)
        try:
            sub = await self.subscriptions.get(workspace_id)
            behind = (SubscriptionStatus.PAST_DUE, SubscriptionStatus.GRACE, SubscriptionStatus.EXPIRED)
            if sub is None or sub.provider != "fawry" or sub.status not in behind:
                raise ConflictError("only a Fawry subscription that is past due, in grace or expired can get more grace")
            last = await self.payments.get(sub.last_payment_id) if sub.last_payment_id else None
            if last is not None and last.status is PaymentStatus.REFUNDED:
                raise ConflictError("this subscription's last payment was refunded")
            now = self.clock.now()
            automatic = sub.grace_until or (sub.period_end + timedelta(days=self.settings.grace_days))
            until = max(automatic, now) + timedelta(days=days)  # never shorter than automatic (F1#8)
            # The plan first, so a failure leaves nothing a retry cannot repair (F1#2); and on
            # every call, not only from EXPIRED, unless an operator set it by hand.
            current = await self.plans.entitlements.get(workspace_id)
            if current is None or (current.source != "manual" and (current.plan_id, current.seats) != (sub.plan_id, sub.seats)):
                await self.plans.set_entitlement(actor, workspace_id, sub.plan_id, sub.seats, source="subscription")
            restored = replace(sub, status=SubscriptionStatus.GRACE, grace_until=until, updated_at=now)
            if sub.status is SubscriptionStatus.EXPIRED:
                # Brought back by a person, not by a payment: never charge a saved card for it
                # (the customer may have cancelled). Renewal falls back to a reference code (F1#1).
                restored = replace(restored, card_token=None)
            await self.subscriptions.put(restored)
        finally:
            await lock.release()
        await self._audit("billing.grace_extended", actor, workspace_id,
                          {"days": days, "was": sub.status.value, "grace_until": until.isoformat()})
        return {"status": "grace", "grace_until": until.isoformat()}

    # -- the provider's answer ------------------------------------------------------------

    async def handle_notification(self, body: bytes) -> str:
        note = await self.provider.parse_notification(body)
        payment = await self.payments.get(note.payment_id)
        if payment is None:
            logger.warning("payment notification for an unknown payment", extra={"payment_id": note.payment_id[:64]})
            await self._audit("billing.unknown_payment", "provider", "", {"payment_id": note.payment_id[:64]})
            return "ignored"
        if payment.workspace_id is None:
            # Its workspace was erased after the payment was created (revision 0030 keeps the row
            # with ``workspace_id`` NULL, F34). There is nothing to apply it to: hold it for a person.
            logger.warning("payment notification for an erased workspace", extra={"payment_id": payment.id})
            await self._audit("billing.needs_review", "provider", "",
                              {"payment_id": payment.id, "reason": "workspace erased"})
            return "needs_review"
        card_token = await self._confirmed_card(payment.workspace_id, note.card_token)
        lock = await self.locks.lock_workspace(payment.workspace_id)
        try:
            return await self._settle(payment.id, note.status, note.amount_piastres, note.provider_ref, card_token)
        finally:
            await lock.release()

    async def _confirmed_card(self, workspace_id: str, card_token: str | None) -> str | None:
        """A card token is kept only if the provider vouches for it (F58). Fawry's notification
        signature does not cover ``cardToken``, so its adapter offers ``confirm_card_token``: the
        token must be one Fawry lists for this workspace's customer profile. A provider without
        it signs the whole notification (the fake). Any doubt drops the token -- the payment still
        applies, and renewals fall back to a reference code."""
        if not card_token:
            return None
        confirm = getattr(self.provider, "confirm_card_token", None)
        if confirm is None:
            return card_token
        try:
            if await confirm(workspace_id, card_token):
                return card_token
        except Exception:
            logger.warning("card token could not be confirmed", exc_info=True)
        await self._audit("billing.card_unconfirmed", "provider", workspace_id, {})
        return None

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
            if refunded is None and payment.status is not PaymentStatus.REFUNDED:
                return "duplicate"
            # On a replay too (the mark is already there): a refund whose downgrade failed
            # half-way is finished by the provider's retry, not left on the paid plan (F8).
            await self._end_refunded(await self.subscriptions.get(payment.workspace_id), payment.id, now)
            if refunded is None:
                return "duplicate"
            await self._audit("billing.refund", "provider", payment.workspace_id, {"payment_id": payment.id})
            return "refunded"
        if status in (PaymentStatus.EXPIRED, PaymentStatus.FAILED):
            marked = await self.payments.mark(payment.id, status, provider_ref=provider_ref, paid_at=None)
            return "recorded" if marked is not None else "duplicate"
        return "pending"

    async def _end_refunded(self, sub, payment_id: str, now) -> None:
        """End the period a refunded payment bought, if it still holds it. Idempotent: Free first,
        then the status, so a failure in between leaves nothing a retry cannot finish (F8). A
        store that took the workspace over since keeps it."""
        if sub is None or sub.last_payment_id != payment_id or sub.provider != "fawry":
            return
        await self._downgrade(sub.workspace_id)
        if sub.status is not SubscriptionStatus.EXPIRED:
            await self.subscriptions.put(replace(sub, status=SubscriptionStatus.EXPIRED, updated_at=now))

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
        await self._email(payment.workspace_id, lambda to, name: self.notifier.receipt(
            to, name, payment.plan_id, payment.amount_piastres, now, sub.period_end, payment.id))
        return "applied"

    # -- the scheduled cycle --------------------------------------------------------------

    async def run_cycle(self) -> dict:
        counts = {"renewals": 0, "advanced": 0, "expired": 0, "reconciled": 0, "errors": 0}
        cycle_lock = await self.locks.try_lock_cycle()
        if cycle_lock is None:
            return {**counts, "skipped": True}
        failures_before = getattr(self.notifier, "failures", 0)
        try:
            await self._run_cycle(counts)
        except Exception:
            # An aborted cycle is still a cycle that ran with an error: the heartbeat says so, so
            # AuraBillingCycleErrors sees it rather than only a stale cycle hours later (review H#4).
            counts["errors"] += 1
            await self._beat(counts, getattr(self.notifier, "failures", 0) - failures_before)
            raise
        finally:
            await cycle_lock.release()
        await self._beat(counts, getattr(self.notifier, "failures", 0) - failures_before)
        return counts

    async def _beat(self, counts: dict, email_failures: int) -> None:
        """Best-effort: a cache outage is logged, never a failed cycle."""
        if self.heartbeat is None:
            return
        beat = {"at": self.clock.now().timestamp(), "errors": counts["errors"], "email_failures": email_failures}
        try:
            await self.heartbeat.set(BILLING_HEARTBEAT_KEY, json.dumps(beat).encode(), ttl_s=HEARTBEAT_TTL_S)
        except Exception:
            logger.warning("billing heartbeat not written", exc_info=True)

    async def _run_cycle(self, counts: dict) -> None:
        """The cycle itself. The caller holds the cycle lock."""
        now = self.clock.now()
        lead = timedelta(days=self.settings.renewal_lead_days)
        for due in await self.subscriptions.due(now + lead):
            try:
                lock = await self.locks.lock_workspace(due.workspace_id)
            except Exception:
                counts["errors"] += 1
                logger.warning("workspace billing lock not taken", exc_info=True, extra={"workspace_id": due.workspace_id})
                continue
            try:
                await self._cycle_one(due.workspace_id, counts)
            except Exception:
                # One workspace that cannot be moved must not stop the rest (F8); its state is
                # left for the next cycle, which repeats the step.
                counts["errors"] += 1
                logger.warning("subscription could not be advanced", exc_info=True, extra={"workspace_id": due.workspace_id})
            finally:
                await lock.release()
        for payment in await self.payments.received():
            try:
                lock = await self.locks.lock_workspace(payment.workspace_id)
            except Exception:
                counts["errors"] += 1
                logger.warning("workspace billing lock not taken", exc_info=True, extra={"payment_id": payment.id})
                continue
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
            try:
                lock = await self.locks.lock_workspace(payment.workspace_id)
            except Exception:
                counts["errors"] += 1
                logger.warning("workspace billing lock not taken", exc_info=True, extra={"payment_id": payment.id})
                continue
            try:
                outcome = await self._settle(payment.id, answer.status, answer.amount_piastres, answer.provider_ref, None)
            except Exception:
                # One payment that cannot be settled must not stop the rest of the cycle (review H#4).
                counts["errors"] += 1
                logger.warning("pending payment could not be settled", exc_info=True, extra={"payment_id": payment.id})
                continue
            finally:
                await lock.release()
            if outcome == "applied":
                counts["reconciled"] += 1

    async def _cycle_one(self, workspace_id: str, counts: dict) -> None:
        """Caller holds the workspace lock. Works on a fresh read, never the ``due`` snapshot."""
        now = self.clock.now()
        sub = await self.subscriptions.get(workspace_id)
        if sub is None:
            return
        if sub.provider == "fawry" and sub.last_payment_id and sub.status is not SubscriptionStatus.EXPIRED:
            # A refund whose ending failed and was never replayed: finish it, renew nothing (F8).
            last = await self.payments.get(sub.last_payment_id)
            if last is not None and last.status is PaymentStatus.REFUNDED:
                await self._end_refunded(sub, last.id, now)
                await self._audit("billing.refund_repaired", "billing", workspace_id, {"payment_id": last.id})
                counts["expired"] += 1
                return
        lead = timedelta(days=self.settings.renewal_lead_days)
        if (sub.status is not SubscriptionStatus.CANCELED and sub.status is not SubscriptionStatus.EXPIRED
                and sub.period_end - lead <= now and sub.provider not in ("apple", "google")
                and not await self._renewal_pending(workspace_id, now)):
            try:
                amount = price_for(self.prices, sub.plan_id, sub.seats)
                renewal = await self._issue(workspace_id, sub.plan_id, sub.seats, amount, "renewal",
                                            email=None, card_token=sub.card_token)
                counts["renewals"] += 1
                if renewal.method == "PAYATFAWRY":  # a card renewal needs nothing from the payer
                    await self._email(workspace_id, lambda to, name: self.notifier.renewal_due(
                        to, name, renewal.amount_piastres, renewal.reference_number, renewal.expires_at))
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
            if moved.status is SubscriptionStatus.EXPIRED:
                # Free before the status: if this fails the subscription is still live, so the
                # next cycle repeats the step instead of skipping an expired one (F8).
                await self._downgrade(workspace_id)
            await self.subscriptions.put(moved)
            counts["advanced"] += 1
            unpaid = (SubscriptionStatus.PAST_DUE, SubscriptionStatus.GRACE)
            if moved.status in unpaid and sub.status not in unpaid and sub.provider not in ("apple", "google"):
                # Once, when it first falls behind; the stores tell their own customers.
                await self._email(workspace_id, lambda to, name: self.notifier.past_due(
                    to, name, moved.grace_until or (moved.period_end + grace)))
            if moved.status is SubscriptionStatus.EXPIRED:
                await self._audit("billing.expired", "billing", workspace_id, {"plan": sub.plan_id})
                counts["expired"] += 1

    # -- helpers --------------------------------------------------------------------------

    async def _downgrade(self, workspace_id: str) -> None:
        """Back to Free -- unless an operator set a plan by hand since (review D1#8). Idempotent:
        already Free is left alone, so a replay can call it again (F8)."""
        current = await self.plans.entitlements.get(workspace_id)
        if current is not None and (current.source == "manual" or current.plan_id == FREE_PLAN):
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

    async def _email(self, workspace_id: str, send) -> None:
        """Send one customer email to the workspace's billing contact. Never raises."""
        if self.notifier is None or self.contacts is None:
            return
        try:
            contact = await self.contacts.get(workspace_id)
            if not contact:
                return
            to, set_by = contact
            workspace = await self.tenancy.store.get_workspace(workspace_id)
            if set_by:
                # Only while whoever gave the address still owns the workspace: after a
                # transfer, no billing email rather than the former Owner's (review F1#4).
                membership = await self.tenancy.store.get_membership(workspace_id, set_by)
                if membership is None or membership.role.value != "owner":
                    return
            await send(to, workspace.name if workspace is not None else "your workspace")
        except Exception:
            logger.warning("billing email could not be prepared", exc_info=True)

    async def _audit(self, action: str, actor: str, workspace_id: str, detail: dict) -> None:
        await self.audit.record(AuditEvent(
            action=action, actor=actor, outcome="ok", at=self.clock.now(),
            resource=f"workspace:{workspace_id}" if workspace_id else "billing", detail=detail,
        ))
