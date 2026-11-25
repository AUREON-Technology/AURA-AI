"""Per-user review profile (ADR-041): ``strict`` / ``standard`` / ``autonomous``.

The properties that matter, each pinned through the real policy, the real registry and the real
engineer tools -- not a stub of the policy:

* ``standard`` is exactly what ran before the profile existed;
* ``strict`` is never looser than ``standard`` and does what it says (every side effect asks,
  network tools are refused);
* ``autonomous`` is never looser than ``standard`` and never skips a taint, remote-origin,
  tool-declared or non-workspace-local confirmation, whatever the profile says;
* the profile a user stored reaches a real kernel turn and changes what that turn's tool call does.
"""

from __future__ import annotations

import itertools

import pytest
from fastapi.testclient import TestClient

from aura_core.adapters.coding_fakes import InMemoryGitHubClient, InMemorySandboxRunner
from aura_core.api import Principal, build_app
from aura_core.container import Container
from aura_core.config.settings import load_settings
from aura_core.domain.identifiers import PlanId, StepId, ToolCallId, UserId
from aura_core.domain.messages import ToolCall
from aura_core.domain.planning import Goal, Plan, PlanStep, StepKind
from aura_core.domain.tools import NETWORK_TAG, ToolSensitivity, ToolSpec
from aura_core.kernel import TurnRequest
from aura_core.ports.runtime import ToolContext
from aura_core.tools.coding import build_github_tools, build_sandbox_tools
from aura_core.tools.policy import AUTONOMY_PROFILES, ToolPolicy
from aura_core.tools.registry import ToolRegistry

CAPS = ("engineer_agent",)
ALICE = UserId("alice")


class ScriptedPlanner:
    """A planner that always plans the one tool step it was given -- the *planning* model is the
    only thing replaced; the executor, registry, policy and tools a turn then runs are the real
    ones. (A real planner needs a real model; the echo provider cannot plan.)"""

    def __init__(self, tool: str, arguments: dict | None = None) -> None:
        self._tool = tool
        self._arguments = dict(arguments or {})

    def needs_plan(self, message, *, available_tools=(), force=None, previous_reply=None) -> bool:
        return True

    @staticmethod
    def goal_from_message(message, *, criteria=()) -> Goal:
        return Goal(description=message.strip(), success_criteria=tuple(criteria))

    async def plan(self, goal, *, available_tools=(), max_steps=None) -> Plan:
        step = PlanStep(id=StepId("s1"), kind=StepKind.TOOL, description="scripted step",
                        tool=self._tool, arguments=self._arguments)
        return Plan(id=PlanId.new(), goal=goal, steps=(step,))

    def can_replan(self, plan, *, limit=None) -> bool:
        return False


def _ctx(autonomy: str = "standard", **kw) -> ToolContext:
    return ToolContext(user_id=ALICE, correlation_id="c1", deadline_s=30.0, autonomy=autonomy, **kw)


def _call(name: str, **arguments) -> ToolCall:
    return ToolCall(id=ToolCallId.new(), name=name, arguments=arguments)


def _engineer_registry(**policy_kw) -> ToolRegistry:
    registry = ToolRegistry(policy=ToolPolicy(**policy_kw))
    registry.register_all(build_sandbox_tools(InMemorySandboxRunner()))
    registry.register_all(build_github_tools(lambda _ctx: _client(), default_repo="acme/app"))
    return registry


async def _client():
    return InMemoryGitHubClient()


# --------------------------------------------------------------------------------------
# The policy matrix: strict and autonomous can only be stricter-or-equal
# --------------------------------------------------------------------------------------

_SPECS = [
    ToolSpec(name="plain_read", description="d"),
    ToolSpec(name="net_read", description="d", tags=(NETWORK_TAG,)),
    ToolSpec(name="local_write", description="d", side_effecting=True, workspace_local=True,
             sensitivity=ToolSensitivity.PERSONAL),
    ToolSpec(name="declared_local", description="d", side_effecting=True, workspace_local=True,
             requires_confirmation=True),
    ToolSpec(name="personal_write", description="d", side_effecting=True,
             sensitivity=ToolSensitivity.PERSONAL),
    ToolSpec(name="pusher", description="d", side_effecting=True,
             sensitivity=ToolSensitivity.CONFIDENTIAL, tags=(NETWORK_TAG,)),
]
_SITUATIONS = [
    dict(tainted=False, host_routed=False, origin=None),
    dict(tainted=True, host_routed=False, origin=None),
    dict(tainted=False, host_routed=True, origin=None),
    dict(tainted=True, host_routed=True, origin=None),
    dict(tainted=False, host_routed=True, origin="remote"),
]


