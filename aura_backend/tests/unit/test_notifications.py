"""The in-app notification inbox (migration 0039): the rules, the store, the producers that fill it and
the routes that read it.

A notification is a kind and its parameters, never prose, and never a credential. The producers are
asserted through their real code paths -- a held tool call, a billing cycle, a plan limit hit over
HTTP -- and what is read back is what a person would see.
"""

from __future__ import annotations

import json
from dataclasses import replace
from datetime import datetime, timedelta, timezone

import pytest

from aura_core.adapters.notifications import InMemoryNotificationSettingsStore, InMemoryNotificationStore
from aura_core.domain.errors import ValidationError
from aura_core.domain.identifiers import PlanId, StepId, UserId
from aura_core.domain.notifications import KINDS, MAX_PARAMS_BYTES, Notification, NotificationSettings
from aura_core.notifications.inbox import Inbox

pytest.importorskip("fastapi")

ALICE, BOB = UserId("alice"), UserId("bob")
T0 = datetime(2026, 11, 23, 9, 0, tzinfo=timezone.utc)


def _n(id_: str, owner: UserId = ALICE, kind: str = "run_failed", minutes: int = 0, **kw) -> Notification:
    return Notification(id=id_, owner_id=owner, kind=kind, created_at=T0 + timedelta(minutes=minutes), **kw)


class _Clock:
    def __init__(self, at: datetime = T0) -> None:
        self.at = at

    def now(self) -> datetime:
        return self.at


# ---------------------------------------------------------------------------------------------
# the rules
# ---------------------------------------------------------------------------------------------


class TestRules:
    def test_a_kind_the_system_does_not_know_is_refused(self) -> None:
        with pytest.raises(ValidationError):
            _n("a", kind="made_up")

    def test_params_must_be_small_json(self) -> None:
        with pytest.raises(ValidationError):
            _n("a", params={"blob": "x" * (MAX_PARAMS_BYTES + 1)})
        with pytest.raises(ValidationError):
            _n("a", params={"bad": object()})
        _n("a", params={"run_id": "r1", "n": 3, "nested": {"a": [1, 2]}})

    def test_settings_refuse_an_unknown_kind_and_default_everything_on(self) -> None:
        with pytest.raises(ValidationError):
            NotificationSettings(disabled_kinds=frozenset({"nope"}))
        fresh = NotificationSettings()
        assert all(fresh.enabled(k) for k in KINDS) and fresh.as_dict()["kinds"] == {k: True for k in KINDS}
        off = NotificationSettings(disabled_kinds=frozenset({"run_failed"}))
        assert off.as_dict()["kinds"]["run_failed"] is False and off.as_dict()["kinds"]["approval_pending"] is True


# ---------------------------------------------------------------------------------------------
# the store
# ---------------------------------------------------------------------------------------------


