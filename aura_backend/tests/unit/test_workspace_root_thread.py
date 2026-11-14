"""The Desktop IDE's active workspace root reaches the daemon as `run`'s `cwd` (audit FSA-006).

The daemon has always accepted a `cwd` on `run` and refused one outside every granted root
(`aura_hostd/lib/src/task_handler.dart`), but nothing ever sent one: a user with several granted
roots had every Engineer command execute in whichever root was granted first, whatever the IDE
showed as open. The 2026-10-31 audit left this half of FSA-006 open because the backend had no
notion of an active root to send. Now it does: `ChatRequest.workspace_root` → `TurnRequest.
workspace_root` → `ToolContext.metadata["workspace_root"]` → `SandboxRunner.run(cwd=...)` →
the daemon's existing containment check.

The field is opaque to the backend on purpose. It is a path on the user's own machine, in that
machine's syntax; only the daemon can say whether it lies inside a granted root, and it already
does. Absent, everything behaves exactly as before, so a client that never sends it (mobile, an
older desktop build) changes nothing.
"""

from __future__ import annotations

import pytest

from aura_core.api import Principal, build_app
from aura_core.domain import UserId
from aura_core.kernel import TurnRequest

_ROOT = "/home/me/projects/aura"
_TURN = {"message": "list the files", "plan": False, "use_tools": False, "use_memory": False,
         "use_knowledge": False}


@pytest.fixture
def client(container):
    from fastapi.testclient import TestClient

    async def authenticate(request) -> Principal:
        return Principal(user_id=UserId("desktop-user"), email="dev@example.com")

    with TestClient(build_app(container, authenticator=authenticate)) as test_client:
        yield test_client


@pytest.fixture
def seen_turns(container, monkeypatch) -> list[TurnRequest]:
    """Records every `TurnRequest` the chat route hands the kernel, then runs it for real."""
    seen: list[TurnRequest] = []
    original = container.kernel.run

    async def recording_run(turn):
        seen.append(turn)
        return await original(turn)

    monkeypatch.setattr(container.kernel, "run", recording_run)
    return seen


class TestChatRequestCarriesTheWorkspaceRoot:
    def test_the_root_reaches_the_turn_request(self, client, seen_turns) -> None:
        response = client.post("/api/v3/chat", json={**_TURN, "workspace_root": _ROOT})

        assert response.status_code == 200, response.text
        assert [turn.workspace_root for turn in seen_turns] == [_ROOT]

    def test_absent_means_none_so_older_clients_are_unchanged(self, client, seen_turns) -> None:
        assert client.post("/api/v3/chat", json=_TURN).status_code == 200
        assert seen_turns[0].workspace_root is None

    def test_an_absurdly_long_root_is_refused_before_it_reaches_anything(
        self, client, seen_turns
    ) -> None:
        response = client.post("/api/v3/chat", json={**_TURN, "workspace_root": "/x" * 3000})

        assert response.status_code == 422
        assert seen_turns == []


class TestKernelPutsTheRootInTheToolContext:
    def test_the_root_rides_tool_context_metadata(self, container) -> None:
        request = TurnRequest(message="hi", user_id=UserId("desktop-user"), workspace_root=_ROOT)

        context = container.kernel._tool_context(request, tainted=False)

        assert context.metadata["workspace_root"] == _ROOT

    def test_no_root_is_none_not_a_missing_key(self, container) -> None:
        request = TurnRequest(message="hi", user_id=UserId("desktop-user"))

        context = container.kernel._tool_context(request, tainted=False)

        assert context.metadata["workspace_root"] is None


class TestAnApprovedTurnKeepsTheRootItWasProposedIn:
    """Approving a held call must run it where it was proposed. The resume request is built from
    whatever the client sends *now*; if the user has since opened another workspace, that root
    would win and a `git reset --hard` approved for repo A would run in repo B -- in a trusted root,
    without a second prompt. So the root is stored with the pending turn and pinned on resume, the
    same way the held turn's message and persona already are."""

    async def test_the_store_keeps_the_root_with_the_pending_turn(self) -> None:
        from aura_core.domain.identifiers import PlanId, StepId
        from aura_core.domain.planning import Goal, Plan, PlanStep, StepKind
        from aura_core.execution.confirmations import PendingCall, PendingConfirmationStore
        from aura_core.llm.cache import InMemoryCache

        held = PlanStep(
            id=StepId("s1"), kind=StepKind.TOOL, tool="run_command", description="reset",
            arguments={"command": "git reset --hard"},
        ).blocked("needs_confirmation")
        store = PendingConfirmationStore(InMemoryCache())
        ids = await store.issue(
            user_id="desktop-user", session_id="s-1", agent="engineer", message="reset it",
            plan=Plan(id=PlanId.new(), goal=Goal(description="g"), steps=(held,)), tainted=False,
            calls=[PendingCall("s1", "call_s1", "run_command", {"command": "git reset --hard"})],
            workspace_root=_ROOT,
        )

        resumed = await store.claim([ids["call_s1"]], user_id="desktop-user", session_id="s-1")

        assert resumed.workspace_root == _ROOT

    async def test_the_kernel_pins_the_stored_root_over_the_resend_s(self, container) -> None:
        from dataclasses import replace

        from aura_core.domain.identifiers import PlanId
        from aura_core.domain.planning import Goal, Plan
        from aura_core.execution.confirmations import ConfirmationGrant, ResumedTurn

        resumed = ResumedTurn(
            message="reset it", agent="engineer",
            plan=Plan(id=PlanId.new(), goal=Goal(description="g"), steps=()),
            tainted=False, grant=ConfirmationGrant({}, binding="t"), approved_call_ids=(),
            workspace_root=_ROOT,
        )
        resend = TurnRequest(
            message="approve", user_id=UserId("desktop-user"), workspace_root="/home/me/other-repo",
        )

        pinned = container.kernel._resumed_request(resend, resumed)

        assert pinned.workspace_root == _ROOT
        assert replace(pinned, workspace_root=None) == replace(
            resend, message="reset it", agent="engineer", workspace_root=None
        )
