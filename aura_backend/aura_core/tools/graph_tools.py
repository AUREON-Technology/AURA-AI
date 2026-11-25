"""Knowledge-graph reading as agent tools (ADR-054): find entities, walk neighbours, find a path.

The graph (``knowledge/graph_pipeline.py`` -> ``GraphStore``) already feeds retrieval, but an agent could not
ask it a question directly. These three tools answer from it, read-only and scoped to the caller:

* **Owner from the context, never from an argument.** The graph is per owner (the caller's ``knowledge_owner`` or
  their user id); there is no parameter that names another owner, so one user's graph is unreachable from another's
  turn. Tested against the real in-memory store with two owners.
* **Bounded.** Depth <= 2 for neighbours, <= 4 hops and <= 200 visited entities for a path, <= 50 results per call.
* **Read-only on purpose.** There is no write tool: the graph is written by the ingestion pipeline, which attaches
  provenance and calibrates confidence (ADR-005). An agent asserting facts through a tool would bypass that, so
  agent-proposed facts are a separate, confirmation-gated design decision, not part of this change.
* **Untrusted.** Entity names and relations come from documents, so a result carries ``untrusted_content``: text to
  reason over, never instructions.
"""

from __future__ import annotations

import json
from collections import deque
from typing import Any, Sequence

from ..domain.errors import ToolExecutionError
from ..domain.tools import ToolCategory, ToolSensitivity
from ..ports.runtime import Tool, ToolContext
from .base import param, tool

__all__ = ["build_graph_tools", "GRAPH_TOOL_NAMES"]

GRAPH_TOOL_NAMES: frozenset[str] = frozenset({"graph_find_entities", "graph_neighbours", "graph_path"})
_COMMON = dict(category=ToolCategory.KNOWLEDGE, sensitivity=ToolSensitivity.PERSONAL, timeout_s=15.0,
               max_result_chars=14_000, tags=("untrusted_content",))
_MAX_VISITED = 200


def _entity(e: Any) -> dict[str, Any]:
    return {"key": e.key, "name": str(e.name)[:120], "type": e.type, "confidence": round(float(e.confidence), 3),
            "aliases": [str(a)[:60] for a in e.aliases[:5]], "mentions": e.mention_count, "sources": len(e.sources),
            "expired": e.valid_to is not None}


def _relation(r: Any) -> dict[str, Any]:
    return {"subject": r.subject_key, "predicate": r.predicate, "object": r.object_key,
            "confidence": round(float(r.confidence), 3), "sources": len(r.sources)}


def _owner(context: ToolContext) -> Any:
    return context.knowledge_owner or context.user_id


def _clean_key(name: str, value: str) -> str:
    key = " ".join(str(value or "").split())
    if not key or len(key) > 200:
        raise ToolExecutionError(f"'{name}' must be 1-200 characters", tool="graph")
    return key


def build_graph_tools(graph: Any) -> list[Tool]:
    """The tools over ``graph`` (a :class:`~aura_core.ports.storage.GraphStore`); nothing when it is ``None``."""
    if graph is None:
        return []

    @tool(
        "graph_find_entities",
        "Find entities in the caller's knowledge graph by name (substring or alias) and/or type: people, "
        "organisations, projects and so on, each with a confidence and how many sources assert it.",
        param("name", "string", "Part of the entity's name.", required=False, max_length=200),
        param("type", "string", "Entity type, e.g. person or organization.", required=False, max_length=60),
        param("limit", "integer", "Most results (default 20, max 50).", required=False, default=20, minimum=1, maximum=50),
        **_COMMON,
    )
    async def graph_find_entities(context: ToolContext, name: str | None = None, type: str | None = None,
                                  limit: int = 20) -> str:
        if not name and not type:
            raise ToolExecutionError("give a name and/or a type to search for", tool="graph_find_entities")
        found = await graph.find_entities(owner_id=_owner(context), name=name, type=type, limit=limit)
        return json.dumps({"entities": [_entity(e) for e in found], "count": len(found), "read_only": True},
                          ensure_ascii=False)

    @tool(
        "graph_neighbours",
        "What an entity is connected to in the caller's knowledge graph: its neighbours and the relations "
        "between them (current facts only), up to two hops.",
        param("key", "string", "The entity key (from graph_find_entities).", max_length=200),
        param("depth", "integer", "Hops, 1 or 2 (default 1).", required=False, default=1, minimum=1, maximum=2),
        param("limit", "integer", "Most entities (default 30, max 50).", required=False, default=30, minimum=1, maximum=50),
        **_COMMON,
    )
    async def graph_neighbours(context: ToolContext, key: str, depth: int = 1, limit: int = 30) -> str:
        owner = _owner(context)
        key = _clean_key("key", key)
        if await graph.get_entity(key, owner_id=owner) is None:
            raise ToolExecutionError(f"no entity with key {key!r} in your graph; use graph_find_entities first",
                                     tool="graph_neighbours")
        entities, relations = await graph.neighbours(key, owner_id=owner, depth=depth, limit=limit)
        return json.dumps({"center": key, "entities": [_entity(e) for e in entities],
                           "relations": [_relation(r) for r in relations][:200], "read_only": True},
                          ensure_ascii=False)

    @tool(
        "graph_path",
        "Find how two entities are connected in the caller's knowledge graph: the shortest chain of current "
        "relations between them (up to 4 hops), or that none was found within the search bound.",
        param("source", "string", "Start entity key.", max_length=200),
        param("target", "string", "End entity key.", max_length=200),
        param("max_hops", "integer", "Longest chain to look for (default 3, max 4).", required=False, default=3,
              minimum=1, maximum=4),
        **_COMMON,
    )
    async def graph_path(context: ToolContext, source: str, target: str, max_hops: int = 3) -> str:
        owner = _owner(context)
        source, target = _clean_key("source", source), _clean_key("target", target)
        for label, key in (("source", source), ("target", target)):
            if await graph.get_entity(key, owner_id=owner) is None:
                raise ToolExecutionError(f"no {label} entity with key {key!r} in your graph", tool="graph_path")
        parents: dict[str, tuple[str, Any]] = {}
        frontier: deque[tuple[str, int]] = deque([(source, 0)])
        seen = {source}
        exhausted = False
        while frontier and target not in seen:
            node, hops = frontier.popleft()
            if hops >= max_hops:
                continue
            _, relations = await graph.neighbours(node, owner_id=owner, depth=1, limit=50)
            for rel in relations:
                for a, b in ((rel.subject_key, rel.object_key), (rel.object_key, rel.subject_key)):
                    if a == node and b not in seen:
                        if len(seen) >= _MAX_VISITED:
                            exhausted = True
                            break
                        seen.add(b)
                        parents[b] = (node, rel)
                        frontier.append((b, hops + 1))
        if target not in seen:
            return json.dumps({"found": False, "searched_entities": len(seen), "max_hops": max_hops,
                               "search_limit_reached": exhausted,
                               "note": "not found within the bound: that is not proof the two are unconnected"})
        chain: list[dict[str, Any]] = []
        node = target
        while node != source:
            parent, rel = parents[node]
            chain.append({"from": parent, "to": node, **_relation(rel)})
            node = parent
        chain.reverse()
        return json.dumps({"found": True, "hops": len(chain), "path": chain, "read_only": True}, ensure_ascii=False)

    return [graph_find_entities, graph_neighbours, graph_path]