class TestStore:
    async def test_a_dedupe_key_is_stored_once_per_owner(self) -> None:
        store = InMemoryNotificationStore()
        assert await store.add(_n("a", dedupe_key="run:1")) is True
        assert await store.add(_n("b", dedupe_key="run:1")) is False, "the same event twice"
        assert await store.add(_n("c", owner=BOB, dedupe_key="run:1")) is True, "another person's own"
        assert await store.add(_n("d")) is True and await store.add(_n("e")) is True, "no key: never deduped"
        assert {n.id for n in await store.list(ALICE)} == {"a", "d", "e"}

    async def test_every_statement_is_scoped_to_the_owner(self) -> None:
        store = InMemoryNotificationStore()
        await store.add(_n("a"))
        assert await store.get(BOB, "a") is None and await store.list(BOB) == [] and await store.unread_count(BOB) == 0
        assert await store.mark_read(BOB, ["a"], at=T0) == 0 and await store.mark_read(BOB, None, at=T0) == 0
        assert await store.delete(BOB, "a") is False and await store.delete_for_owner(BOB) == 0
        assert (await store.get(ALICE, "a")).read_at is None, "Bob marked Alice's read"
        assert await store.delete_for_owner(ALICE) == 1 and await store.get(ALICE, "a") is None

    async def test_marking_read_changes_only_unread_rows_and_keeps_the_first_time(self) -> None:
        store = InMemoryNotificationStore()
        for i in ("a", "b", "c"):
            await store.add(_n(i))
        first, later = T0 + timedelta(minutes=5), T0 + timedelta(minutes=9)

        assert await store.mark_read(ALICE, ["a", "nope"], at=first) == 1
        assert await store.mark_read(ALICE, ["a"], at=later) == 0, "already read"
        assert (await store.get(ALICE, "a")).read_at == first
        assert await store.unread_count(ALICE) == 2
        assert await store.mark_read(ALICE, None, at=later) == 2 and await store.unread_count(ALICE) == 0
        assert (await store.get(ALICE, "a")).read_at == first and (await store.get(ALICE, "b")).read_at == later
        assert await store.mark_read(ALICE, [], at=later) == 0

    async def test_listing_is_newest_first_filtered_and_paged_without_gaps(self) -> None:
        store = InMemoryNotificationStore()
        for i in range(7):
            await store.add(_n(f"n{i}", kind="renewal_due" if i % 2 else "run_failed", minutes=i))
        await store.add(_n("tie-a", minutes=0))
        await store.mark_read(ALICE, ["n6", "n5"], at=T0)

        assert [n.id for n in await store.list(ALICE, limit=3)] == ["n6", "n5", "n4"]
        assert [n.id for n in await store.list(ALICE, unread_only=True, limit=2)] == ["n4", "n3"]
        assert [n.id for n in await store.list(ALICE, kind="renewal_due")] == ["n5", "n3", "n1"]
        seen, before = [], None
        while page := await store.list(ALICE, limit=3, before=before):
            seen += [n.id for n in page]
            before = (page[-1].created_at, page[-1].id)
        assert seen == ["n6", "n5", "n4", "n3", "n2", "n1", "tie-a", "n0"] and len(set(seen)) == 8

    async def test_the_inbox_is_capped_and_drops_read_ones_first_then_the_oldest(self) -> None:
        store = InMemoryNotificationStore(max_per_owner=4)
        for i in range(4):
            await store.add(_n(f"n{i}", minutes=i))
        await store.mark_read(ALICE, ["n2"], at=T0)          # a read one in the middle

        await store.add(_n("n4", minutes=4))
        assert {n.id for n in await store.list(ALICE)} == {"n0", "n1", "n3", "n4"}, "the read one should go first"
        await store.add(_n("n5", minutes=5))
        assert {n.id for n in await store.list(ALICE)} == {"n1", "n3", "n4", "n5"}, "then the oldest"
        await store.add(_n("bob1", owner=BOB))
        assert len(await store.list(ALICE)) == 4 and len(await store.list(BOB)) == 1, "one person's cap is not another's"


# ---------------------------------------------------------------------------------------------
# the inbox service
# ---------------------------------------------------------------------------------------------


@pytest.fixture
def inbox_parts():
    store, settings = InMemoryNotificationStore(), InMemoryNotificationSettingsStore()
    return store, settings, Inbox(store, settings, _Clock())


class TestInbox:
    async def test_it_stores_a_notification_with_its_params_and_time(self, inbox_parts) -> None:
        store, _, inbox = inbox_parts
        assert await inbox.notify(ALICE, "payment_received", {"plan_id": "pro", "amount_piastres": 15000}, dedupe_key="p1") is True
        (row,) = await store.list(ALICE)
        assert (row.kind, dict(row.params), row.created_at, row.read_at, row.dedupe_key) == (
            "payment_received", {"plan_id": "pro", "amount_piastres": 15000}, T0, None, "p1",
        )

    async def test_a_kind_the_person_switched_off_is_not_stored_and_the_others_are(self, inbox_parts) -> None:
        store, settings, inbox = inbox_parts
        await settings.put(ALICE, NotificationSettings(disabled_kinds=frozenset({"run_failed"})), at=T0)

        assert await inbox.notify(ALICE, "run_failed", {}) is False
        assert await inbox.notify(ALICE, "renewal_due", {}) is True
        assert await inbox.notify(BOB, "run_failed", {}) is True, "Alice's setting is not Bob's"
        assert [n.kind for n in await store.list(ALICE)] == ["renewal_due"]

    async def test_a_repeated_event_is_one_notification(self, inbox_parts) -> None:
        store, _, inbox = inbox_parts
        assert await inbox.notify(ALICE, "run_failed", {"run_id": "r"}, dedupe_key="run:r") is True
        assert await inbox.notify(ALICE, "run_failed", {"run_id": "r"}, dedupe_key="run:r") is False
        assert len(await store.list(ALICE)) == 1

    async def test_it_never_raises_whatever_goes_wrong(self) -> None:
        class Down:
            async def get(self, *_):
                raise ConnectionError("db down")

            async def add(self, *_):
                raise ConnectionError("db down")

        up, settings = InMemoryNotificationStore(), InMemoryNotificationSettingsStore()
        assert await Inbox(Down(), settings, _Clock()).notify(ALICE, "run_failed", {}) is False
        assert await Inbox(up, Down(), _Clock()).notify(ALICE, "run_failed", {}) is False
        assert await Inbox(up, settings, _Clock()).notify(ALICE, "made_up_kind", {}) is False
        assert await Inbox(up, settings, _Clock()).notify(ALICE, "run_failed", {"x": "y" * 5000}) is False
        assert await up.list(ALICE) == []


