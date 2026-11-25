"""Superseded -- deliberately NOT registered as a tool (ADR-036). The platform already has the
platform's knowledge graph: `aura_core/knowledge/graph_pipeline.py` (`KnowledgeGraphPipeline`),
`domain/graph.py` and `adapters/postgres_graph.py` -- owner-scoped, provenance-carrying and
durable. This in-memory class has no owner scoping and no provenance, and its returned triples
share their `metadata` dicts with the store, so registering it would create a second, weaker
source of truth.

Kept for reference only: nothing under ``aura_core`` may import it
(``tests/architecture/test_superseded_open_tools_unused.py``).
"""

import copy
from collections import defaultdict, deque
from typing import Any, Dict, Iterable, List, Optional, Set

#: Direction keywords Mermaid accepts for ``graph``.
_MERMAID_DIRECTIONS = {"TD", "TB", "BT", "RL", "LR"}
#: ``find_paths_between_entities`` stops after this many partial paths / found paths, and says so
#: through ``find_paths`` -- a dense graph has exponentially many simple paths.
MAX_PATH_EXPANSIONS = 100_000
DEFAULT_MAX_PATHS = 100


def _name(value: Any) -> Optional[str]:
    """A usable node/relation name: a non-empty string. Anything else is ``None``."""
    return value if isinstance(value, str) and value.strip() else None


def _mermaid_label(text: str) -> str:
    """Escape a label for a Mermaid quoted string (quotes, newlines and angle brackets)."""
    return (
        text.replace("&", "#amp;").replace('"', "#quot;").replace("<", "#lt;").replace(">", "#gt;")
        .replace("\r", " ").replace("\n", " ")
    )


