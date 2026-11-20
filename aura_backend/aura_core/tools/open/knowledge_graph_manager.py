"""Superseded -- deliberately NOT registered as a tool (ADR-036). The platform already has the
platform's knowledge graph: `aura_core/knowledge/graph_pipeline.py` (`KnowledgeGraphPipeline`),
`domain/graph.py` and `adapters/postgres_graph.py` -- owner-scoped, provenance-carrying and
durable. This in-memory class has no owner scoping and no provenance, and its returned triples
share their `metadata` dicts with the store, so registering it would create a second, weaker
source of truth.

Kept for reference only: nothing under ``aura_core`` may import it
(``tests/architecture/test_superseded_open_tools_unused.py``).
"""

from typing import Any, Dict, List, Optional
from collections import defaultdict, deque
from typing import Any, Dict, List, Optional, Set


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
        """Add or update a directed relation triple in the graph."""
        for item in self.triples:
            if item["entity"] == entity and item["relation"] == relation and item["target"] == target:
                if metadata:
                    item["metadata"].update(metadata)
                return {"added": False, "updated": True, "triple": dict(item)}

        entry = {
            "entity": entity,
            "relation": relation,
            "target": target,
            "metadata": dict(metadata) if metadata else {},
        }
        self.triples.append(entry)
        return {"added": True, "updated": False, "triple": dict(entry)}

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
            matches.append(dict(item))
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

    def find_paths_between_entities(
        self,
        start_entity: str,
        end_entity: str,
        max_depth: int = 5,
    ) -> List[List[Dict[str, str]]]:
        """Find multi-hop reasoning relationship paths between start_entity and end_entity using BFS."""
        if start_entity == end_entity:
            return []

        adj: Dict[str, List[Dict[str, str]]] = defaultdict(list)
        for t in self.triples:
            adj[t["entity"]].append({"relation": t["relation"], "target": t["target"]})

        found_paths: List[List[Dict[str, str]]] = []
        queue = deque([([(start_entity, "START")], set([start_entity]))])

        while queue:
            current_path, visited = queue.popleft()
            if len(current_path) - 1 >= max_depth:
                continue

            last_node = current_path[-1][0]
            for edge in adj.get(last_node, []):
                nxt = edge["target"]
                rel = edge["relation"]
                new_path = current_path + [(nxt, rel)]

                if nxt == end_entity:
                    formatted_path = [
                        {"from": new_path[i][0], "relation": new_path[i + 1][1], "to": new_path[i + 1][0]}
                        for i in range(len(new_path) - 1)
                    ]
                    found_paths.append(formatted_path)
                elif nxt not in visited:
                    queue.append((new_path, visited | {nxt}))

        return found_paths

    def detect_conflicting_facts(self) -> List[Dict[str, Any]]:
        """Identify potential contradictions or inverse conflicts (e.g., is_a vs is_not_a, or duplicate contradictory attributes)."""
        conflicts: List[Dict[str, Any]] = []
        grouped: Dict[tuple, Set[str]] = defaultdict(set)

        for t in self.triples:
            grouped[(t["entity"], t["relation"])].add(t["target"])

        # Check single-valued functional relations like 'has_parent', 'status', 'version'
        functional_relations = {"status", "version", "is_active", "primary_owner"}
        for (ent, rel), targets in grouped.items():
            if rel.lower() in functional_relations and len(targets) > 1:
                conflicts.append({
                    "entity": ent,
                    "relation": rel,
                    "conflicting_targets": sorted(list(targets)),
                    "type": "Single-valued property has multiple distinct values",
                })

        return conflicts

    def export_to_mermaid(self, direction: str = "TD") -> str:
        """Render the knowledge graph as an interactive Mermaid diagram."""
        lines = [f"graph {direction}"]
        if not self.triples:
            lines.append("    empty_kg[\"Empty Knowledge Graph\"]")
            return "\n".join(lines)

        def sanitize(text: str) -> str:
            return "".join(c if c.isalnum() else "_" for c in text)

        for t in self.triples:
            s_id = sanitize(t["entity"])
            t_id = sanitize(t["target"])
            rel = t.get("relation", "relates_to")
            lines.append(f'    {s_id}["{t["entity"]}"] -->|"{rel}"| {t_id}["{t["target"]}"]')

        return "\n".join(lines)