# ---------------------------------------------------------------------------------------------
# producers
# ---------------------------------------------------------------------------------------------


def _held_turn():
    from aura_core.domain.planning import Goal, Plan, PlanStep, StepKind
    from aura_core.execution.confirmations import PendingCall

    steps = (
        PlanStep(id=StepId("s1"), kind=StepKind.TOOL, tool="delete_record", description="d", arguments={"target": "SECRET-ARG"}).blocked("needs_confirmation"),
        PlanStep(id=StepId("s2"), kind=StepKind.TOOL, tool="send_message", description="s", arguments={"target": "bob"}).blocked("needs_confirmation"),
    )
    calls = [PendingCall("s1", "step_s1", "delete_record", {"target": "SECRET-ARG"}),
             PendingCall("s2", "step_s2", "send_message", {"target": "bob"})]
    return Plan(id=PlanId.new(), goal=Goal(description="g"), steps=steps), calls


class TestApprovalHold:
    async def test_a_held_turn_tells_the_person_the_tools_and_never_the_ids_or_arguments(self, inbox_parts) -> None:
        from aura_core.execution.confirmations import PendingConfirmationStore
        from aura_core.llm.cache import InMemoryCache

        store, _, inbox = inbox_parts
        confirmations = PendingConfirmationStore(InMemoryCache(), inbox=inbox)
        plan, calls = _held_turn()

        ids = await confirmations.issue(user_id="alice", session_id="conv-9", agent="orchestrator", message="do it SECRET-MSG",
                                        plan=plan, tainted=False, turn_id="t1", calls=calls)

        (row,) = await store.list(ALICE)
        assert row.kind == "approval_pending"
        assert dict(row.params) == {"tools": ["delete_record", "send_message"], "count": 2, "session_id": "conv-9"}
        blob = json.dumps(row.as_dict())
        assert "SECRET" not in blob and not any(i in blob for i in ids.values()) and "apr." not in blob, blob

    async def test_the_hold_goes_on_when_the_inbox_is_down_or_the_kind_is_off(self, inbox_parts) -> None:
        from aura_core.execution.confirmations import PendingConfirmationStore
        from aura_core.llm.cache import InMemoryCache

        store, settings, inbox = inbox_parts
        await settings.put(ALICE, NotificationSettings(disabled_kinds=frozenset({"approval_pending"})), at=T0)
        plan, calls = _held_turn()
        ids = await PendingConfirmationStore(InMemoryCache(), inbox=inbox).issue(
            user_id="alice", session_id="s", agent="o", message="m", plan=plan, tainted=False, turn_id="t", calls=calls)
        assert set(ids) == {"step_s1", "step_s2"} and await store.list(ALICE) == []

        class Down:
            async def get(self, *_):
                raise ConnectionError("x")

        broken = PendingConfirmationStore(InMemoryCache(), inbox=Inbox(store, Down(), _Clock()))
        assert set(await broken.issue(user_id="alice", session_id="s", agent="o", message="m", plan=plan,
                                      tainted=False, turn_id="t", calls=calls)) == {"step_s1", "step_s2"}


