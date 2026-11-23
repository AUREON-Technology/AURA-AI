"""The approvals record and its routes: ``/api/v3/approvals`` (migration 0037).

Three levels, like ``test_confirmation_binding.py`` which pins the mechanism this records:

* the ledger's own rules (owner scoping, a decision is never rewritten, ``expired`` is derived);
* the confirmation store writing to it (hold, approve, reject) -- and never depending on it;
* the HTTP routes over the real kernel: a turn really held, listed, refused, and an approved one.

Nothing internal is mocked: the in-memory ledger and cache are the real substrate, the registry and
policy are the real ones, and the planner is replaced only to script what "the model" plans.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from aura_core.adapters.approvals import InMemoryApprovalLedger
from aura_core.domain.approvals import ApprovalStatus, ToolApproval
from aura_core.domain.errors import ConfirmationInvalidError
from aura_core.domain.identifiers import PlanId, StepId, UserId
from aura_core.domain.planning import Goal, Plan, PlanStep, StepKind, StepStatus
from aura_core.execution.confirmations import PendingCall, PendingConfirmationStore
from aura_core.llm.cache import InMemoryCache

ALICE, BOB = UserId("alice"), UserId("bob")
T0 = datetime(2026, 11, 23, 9, 0, tzinfo=timezone.utc)
HOUR = timedelta(hours=1)


def _row(id_: str, owner: UserId = ALICE, *, tool: str = "delete_record", created: datetime = T0, **kw) -> ToolApproval:
    return ToolApproval(
        id=id_, owner_id=owner, tool=tool, call_id=f"step_{id_}", digest="d" * 8,
        created_at=created, expires_at=created + HOUR, **kw,
    )


# ---------------------------------------------------------------------------------------------
# the ledger
# ---------------------------------------------------------------------------------------------


class TestLedgerRules:
    async def test_recording_the_same_held_call_twice_keeps_one_row(self) -> None:
        ledger = InMemoryApprovalLedger()
        await ledger.record_held([_row("a")])
        await ledger.record_held([_row("a", tool="something_else")])
        assert (await ledger.get(ALICE, "a")).tool == "delete_record"

    async def test_every_statement_is_scoped_to_the_owner(self) -> None:
        ledger = InMemoryApprovalLedger()
        await ledger.record_held([_row("a")])

        assert await ledger.get(BOB, "a") is None
        assert await ledger.list(BOB, now=T0) == []
        assert await ledger.decide(BOB, ["a"], ApprovalStatus.APPROVED, at=T0, via="chat") == 0
        assert (await ledger.get(ALICE, "a")).status is ApprovalStatus.PENDING, "Bob decided Alice's"
        assert await ledger.delete_for_owner(BOB) == 0 and await ledger.get(ALICE, "a") is not None
        assert await ledger.delete_for_owner(ALICE) == 1 and await ledger.get(ALICE, "a") is None

    async def test_a_decision_is_never_rewritten(self) -> None:
        ledger = InMemoryApprovalLedger()
        await ledger.record_held([_row("a")])
        assert await ledger.decide(ALICE, ["a"], ApprovalStatus.REJECTED, at=T0, via="reject") == 1
        assert await ledger.decide(ALICE, ["a"], ApprovalStatus.APPROVED, at=T0 + HOUR, via="chat") == 0

        row = await ledger.get(ALICE, "a")
        assert (row.status, row.decided_via, row.decided_at) == (ApprovalStatus.REJECTED, "reject", T0)

    async def test_expired_is_derived_from_pending_and_the_clock_never_stored(self) -> None:
        ledger = InMemoryApprovalLedger()
        await ledger.record_held([_row("a"), _row("b", created=T0 - 2 * HOUR)])
        await ledger.decide(ALICE, ["a"], ApprovalStatus.APPROVED, at=T0, via="chat")

        pending = [r.id for r in await ledger.list(ALICE, now=T0 + timedelta(minutes=5), status=ApprovalStatus.PENDING)]
        expired = [r.id for r in await ledger.list(ALICE, now=T0 + timedelta(minutes=5), status=ApprovalStatus.EXPIRED)]
        assert pending == [] and expired == ["b"]
        # An approved call does not "expire" when its hour passes.
        assert (await ledger.get(ALICE, "a")).effective_status(T0 + 5 * HOUR) is ApprovalStatus.APPROVED
        with pytest.raises(ValueError):
            _row("c", status=ApprovalStatus.EXPIRED)

    async def test_listing_is_newest_first_filtered_and_paged_without_gaps_or_repeats(self) -> None:
        ledger = InMemoryApprovalLedger()
        rows = [_row(f"r{i}", tool="send_message" if i % 2 else "delete_record", created=T0 + timedelta(minutes=i)) for i in range(7)]
        await ledger.record_held(rows)
        now = T0 + timedelta(minutes=10)

        assert [r.id for r in await ledger.list(ALICE, now=now, limit=3)] == ["r6", "r5", "r4"]
        assert [r.id for r in await ledger.list(ALICE, now=now, tool="send_message")] == ["r5", "r3", "r1"]
        assert [r.id for r in await ledger.list(ALICE, now=now, since=T0 + timedelta(minutes=2), until=T0 + timedelta(minutes=5))] == ["r4", "r3", "r2"]
        seen, before = [], None
        while True:
            page = await ledger.list(ALICE, now=now, limit=3, before=before)
            if not page:
                break
            seen += [r.id for r in page]
            before = (page[-1].created_at, page[-1].id)
        assert seen == [f"r{i}" for i in range(6, -1, -1)]

    async def test_rows_created_at_the_same_instant_page_by_id(self) -> None:
        ledger = InMemoryApprovalLedger()
        await ledger.record_held([_row(i) for i in ("a", "b", "c")])
        first = await ledger.list(ALICE, now=T0, limit=2)
        rest = await ledger.list(ALICE, now=T0, limit=2, before=(first[-1].created_at, first[-1].id))
        assert [r.id for r in first] == ["c", "b"] and [r.id for r in rest] == ["a"]


# ---------------------------------------------------------------------------------------------
# the confirmation store writing to it
# ---------------------------------------------------------------------------------------------


def _plan(*steps: PlanStep) -> Plan:
    return Plan(id=PlanId.new(), goal=Goal(description="g"), steps=tuple(steps))


def _held(step_id: str, tool: str, target: str) -> PlanStep:
    return PlanStep(
        id=StepId(step_id), kind=StepKind.TOOL, tool=tool, description=f"{tool} {target}",
        arguments={"target": target},
    ).blocked("needs_confirmation")


async def _hold(store: PendingConfirmationStore, *, user: UserId = ALICE, session: str | None = "s-1") -> dict:
    return await store.issue(
        user_id=user.value, session_id=session, agent="orchestrator", message="do it",
        plan=_plan(_held("s1", "delete_record", "SECRET-ARGUMENT"), _held("s2", "send_message", "bob")),
        tainted=False, turn_id="t-1",
        calls=[
            PendingCall("s1", "step_s1", "delete_record", {"target": "SECRET-ARGUMENT"}),
            PendingCall("s2", "step_s2", "send_message", {"target": "bob"}),
        ],
    )


@pytest.fixture
def clock():
    now = [T0]

    class _Clock:
        def __call__(self) -> datetime:
            return now[0]

        def advance(self, delta: timedelta) -> None:
            now[0] += delta

    return _Clock()


@pytest.fixture
def ledger() -> InMemoryApprovalLedger:
    return InMemoryApprovalLedger()


@pytest.fixture
def store(ledger, clock) -> PendingConfirmationStore:
    return PendingConfirmationStore(InMemoryCache(), ledger=ledger, clock=clock)


class TestHolding:
    async def test_each_held_call_becomes_a_pending_row_with_its_id_tool_and_hour(self, store, ledger) -> None:
        ids = await _hold(store)

        rows = {r.call_id: r for r in await ledger.list(ALICE, now=T0)}
        assert set(rows) == {"step_s1", "step_s2"}
        assert rows["step_s1"].id == ids["step_s1"] and rows["step_s1"].tool == "delete_record"
        assert rows["step_s1"].status is ApprovalStatus.PENDING and rows["step_s1"].decided_at is None
        assert (rows["step_s1"].session_id, rows["step_s1"].turn_id) == ("s-1", "t-1")
        assert rows["step_s1"].expires_at - rows["step_s1"].created_at == HOUR

    async def test_a_row_holds_no_arguments_and_no_message(self, store, ledger) -> None:
        await _hold(store)
        for row in await ledger.list(ALICE, now=T0):
            blob = repr(row) + repr(row.as_dict(T0))
            assert "SECRET-ARGUMENT" not in blob and "do it" not in blob, blob

    async def test_the_digest_is_the_one_the_cache_binds_the_call_to(self, store, ledger) -> None:
        from aura_core.execution.confirmations import call_digest

        ids = await _hold(store)
        row = await ledger.get(ALICE, ids["step_s1"])
        token = ids["step_s1"].split(".")[1]
        assert row.digest == call_digest("delete_record", {"target": "SECRET-ARGUMENT"}, binding=token)


class TestApproving:
    async def test_the_approved_call_is_approved_and_the_others_of_the_turn_superseded(self, store, ledger, clock) -> None:
        ids = await _hold(store)
        clock.advance(timedelta(minutes=3))

        await store.claim([ids["step_s1"]], user_id=ALICE.value, session_id="s-1")

        approved, other = await ledger.get(ALICE, ids["step_s1"]), await ledger.get(ALICE, ids["step_s2"])
        assert (approved.status, approved.decided_via, approved.decided_at) == (ApprovalStatus.APPROVED, "chat", T0 + timedelta(minutes=3))
        assert (other.status, other.decided_via) == (ApprovalStatus.SUPERSEDED, "chat")

    async def test_a_refused_claim_decides_nothing(self, store, ledger) -> None:
        ids = await _hold(store)
        with pytest.raises(ConfirmationInvalidError):
            await store.claim([ids["step_s1"]], user_id=BOB.value, session_id="s-1")
        with pytest.raises(ConfirmationInvalidError):
            await store.claim([ids["step_s1"]], user_id=ALICE.value, session_id="another-chat")

        assert {r.status for r in await ledger.list(ALICE, now=T0)} == {ApprovalStatus.PENDING}


class TestRejecting:
    async def test_it_discards_the_whole_turn_and_records_every_call_rejected(self, store, ledger, clock) -> None:
        ids = await _hold(store)
        clock.advance(timedelta(minutes=2))

        await store.reject(ids["step_s1"], user_id=ALICE.value, session_id="s-1")

        rows = await ledger.list(ALICE, now=clock())
        assert {(r.status, r.decided_via, r.decided_at) for r in rows} == {
            (ApprovalStatus.REJECTED, "reject", T0 + timedelta(minutes=2))
        }
        # Neither call can be approved afterwards: the held turn is gone.
        for approval_id in ids.values():
            with pytest.raises(ConfirmationInvalidError):
                await store.claim([approval_id], user_id=ALICE.value, session_id="s-1")

    async def test_an_approved_turn_cannot_then_be_rejected_and_the_reverse(self, store, ledger) -> None:
        ids = await _hold(store)
        await store.claim([ids["step_s1"]], user_id=ALICE.value, session_id="s-1")
        with pytest.raises(ConfirmationInvalidError):
            await store.reject(ids["step_s1"], user_id=ALICE.value, session_id="s-1")
        assert (await ledger.get(ALICE, ids["step_s1"])).status is ApprovalStatus.APPROVED

    async def test_someone_else_cannot_reject_it_and_it_stays_approvable(self, store, ledger) -> None:
        ids = await _hold(store)
        with pytest.raises(ConfirmationInvalidError):
            await store.reject(ids["step_s1"], user_id=BOB.value, session_id="s-1")
        with pytest.raises(ConfirmationInvalidError):
            await store.reject(ids["step_s1"], user_id=ALICE.value, session_id="another-chat")

        assert {r.status for r in await ledger.list(ALICE, now=T0)} == {ApprovalStatus.PENDING}
        await store.claim([ids["step_s1"]], user_id=ALICE.value, session_id="s-1")  # still works

    async def test_a_forged_or_unknown_id_is_refused(self, store) -> None:
        for forged in ("apr.nope.nope", "not-an-approval", ""):
            with pytest.raises(ConfirmationInvalidError):
                await store.reject(forged, user_id=ALICE.value, session_id="s-1")


class _BrokenLedger:
    async def record_held(self, approvals):
        raise ConnectionError("db down")

    async def decide(self, *args, **kwargs):
        raise ConnectionError("db down")


class TestTheLedgerIsHistoryNotTheGuarantee:
    async def test_a_ledger_that_is_down_never_stops_a_hold_an_approval_or_a_rejection(self, clock) -> None:
        store = PendingConfirmationStore(InMemoryCache(), ledger=_BrokenLedger(), clock=clock)

        ids = await _hold(store)
        assert set(ids) == {"step_s1", "step_s2"}
        resumed = await store.claim([ids["step_s1"]], user_id=ALICE.value, session_id="s-1")
        assert resumed.approved_call_ids == ("step_s1",)

        again = await _hold(store)
        await store.reject(again["step_s1"], user_id=ALICE.value, session_id="s-1")
        with pytest.raises(ConfirmationInvalidError):
            await store.claim([again["step_s1"]], user_id=ALICE.value, session_id="s-1")

    async def test_without_a_ledger_the_store_behaves_as_before(self) -> None:
        store = PendingConfirmationStore(InMemoryCache())
        ids = await _hold(store)
        await store.claim([ids["step_s1"]], user_id=ALICE.value, session_id="s-1")


# ---------------------------------------------------------------------------------------------
# the routes, over the real kernel
# ---------------------------------------------------------------------------------------------


class _Ran:
    def __init__(self) -> None:
        self.targets: list[str] = []


class _Planner:
    """Stands in for the model's planning output (the part an attacker can steer)."""

    def __init__(self, plan: Plan) -> None:
        self.plan_to_return = plan

    def needs_plan(self, message, *, available_tools=(), force=None, previous_reply=None) -> bool:
        return True

    def goal_from_message(self, message, *, criteria=()):
        return Goal(description=message)

    async def plan(self, goal, available_tools=()):
        return self.plan_to_return