def _rank(decision) -> int:
    """deny > confirm > allow."""
    if not decision.allowed:
        return 2
    return 1 if decision.requires_confirmation else 0


def _decide(policy: ToolPolicy, spec: ToolSpec, profile: str, situation: dict, confirmed: bool = False):
    context = _ctx(profile, tainted=situation["tainted"], metadata={"origin": situation["origin"]})
    return policy.evaluate(
        spec, context=context, capabilities=(), confirmed=confirmed, host_routed=situation["host_routed"],
    )


class TestMatrix:
    @pytest.mark.parametrize("spec,situation", list(itertools.product(_SPECS, _SITUATIONS)))
    def test_standard_is_exactly_the_policy_without_profiles(self, spec, situation) -> None:
        with_profile = _decide(ToolPolicy(allow_autonomous=True), spec, "standard", situation)
        baseline = ToolPolicy().evaluate(
            spec, context=ToolContext(user_id=ALICE, correlation_id="c", deadline_s=1.0,
                                      tainted=situation["tainted"],
                                      metadata={"origin": situation["origin"]}),
            host_routed=situation["host_routed"],
        )
        assert (with_profile.allowed, with_profile.requires_confirmation, with_profile.reason) == (
            baseline.allowed, baseline.requires_confirmation, baseline.reason)

    @pytest.mark.parametrize("profile", ["strict", "autonomous"])
    @pytest.mark.parametrize("confirmed", [False, True])
    @pytest.mark.parametrize("spec,situation", list(itertools.product(_SPECS, _SITUATIONS)))
    def test_no_profile_is_ever_looser_than_standard(self, profile, confirmed, spec, situation) -> None:
        policy = ToolPolicy(allow_autonomous=True)
        standard = _decide(policy, spec, "standard", situation, confirmed)
        chosen = _decide(policy, spec, profile, situation, confirmed)
        assert _rank(chosen) >= _rank(standard), (profile, spec.name, situation, confirmed)

    @pytest.mark.parametrize("spec,situation", list(itertools.product(_SPECS, _SITUATIONS)))
    def test_autonomous_changes_nothing_today(self, spec, situation) -> None:
        """On the shipped tool set there is no confirmation it may waive -- stated as a test so
        the day one is added, this is where somebody has to decide it is safe."""
        policy = ToolPolicy(allow_autonomous=True)
        a = _decide(policy, spec, "autonomous", situation)
        s = _decide(policy, spec, "standard", situation)
        assert (a.allowed, a.requires_confirmation) == (s.allowed, s.requires_confirmation)

    def test_autonomous_is_standard_where_the_deployment_has_not_enabled_it(self) -> None:
        policy = ToolPolicy()  # allow_autonomous defaults to off
        assert policy.effective_autonomy(_ctx("autonomous")) == "standard"
        assert ToolPolicy(allow_autonomous=True).effective_autonomy(_ctx("autonomous")) == "autonomous"

    def test_an_unknown_profile_value_means_standard_never_something_looser(self) -> None:
        assert ToolPolicy(allow_autonomous=True).effective_autonomy(_ctx("yolo")) == "standard"

    def test_autonomous_never_skips_a_taint_remote_or_declared_confirmation(self) -> None:
        policy = ToolPolicy(allow_autonomous=True)
        local = _SPECS[2]
        tainted = _decide(policy, local, "autonomous", dict(tainted=True, host_routed=True, origin=None))
        remote = _decide(policy, local, "autonomous", dict(tainted=False, host_routed=True, origin="remote"))
        declared = _decide(policy, _SPECS[3], "autonomous", _SITUATIONS[0])
        push = _decide(policy, _SPECS[5], "autonomous", _SITUATIONS[0])
        assert all(d.requires_confirmation for d in (tainted, remote, declared, push))

    def test_strict_denies_network_tools_even_when_confirmed(self) -> None:
        decision = _decide(ToolPolicy(), _SPECS[1], "strict", _SITUATIONS[0], confirmed=True)
        assert not decision.allowed and decision.reason == "autonomy_strict_denies_network"

    def test_strict_asks_before_a_workspace_local_write_that_standard_runs(self) -> None:
        local = _SPECS[2]
        assert not _decide(ToolPolicy(), local, "standard", _SITUATIONS[0]).requires_confirmation
        strict = _decide(ToolPolicy(), local, "strict", _SITUATIONS[0])
        assert strict.requires_confirmation and strict.reason == "autonomy_strict_side_effect"
        assert not _decide(ToolPolicy(), local, "strict", _SITUATIONS[0], confirmed=True).requires_confirmation

    def test_strict_leaves_reads_alone(self) -> None:
        decision = _decide(ToolPolicy(), _SPECS[0], "strict", _SITUATIONS[0])
        assert decision.allowed and not decision.requires_confirmation

    def test_the_known_profiles(self) -> None:
        assert AUTONOMY_PROFILES == ("strict", "standard", "autonomous")