class TestRunFailed:
    """A run that ends failed or out of budget is the one a person did not see finish; the others
    (completed, partial, awaiting approval, cancelled) have their outcome inline."""

    @staticmethod
    def _loop(inbox, executor):
        from aura_core.adapters.memory_stores import InMemoryEngineerRunStore
        from aura_core.execution.agentic_loop import AgenticExecutor
        from tests.unit.test_agentic_loop import _FakePlanner

        runs = InMemoryEngineerRunStore()
        return AgenticExecutor(planner=_FakePlanner(), executor=executor, runs=runs, inbox=inbox), runs

    @staticmethod
    async def _run(loop, goal: str = "SECRET-GOAL"):
        from aura_core.execution.run_recorder import AgentRunContext
        from aura_core.ports.runtime import ToolContext
        from tests.unit.test_agentic_loop import _plan

        return await loop.run(
            _plan(), context=ToolContext(user_id=ALICE, correlation_id="c1", deadline_s=30.0), max_iterations=0,
            run_context=AgentRunContext(user_id=ALICE, agent_id="engineer", turn_id="t", correlation_id="c", goal=goal),
        )

    async def test_a_failed_run_notifies_with_identifiers_only(self, inbox_parts) -> None:
        from tests.unit.test_agentic_loop import _ScriptedExecutor

        store, _, inbox = inbox_parts
        loop, runs = self._loop(inbox, _ScriptedExecutor([False]))

        await self._run(loop)

        [run] = await runs.list_runs(ALICE)
        (row,) = await store.list(ALICE)
        assert row.kind == "run_failed" and run.status == "failed"
        assert dict(row.params) == {"run_id": run.id.value, "agent_id": "engineer", "status": "failed"}
        assert "SECRET-GOAL" not in json.dumps(row.as_dict()), "the instruction reached a notification"

    async def test_a_run_that_completes_says_nothing(self, inbox_parts) -> None:
        from tests.unit.test_agentic_loop import _ScriptedExecutor

        store, _, inbox = inbox_parts
        loop, _ = self._loop(inbox, _ScriptedExecutor([True]))
        await self._run(loop)
        assert await store.list(ALICE) == []

    async def test_a_loop_that_raises_notifies_and_still_raises(self, inbox_parts) -> None:
        store, _, inbox = inbox_parts

        class Boom:
            async def execute(self, plan, *, context, **kw):
                raise RuntimeError("boom")

        loop, runs = self._loop(inbox, Boom())
        with pytest.raises(RuntimeError):
            await self._run(loop)
        (row,) = await store.list(ALICE)
        assert row.kind == "run_failed" and dict(row.params)["status"] == "failed"

    @pytest.mark.parametrize("status_case,notified", [
        ("budget", True), ("partial", False), ("awaiting_approval", False), ("cancelled", False),
    ])
    async def test_only_failed_and_out_of_budget_runs_notify(self, inbox_parts, status_case, notified) -> None:
        from types import SimpleNamespace

        from aura_core.domain.agent_runs import AgentBudget
        from aura_core.execution.run_recorder import AgentRunContext, RunRecorder

        store, _, inbox = inbox_parts
        recorder = RunRecorder(
            context=AgentRunContext(user_id=ALICE, agent_id="engineer", turn_id="t", correlation_id="c", goal="g"),
            budget=AgentBudget(), inbox=inbox,
        )
        failed = SimpleNamespace(succeeded=False, blocked_reason=None)
        if status_case == "budget":
            await recorder.finish(failed, stop_reason="budget", exhausted_budget="tool_calls")
        elif status_case == "partial":
            recorder.run = recorder.run.with_(plan_steps_succeeded=1)
            await recorder.finish(failed, stop_reason="plan_failed", exhausted_budget=None)
        elif status_case == "awaiting_approval":
            await recorder.finish(SimpleNamespace(succeeded=False, blocked_reason="needs_confirmation"),
                                  stop_reason="needs_confirmation", exhausted_budget=None)
        else:
            await recorder.finish(failed, stop_reason="run_cancelled", exhausted_budget=None)

        rows = await store.list(ALICE)
        assert [r.kind for r in rows] == (["run_failed"] if notified else [])
        if notified:
            assert dict(rows[0].params)["status"] == "budget_exhausted"

    async def test_a_kind_switched_off_or_a_broken_inbox_never_changes_the_run(self, inbox_parts) -> None:
        from tests.unit.test_agentic_loop import _ScriptedExecutor

        store, settings, inbox = inbox_parts
        await settings.put(ALICE, NotificationSettings(disabled_kinds=frozenset({"run_failed"})), at=T0)
        loop, runs = self._loop(inbox, _ScriptedExecutor([False]))
        await self._run(loop)
        assert await store.list(ALICE) == [] and (await runs.list_runs(ALICE))[0].status == "failed"

        class Down:
            async def notify(self, *a, **k):
                raise ConnectionError("db down")

        loop2, runs2 = self._loop(Down(), _ScriptedExecutor([False]))
        await self._run(loop2)
        assert (await runs2.list_runs(ALICE))[0].status == "failed"


