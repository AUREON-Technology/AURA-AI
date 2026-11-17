"""In-app subscriptions from the App Store and Google Play (SaaS P1-D2, spec §5.4).

A store purchase reaches the same ``Subscription`` record a Fawry payment does. It is tied to a
workspace only through a binding token AURA issued to that workspace's owner before the purchase
(Apple ``appAccountToken``, Google ``obfuscatedExternalAccountId``).

Rules this module keeps:

* **Nothing unverified changes state.** Apple's payload is checked against the pinned root; a
  Google push only names a purchase, whose state is then read from the Play API.
* **Applied once.** Under the workspace lock: an event already recorded is a ``duplicate``; it is
  recorded only *after* it was applied, so a crash in between is retried by the store, not lost.
* **The period never moves backwards** (``max`` of expiries), and a stale Apple event -- one about
  a transaction that expired before the current period ends -- changes nothing.
* **Refund or revoke: Free at once**, whatever the expiry says.
* **One active provider:** a store event for a workspace on another provider is ignored unless it
  activates, and an activation over a live Fawry subscription is audited as a provider switch.
* Expiry leaves an operator's hand-set (``manual``) plan alone, as the Fawry path does.
"""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass, replace
from datetime import datetime, timedelta
from typing import Any, Mapping

from ..domain.billing import BillingUnavailableError, StoreBinding, Subscription, SubscriptionStatus
from ..domain.errors import AuthorizationError, ValidationError
from ..domain.tenancy import WorkspaceKind, WorkspaceRole
from ..ports.runtime import AuditEvent

__all__ = ["StoreBillingService"]

logger = logging.getLogger("aura.billing.stores")
FREE_PLAN = "free"

ACTIVATE = "activate"
GRACE = "grace"
PAST_DUE = "past_due"
CANCEL = "cancel"
RESUME = "resume"
EXPIRE = "expire"
REVOKE = "revoke"

_APPLE_ACTIVATE = frozenset({"SUBSCRIBED", "DID_RENEW", "OFFER_REDEEMED"})
_APPLE_EXPIRE = frozenset({"EXPIRED", "GRACE_PERIOD_EXPIRED"})
_APPLE_REVOKE = frozenset({"REVOKE", "REFUND"})
_GOOGLE = {"active": ACTIVATE, "grace": GRACE, "on_hold": PAST_DUE, "paused": PAST_DUE,
           "canceled": CANCEL, "expired": EXPIRE}
_LIVE = (SubscriptionStatus.ACTIVE, SubscriptionStatus.PAST_DUE, SubscriptionStatus.GRACE, SubscriptionStatus.CANCELED)


@dataclass(frozen=True, slots=True)
class _Change:
    provider: str
    event_id: str
    token: str | None
    product_id: str | None
    action: str
    expires_at: datetime | None
    #: Apple only: an event about a transaction that ended before the current period is stale.
    stale_check: bool = False


