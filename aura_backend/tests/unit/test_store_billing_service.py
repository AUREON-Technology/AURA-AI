"""SaaS P1-D2: StoreBillingService -- App Store and Google Play events onto AURA's subscription."""

from __future__ import annotations

import base64
import json
from dataclasses import replace
from datetime import timedelta

import pytest

from aura_core.adapters.apple_store import AppleEvent, AppleTransaction
from aura_core.adapters.billing import (
    FakePaymentProvider,
    InMemoryBillingLocks,
    InMemoryPaymentStore,
    InMemoryStoreBindingStore,
    InMemoryStoreEventLog,
    InMemorySubscriptionStore,
)
from aura_core.adapters.google_play import PlaySubscription, parse_push
from aura_core.billing import BillingService, StoreBillingService
from aura_core.config.settings import BillingSettings
from aura_core.domain.billing import DEFAULT_PRICES, StoreBinding, SubscriptionStatus
from aura_core.domain.errors import AuthenticationError, AuthorizationError, ConflictError, ValidationError

DAY = 86400
PKG = "tech.aureon.app"
PRODUCTS = {"apple": {"aura.pro.monthly": "pro"}, "google": {"aura_pro": "pro"}}


class FakeApple:
    """Stands in for the verifier: the 'signed payload' is the key of an event built by the test."""

    def __init__(self) -> None:
        self.events: dict[str, AppleEvent] = {}

    async def verify(self, signed_payload: str) -> AppleEvent:
        if signed_payload not in self.events:
            raise AuthenticationError("bad signature")
        return self.events[signed_payload]


class FakePush:
    async def verify(self, authorization):
        if authorization != "Bearer google":
            raise AuthenticationError("bad push token")

    def parse(self, body):
        return parse_push(body)


class FakePlay:
    package_name = PKG

    def __init__(self) -> None:
        self.states: dict[str, PlaySubscription] = {}
        self.acknowledged: list[tuple[str, str]] = []

    async def subscription(self, token: str) -> PlaySubscription:
        if token not in self.states:
            raise ValidationError("unknown purchase")
        return self.states[token]

    async def acknowledge(self, product_id: str, token: str) -> None:
        self.acknowledged.append((product_id, token))


@pytest.fixture
def env(container, clock):
    container.plans.clock = container.tenancy.clock = container.clock = clock
    subs, locks = InMemorySubscriptionStore(), InMemoryBillingLocks()
    settings = BillingSettings(enabled=True)
    billing = BillingService(
        subscriptions=subs, payments=InMemoryPaymentStore(), provider=FakePaymentProvider(), plans=container.plans,
        tenancy=container.tenancy, clock=clock, audit=container.audit, settings=settings, prices=DEFAULT_PRICES,
        locks=locks,
    )
    apple, play = FakeApple(), FakePlay()
    stores = StoreBillingService(
        bindings=InMemoryStoreBindingStore(), events=InMemoryStoreEventLog(), subscriptions=subs,
        plans=container.plans, tenancy=container.tenancy, clock=clock, audit=container.audit, locks=locks,
        settings=settings, products=PRODUCTS, apple=apple, google_push=FakePush(), google_play=play,
    )
    return type("Env", (), {"billing": billing, "stores": stores, "apple": apple, "play": play,
                            "container": container, "clock": clock})


async def _bound(env, user="ann"):
    ws = (await env.container.tenancy.ensure_personal(user)).id
    return ws, await env.stores.issue_token(user, ws)


def _apple(env, key, kind, token, *, days=30, subtype=None, revoked=False, tx="t-1", product="aura.pro.monthly"):
    expires = env.clock.now() + timedelta(days=days) if days is not None else None
    env.apple.events[key] = AppleEvent(
        event_id=key, type=kind, subtype=subtype, bundle_id=PKG, environment="Sandbox", signed_at=env.clock.now(),
        transaction=AppleTransaction(token, product, "orig-1", tx, expires, revoked),
    )
    return key


async def _plan(env, ws) -> str:
    return (await env.container.plans.entitlement(ws))[0].id


async def _actions(env) -> list[str]:
    return [e.action for e in await env.container.audit.query(limit=500)]