# --------------------------------------------------------------------------------------
# The real registry with the real engineer tools
# --------------------------------------------------------------------------------------


class TestRealTools:
    async def test_strict_stops_for_a_write_that_standard_performs(self) -> None:
        registry = _engineer_registry()
        standard, _ = await registry.execute(
            _call("write_file", path="a.txt", content="x"), _ctx("standard"), capabilities=CAPS)
        assert standard.ok
        result, report = await registry.execute(
            _call("write_file", path="b.txt", content="x"), _ctx("strict"), capabilities=CAPS)
        assert report.needs_confirmation and report.policy_reason == "autonomy_strict_side_effect"
        assert not result.ok
        # ...and the approved call then runs.
        done, _ = await registry.execute(
            _call("write_file", path="b.txt", content="x"), _ctx("strict"), capabilities=CAPS, confirmed=True)
        assert done.ok

    async def test_strict_refuses_a_forge_read_that_standard_allows(self) -> None:
        registry = _engineer_registry()
        ok, _ = await registry.execute(
            _call("github_get_issue", number=1, repo="acme/app"), _ctx("standard"), capabilities=CAPS)
        assert "not permitted" not in ok.content
        result, report = await registry.execute(
            _call("github_get_issue", number=1, repo="acme/app"), _ctx("strict"), capabilities=CAPS)
        assert not report.allowed and report.policy_reason == "autonomy_strict_denies_network"

    async def test_strict_lets_a_read_only_workspace_tool_run(self) -> None:
        registry = _engineer_registry()
        result, report = await registry.execute(
            _call("git_status"), _ctx("strict"), capabilities=CAPS)
        assert report.allowed and not report.needs_confirmation

    async def test_every_network_tool_is_tagged(self) -> None:
        """Strict's promise is only as good as the tag: these are the tools that reach out."""
        registry = _engineer_registry()
        tagged = {n for n, t in registry._tools.items() if NETWORK_TAG in t.spec.tags}
        assert {"github_get_issue", "github_commit_files", "github_create_draft_pull_request"} <= tagged
        assert not {"write_file", "run_command", "git_status"} & tagged


# --------------------------------------------------------------------------------------
# The API and a real kernel turn
# --------------------------------------------------------------------------------------


def _app_client(container, user: str = "alice", *, service: bool = False) -> TestClient:
    async def authenticator(_request):
        key = None
        if service:
            from aura_core.domain.service_keys import ServiceKey, token_digest
            from datetime import datetime, timezone

            key = ServiceKey(key_id="0123456789abcdef", name="svc", digest=token_digest("t"),
                             created_at=datetime(2026, 1, 1, tzinfo=timezone.utc), created_by="ops")
        return Principal(user_id=UserId(user), role="Developer", capabilities=CAPS, service_key=key)

    return TestClient(build_app(container, authenticator=authenticator))