def _app(container, role: str = "User"):
    from aura_core.api import Principal, build_app
    from aura_core.domain.errors import AuthenticationError

    async def authenticate(request):
        header = request.headers.get("authorization", "")
        if not header.startswith("Bearer "):
            raise AuthenticationError("missing")
        return Principal(user_id=UserId(header[7:]), email="", role=role)

    return build_app(container, authenticator=authenticate)


def _as(user: str) -> dict:
    return {"Authorization": f"Bearer {user}"}


class _RaisingInbox:
    """Breaks the ``Inbox`` contract (``notify`` never raises) to prove no producer depends on it."""

    async def notify(self, *args, **kwargs):
        raise ConnectionError("db down")


class TestProducersDoNotDependOnTheInboxKeepingItsContract:
    async def test_a_hold_still_returns_its_approval_ids(self) -> None:
        from aura_core.execution.confirmations import PendingConfirmationStore
        from aura_core.llm.cache import InMemoryCache

        plan, calls = _held_turn()
        ids = await PendingConfirmationStore(InMemoryCache(), inbox=_RaisingInbox()).issue(
            user_id="alice", session_id="s", agent="o", message="m", plan=plan, tainted=False, turn_id="t", calls=calls)
        assert set(ids) == {"step_s1", "step_s2"}

    async def test_a_plan_limit_is_still_a_402(self, container) -> None:
        from fastapi.testclient import TestClient

        container.inbox = _RaisingInbox()
        workspace = (await container.tenancy.ensure_personal("alice")).id
        usage = await container.plans.usage(workspace)
        await container.plans.meter.try_consume(workspace, "messages", usage["period"], usage["messages_allowed"], usage["messages_allowed"])
        with TestClient(_app(container)) as client:
            assert client.post("/api/v3/chat", json={"message": "hi"}, headers=_as("alice")).status_code == 402


class TestUsageLimit:
    async def test_hitting_the_message_limit_leaves_one_notification_per_period(self, container) -> None:
        from fastapi.testclient import TestClient

        from aura_core.domain.plans import period_resets_at

        workspace = (await container.tenancy.ensure_personal("alice")).id
        usage = await container.plans.usage(workspace)
        await container.plans.meter.try_consume(workspace, "messages", usage["period"], usage["messages_allowed"], usage["messages_allowed"])
        with TestClient(_app(container)) as client:
            first = client.post("/api/v3/chat", json={"message": "hi"}, headers=_as("alice"))
            second = client.post("/api/v3/chat", json={"message": "hi again"}, headers=_as("alice"))
            bob = client.get("/api/v3/notifications", headers=_as("bob")).json()
            mine = client.get("/api/v3/notifications", headers=_as("alice")).json()

        assert first.status_code == second.status_code == 402, "the limit itself must still be refused"
        (row,) = mine["notifications"]
        assert row["kind"] == "usage_limit_reached"
        assert row["params"] == {"limit": "messages", "plan": "free", "resets_at": period_resets_at(usage["period"]).isoformat()}
        assert bob["notifications"] == [], "the limit hit was not Alice's alone"

    async def test_a_full_storage_allowance_notifies_on_the_upload_that_hit_it(self, container) -> None:
        from fastapi.testclient import TestClient

        workspace = (await container.tenancy.ensure_personal("alice")).id
        usage = await container.plans.usage(workspace)
        from aura_core.domain.plans import STORAGE_PERIOD
        from aura_core.plans.service import STORAGE

        await container.plans.meter.try_consume(workspace, STORAGE, STORAGE_PERIOD, usage["storage_allowed"], usage["storage_allowed"])
        with TestClient(_app(container)) as client:
            refused = client.post("/api/v3/knowledge/documents", json={"title": "t", "content": "some text to store"}, headers=_as("alice"))
            rows = client.get("/api/v3/notifications", headers=_as("alice")).json()["notifications"]
        assert refused.status_code == 402
        assert [r["params"]["limit"] for r in rows] == ["storage"] and rows[0]["kind"] == "usage_limit_reached"

    async def test_an_operator_who_is_not_metered_gets_none(self, container) -> None:
        from fastapi.testclient import TestClient

        with TestClient(_app(container, role="Admin")) as client:
            client.post("/api/v3/chat", json={"message": "hi"}, headers=_as("ops"))
            assert client.get("/api/v3/notifications", headers=_as("ops")).json()["notifications"] == []