async def test_store_token_only_for_the_owner_of_a_personal_workspace(env) -> None:
    ws, token = await _bound(env)
    assert len(token) == 36 and (await env.stores.bindings.get(token)).workspace_id == ws
    team = await env.container.tenancy.create_team("ann", "Acme")
    with pytest.raises(ValidationError):
        await env.stores.issue_token("ann", team.id)
    with pytest.raises(AuthorizationError):
        await env.stores.issue_token("bob", ws)


async def test_subscribe_activates_pro_until_the_store_expiry(env) -> None:
    ws, token = await _bound(env)
    assert await env.stores.handle_apple(_apple(env, "n1", "SUBSCRIBED", token)) == "applied"
    sub = await env.billing.subscriptions.get(ws)
    assert sub.status is SubscriptionStatus.ACTIVE and sub.provider == "apple"
    assert sub.period_end == env.clock.now() + timedelta(days=30)
    assert await _plan(env, ws) == "pro"


async def test_forged_payload_changes_nothing(env) -> None:
    """Review focus #1 at the service: the verifier's refusal propagates, nothing is written."""
    ws, _token = await _bound(env)
    with pytest.raises(AuthenticationError):
        await env.stores.handle_apple("forged")
    assert await env.billing.subscriptions.get(ws) is None


async def test_same_notification_twice_is_applied_once(env) -> None:
    """Review focus #2."""
    ws, token = await _bound(env)
    key = _apple(env, "n1", "SUBSCRIBED", token)
    assert await env.stores.handle_apple(key) == "applied"
    assert await env.stores.handle_apple(key) == "duplicate"
    assert (await _actions(env)).count("billing.store_active") == 1


async def test_unbound_or_foreign_binding_is_ignored_and_audited(env) -> None:
    """Review focus #3."""
    ws, _token = await _bound(env)
    assert await env.stores.handle_apple(_apple(env, "n1", "SUBSCRIBED", "0" * 8)) == "ignored"
    await env.stores.bindings.put(StoreBinding("planted", ws, "mallory", env.clock.now()))
    assert await env.stores.handle_apple(_apple(env, "n2", "SUBSCRIBED", "planted")) == "ignored"
    assert await env.stores.handle_apple(_apple(env, "n3", "SUBSCRIBED", None)) == "ignored"
    assert await env.billing.subscriptions.get(ws) is None and await _plan(env, ws) == "free"
    assert (await _actions(env)).count("billing.store_ignored") == 3


async def test_unknown_product_is_ignored(env) -> None:
    ws, token = await _bound(env)
    assert await env.stores.handle_apple(_apple(env, "n1", "SUBSCRIBED", token, product="aura.gold")) == "ignored"
    assert await env.billing.subscriptions.get(ws) is None


async def test_period_never_moves_backwards(env) -> None:
    """Review focus #4: renew, then a late DID_RENEW / EXPIRED about an older transaction."""
    ws, token = await _bound(env)
    await env.stores.handle_apple(_apple(env, "n1", "SUBSCRIBED", token, days=30, tx="t-1"))
    await env.stores.handle_apple(_apple(env, "n2", "DID_RENEW", token, days=60, tx="t-2"))
    end = (await env.billing.subscriptions.get(ws)).period_end
    assert await env.stores.handle_apple(_apple(env, "n3", "DID_RENEW", token, days=30, tx="t-1")) == "applied"
    assert (await env.billing.subscriptions.get(ws)).period_end == end
    assert await env.stores.handle_apple(_apple(env, "n4", "EXPIRED", token, days=30, tx="t-1")) == "stale"
    sub = await env.billing.subscriptions.get(ws)
    assert sub.status is SubscriptionStatus.ACTIVE and await _plan(env, ws) == "pro"
    env.clock.advance(61 * DAY)  # the transaction that holds the period ends with it: that expiry applies
    assert await env.stores.handle_apple(_apple(env, "n5", "EXPIRED", token, days=-1, tx="t-2")) == "applied"
    assert await _plan(env, ws) == "free"


async def test_expiry_at_the_current_period_end_downgrades(env) -> None:
    ws, token = await _bound(env)
    await env.stores.handle_apple(_apple(env, "n1", "SUBSCRIBED", token, days=30))
    end = (await env.billing.subscriptions.get(ws)).period_end
    env.clock.advance(31 * DAY)
    env.apple.events["n2"] = replace(env.apple.events["n1"], event_id="n2", type="EXPIRED", subtype="VOLUNTARY")
    assert env.apple.events["n2"].transaction.expires_at == end
    assert await env.stores.handle_apple("n2") == "applied"
    assert (await env.billing.subscriptions.get(ws)).status is SubscriptionStatus.EXPIRED
    assert await _plan(env, ws) == "free"


