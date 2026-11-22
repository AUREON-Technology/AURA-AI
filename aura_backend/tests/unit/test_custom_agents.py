"""Custom agents (migration 0035): a built-in persona, narrowed and given instructions by its owner.

The property that matters is that a custom agent can only ever *narrow* its base persona and only
ever belongs to one person. So these check, in order: what a field may hold (the domain), how the
join with a base persona narrows and never widens (``apply_custom_agent``), the store's rules, the
HTTP surface with two people (ownership, refusals, limits), and that a turn really runs with the
instructions and the narrower tool list (what the model was shown, via the echo provider).
The SQL is ``tests/load/test_custom_agents_postgres.py``'s.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone

import pytest
from fastapi.testclient import TestClient

from aura_core.adapters.custom_agents import InMemoryCustomAgentStore
from aura_core.agents.custom import apply_custom_agent
from aura_core.agents.registry import build_default_agent_registry
from aura_core.api import Principal, build_app
from aura_core.domain.custom_agents import (
    MAX_CUSTOM_AGENTS_PER_OWNER,
    MAX_INSTRUCTION_CHARS,
    CustomAgent,
    CustomAgentConflict,
    CustomAgentError,
    clean_description,
    clean_instruction,
    clean_name,
    clean_tools,
    custom_spec_id,
    is_custom_agent_id,
    new_custom_agent_id,
)
from aura_core.domain.errors import AuthenticationError
from aura_core.domain.identifiers import UserId
from aura_core.domain.tools import ToolSensitivity

NOW = datetime(2026, 11, 22, 12, 0, tzinfo=timezone.utc)
ALICE, BOB = {"Authorization": "Bearer alice"}, {"Authorization": "Bearer bob"}
REGISTRY = build_default_agent_registry()


def _agent(owner: str = "alice", **overrides) -> CustomAgent:
    base = dict(id=new_custom_agent_id(), owner_id=UserId(owner), name="My planner",
                base_agent_id="task_planner", created_at=NOW, updated_at=NOW)
    base.update(overrides)
    return CustomAgent(**base)


# --------------------------------------------------------------------------------------- domain


class TestFields:
    def test_a_name_is_trimmed_and_collapsed(self) -> None:
        assert clean_name("  My   planner \n") == "My planner"

    @pytest.mark.parametrize("bad", ["", "   ", None, 7, "x" * 61, "bad\x00name"])
    def test_a_name_must_be_real_text_of_bounded_length(self, bad) -> None:
        with pytest.raises(CustomAgentError) as refused:
            clean_name(bad)
        assert refused.value.field == "name"

    def test_an_instruction_keeps_its_newlines_but_not_control_characters(self) -> None:
        assert clean_instruction("  Be brief.\nAnswer in Arabic.  ") == "Be brief.\nAnswer in Arabic."
        assert clean_instruction(None) == "" and clean_instruction("") == ""
        for bad in ("x" * (MAX_INSTRUCTION_CHARS + 1), "bell\x07", 5):
            with pytest.raises(CustomAgentError):
                clean_instruction(bad)

    def test_a_description_is_bounded(self) -> None:
        assert clean_description(None) == "" and clean_description(" a  b ") == "a b"
        with pytest.raises(CustomAgentError):
            clean_description("x" * 281)

    def test_tools_are_sorted_deduplicated_and_named_like_tools(self) -> None:
        assert clean_tools(["b_tool", "a_tool", "a_tool"]) == ("a_tool", "b_tool")
        assert clean_tools(None) is None and clean_tools([]) == ()
        for bad in ("a_tool", [1], ["has space"], ["../etc"], [""], ["x"] * 0 + [f"t{i}" for i in range(65)]):
            with pytest.raises(CustomAgentError) as refused:
                clean_tools(bad)
            assert refused.value.field == "allowed_tools"

    def test_ids_tell_a_custom_agent_from_a_built_in(self) -> None:
        assert is_custom_agent_id(new_custom_agent_id())
        for builtin in ("orchestrator", "engineer", "custom:abc", "", "not-a-uuid"):
            assert not is_custom_agent_id(builtin)
        assert custom_spec_id("abc") == "custom:abc"

    def test_the_engineer_can_never_be_a_base_and_a_ceiling_must_be_a_real_one(self) -> None:
        with pytest.raises(ValueError):
            _agent(base_agent_id="engineer")
        with pytest.raises(ValueError):
            _agent(risk_ceiling="ROOT")


# ------------------------------------------------------------------------- narrowing, never widening


class TestApplyCustomAgent:
    def test_the_tool_list_is_the_intersection_with_what_the_base_permits(self) -> None:
        base = REGISTRY.get("task_planner")
        assert base.allowed_tools, "task_planner is expected to have an explicit tool set"
        granted = sorted(base.allowed_tools)[:2]
        spec = apply_custom_agent(base, _agent(allowed_tools=tuple(granted)))
        assert spec.allowed_tools == frozenset(granted)

    def test_a_tool_the_base_does_not_have_cannot_appear_even_if_it_is_stored(self) -> None:
        """A list written when the base was wider, or edited in the database: the turn re-intersects."""
        base = REGISTRY.get("task_planner")
        outside = "docker_build"
        assert not base.permits_tool(outside)
        spec = apply_custom_agent(base, _agent(allowed_tools=(sorted(base.allowed_tools)[0], outside)))
        assert outside not in spec.allowed_tools and not spec.permits_tool(outside)

    def test_on_an_unrestricted_base_a_list_becomes_exactly_that_list_with_no_family_grants(self) -> None:
        base = REGISTRY.get("orchestrator")
        assert base.allowed_tools is None
        spec = apply_custom_agent(base, _agent(base_agent_id="orchestrator", allowed_tools=("current_datetime",)))
        assert spec.allowed_tools == frozenset({"current_datetime"})
        assert spec.allowed_tool_prefixes == frozenset()
        assert spec.permits_tool("current_datetime") and not spec.permits_tool("search_knowledge")

    def test_no_list_keeps_everything_the_base_had(self) -> None:
        for base_id in ("task_planner", "orchestrator", "research_agent"):
            base = REGISTRY.get(base_id)
            spec = apply_custom_agent(base, _agent(base_agent_id=base_id))
            assert (spec.allowed_tools, spec.allowed_tool_prefixes) == (base.allowed_tools, base.allowed_tool_prefixes)

    def test_a_ceiling_can_be_lowered_and_never_raised(self) -> None:
        base = REGISTRY.get("task_planner")
        lowered = apply_custom_agent(base, _agent(risk_ceiling="PUBLIC"))
        assert lowered.risk_ceiling is ToolSensitivity.PUBLIC
        raised = apply_custom_agent(base, _agent(risk_ceiling="SYSTEM"))
        assert raised.risk_ceiling == base.risk_ceiling, "a custom agent must not exceed its base's ceiling"
        for ceiling in ("PUBLIC", "PERSONAL", "CONFIDENTIAL", "SYSTEM"):
            assert apply_custom_agent(base, _agent(risk_ceiling=ceiling)).risk_ceiling <= base.risk_ceiling

    def test_identity_is_the_agents_and_everything_else_is_the_bases(self) -> None:
        base = REGISTRY.get("task_planner")
        custom = _agent(name="Weekly planner", instruction="Plan by week.")
        spec = apply_custom_agent(base, custom)
        assert spec.id == f"custom:{custom.id}" and spec.name == "Weekly planner"
        assert spec.extra_instruction == "Plan by week."
        for inherited in ("prompt_template_id", "required_capabilities", "memory_scope", "max_plan_iterations",
                          "token_budget", "time_budget_s", "tool_budget", "risk_budget"):
            assert getattr(spec, inherited) == getattr(base, inherited), inherited
        assert base.extra_instruction == "", "the shared built-in was changed"


# ------------------------------------------------------------------------------------------ store


class TestInMemoryStore:
    async def test_it_is_scoped_to_the_owner(self) -> None:
        store = InMemoryCustomAgentStore()
        mine = await store.create(_agent("alice"), limit=5)
        assert await store.get(UserId("alice"), mine.id) == mine
        assert await store.get(UserId("bob"), mine.id) is None
        assert await store.list_for_owner(UserId("bob")) == []
        assert await store.delete(UserId("bob"), mine.id) is False
        assert await store.update(_agent("bob", id=mine.id)) is None
        assert await store.get(UserId("alice"), mine.id) is not None, "Bob's attempts changed Alice's agent"

    async def test_names_are_unique_per_owner_ignoring_case_and_not_across_owners(self) -> None:
        store = InMemoryCustomAgentStore()
        await store.create(_agent("alice", name="Planner"), limit=5)
        with pytest.raises(CustomAgentConflict) as taken:
            await store.create(_agent("alice", name="planner"), limit=5)
        assert taken.value.reason == "name"
        await store.create(_agent("bob", name="Planner"), limit=5)

    async def test_the_limit_is_enforced_at_write(self) -> None:
        store = InMemoryCustomAgentStore()
        for i in range(3):
            await store.create(_agent("alice", name=f"A{i}"), limit=3)
        with pytest.raises(CustomAgentConflict) as full:
            await store.create(_agent("alice", name="A3"), limit=3)
        assert full.value.reason == "limit"

    async def test_an_update_may_not_take_another_agents_name_but_may_keep_its_own(self) -> None:
        store = InMemoryCustomAgentStore()
        first = await store.create(_agent("alice", name="One"), limit=5)
        await store.create(_agent("alice", name="Two"), limit=5)
        from dataclasses import replace

        assert (await store.update(replace(first, name="ONE", instruction="x"))).instruction == "x"
        with pytest.raises(CustomAgentConflict):
            await store.update(replace(first, name="two"))

    async def test_delete_for_owner_removes_only_that_owners(self) -> None:
        store = InMemoryCustomAgentStore()
        await store.create(_agent("alice", name="A"), limit=5)
        await store.create(_agent("alice", name="B"), limit=5)
        kept = await store.create(_agent("bob", name="A"), limit=5)
        assert await store.delete_for_owner(UserId("alice")) == 2
        assert await store.list_for_owner(UserId("bob")) == [kept]


# ----------------------------------------------------------------------------------------- over HTTP


def _app(container):
    async def authenticator(request):
        header = request.headers.get("authorization", "")
        name = header.removeprefix("Bearer ")
        if name not in ("alice", "bob"):
            raise AuthenticationError("unknown principal")
        return Principal(user_id=UserId(name), email=f"{name}@example.com", email_verified=True)

    return build_app(container, authenticator=authenticator)


@pytest.fixture
def client(container):
    with TestClient(_app(container)) as c:
        yield c


def _create(client, who=ALICE, **body):
    body = {"name": "My planner", "base_agent": "task_planner", **body}
    return client.post("/api/v3/agents", headers=who, json=body)


class TestHttp:
    def test_create_get_list_update_configure_delete(self, client) -> None:
        created = _create(client, instruction="Plan by week.", description="Weekly plans")
        assert created.status_code == 201, created.text
        agent = created.json()
        assert agent["kind"] == "custom" and agent["base_agent"] == "task_planner"
        assert agent["instruction"] == "Plan by week." and agent["allowed_tools"] is None

        fetched = client.get(f"/api/v3/agents/{agent['id']}", headers=ALICE).json()
        assert fetched == agent

        listed = client.get("/api/v3/agents", headers=ALICE).json()
        summary = [a for a in listed["custom_agents"] if a["id"] == agent["id"]]
        assert len(summary) == 1 and "instruction" not in summary[0], "the list does not carry instruction text"
        assert {"orchestrator", "engineer"} <= {a["id"] for a in listed["agents"]}, "the built-in list is unchanged"
        assert all("custom_agents" not in a for a in listed["agents"])

        patched = client.patch(f"/api/v3/agents/{agent['id']}", headers=ALICE,
                               json={"name": "Weekly planner", "instruction": None})
        assert patched.status_code == 200, patched.text
        assert patched.json()["name"] == "Weekly planner" and patched.json()["instruction"] == ""
        assert patched.json()["updated_at"] >= agent["updated_at"]

        configured = client.put(f"/api/v3/agents/{agent['id']}/config", headers=ALICE,
                                json={"instruction": "Be terse.", "allowed_tools": ["recall_memory"], "risk_ceiling": "PUBLIC"})
        assert configured.status_code == 200, configured.text
        body = configured.json()
        assert (body["instruction"], body["allowed_tools"], body["risk_ceiling"]) == ("Be terse.", ["recall_memory"], "PUBLIC")
        assert body["name"] == "Weekly planner", "config replaces behaviour, not identity"
        cleared = client.put(f"/api/v3/agents/{agent['id']}/config", headers=ALICE, json={}).json()
        assert (cleared["instruction"], cleared["allowed_tools"], cleared["risk_ceiling"]) == ("", None, None)

        assert client.delete(f"/api/v3/agents/{agent['id']}", headers=ALICE).json() == {"deleted": True, "id": agent["id"]}
        assert client.get(f"/api/v3/agents/{agent['id']}", headers=ALICE).status_code == 404
        assert client.delete(f"/api/v3/agents/{agent['id']}", headers=ALICE).status_code == 404

    def test_a_built_in_is_readable_and_read_only(self, client) -> None:
        view = client.get("/api/v3/agents/task_planner", headers=ALICE).json()
        assert view["kind"] == "builtin" and view["read_only"] is True and view["allowed_tools"]
        assert client.patch("/api/v3/agents/task_planner", headers=ALICE, json={"name": "x"}).status_code == 403
        assert client.put("/api/v3/agents/task_planner/config", headers=ALICE, json={}).status_code == 403
        assert client.delete("/api/v3/agents/task_planner", headers=ALICE).status_code == 403
        assert client.get("/api/v3/agents/no-such-agent", headers=ALICE).status_code == 404

    def test_the_literal_runs_routes_still_win_over_the_id_route(self, client) -> None:
        assert client.get("/api/v3/agents/runs", headers=ALICE).status_code == 200
        assert client.get("/api/v3/agents/runs/does-not-exist", headers=ALICE).status_code == 404

    @pytest.mark.parametrize("body, field", [
        ({"base_agent": "engineer"}, "base_agent"),
        ({"base_agent": "no-such-agent"}, "base_agent"),
        ({"allowed_tools": ["docker_build"]}, "allowed_tools"),          # outside task_planner's grant
        ({"allowed_tools": ["not_a_tool_here"]}, "allowed_tools"),       # not a tool this server has
        ({"allowed_tools": ["has space"]}, "allowed_tools"),
        ({"risk_ceiling": "SYSTEM"}, "risk_ceiling"),                    # above task_planner's
        ({"risk_ceiling": "ROOT"}, "risk_ceiling"),
        ({"name": "  "}, "name"),
        ({"name": "x" * 61}, "name"),
        ({"instruction": "x" * (MAX_INSTRUCTION_CHARS + 1)}, "instruction"),
        ({"instruction": "bell\x07"}, "instruction"),
    ])
    def test_what_a_custom_agent_may_not_be(self, client, body, field) -> None:
        response = _create(client, **body)
        assert response.status_code == 422, response.text
        assert response.json()["details"]["field"] == field
        assert client.get("/api/v3/agents", headers=ALICE).json()["custom_agents"] == []

    def test_unknown_and_missing_fields_are_refused(self, client) -> None:
        assert client.post("/api/v3/agents", headers=ALICE, json={"name": "x", "role": "admin"}).status_code == 422
        assert client.post("/api/v3/agents", headers=ALICE, json={}).status_code == 422
        agent = _create(client).json()
        assert client.patch(f"/api/v3/agents/{agent['id']}", headers=ALICE, json={}).status_code == 422
        assert client.patch(f"/api/v3/agents/{agent['id']}", headers=ALICE, json={"base_agent": "orchestrator"}).status_code == 422

    def test_an_edit_that_widens_is_refused_like_a_create(self, client) -> None:
        agent = _create(client).json()
        assert client.patch(f"/api/v3/agents/{agent['id']}", headers=ALICE, json={"allowed_tools": ["docker_build"]}).status_code == 422
        assert client.put(f"/api/v3/agents/{agent['id']}/config", headers=ALICE, json={"risk_ceiling": "SYSTEM"}).status_code == 422
        assert client.get(f"/api/v3/agents/{agent['id']}", headers=ALICE).json()["allowed_tools"] is None

    def test_names_are_unique_per_person_and_the_count_is_limited(self, client) -> None:
        assert _create(client, name="Planner").status_code == 201
        taken = _create(client, name="planner")
        assert taken.status_code == 409 and taken.json()["details"]["reason"] == "name"
        assert _create(client, BOB, name="Planner").status_code == 201, "another person may use the same name"
        for i in range(MAX_CUSTOM_AGENTS_PER_OWNER - 1):
            assert _create(client, name=f"Agent {i}").status_code == 201
        full = _create(client, name="One too many")
        assert full.status_code == 409 and full.json()["details"]["reason"] == "limit"

    def test_clone_a_built_in_and_a_custom_agent(self, client) -> None:
        from_builtin = client.post("/api/v3/agents/research_agent/clone", headers=ALICE, json={})
        assert from_builtin.status_code == 201, from_builtin.text
        assert from_builtin.json()["base_agent"] == "research_agent"
        assert from_builtin.json()["name"] == "Research Agent (copy)" and from_builtin.json()["allowed_tools"] is None

        mine = _create(client, name="Tuned", instruction="Cite sources.", allowed_tools=["recall_memory"]).json()
        copy = client.post(f"/api/v3/agents/{mine['id']}/clone", headers=ALICE, json={}).json()
        again = client.post(f"/api/v3/agents/{mine['id']}/clone", headers=ALICE).json()
        assert copy["name"] == "Tuned (copy)" and again["name"] == "Tuned (copy 2)", "a taken default name gets a suffix"
        assert (copy["instruction"], copy["allowed_tools"], copy["base_agent"]) == ("Cite sources.", ["recall_memory"], "task_planner")
        assert copy["id"] != mine["id"] and copy["created_at"] >= mine["created_at"]
        named = client.post(f"/api/v3/agents/{mine['id']}/clone", headers=ALICE, json={"name": "Mine too"})
        assert named.status_code == 201 and named.json()["name"] == "Mine too"
        assert client.post(f"/api/v3/agents/{mine['id']}/clone", headers=ALICE, json={"name": "mine too"}).status_code == 409

    def test_the_engineer_cannot_be_cloned_into_a_custom_agent(self, client) -> None:
        refused = client.post("/api/v3/agents/engineer/clone", headers=ALICE, json={})
        assert refused.status_code == 422 and refused.json()["details"]["field"] == "base_agent"
        assert client.get("/api/v3/agents", headers=ALICE).json()["custom_agents"] == []

    def test_every_route_needs_a_signed_in_caller(self, client) -> None:
        agent = _create(client).json()["id"]
        for method, path in (("GET", "/api/v3/agents"), ("POST", "/api/v3/agents"), ("GET", f"/api/v3/agents/{agent}"),
                             ("PATCH", f"/api/v3/agents/{agent}"), ("PUT", f"/api/v3/agents/{agent}/config"),
                             ("DELETE", f"/api/v3/agents/{agent}"), ("POST", f"/api/v3/agents/{agent}/clone")):
            assert client.request(method, path, json={}).status_code == 401, (method, path)

    def test_changes_are_rate_limited_per_person(self, client) -> None:
        from aura_core.api.agents import WRITES_PER_MINUTE

        statuses = [client.post("/api/v3/agents/orchestrator/clone", headers=ALICE, json={"name": f"A{i}"}).status_code
                    for i in range(WRITES_PER_MINUTE + 1)]
        assert 429 in statuses and statuses.index(429) >= 20, statuses
        assert client.post("/api/v3/agents/orchestrator/clone", headers=BOB, json={}).status_code == 201


# ---------------------------------------------------------------- ownership: another person's agent


class TestOwnership:
    def test_bob_cannot_read_change_clone_use_or_delete_alices_agent(self, client) -> None:
        mine = _create(client, instruction="PRIVATE-NOTES-ALICE").json()
        for method, path, body in (
            ("GET", f"/api/v3/agents/{mine['id']}", None),
            ("PATCH", f"/api/v3/agents/{mine['id']}", {"name": "stolen", "instruction": "x"}),
            ("PUT", f"/api/v3/agents/{mine['id']}/config", {"instruction": "x"}),
            ("DELETE", f"/api/v3/agents/{mine['id']}", None),
            ("POST", f"/api/v3/agents/{mine['id']}/clone", {}),
        ):
            theirs = client.request(method, path, headers=BOB, json=body)
            assert theirs.status_code == 404, f"Bob reached Alice's agent via {method} {path}: {theirs.text}"
            assert "PRIVATE-NOTES-ALICE" not in theirs.text
        theirs_chat = client.post("/api/v3/chat", headers=BOB, json={"message": "hi", "agent": mine["id"]})
        missing_chat = client.post("/api/v3/chat", headers=BOB,
                                   json={"message": "hi", "agent": "00000000-0000-4000-8000-000000000000"})
        assert theirs_chat.status_code == missing_chat.status_code == 404, "Bob ran a turn as Alice's agent"
        assert theirs_chat.json()["error"] == missing_chat.json()["error"] == "not_found"
        assert "PRIVATE-NOTES-ALICE" not in theirs_chat.text
        assert client.get("/api/v3/agents", headers=BOB).json()["custom_agents"] == []
        untouched = client.get(f"/api/v3/agents/{mine['id']}", headers=ALICE).json()
        assert untouched["name"] == "My planner" and untouched["instruction"] == "PRIVATE-NOTES-ALICE"


# ----------------------------------------------------- a turn really runs as the custom agent


def _shown(request) -> str:
    return "\n".join(m.content for m in request.conversation)


class TestTurnsRunAsTheCustomAgent:
    def test_the_instructions_reach_the_model_under_the_personas_prompt_and_tools_are_narrowed(self, client, container) -> None:
        echo = container.router.get("echo")
        plain = client.post("/api/v3/chat", headers=ALICE, json={"message": "what time is it", "agent": "orchestrator"})
        assert plain.status_code == 200, plain.text
        assert "PERSONA-NOTES-XYZZY" not in _shown(echo.calls[-1])

        mine = _create(client, base_agent="orchestrator", name="Clock", instruction="Always answer like PERSONA-NOTES-XYZZY.",
                       allowed_tools=["current_datetime"]).json()
        reply = client.post("/api/v3/chat", headers=ALICE, json={"message": "what time is it", "agent": mine["id"]})
        assert reply.status_code == 200, reply.text
        shown = _shown(echo.calls[-1])
        assert "PERSONA-NOTES-XYZZY" in shown, "the agent's instructions never reached the model"
        assert "not instructions from the system" in shown, "the notes must be framed as the user's, lower priority"
        assert shown.index("PERSONA-NOTES-XYZZY") > shown.index("Persona notes written by the user"), "notes sit under the frame"
        assert reply.json()["resolved_agent"] == f"custom:{mine['id']}"

    def test_the_stream_runs_as_the_custom_agent_too(self, client, container) -> None:
        echo = container.router.get("echo")
        mine = _create(client, base_agent="orchestrator", name="Streamer", instruction="STREAM-NOTES-QWERTY").json()
        with client.stream("POST", "/api/v3/chat/stream", headers=ALICE, json={"message": "hello", "agent": mine["id"]}) as response:
            assert response.status_code == 200
            body = "".join(response.iter_text())
        assert "event: done" in body and f"custom:{mine['id']}" in body
        assert "STREAM-NOTES-QWERTY" in _shown(echo.calls[-1])

    def test_a_deleted_agent_cannot_be_used_and_a_built_in_is_unchanged_by_its_clones(self, client, container) -> None:
        echo = container.router.get("echo")
        mine = _create(client, base_agent="orchestrator", name="Gone", instruction="GONE-NOTES").json()
        client.delete(f"/api/v3/agents/{mine['id']}", headers=ALICE)
        assert client.post("/api/v3/chat", headers=ALICE, json={"message": "hi", "agent": mine["id"]}).status_code == 404
        client.post("/api/v3/chat", headers=ALICE, json={"message": "hi", "agent": "orchestrator"})
        assert "GONE-NOTES" not in _shown(echo.calls[-1])
        assert REGISTRY.get("orchestrator").extra_instruction == ""

    def test_an_unknown_non_uuid_agent_still_falls_back_as_it_always_did(self, client) -> None:
        reply = client.post("/api/v3/chat", headers=ALICE, json={"message": "hi", "agent": "no-such-agent"})
        assert reply.status_code == 200 and reply.json()["resolved_agent"] == "orchestrator"


class TestToolsAreNarrowedWhenTheyRun:
    """The model is shown no tools in this harness (tools run through the planner), so narrowing is
    proved where it bites: a plan that asks for a tool is executed for the plain persona and refused
    for the custom agent that was not given it, while a tool it *was* given still runs."""

    async def _kernel(self, container, tool: str, arguments: dict):
        from tests.unit.test_kernel_degradation import _FixedPlanner, _tool_plan

        await container.startup()
        container.kernel._deps.planner = _FixedPlanner(_tool_plan(tool, arguments))
        return container.kernel

    @staticmethod
    def _ran(result, tool: str) -> bool:
        return any(t.name == tool and t.ok for t in result.telemetry.tools)

    async def test_a_tool_outside_the_list_is_refused_and_one_inside_still_runs(self, container) -> None:
        from aura_core.kernel import TurnRequest

        base = dict(message="What is 2+2?", user_id=UserId("test-user"), plan=True, agent="orchestrator")
        narrowed = _agent(base_agent_id="orchestrator", allowed_tools=("current_datetime",))

        kernel = await self._kernel(container, "calculator", {"expression": "2+2"})
        assert self._ran(await kernel.run(TurnRequest(**base)), "calculator"), "control: the plain persona runs it"
        refused = await kernel.run(TurnRequest(**base, custom_agent=narrowed))
        assert not self._ran(refused, "calculator"), "the custom agent ran a tool it was not given"

        kernel = await self._kernel(container, "current_datetime", {})
        allowed = await kernel.run(TurnRequest(**base, custom_agent=narrowed))
        assert self._ran(allowed, "current_datetime"), "narrowing must not break the tools the agent was given"

    async def test_a_custom_agent_on_a_narrow_base_cannot_use_what_the_base_never_had(self, container) -> None:
        """Defence in depth: the list is stored as written, and the turn intersects it with the base."""
        from aura_core.kernel import TurnRequest

        base_spec = REGISTRY.get("task_planner")
        outside = next(name for name in ("calculator", "current_datetime", "search_knowledge")
                       if not base_spec.permits_tool(name))
        kernel = await self._kernel(container, outside, {"expression": "1+1"} if outside == "calculator" else {})
        sneaky = _agent(base_agent_id="task_planner", allowed_tools=(outside,))
        result = await kernel.run(TurnRequest(message="go", user_id=UserId("test-user"), plan=True,
                                              agent="task_planner", custom_agent=sneaky))
        assert not self._ran(result, outside), f"a custom agent used {outside!r}, which task_planner does not have"

    async def test_the_instruction_survives_in_the_system_prompt_of_the_resolved_spec(self, container) -> None:
        from aura_core.kernel import TurnRequest

        await container.startup()
        notes = _agent(base_agent_id="orchestrator", instruction="KERNEL-NOTES-MNBV")
        request = TurnRequest(message="hi", user_id=UserId("test-user"), custom_agent=notes)
        prompt = await container.kernel._render_system_prompt(request, ())
        assert "KERNEL-NOTES-MNBV" in prompt
        assert "KERNEL-NOTES-MNBV" not in await container.kernel._render_system_prompt(
            TurnRequest(message="hi", user_id=UserId("test-user")), ())


# --------------------------------------------------------------------------------------- erasure


async def test_erasing_an_account_removes_its_custom_agents_and_nobody_elses(container) -> None:
    from api.admin_platform import erase_account

    store = container.custom_agents
    await store.create(_agent("alice", name="A"), limit=5)
    kept = await store.create(_agent("bob", name="A"), limit=5)

    result = await erase_account(container, "alice", unknown_user_ok=True)

    assert result["forgotten"] is True and result["failed"] == []
    assert await store.list_for_owner(UserId("alice")) == []
    assert await store.list_for_owner(UserId("bob")) == [kept]