class TestBilling:
    @pytest.fixture
    def env(self, container, clock):
        from aura_core.adapters.billing import (
            FakePaymentProvider, InMemoryBillingContactStore, InMemoryBillingLocks, InMemoryPaymentStore,
            InMemorySubscriptionStore,
        )
        from aura_core.billing import BillingService
        from aura_core.config.settings import BillingSettings
        from aura_core.domain.billing import DEFAULT_PRICES

        container.plans.clock = container.tenancy.clock = container.clock = clock
        inbox = Inbox(container.notification_store, container.notification_settings, clock)
        provider = FakePaymentProvider()
        billing = BillingService(
            subscriptions=InMemorySubscriptionStore(), payments=InMemoryPaymentStore(), provider=provider,
            plans=container.plans, tenancy=container.tenancy, clock=clock, audit=container.audit,
            settings=BillingSettings(enabled=True), prices=DEFAULT_PRICES, locks=InMemoryBillingLocks(),
            contacts=InMemoryBillingContactStore(), inbox=inbox,
        )
        return container, billing, provider

    @staticmethod
    async def _paid(billing, provider, container, user="ann", card=None, email=True):
        ws = (await container.tenancy.ensure_personal(user)).id
        checkout = await billing.checkout(user, ws, "pro", 1, email=f"{user}@example.com" if email else None)
        await billing.handle_notification(provider.notification(checkout["payment_id"], "paid", 15000, card_token=card))
        return ws

    async def test_a_payment_is_mirrored_to_the_owner_without_the_reference_or_card(self, env) -> None:
        container, billing, provider = env
        ws = await self._paid(billing, provider, container, card="tok-secret")
        (row,) = await container.notification_store.list(UserId("ann"))
        assert row.kind == "payment_received"
        assert dict(row.params)["workspace_id"] == ws and dict(row.params)["plan_id"] == "pro" and dict(row.params)["amount_piastres"] == 15000
        assert "tok-secret" not in json.dumps(row.as_dict())

    async def test_it_does_not_need_a_billing_email_address(self, env) -> None:
        container, billing, provider = env
        await self._paid(billing, provider, container, "dan", email=False)
        assert [n.kind for n in await container.notification_store.list(UserId("dan"))] == ["payment_received"]

    async def test_only_the_workspace_owner_is_told_not_its_members(self, env) -> None:
        from aura_core.domain.tenancy import Membership, WorkspaceRole

        container, billing, provider = env
        team = await container.tenancy.create_team("own", "Acme")
        await container.tenancy.store.add_member(Membership(team.id, "mia", WorkspaceRole.MEMBER, T0))
        checkout = await billing.checkout("own", team.id, "team", 2, email="own@example.com")
        from aura_core.domain.billing import DEFAULT_PRICES, price_for

        paid = await billing.handle_notification(
            provider.notification(checkout["payment_id"], "paid", price_for(DEFAULT_PRICES, "team", 2)))
        assert paid == "applied", paid

        assert [n.kind for n in await container.notification_store.list(UserId("own"))] == ["payment_received"]
        assert await container.notification_store.list(UserId("mia")) == []

    async def test_a_reference_renewal_notifies_and_a_card_renewal_does_not(self, env) -> None:
        container, billing, provider = env
        ref_ws = await self._paid(billing, provider, container, "ann")
        await self._paid(billing, provider, container, "cal", card="tok")
        sub = await billing.subscriptions.get(ref_ws)
        container.clock.advance((sub.period_end - container.clock.now()).total_seconds() - 86400)

        assert (await billing.run_cycle())["renewals"] == 2
        assert [n.kind for n in await container.notification_store.list(UserId("ann"))] == ["renewal_due", "payment_received"]
        assert [n.kind for n in await container.notification_store.list(UserId("cal"))] == ["payment_received"]

    async def test_going_past_due_notifies_once_however_many_cycles_run(self, env) -> None:
        container, billing, provider = env
        ws = await self._paid(billing, provider, container)
        sub = await billing.subscriptions.get(ws)
        container.clock.advance((sub.period_end - container.clock.now()).total_seconds() + 3600)
        await billing.run_cycle()
        container.clock.advance(3600)
        await billing.run_cycle()
        past = [n for n in await container.notification_store.list(UserId("ann")) if n.kind == "payment_past_due"]
        assert len(past) == 1 and "grace_until" in past[0].params

    async def test_a_broken_inbox_never_fails_the_payment(self, env) -> None:
        container, billing, provider = env

        class Down:
            async def notify(self, *a, **k):
                raise ConnectionError("db down")

        billing.inbox = Down()
        ws = await self._paid(billing, provider, container)
        assert (await billing.subscriptions.get(ws)).plan_id == "pro"