@pytest.mark.parametrize("kind", ["REFUND", "REVOKE"])
async def test_refund_or_revoke_is_free_at_once(env, kind) -> None:
    """Review focus #5: whatever the expiry says."""
    ws, token = await _bound(env)
    await env.stores.handle_apple(_apple(env, "n1", "SUBSCRIBED", token, days=30))
    assert await env.stores.handle_apple(_apple(env, "n2", kind, token, days=30, revoked=True)) == "applied"
    assert (await env.billing.subscriptions.get(ws)).status is SubscriptionStatus.EXPIRED
    assert await _plan(env, ws) == "free"


async def test_renewal_failures_and_auto_renew(env) -> None:
    ws, token = await _bound(env)
    await env.stores.handle_apple(_apple(env, "n1", "SUBSCRIBED", token, days=30))
    sub = await env.billing.subscriptions.get(ws)
    await env.stores.handle_apple(_apple(env, "n2", "DID_CHANGE_RENEWAL_STATUS", token, subtype="AUTO_RENEW_DISABLED"))
    assert (await env.billing.subscriptions.get(ws)).status is SubscriptionStatus.CANCELED
    assert await _plan(env, ws) == "pro"  # access until the end of what was paid for
    await env.stores.handle_apple(_apple(env, "n3", "DID_CHANGE_RENEWAL_STATUS", token, subtype="AUTO_RENEW_ENABLED"))
    assert (await env.billing.subscriptions.get(ws)).status is SubscriptionStatus.ACTIVE
    env.clock.advance(30 * DAY)
    await env.stores.handle_apple(_apple(env, "n4", "DID_FAIL_TO_RENEW", token, days=0, subtype="GRACE_PERIOD"))
    got = await env.billing.subscriptions.get(ws)
    assert got.status is SubscriptionStatus.GRACE and got.grace_until > env.clock.now()
    assert got.period_end == sub.period_end and await _plan(env, ws) == "pro"
    await env.stores.handle_apple(_apple(env, "n5", "DID_FAIL_TO_RENEW", token, days=0))
    assert (await env.billing.subscriptions.get(ws)).status is SubscriptionStatus.PAST_DUE


async def test_a_store_event_does_not_touch_a_fawry_subscription(env) -> None:
    ws, token = await _bound(env)
    checkout = await env.billing.checkout("ann", ws, "pro", 1, email=None)
    await env.billing.handle_notification(env.billing.provider.notification(checkout["payment_id"], "paid", 15000))
    assert await env.stores.handle_apple(_apple(env, "n1", "EXPIRED", token, days=0)) == "ignored"
    assert await env.stores.handle_apple(_apple(env, "n2", "REFUND", token, revoked=True)) == "ignored"
    assert (await env.billing.subscriptions.get(ws)).provider == "fawry" and await _plan(env, ws) == "pro"
    assert await env.stores.handle_apple(_apple(env, "n3", "SUBSCRIBED", token, days=30)) == "applied"
    assert "billing.provider_switch" in await _actions(env)
    assert (await env.billing.subscriptions.get(ws)).provider == "apple"


async def test_cycle_issues_no_renewal_for_a_store_subscription_but_expires_it(env) -> None:
    ws, token = await _bound(env)
    await env.stores.handle_apple(_apple(env, "n1", "SUBSCRIBED", token, days=30))
    env.clock.advance(29 * DAY)
    assert (await env.billing.run_cycle())["renewals"] == 0
    env.clock.advance(20 * DAY)  # the store went silent: period + grace passed
    await env.billing.run_cycle()
    assert (await env.billing.subscriptions.get(ws)).status is SubscriptionStatus.EXPIRED
    assert await _plan(env, ws) == "free"