class KnowledgeGraphManager:
    """Lightweight in-memory knowledge graph manager for triples (entity, relation, target), reasoning paths, and Mermaid visualization."""

    def __init__(self) -> None:
        self.triples: List[Dict[str, Any]] = []

    def add_relation(
        self,
        entity: str,
        relation: str,
        target: str,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """Add or update a directed relation triple in the graph.

        Entity, relation and target must be non-empty strings and ``metadata`` a dict; otherwise
        nothing is stored and the result carries an ``error``. Returned triples are copies.
        """
        if _name(entity) is None or _name(relation) is None or _name(target) is None:
            return {"added": False, "updated": False,
                    "error": "entity, relation and target must be non-empty strings."}
        if metadata is not None and not isinstance(metadata, dict):
            return {"added": False, "updated": False, "error": "metadata must be an object."}

        for item in self.triples:
            if item["entity"] == entity and item["relation"] == relation and item["target"] == target:
                if metadata:
                    item["metadata"].update(copy.deepcopy(metadata))
                return {"added": False, "updated": True, "triple": copy.deepcopy(item)}

        entry = {
            "entity": entity,
            "relation": relation,
            "target": target,
            "metadata": copy.deepcopy(metadata) if metadata else {},
        }
        self.triples.append(entry)
        return {"added": True, "updated": False, "triple": copy.deepcopy(entry)}

    def query_relations(
        self,
        entity: Optional[str] = None,
        relation: Optional[str] = None,
        target: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        """Query triples matching optional entity, relation, or target filters."""
        matches: List[Dict[str, Any]] = []
        for item in self.triples:
            if entity is not None and item["entity"] != entity:
                continue
            if relation is not None and item["relation"] != relation:
                continue
            if target is not None and item["target"] != target:
                continue
            matches.append(copy.deepcopy(item))
        return matches

    def get_neighbors(self, node: str) -> Dict[str, List[str]]:
        """Return outgoing targets and incoming entities for a given node."""
        outgoing = [t["target"] for t in self.triples if t["entity"] == node]
        incoming = [t["entity"] for t in self.triples if t["target"] == node]
        return {"node": node, "outgoing": outgoing, "incoming": incoming}

    def stats(self) -> Dict[str, Any]:
        """Return counts of unique nodes and relation triples."""
        nodes = set()
        for t in self.triples:
            nodes.add(t["entity"])
            nodes.add(t["target"])
        return {
            "triples_count": len(self.triples),
            "unique_nodes_count": len(nodes),
        }

    def find_paths(
        self,
        start_entity: str,
        end_entity: str,
        max_depth: int = 5,
        max_paths: int = DEFAULT_MAX_PATHS,
    ) -> Dict[str, Any]:
        """Like :meth:`find_paths_between_entities`, but says when the search was cut short.

        ``truncated`` is True when ``max_paths`` paths were found (there may be more) or the search
        hit its work budget: "these are *some* of the paths", never "these are all of them".
        """
        try:
            max_depth = int(max_depth)
            max_paths = max(1, int(max_paths))
        except (TypeError, ValueError, OverflowError):
            return {"paths": [], "truncated": False, "error": "max_depth and max_paths must be integers."}
        if start_entity == end_entity:
            return {"paths": [], "truncated": False}

        adj: Dict[str, List[Dict[str, str]]] = defaultdict(list)
        for t in self.triples:
            adj[t["entity"]].append({"relation": t["relation"], "target": t["target"]})

        found_paths: List[List[Dict[str, str]]] = []
        queue = deque([([(start_entity, "START")], frozenset([start_entity]))])
        expansions = 0

        while queue:
            current_path, visited = queue.popleft()
            if len(current_path) - 1 >= max_depth:
                continue

            last_node = current_path[-1][0]
            for edge in adj.get(last_node, []):
                expansions += 1
                if expansions > MAX_PATH_EXPANSIONS:
                    return {"paths": found_paths, "truncated": True}
                nxt = edge["target"]
                rel = edge["relation"]
                new_path = current_path + [(nxt, rel)]

                if nxt == end_entity:
                    found_paths.append([
                        {"from": new_path[i][0], "relation": new_path[i + 1][1], "to": new_path[i + 1][0]}
                        for i in range(len(new_path) - 1)
                    ])
                    if len(found_paths) >= max_paths:
                        return {"paths": found_paths, "truncated": True}
                elif nxt not in visited:
                    queue.append((new_path, visited | {nxt}))

        return {"paths": found_paths, "truncated": False}

    def find_paths_between_entities(
        self,
        start_entity: str,
        end_entity: str,
        max_depth: int = 5,
        max_paths: int = DEFAULT_MAX_PATHS,
    ) -> List[List[Dict[str, str]]]:
        """Find multi-hop reasoning relationship paths between start_entity and end_entity using BFS.

        At most ``max_paths`` paths (shortest first) and a bounded amount of search work; use
        :meth:`find_paths` to learn whether the list was cut short.
        """
        return self.find_paths(start_entity, end_entity, max_depth, max_paths)["paths"]

    def detect_conflicting_facts(
        self,
        functional_relations: Optional[Iterable[str]] = None,
    ) -> List[Dict[str, Any]]:
        """Identify contradictions: a single-valued property with several values, or a relation
        asserted together with its negation (``is_a`` / ``is_not_a``, ``uses`` / ``not_uses``) for
        the same entity and target.

        ``functional_relations`` replaces the default single-valued set
        (``status``, ``version``, ``is_active``, ``primary_owner``).
        """
        conflicts: List[Dict[str, Any]] = []
        grouped: Dict[tuple, Set[str]] = defaultdict(set)
        pairs: Dict[tuple, Set[str]] = defaultdict(set)

        for t in self.triples:
            grouped[(t["entity"], t["relation"])].add(t["target"])
            pairs[(t["entity"], t["target"])].add(t["relation"].strip().lower())

        # Check single-valued functional relations like 'has_parent', 'status', 'version'
        functional = {"status", "version", "is_active", "primary_owner"}
        if functional_relations is not None:
            functional = {str(r).lower() for r in functional_relations}
        for (ent, rel), targets in grouped.items():
            if rel.lower() in functional and len(targets) > 1:
                conflicts.append({
                    "entity": ent,
                    "relation": rel,
                    "conflicting_targets": sorted(list(targets)),
                    "type": "Single-valued property has multiple distinct values",
                })

        def positive(rel: str) -> Optional[str]:
            if rel.startswith("is_not_"):
                return "is_" + rel[len("is_not_"):]
            if rel.startswith("not_"):
                return rel[len("not_"):]
            return None

        for (ent, tgt), rels in pairs.items():
            for rel in sorted(rels):
                pos = positive(rel)
                if pos and pos in rels:
                    conflicts.append({
                        "entity": ent,
                        "relation": pos,
                        "conflicting_targets": [tgt],
                        "negated_by": rel,
                        "type": "Relation is asserted and negated for the same target",
                    })

        return conflicts

    def export_to_mermaid(self, direction: str = "TD") -> str:
        """Render the knowledge graph as an interactive Mermaid diagram.

        Every distinct node gets its own id (``n0``, ``n1``...) and its text goes in an escaped
        label, so names that are emoji, Arabic, keywords such as ``end``, or contain quotes can
        neither collide with each other nor break the diagram's syntax.
        """
        direction = direction if direction in _MERMAID_DIRECTIONS else "TD"
        lines = [f"graph {direction}"]
        if not self.triples:
            lines.append("    empty_kg[\"Empty Knowledge Graph\"]")
            return "\n".join(lines)

        ids: Dict[str, str] = {}

        def node(name: str) -> str:
            if name not in ids:
                ids[name] = f"n{len(ids)}"
            return f'{ids[name]}["{_mermaid_label(name)}"]'

        for t in self.triples:
            src = node(t["entity"])
            dst = node(t["target"])
            rel = _mermaid_label(t.get("relation", "relates_to"))
            lines.append(f'    {src} -->|"{rel}"| {dst}')

        return "\n".join(lines)