def _app(container):
    from aura_core.api import Principal, build_app
    from aura_core.domain.errors import AuthenticationError

    async def authenticate(request) -> Principal:
        header = request.headers.get("authorization", "")
        if not header.startswith("Bearer "):
            raise AuthenticationError("missing")
        return Principal(user_id=UserId(header[7:]), email="")

    return build_app(container, authenticator=authenticate)


def _hold_turn(container, ran: _Ran, target: str = "SECRET-TARGET") -> dict:
    from aura_core.domain.tools import ParameterSpec, ToolCategory, ToolSensitivity, ToolSpec
    from aura_core.tools.base import FunctionTool

    async def handler(target: str, context) -> str:
        ran.targets.append(target)
        return f"deleted {target}"

    spec = ToolSpec(
        name="delete_record", description="Always requires confirmation.",
        parameters=(ParameterSpec(name="target", type="string", description="target"),),
        category=ToolCategory.UTILITY, sensitivity=ToolSensitivity.CONFIDENTIAL, side_effecting=True,
    )
    if not any(s.name == "delete_record" for s in container.tools.available()):
        container.tools.register(FunctionTool(spec, handler))
    step = PlanStep(
        id=StepId("s1"), kind=StepKind.TOOL, tool="delete_record", description=f"delete {target}",
        arguments={"target": target},
    )
    container.kernel._deps.planner = _Planner(_plan(step))
    return {"message": "delete the record", "session_id": "conv-1", "plan": True}