async def test_fawry_payment_takes_the_subscription_back_from_a_store(env) -> None:
    ws, token = await _bound(env)
    await env.stores.handle_apple(_apple(env, "n1", "SUBSCRIBED", token, days=30))
    with pytest.raises(ConflictError):  # already has Pro from the App Store
        await env.billing.checkout("ann", ws, "pro", 1, email=None)
    await env.stores.handle_apple(_apple(env, "n2", "DID_CHANGE_RENEWAL_STATUS", token, subtype="AUTO_RENEW_DISABLED"))
    checkout = await env.billing.checkout("ann", ws, "pro", 1, email=None)
    await env.billing.handle_notification(env.billing.provider.notification(checkout["payment_id"], "paid", 15000))
    sub = await env.billing.subscriptions.get(ws)
    assert sub.provider == "fawry" and sub.status is SubscriptionStatus.ACTIVE


async def test_store_expiry_leaves_a_manual_plan_alone(env) -> None:
    ws, token = await _bound(env)
    await env.stores.handle_apple(_apple(env, "n1", "SUBSCRIBED", token, days=30))
    await env.container.plans.set_entitlement("admin", ws, "pro", 1, source="manual")
    await env.stores.handle_apple(_apple(env, "n2", "REFUND", token, revoked=True))
    assert await _plan(env, ws) == "pro"


# -- Google -----------------------------------------------------------------------------------

def _push(token: str, message_id: str = "m-1", *, voided: bool = False, package: str = PKG) -> bytes:
    data = ({"packageName": package, "voidedPurchaseNotification": {"purchaseToken": token, "productType": 1}}
            if voided else
            {"packageName": package, "subscriptionNotification": {"notificationType": 4, "purchaseToken": token}})
    return json.dumps({"message": {"messageId": message_id,
                                   "data": base64.b64encode(json.dumps(data).encode()).decode()}}).encode()


def _play(env, binding, state="active", *, days=30, acknowledged=False, product="aura_pro"):
    return PlaySubscription(state, product, env.clock.now() + timedelta(days=days), binding, None, acknowledged)


async def test_google_active_then_replay_then_expired(env) -> None:
    ws, token = await _bound(env)
    env.play.states["p1"] = _play(env, token)
    assert await env.stores.handle_google("Bearer google", _push("p1")) == "applied"
    assert await _plan(env, ws) == "pro" and env.play.acknowledged == [("aura_pro", "p1")]
    assert (await env.billing.subscriptions.get(ws)).provider == "google"
    assert await env.stores.handle_google("Bearer google", _push("p1")) == "duplicate"
    env.play.states["p1"] = _play(env, token, "expired", days=0)
    assert await env.stores.handle_google("Bearer google", _push("p1", "m-2")) == "applied"
    assert await _plan(env, ws) == "free"


async def test_google_states_map_onto_the_subscription(env) -> None:
    ws, token = await _bound(env)
    expected = {"grace": SubscriptionStatus.GRACE, "on_hold": SubscriptionStatus.PAST_DUE,
                "paused": SubscriptionStatus.PAST_DUE, "canceled": SubscriptionStatus.CANCELED,
                "active": SubscriptionStatus.ACTIVE}
    env.play.states["p1"] = _play(env, token)
    await env.stores.handle_google("Bearer google", _push("p1", "m-0"))
    for i, (state, status) in enumerate(expected.items()):
        env.play.states["p1"] = _play(env, token, state, acknowledged=True)
        await env.stores.handle_google("Bearer google", _push("p1", f"m-{i + 1}"))
        assert (await env.billing.subscriptions.get(ws)).status is status, state
    assert await _plan(env, ws) == "pro"


async def test_google_voided_purchase_is_free_at_once(env) -> None:
    ws, token = await _bound(env)
    env.play.states["p1"] = _play(env, token)
    await env.stores.handle_google("Bearer google", _push("p1"))
    assert await env.stores.handle_google("Bearer google", _push("p1", "m-2", voided=True)) == "applied"
    assert await _plan(env, ws) == "free"


async def test_google_refusals(env) -> None:
    ws, token = await _bound(env)
    env.play.states["p1"] = _play(env, token)
    with pytest.raises(AuthenticationError):
        await env.stores.handle_google("Bearer forged", _push("p1"))
    assert await env.stores.handle_google("Bearer google", _push("p1", package="com.other")) == "ignored"
    env.play.states["p2"] = _play(env, "not-a-binding")
    assert await env.stores.handle_google("Bearer google", _push("p2")) == "ignored"
    assert await env.billing.subscriptions.get(ws) is None
