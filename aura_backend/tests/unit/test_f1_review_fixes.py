"""SaaS P1-F1 review regressions."""

from __future__ import annotations

import asyncio
from dataclasses import replace
from datetime import timedelta

import pytest

from aura_core.adapters.billing import (
    FakePaymentProvider,
    InMemoryBillingContactStore,
    InMemoryBillingLocks,
    InMemoryPaymentStore,
    InMemorySubscriptionStore,
)
from aura_core.adapters.email import InMemoryEmailSender
from aura_core.adapters.sharing import InMemorySharedConversationStore
from aura_core.billing import BillingService
from aura_core.config.settings import BillingSettings, EmailSettings
from aura_core.domain.billing import DEFAULT_PRICES, SubscriptionStatus
from aura_core.domain.errors import ConflictError, ValidationError
from aura_core.domain.sharing import MAX_SHARES_PER_MEMBER
from aura_core.domain.tenancy import WorkspaceRole
from aura_core.notifications import Notifier
from aura_core.ports.email import EmailMessage
from aura_core.sharing import SharingService

DAY = 86400
MSGS = [{"role": "user", "text": "hi"}]


@pytest.fixture
def outbox():
    return InMemoryEmailSender()


@pytest.fixture
def billing(container, clock, outbox):
    container.plans.clock = container.tenancy.clock = container.clock = clock
    notifier = Notifier(sender=outbox, settings=EmailSettings(enabled=True, app_base_url="https://a"),
                        audit=container.audit, clock=clock)
    return BillingService(
        subscriptions=InMemorySubscriptionStore(), payments=InMemoryPaymentStore(), provider=FakePaymentProvider(),
        plans=container.plans, tenancy=container.tenancy, clock=clock, audit=container.audit,
        settings=BillingSettings(enabled=True), prices=DEFAULT_PRICES, locks=InMemoryBillingLocks(),
        contacts=InMemoryBillingContactStore(), notifier=notifier,
    )


async def _paid(billing, container, user="ann", card=None):
    ws = (await container.tenancy.ensure_personal(user)).id
    checkout = await billing.checkout(user, ws, "pro", 1, email=f"{user}@example.com")
    await billing.handle_notification(billing.provider.notification(checkout["payment_id"], "paid", 15000, card_token=card))
    return ws


async def test_extend_grace_from_expired_never_charges_a_saved_card(billing, container) -> None:
    """#1: a cancelled or refunded customer brought back by an operator must not be auto-charged."""
    ws = await _paid(billing, container, card="tok")
    sub = await billing.subscriptions.get(ws)
    await billing.subscriptions.put(replace(sub, status=SubscriptionStatus.EXPIRED))
    await billing.extend_grace("sa", ws, 3)
    assert (await billing.subscriptions.get(ws)).card_token is None


async def test_extend_grace_refused_after_a_refund(billing, container) -> None:
    ws = await _paid(billing, container)
    sub = await billing.subscriptions.get(ws)
    await billing.handle_notification(billing.provider.notification(sub.last_payment_id, "refunded", 15000))
    with pytest.raises(ConflictError):
        await billing.extend_grace("sa", ws, 3)


async def test_extend_grace_repairs_the_entitlement_on_retry(billing, container, monkeypatch) -> None:
    """#2: a failed entitlement write must not leave GRACE with Free that a retry cannot fix."""
    ws = await _paid(billing, container)
    sub = await billing.subscriptions.get(ws)
    await billing.subscriptions.put(replace(sub, status=SubscriptionStatus.EXPIRED))
    await container.plans.set_entitlement("billing", ws, "free", 1, source="subscription")
    original = container.plans.set_entitlement

    async def boom(*a, **k):
        raise RuntimeError("db down")

    monkeypatch.setattr(container.plans, "set_entitlement", boom)
    with pytest.raises(RuntimeError):
        await billing.extend_grace("sa", ws, 3)
    monkeypatch.setattr(container.plans, "set_entitlement", original)
    await billing.extend_grace("sa", ws, 3)
    assert (await container.plans.entitlement(ws))[0].id == "pro"


