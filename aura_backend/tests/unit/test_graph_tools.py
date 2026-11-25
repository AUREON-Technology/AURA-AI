"""ADR-054: knowledge-graph reading tools over the real in-memory graph store, two owners."""

from __future__ import annotations

import json

import pytest

from aura_core.adapters.memory_stores import InMemoryGraphStore
from aura_core.domain.errors import ToolExecutionError
from aura_core.domain.identifiers import UserId
from aura_core.domain.knowledge import Entity, GraphDelta, Relation
from aura_core.ports.runtime import ToolContext
from aura_core.tools.graph_tools import GRAPH_TOOL_NAMES, build_graph_tools

ALICE, BOB = UserId("alice"), UserId("bob")


def entity(key: str, type: str = "person", owner: UserId = ALICE, **kw) -> Entity:
    return Entity(key=key, name=key.title(), type=type, owner_id=owner, sources=("doc:1",), **kw)


def relation(a: str, p: str, b: str, owner: UserId = ALICE) -> Relation:
    return Relation(subject_key=a, predicate=p, object_key=b, owner_id=owner, confidence=0.8, sources=("doc:1",))


@pytest.fixture()
async def graph():
    store = InMemoryGraphStore()
    chain = ["ann", "bo", "cy", "di", "ed", "fay"]
    await store.apply(GraphDelta(
        entities=tuple(entity(k) for k in chain) + (entity("acme", "organization"),),
        relations=tuple(relation(a, "works_with", b) for a, b in zip(chain, chain[1:])) + (relation("ann", "works_at", "acme"),),
        source="doc:1"), owner_id=ALICE)
    await store.apply(GraphDelta(entities=(entity("secret", owner=BOB), entity("ann", owner=BOB, aliases=("annie",))),
                                 relations=(relation("ann", "knows", "secret", BOB),), source="doc:2"), owner_id=BOB)
    return store


def ctx(user: UserId, owner: UserId | None = None) -> ToolContext:
    return ToolContext(user_id=user, correlation_id="c", deadline_s=15.0, knowledge_owner=owner)


async def call(graph, tool_name: str, user: UserId = ALICE, **args) -> dict:
    tools = {t.spec.name: t for t in build_graph_tools(graph)}
    return json.loads(await tools[tool_name].execute(args, ctx(user)))


class TestFind:
    async def test_by_name_and_by_type(self, graph) -> None:
        by_name = await call(graph, "graph_find_entities", name="an")
        assert {e["key"] for e in by_name["entities"]} == {"ann"}
        orgs = await call(graph, "graph_find_entities", type="organization")
        assert [e["key"] for e in orgs["entities"]] == ["acme"] and orgs["entities"][0]["sources"] == 1

    async def test_a_search_needs_a_name_or_a_type(self, graph) -> None:
        with pytest.raises(ToolExecutionError, match="name and/or a type"):
            await call(graph, "graph_find_entities")

    async def test_another_owners_graph_is_invisible(self, graph) -> None:
        assert (await call(graph, "graph_find_entities", name="secret"))["count"] == 0
        assert (await call(graph, "graph_find_entities", user=BOB, name="secret"))["count"] == 1
        bob_ann = await call(graph, "graph_find_entities", user=BOB, name="ann")
        assert bob_ann["entities"][0]["aliases"] == ["annie"], "each owner sees their own 'ann'"

    async def test_the_knowledge_owner_of_the_context_is_the_scope(self, graph) -> None:
        tools = {t.spec.name: t for t in build_graph_tools(graph)}
        out = json.loads(await tools["graph_find_entities"].execute({"name": "secret"}, ctx(ALICE, owner=BOB)))
        assert out["count"] == 1, "a workspace turn reads the workspace owner's graph, as search_knowledge does"


class TestNeighbours:
    async def test_one_and_two_hops(self, graph) -> None:
        one = await call(graph, "graph_neighbours", key="ann")
        assert {e["key"] for e in one["entities"]} >= {"bo", "acme"}
        assert all(r["subject"] == "ann" or r["object"] == "ann" for r in one["relations"][:2])
        two = await call(graph, "graph_neighbours", key="ann", depth=2)
        assert "cy" in {e["key"] for e in two["entities"]}

    async def test_an_unknown_key_and_another_owners_key_are_refused(self, graph) -> None:
        with pytest.raises(ToolExecutionError, match="no entity"):
            await call(graph, "graph_neighbours", key="nobody")
        with pytest.raises(ToolExecutionError, match="no entity"):
            await call(graph, "graph_neighbours", key="secret")

    async def test_depth_is_capped_by_the_schema(self, graph) -> None:
        tools = {t.spec.name: t for t in build_graph_tools(graph)}
        depth = next(p for p in tools["graph_neighbours"].spec.parameters if p.name == "depth")
        assert depth.maximum == 2


class TestPath:
    async def test_the_shortest_chain_is_found(self, graph) -> None:
        out = await call(graph, "graph_path", source="ann", target="di")
        assert out["found"] and out["hops"] == 3
        assert [(s["from"], s["to"]) for s in out["path"]] == [("ann", "bo"), ("bo", "cy"), ("cy", "di")]
        assert all(s["predicate"] == "works_with" for s in out["path"])

    async def test_edges_are_followed_in_either_direction(self, graph) -> None:
        out = await call(graph, "graph_path", source="cy", target="ann")
        assert out["found"] and out["hops"] == 2

    async def test_beyond_the_hop_bound_it_says_not_found_and_not_unconnected(self, graph) -> None:
        out = await call(graph, "graph_path", source="ann", target="fay", max_hops=3)
        assert out["found"] is False and "not proof" in out["note"]
        assert (await call(graph, "graph_path", source="ann", target="ed", max_hops=4))["hops"] == 4
        assert (await call(graph, "graph_path", source="ann", target="fay", max_hops=4))["found"] is False

    async def test_a_path_never_crosses_into_another_owners_graph(self, graph) -> None:
        with pytest.raises(ToolExecutionError, match="no target entity"):
            await call(graph, "graph_path", source="ann", target="secret")


def test_there_is_no_write_tool_and_no_owner_argument(graph) -> None:
    tools = build_graph_tools(graph)
    assert {t.spec.name for t in tools} == GRAPH_TOOL_NAMES
    for t in tools:
        assert not t.spec.side_effecting and "untrusted_content" in t.spec.tags
        assert not {"owner", "owner_id", "user", "user_id"} & {p.name for p in t.spec.parameters}
    assert build_graph_tools(None) == []


def test_registered_with_the_container_and_granted_to_the_right_personas() -> None:
    from aura_core.agents.registry import build_default_agent_registry
    from aura_core.tools.builtin import default_tools

    assert GRAPH_TOOL_NAMES <= {t.spec.name for t in default_tools(graph_store=InMemoryGraphStore())}
    assert not any(t.spec.name in GRAPH_TOOL_NAMES for t in default_tools())
    agents = build_default_agent_registry()
    assert all(agents.get("engineer").permits_tool(n) for n in GRAPH_TOOL_NAMES)
    assert not any(agents.get("coding_agent").permits_tool(n) for n in GRAPH_TOOL_NAMES)