class TestApi:
    def test_default_then_set_then_read_back(self, container) -> None:
        with _app_client(container) as client:
            first = client.get("/api/v3/engineering/policy").json()
            assert first["profile"] == "standard" and first["source"] == "default"
            assert first["profiles"] == ["strict", "standard", "autonomous"]
            put = client.put("/api/v3/engineering/policy", json={"profile": "strict"})
            assert put.status_code == 200 and put.json()["profile"] == "strict"
            assert client.get("/api/v3/engineering/policy").json()["source"] == "user"

    def test_an_unknown_profile_is_422(self, container) -> None:
        with _app_client(container) as client:
            assert client.put("/api/v3/engineering/policy", json={"profile": "reckless"}).status_code == 422

    def test_autonomous_is_refused_where_the_operator_has_not_enabled_it(self, container) -> None:
        with _app_client(container) as client:
            response = client.put("/api/v3/engineering/policy", json={"profile": "autonomous"})
            assert response.status_code == 409 and response.json()["error"] == "autonomy_not_enabled"
            assert client.get("/api/v3/engineering/policy").json()["profile"] == "standard"

    def test_autonomous_is_accepted_when_enabled_and_reported_as_effective(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("AURA_AUTONOMY_ALLOW_AUTONOMOUS", "true")
        enabled = Container(load_settings(refresh=True, validate=False))
        with _app_client(enabled) as client:
            body = client.put("/api/v3/engineering/policy", json={"profile": "autonomous"}).json()
            assert body["profile"] == "autonomous" and body["effective"] == "autonomous"
            assert body["autonomous_enabled"] is True

    def test_the_profile_is_one_persons_own(self, container) -> None:
        with _app_client(container, "alice") as alice, _app_client(container, "bob") as bob:
            alice.put("/api/v3/engineering/policy", json={"profile": "strict"})
            assert bob.get("/api/v3/engineering/policy").json()["profile"] == "standard"
            assert alice.get("/api/v3/engineering/policy").json()["profile"] == "strict"

    def test_a_service_key_has_no_profile(self, container) -> None:
        with _app_client(container, service=True) as client:
            assert client.get("/api/v3/engineering/policy").status_code in (403, 404)
            assert client.put("/api/v3/engineering/policy", json={"profile": "strict"}).status_code in (403, 404)

    async def test_a_change_is_audited_with_before_and_after(self, container) -> None:
        recorded: list = []
        original = container.audit.record

        async def spy(event):
            recorded.append(event)
            return await original(event)

        container.audit.record = spy  # observe the real store's writes; it still stores them
        with _app_client(container) as client:
            client.put("/api/v3/engineering/policy", json={"profile": "strict"})
        changes = [e for e in recorded if e.action == "autonomy.profile_changed"]
        assert len(changes) == 1
        assert changes[0].detail == {"from": "standard", "to": "strict"}
        assert changes[0].resource == "user:alice"


class TestRealKernelTurn:
    async def _turn(self, container, user: UserId):
        await container.startup()
        # The default test container has no server-side sandbox (that needs Docker), so a write
        # would fail on arrival; give its registry the real tools over a real-directory runner.
        self.runner = InMemorySandboxRunner()
        for tool in build_sandbox_tools(self.runner):
            container.tools.register(tool, overwrite=True)
        container.kernel._deps.planner = ScriptedPlanner(
            "write_file", {"path": "note.txt", "content": "hello"})
        return await container.kernel.run(TurnRequest(
            message="write a note", user_id=user, capabilities=CAPS))

    async def test_standard_writes_without_asking(self, container) -> None:
        result = await self._turn(container, UserId("std-user"))
        assert result.tool_calls == ("write_file",) and result.needs_confirmation == ()
        (root,) = self.runner._scratch_dirs.values()
        assert (root / "note.txt").read_text() == "hello", "the write really happened"

    async def test_the_stored_strict_profile_holds_the_same_write_for_confirmation(self, container) -> None:
        user = UserId("strict-user")
        await container.autonomy.put(user, "strict", at="2026-11-23T00:00:00+00:00")
        result = await self._turn(container, user)
        assert result.needs_confirmation == ("write_file",)
        assert result.pending_tool_calls, "the held call is offered to the user for approval"
        assert not self.runner._scratch_dirs or not any(
            (root / "note.txt").exists() for root in self.runner._scratch_dirs.values()), "and it did not run"

    async def test_an_unreadable_profile_store_does_not_fail_the_turn(self, container) -> None:
        class Broken:
            async def get(self, _user):
                raise RuntimeError("cache down")

        container.kernel._deps.autonomy = Broken()
        result = await self._turn(container, UserId("other"))
        assert result.tool_calls == ("write_file",)