def _as(user: str) -> dict:
    return {"Authorization": f"Bearer {user}"}


class TestRoutes:
    def test_a_held_turn_is_listed_pending_without_its_arguments_and_can_be_read_by_id(self, container) -> None:
        from fastapi.testclient import TestClient

        ran = _Ran()
        turn = _hold_turn(container, ran)
        with TestClient(_app(container)) as client:
            held = client.post("/api/v3/chat", json=turn, headers=_as("alice"))
            (pending,) = held.json()["pending_tool_calls"]

            listing = client.get("/api/v3/approvals", headers=_as("alice"))
            one = client.get(f"/api/v3/approvals/{pending['approval_id']}", headers=_as("alice"))

        assert listing.status_code == 200
        (row,) = listing.json()["approvals"]
        assert (row["approval_id"], row["status"], row["tool"], row["session_id"]) == (
            pending["approval_id"], "pending", "delete_record", "conv-1",
        )
        assert one.json() == row
        assert "SECRET-TARGET" not in listing.text and "SECRET-TARGET" not in one.text
        assert ran.targets == []

    def test_approving_through_chat_moves_the_row_to_approved_and_it_stays_in_the_history(self, container) -> None:
        from fastapi.testclient import TestClient

        ran = _Ran()
        turn = _hold_turn(container, ran)
        with TestClient(_app(container)) as client:
            (pending,) = client.post("/api/v3/chat", json=turn, headers=_as("alice")).json()["pending_tool_calls"]
            approved = client.post(
                "/api/v3/chat", json={**turn, "message": "yes", "approval_ids": [pending["approval_id"]]},
                headers=_as("alice"),
            )
            assert approved.status_code == 200 and ran.targets == ["SECRET-TARGET"]

            history = client.get("/api/v3/approvals", params={"status": "approved"}, headers=_as("alice")).json()
            still_pending = client.get("/api/v3/approvals", params={"status": "pending"}, headers=_as("alice")).json()

        (row,) = history["approvals"]
        assert (row["status"], row["decided_via"]) == ("approved", "chat") and row["decided_at"]
        assert still_pending["approvals"] == []

    def test_rejecting_discards_the_turn_nothing_runs_and_a_later_approval_is_a_409(self, container) -> None:
        from fastapi.testclient import TestClient

        ran = _Ran()
        turn = _hold_turn(container, ran)
        with TestClient(_app(container)) as client:
            (pending,) = client.post("/api/v3/chat", json=turn, headers=_as("alice")).json()["pending_tool_calls"]
            approval_id = pending["approval_id"]

            rejected = client.post(f"/api/v3/approvals/{approval_id}/reject", headers=_as("alice"))
            assert rejected.status_code == 200
            assert (rejected.json()["status"], rejected.json()["decided_via"]) == ("rejected", "reject")

            late = client.post("/api/v3/chat", json={**turn, "message": "yes", "approval_ids": [approval_id]}, headers=_as("alice"))
            again = client.post(f"/api/v3/approvals/{approval_id}/reject", headers=_as("alice"))
            history = client.get("/api/v3/approvals", params={"status": "rejected"}, headers=_as("alice")).json()

        assert late.status_code == 409 and late.json()["error"] == "confirmation_invalid"
        assert again.status_code == 409 and again.json()["details"]["reason"] == "not_pending"
        assert ran.targets == [], "a rejected call ran"
        assert [r["approval_id"] for r in history["approvals"]] == [approval_id]

    def test_someone_else_sees_nothing_and_cannot_reject_and_the_owner_can_still_approve(self, container) -> None:
        from fastapi.testclient import TestClient

        ran = _Ran()
        turn = _hold_turn(container, ran)
        with TestClient(_app(container)) as client:
            (pending,) = client.post("/api/v3/chat", json=turn, headers=_as("alice")).json()["pending_tool_calls"]
            approval_id = pending["approval_id"]

            assert client.get("/api/v3/approvals", headers=_as("bob")).json()["approvals"] == []
            assert client.get(f"/api/v3/approvals/{approval_id}", headers=_as("bob")).status_code == 404
            assert client.post(f"/api/v3/approvals/{approval_id}/reject", headers=_as("bob")).status_code == 404
            missing = client.post("/api/v3/approvals/apr.no.such/reject", headers=_as("bob"))
            assert missing.status_code == 404, "another person's id must answer like a missing one"

            approved = client.post(
                "/api/v3/chat", json={**turn, "message": "yes", "approval_ids": [approval_id]}, headers=_as("alice"),
            )
        assert approved.status_code == 200 and ran.targets == ["SECRET-TARGET"], "Bob's attempts spoiled Alice's approval"

    def test_a_hold_past_its_hour_is_expired_and_cannot_be_rejected(self, container) -> None:
        import asyncio

        from fastapi.testclient import TestClient

        old = datetime.now(timezone.utc) - 3 * HOUR
        asyncio.run(container.approval_ledger.record_held([_row("apr.old.one", created=old)]))
        with TestClient(_app(container)) as client:
            listing = client.get("/api/v3/approvals", params={"status": "expired"}, headers=_as("alice")).json()
            pending = client.get("/api/v3/approvals", params={"status": "pending"}, headers=_as("alice")).json()
            refused = client.post("/api/v3/approvals/apr.old.one/reject", headers=_as("alice"))

        assert [r["approval_id"] for r in listing["approvals"]] == ["apr.old.one"]
        assert listing["approvals"][0]["status"] == "expired" and pending["approvals"] == []
        assert refused.status_code == 409 and refused.json()["details"] == {"reason": "not_pending", "status": "expired"}

    def test_a_row_whose_held_turn_is_gone_from_the_cache_is_a_409_not_a_success(self, container) -> None:
        import asyncio

        from fastapi.testclient import TestClient

        ran = _Ran()
        turn = _hold_turn(container, ran)
        with TestClient(_app(container)) as client:
            (pending,) = client.post("/api/v3/chat", json=turn, headers=_as("alice")).json()["pending_tool_calls"]
            token = pending["approval_id"].split(".")[1]
            asyncio.run(container.cache_backend.delete(f"tool-confirm:pending:v1:{token}"))

            refused = client.post(f"/api/v3/approvals/{pending['approval_id']}/reject", headers=_as("alice"))
        assert refused.status_code == 409 and refused.json()["details"]["reason"] == "no_longer_pending"

    def test_bad_filters_and_cursors_are_422_and_a_cursor_is_only_valid_for_its_own_filters(self, container) -> None:
        import asyncio

        from fastapi.testclient import TestClient

        asyncio.run(container.approval_ledger.record_held(
            [_row(f"apr.t.{i}", created=datetime.now(timezone.utc) - timedelta(minutes=i)) for i in range(5)]
        ))
        with TestClient(_app(container)) as client:
            assert client.get("/api/v3/approvals", params={"status": "maybe"}, headers=_as("alice")).status_code == 422
            assert client.get("/api/v3/approvals", params={"cursor": "garbage"}, headers=_as("alice")).status_code == 422
            first = client.get("/api/v3/approvals", params={"limit": 2}, headers=_as("alice")).json()
            second = client.get("/api/v3/approvals", params={"limit": 2, "cursor": first["next_cursor"]}, headers=_as("alice")).json()
            other_filter = client.get(
                "/api/v3/approvals", params={"limit": 2, "tool": "send_message", "cursor": first["next_cursor"]},
                headers=_as("alice"),
            )
            third = client.get("/api/v3/approvals", params={"limit": 2, "cursor": second["next_cursor"]}, headers=_as("alice")).json()

        ids = [r["approval_id"] for page in (first, second, third) for r in page["approvals"]]
        assert ids == [f"apr.t.{i}" for i in range(5)] and third["next_cursor"] is None
        assert other_filter.status_code == 422, "a cursor made for other filters would skip or repeat rows"


class TestAccountErasure:
    async def test_erasing_an_account_deletes_its_approvals_and_only_its_own(self, container) -> None:
        from api.admin_platform import _erase_core

        await container.approval_ledger.record_held([_row("a1"), _row("a2"), _row("b1", BOB)])

        counts, failed = await _erase_core(container, ALICE.value, set())

        assert counts["tool_approvals"] == 2 and "tool_approvals" not in failed
        assert await container.approval_ledger.list(ALICE, now=T0) == []
        assert [r.id for r in await container.approval_ledger.list(BOB, now=T0)] == ["b1"], "erasure reached another account"