# ---------------------------------------------------------------------------------------------
# the routes
# ---------------------------------------------------------------------------------------------


class TestRoutes:
    async def _seed(self, container, owner: UserId = ALICE, n: int = 3) -> list[str]:
        base = datetime.now(timezone.utc) - timedelta(hours=1)
        ids = []
        for i in range(n):
            row = _n(f"{owner.value}-{i}", owner=owner, kind="renewal_due" if i % 2 else "run_failed", minutes=0)
            row = replace(row, created_at=base + timedelta(minutes=i))
            await container.notification_store.add(row)
            ids.append(row.id)
        return ids

    async def test_listing_marking_the_badge_and_deleting(self, container) -> None:
        from fastapi.testclient import TestClient

        ids = await self._seed(container)
        with TestClient(_app(container)) as client:
            listing = client.get("/api/v3/notifications", headers=_as("alice")).json()
            badge = client.get("/api/v3/notifications/unread-count", headers=_as("alice")).json()
            one = client.post(f"/api/v3/notifications/{ids[1]}/read", headers=_as("alice"))
            after_one = client.get("/api/v3/notifications/unread-count", headers=_as("alice")).json()
            unread = client.get("/api/v3/notifications", params={"unread": "true"}, headers=_as("alice")).json()
            deleted = client.delete(f"/api/v3/notifications/{ids[0]}", headers=_as("alice"))
            again = client.delete(f"/api/v3/notifications/{ids[0]}", headers=_as("alice"))
            all_read = client.post("/api/v3/notifications/read-all", headers=_as("alice")).json()
            final = client.get("/api/v3/notifications", headers=_as("alice")).json()

        assert [n["id"] for n in listing["notifications"]] == [ids[2], ids[1], ids[0]] and listing["unread_count"] == 3
        assert badge == {"unread": 3} and after_one == {"unread": 2}
        assert one.json()["read"] is True and one.json()["read_at"] and one.json()["kind"] == "renewal_due"
        assert [n["id"] for n in unread["notifications"]] == [ids[2], ids[0]]
        assert deleted.json() == {"deleted": True} and again.status_code == 404
        assert all_read == {"marked": 1, "unread": 0}
        assert [n["id"] for n in final["notifications"]] == [ids[2], ids[1]] and final["unread_count"] == 0

    async def test_filters_and_paging_over_http(self, container) -> None:
        from fastapi.testclient import TestClient

        ids = await self._seed(container, n=5)
        with TestClient(_app(container)) as client:
            first = client.get("/api/v3/notifications", params={"limit": 2}, headers=_as("alice")).json()
            second = client.get("/api/v3/notifications", params={"limit": 2, "cursor": first["next_cursor"]}, headers=_as("alice")).json()
            third = client.get("/api/v3/notifications", params={"limit": 2, "cursor": second["next_cursor"]}, headers=_as("alice")).json()
            kind = client.get("/api/v3/notifications", params={"kind": "renewal_due"}, headers=_as("alice")).json()
            wrong_cursor = client.get("/api/v3/notifications", params={"limit": 2, "kind": "run_failed", "cursor": first["next_cursor"]}, headers=_as("alice"))
            bad_kind = client.get("/api/v3/notifications", params={"kind": "nonsense"}, headers=_as("alice"))
            bad_cursor = client.get("/api/v3/notifications", params={"cursor": "garbage"}, headers=_as("alice"))

        assert [n["id"] for p in (first, second, third) for n in p["notifications"]] == list(reversed(ids))
        assert third["next_cursor"] is None
        assert [n["kind"] for n in kind["notifications"]] == ["renewal_due", "renewal_due"]
        assert wrong_cursor.status_code == bad_kind.status_code == bad_cursor.status_code == 422

    async def test_settings_switch_kinds_off_replace_and_gate_what_is_stored(self, container) -> None:
        from fastapi.testclient import TestClient

        with TestClient(_app(container)) as client:
            fresh = client.get("/api/v3/me/notification-settings", headers=_as("alice")).json()
            off = client.put("/api/v3/me/notification-settings", json={"disabled_kinds": ["run_failed", "renewal_due"]}, headers=_as("alice"))
            await container.inbox.notify(ALICE, "run_failed", {})
            await container.inbox.notify(ALICE, "payment_received", {})
            stored = client.get("/api/v3/notifications", headers=_as("alice")).json()["notifications"]
            replaced = client.put("/api/v3/me/notification-settings", json={"disabled_kinds": ["payment_received"]}, headers=_as("alice")).json()
            none = client.put("/api/v3/me/notification-settings", json={}, headers=_as("alice")).json()
            bad = client.put("/api/v3/me/notification-settings", json={"disabled_kinds": ["nonsense"]}, headers=_as("alice"))
            bobs = client.get("/api/v3/me/notification-settings", headers=_as("bob")).json()

        assert all(fresh["kinds"].values()) and set(fresh["kinds"]) == set(KINDS) and fresh["updated_at"] is None
        assert off.json()["kinds"]["run_failed"] is False and off.json()["kinds"]["renewal_due"] is False
        assert [n["kind"] for n in stored] == ["payment_received"], "a switched-off kind was stored"
        assert replaced["kinds"]["payment_received"] is False and replaced["kinds"]["run_failed"] is True, "a PUT must replace"
        assert all(none["kinds"].values())
        assert bad.status_code == 422
        assert all(bobs["kinds"].values()), "Alice's settings reached Bob"

    async def test_one_persons_inbox_is_not_anothers(self, container) -> None:
        from fastapi.testclient import TestClient

        ids = await self._seed(container, ALICE, 2)
        await self._seed(container, BOB, 1)
        with TestClient(_app(container)) as client:
            bob = client.get("/api/v3/notifications", headers=_as("bob")).json()
            client.post("/api/v3/notifications/read-all", headers=_as("bob"))
            alice = client.get("/api/v3/notifications", headers=_as("alice")).json()
        assert len(bob["notifications"]) == 1 and bob["notifications"][0]["id"] == "bob-0"
        assert alice["unread_count"] == 2 and {n["id"] for n in alice["notifications"]} == set(ids)

    async def test_it_needs_a_signed_in_caller(self, container) -> None:
        from fastapi.testclient import TestClient

        with TestClient(_app(container)) as client:
            for method, path in (("get", "/api/v3/notifications"), ("get", "/api/v3/notifications/unread-count"),
                                 ("post", "/api/v3/notifications/read-all"), ("get", "/api/v3/me/notification-settings")):
                assert getattr(client, method)(path).status_code == 401, path


class TestAccountErasure:
    async def test_erasing_an_account_deletes_its_inbox_and_settings_and_only_its_own(self, container) -> None:
        from api.admin_platform import _erase_core

        await container.inbox.notify(ALICE, "run_failed", {})
        await container.inbox.notify(BOB, "run_failed", {})
        await container.notification_settings.put(ALICE, NotificationSettings(disabled_kinds=frozenset({"run_failed"})), at=T0)
        await container.notification_settings.put(BOB, NotificationSettings(disabled_kinds=frozenset({"renewal_due"})), at=T0)

        counts, failed = await _erase_core(container, ALICE.value, set())

        assert counts["notifications"] == 1 and counts["notification_settings"] is True and not failed
        assert await container.notification_store.list(ALICE) == [] and await container.notification_settings.get(ALICE) is None
        assert len(await container.notification_store.list(BOB)) == 1, "erasure reached another account"
        assert (await container.notification_settings.get(BOB)).disabled_kinds == {"renewal_due"}