@dataclass
class StoreBillingService:
    bindings: Any
    events: Any
    subscriptions: Any
    plans: Any
    tenancy: Any
    clock: Any
    audit: Any
    locks: Any
    settings: Any
    #: ``{"apple": {productId: plan}, "google": {...}}`` (``load_store_products``).
    products: Mapping[str, Mapping[str, str]]
    apple: Any = None
    google_push: Any = None
    google_play: Any = None

    # -- before the purchase --------------------------------------------------------------

    async def issue_token(self, actor: str, workspace_id: str) -> str:
        workspace, membership = await self.tenancy.resolve(actor, workspace_id)
        if membership.role is not WorkspaceRole.OWNER:
            raise AuthorizationError("only the workspace owner can buy a plan")
        if workspace.kind is not WorkspaceKind.PERSONAL:
            raise ValidationError("team plans are sold on the web, not in the apps")
        token = str(uuid.uuid4())
        await self.bindings.put(StoreBinding(token, workspace_id, actor, self.clock.now()))
        await self._audit("billing.store_token", actor, workspace_id, {})
        return token

    # -- Apple ----------------------------------------------------------------------------

    async def handle_apple(self, signed_payload: str) -> str:
        if self.apple is None:
            raise BillingUnavailableError("the App Store is not configured on this deployment")
        event = await self.apple.verify(signed_payload)
        tx = event.transaction
        if tx is None:
            return "ignored"  # e.g. TEST, or a summary with no transaction
        action = self._apple_action(event.type, event.subtype)
        if action is None:
            return "ignored"
        return await self._apply(_Change(
            "apple", event.event_id, tx.app_account_token, tx.product_id,
            REVOKE if tx.revoked else action, tx.expires_at, stale_check=action not in (ACTIVATE, REVOKE),
        ))

    @staticmethod
    def _apple_action(kind: str, subtype: str | None) -> str | None:
        if kind in _APPLE_ACTIVATE:
            return ACTIVATE
        if kind == "DID_FAIL_TO_RENEW":
            return GRACE if subtype == "GRACE_PERIOD" else PAST_DUE
        if kind == "DID_CHANGE_RENEWAL_STATUS":
            return {"AUTO_RENEW_DISABLED": CANCEL, "AUTO_RENEW_ENABLED": RESUME}.get(subtype or "")
        if kind in _APPLE_EXPIRE:
            return EXPIRE
        if kind in _APPLE_REVOKE:
            return REVOKE
        return None

    # -- Google ---------------------------------------------------------------------------

    async def handle_google(self, authorization: str | None, body: bytes) -> str:
        if self.google_push is None or self.google_play is None:
            raise BillingUnavailableError("Google Play is not configured on this deployment")
        await self.google_push.verify(authorization)
        push = self.google_push.parse(body)
        if push is None:
            return "ignored"
        if push.package_name != self.google_play.package_name:
            await self._audit("billing.store_ignored", "google", "", {"reason": "another package"})
            return "ignored"
        state = await self.google_play.subscription(push.purchase_token)
        action = REVOKE if push.voided else _GOOGLE.get(state.state)
        if action is None:
            return "ignored"
        result = await self._apply(_Change(
            "google", push.message_id, state.external_account_id, state.product_id, action, state.expires_at,
        ))
        if result == "applied" and action == ACTIVATE and not state.acknowledged and state.product_id:
            try:
                await self.google_play.acknowledge(state.product_id, push.purchase_token)
            except Exception:
                logger.warning("Play purchase could not be acknowledged; the app acknowledges too", exc_info=True)
        return result

    # -- shared ---------------------------------------------------------------------------

    async def _apply(self, change: _Change) -> str:
        binding = await self.bindings.get(change.token) if change.token else None
        if binding is None:
            await self._audit("billing.store_ignored", change.provider, "", {"reason": "unbound purchase"})
            return "ignored"
        ws = binding.workspace_id
        try:
            _w, membership = await self.tenancy.resolve(binding.created_by, ws)
        except AuthorizationError:
            membership = None
        if membership is None or membership.role is not WorkspaceRole.OWNER:
            await self._audit("billing.store_ignored", change.provider, ws, {"reason": "binder is not the owner"})
            return "ignored"
        plan_id = self.products.get(change.provider, {}).get(change.product_id or "")
        if plan_id is None:
            await self._audit("billing.store_ignored", change.provider, ws,
                              {"reason": "unknown product", "product": (change.product_id or "")[:100]})
            return "ignored"
        lock = await self.locks.lock_workspace(ws)
        try:
            if await self.events.seen(change.provider, change.event_id):
                return "duplicate"
            result = await self._transition(ws, plan_id, change)
            await self.events.record(change.provider, change.event_id, self.clock.now())
            return result
        finally:
            await lock.release()

    async def _transition(self, ws: str, plan_id: str, change: _Change) -> str:
        """Caller holds the workspace lock."""
        now = self.clock.now()
        sub = await self.subscriptions.get(ws)
        if change.action == ACTIVATE:
            if change.expires_at is None or change.expires_at <= now:
                return "ignored"  # nothing left to grant
            live_elsewhere = (sub is not None and sub.provider != change.provider and sub.status in _LIVE
                              and sub.period_end > now)
            if live_elsewhere:
                await self._audit("billing.provider_switch", change.provider, ws, {"from": sub.provider})
            keep = sub is not None and sub.status is not SubscriptionStatus.EXPIRED
            period_end = max(change.expires_at, sub.period_end) if keep else change.expires_at
            await self.subscriptions.put(Subscription(
                workspace_id=ws, plan_id=plan_id, seats=1, status=SubscriptionStatus.ACTIVE,
                provider=change.provider,
                period_start=sub.period_start if keep and sub.provider == change.provider else now,
                period_end=period_end, updated_at=now, grace_until=None,
                last_payment_id=sub.last_payment_id if sub is not None else None,
            ))
            await self.plans.set_entitlement("billing", ws, plan_id, 1, source="subscription")
            await self._audit("billing.store_active", change.provider, ws,
                              {"plan": plan_id, "period_end": period_end.isoformat()})
            return "applied"
        if sub is None or sub.provider != change.provider:
            return "ignored"  # this store does not own the workspace's subscription
        if change.action == REVOKE:
            await self.subscriptions.put(replace(sub, status=SubscriptionStatus.EXPIRED, updated_at=now))
            await self._downgrade(ws)
            await self._audit("billing.store_revoked", change.provider, ws, {})
            return "applied"
        if change.stale_check and change.expires_at is not None and change.expires_at < sub.period_end:
            return "stale"  # about a transaction that ended before the period we already hold
        if sub.status is SubscriptionStatus.EXPIRED and change.action != EXPIRE:
            return "ignored"
        if change.action == EXPIRE:
            if sub.status is not SubscriptionStatus.EXPIRED:
                await self.subscriptions.put(replace(sub, status=SubscriptionStatus.EXPIRED, updated_at=now))
                await self._downgrade(ws)
                await self._audit("billing.expired", change.provider, ws, {"plan": sub.plan_id})
            return "applied"
        if change.action == GRACE:
            until = change.expires_at if change.provider == "google" and change.expires_at else \
                max(change.expires_at or now, now) + timedelta(days=self.settings.grace_days)
            await self.subscriptions.put(replace(sub, status=SubscriptionStatus.GRACE, grace_until=until, updated_at=now))
        elif change.action == PAST_DUE:
            await self.subscriptions.put(replace(sub, status=SubscriptionStatus.PAST_DUE, updated_at=now))
        elif change.action == CANCEL:
            end = max(sub.period_end, change.expires_at) if change.expires_at else sub.period_end
            await self.subscriptions.put(replace(sub, status=SubscriptionStatus.CANCELED, period_end=end, updated_at=now))
        elif change.action == RESUME:
            if sub.status is SubscriptionStatus.CANCELED and sub.period_end > now:
                await self.subscriptions.put(replace(sub, status=SubscriptionStatus.ACTIVE, updated_at=now))
        await self._audit(f"billing.store_{change.action}", change.provider, ws, {})
        return "applied"

    async def _downgrade(self, workspace_id: str) -> None:
        current = await self.plans.entitlements.get(workspace_id)
        if current is not None and current.source == "manual":
            return
        await self.plans.set_entitlement("billing", workspace_id, FREE_PLAN, 1, source="subscription")

    async def _audit(self, action: str, actor: str, workspace_id: str, detail: dict) -> None:
        await self.audit.record(AuditEvent(
            action=action, actor=actor, outcome="ok", at=self.clock.now(),
            resource=f"workspace:{workspace_id}" if workspace_id else "billing", detail=detail,
        ))