async def test_extend_grace_never_shortens_the_automatic_grace(billing, container) -> None:
    """#8."""
    ws = await _paid(billing, container)
    sub = await billing.subscriptions.get(ws)
    container.clock.advance((sub.period_end - container.clock.now()).total_seconds() + 3600)
    await billing.subscriptions.put(replace(sub, status=SubscriptionStatus.PAST_DUE))
    result = await billing.extend_grace("sa", ws, 1)
    assert (await billing.subscriptions.get(ws)).grace_until >= sub.period_end + timedelta(days=billing.settings.grace_days)
    assert result["status"] == "grace"


async def test_a_slow_smtp_host_does_not_hold_the_settle(billing, container, clock, monkeypatch) -> None:
    """#3: the send is bounded."""
    import aura_core.notifications.service as notifications

    monkeypatch.setattr(notifications, "SEND_TIMEOUT_S", 0.05)

    class Slow:
        async def send(self, message):
            await asyncio.sleep(5)

    billing.notifier.sender = Slow()
    started = asyncio.get_running_loop().time()
    await _paid(billing, container)
    assert asyncio.get_running_loop().time() - started < 2
    assert any(e.action == "email.failed" for e in await container.audit.query(limit=50))


async def test_billing_email_goes_to_the_current_owner_only(billing, container, outbox) -> None:
    """#4: after a transfer, the former Owner stops getting the team's billing email."""
    team = await container.tenancy.create_team("own", "Acme")
    await container.plans.set_plan("sa", team.id, "team", 5)
    _i, token = await container.tenancy.invite("own", team.id, "amy@example.com", WorkspaceRole.MEMBER)
    await container.tenancy.accept("amy", token)
    checkout = await billing.checkout("own", team.id, "team", 2, email="own@example.com")
    await container.tenancy.transfer("own", team.id, "amy")
    outbox.outbox.clear()
    await billing.handle_notification(billing.provider.notification(checkout["payment_id"], "paid", 24000))
    assert [m for m in outbox.outbox if m.tag == "receipt"] == []


@pytest.fixture
def sharing(container, clock):
    container.tenancy.clock = clock
    return SharingService(store=InMemorySharedConversationStore(), tenancy=container.tenancy, clock=clock,
                          audit=container.audit)


async def _team(container):
    ws = await container.tenancy.create_team("own", "Acme")
    await container.plans.set_plan("sa", ws.id, "team", 5)
    _i, token = await container.tenancy.invite("own", ws.id, "amy@example.com", WorkspaceRole.MEMBER)
    await container.tenancy.accept("amy", token)
    return ws.id


async def test_shares_per_member_are_capped(container, sharing) -> None:
    """#5."""
    ws = await _team(container)
    for i in range(MAX_SHARES_PER_MEMBER):
        await sharing.share("amy", ws, f"c{i}", "t", MSGS)
    await sharing.share("amy", ws, "c0", "again", MSGS)  # re-sharing never counts
    with pytest.raises(ConflictError):
        await sharing.share("amy", ws, "one-more", "t", MSGS)
    await sharing.share("own", ws, "own-1", "t", MSGS)  # per member


@pytest.mark.parametrize("field", ["text", "title", "source"])
async def test_nul_is_refused_before_storage(container, sharing, field) -> None:
    """#6: Postgres rejects NUL; it must be a 422, not a 500."""
    ws = await _team(container)
    messages = [{"role": "user", "text": "a\x00b" if field == "text" else "ok"}]
    with pytest.raises(ValidationError):
        await sharing.share("amy", ws, "s\x00" if field == "source" else "s", "t\x00" if field == "title" else "t", messages)


async def test_erasing_a_member_removes_their_shares(container, sharing) -> None:
    """#7."""
    ws = await _team(container)
    container.sharing = sharing
    await sharing.share("amy", ws, "c1", "t", MSGS)
    from api.admin_platform import _erase_core

    counts, failed = await _erase_core(container, "amy", set())
    assert failed == [] and counts["shared_chats"] == 1
    assert await sharing.list("own", ws) == []


def test_email_repr_hides_the_body() -> None:
    """#9: an invite's body carries a token."""
    assert "SECRET" not in repr(EmailMessage(to="a@b.co", subject="s", text="SECRET", tag="invite"))
