"""Superseded -- deliberately NOT registered as a tool (ADR-036). The platform already has per-fact
provenance in `aura_core/domain/graph.py` (`FactProvenance`) and `domain/ausm.py` (`Provenance`).
This tracker keeps its edges in one process-wide in-memory object, so registering it would share
lineage between users and lose it on restart.

Kept for reference only: nothing under ``aura_core`` may import it
(``tests/architecture/test_superseded_open_tools_unused.py``).
"""

import copy
from collections import defaultdict, deque
from typing import Any, Dict, List, Optional, Set

_MERMAID_DIRECTIONS = {"TD", "TB", "BT", "RL", "LR"}


def _name(value: Any) -> Optional[str]:
    return value if isinstance(value, str) and value.strip() else None


def _mermaid_label(text: str) -> str:
    return (
        str(text).replace("&", "#amp;").replace('"', "#quot;").replace("<", "#lt;").replace(">", "#gt;")
        .replace("\r", " ").replace("\n", " ")
    )


class DataLineageProvenanceTracker:
    """Tracker for recording and querying data transformations, sources, provenance trees, and Mermaid lineage export."""

    def __init__(self) -> None:
        self.edges: List[Dict[str, Any]] = []

    def record_transformation(
        self,
        source: str,
        target: str,
        operation: str,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """Record a directional lineage edge from a source dataset/node to a target node.

        ``source`` and ``target`` must be non-empty strings and ``metadata`` a dict; otherwise
        nothing is recorded and the result has ``recorded: False`` and an ``error``.
        """
        if _name(source) is None or _name(target) is None:
            return {"recorded": False, "error": "source and target must be non-empty strings.",
                    "total_edges": len(self.edges)}
        if metadata is not None and not isinstance(metadata, dict):
            return {"recorded": False, "error": "metadata must be an object.", "total_edges": len(self.edges)}
        edge = {
            "source": source,
            "target": target,
            "operation": operation,
            "metadata": copy.deepcopy(metadata) if metadata else {},
        }
        self.edges.append(edge)
        return {"recorded": True, "edge": copy.deepcopy(edge), "total_edges": len(self.edges)}

    def get_upstream_sources(self, target: str) -> List[str]:
        """Return direct upstream sources feeding into target."""
        return [e["source"] for e in self.edges if e["target"] == target]

    def get_downstream_targets(self, source: str) -> List[str]:
        """Return direct downstream targets fed by source."""
        return [e["target"] for e in self.edges if e["source"] == source]

    def trace_path(self, start_node: str, max_hops: int = 20) -> List[str]:
        """Trace a linear path starting from start_node without revisiting nodes.

        Follows the first recorded edge at every fork; use :meth:`get_downstream_targets` to see the
        other branches.
        """
        path: List[str] = [start_node]
        visited = {start_node}
        current = start_node

        try:
            hops = max(0, int(max_hops))
        except (TypeError, ValueError, OverflowError):
            hops = 20
        for _ in range(hops):
            next_nodes = [e["target"] for e in self.edges if e["source"] == current and e["target"] not in visited]
            if not next_nodes:
                break
            nxt = next_nodes[0]
            path.append(nxt)
            visited.add(nxt)
            current = nxt

        return path

    @staticmethod
    def build_lineage_payload(
        data_source: str,
        target_node: str,
        stages: Optional[List[str]] = None,
    ) -> Dict[str, Any]:
        """Build a stateless provenance path descriptor for a data flow."""
        if stages is None:
            pipeline_stages = ["ingestion", "normalization", "indexing"]
        elif isinstance(stages, str):
            pipeline_stages = [stages]  # not list("abc") == ["a", "b", "c"]
        else:
            pipeline_stages = [str(x) for x in stages]
        lineage_path = [str(data_source)] + pipeline_stages + [str(target_node)]
        return {
            "source": data_source,
            "destination": target_node,
            "lineage_path": lineage_path,
            "hops": len(lineage_path) - 1,
        }

    def get_provenance_chain(self, target: str) -> List[str]:
        """Retrieve complete upstream ancestry trace from original roots up to target."""
        ancestors: List[str] = []
        queue = deque([target])
        visited: Set[str] = {target}

        while queue:
            curr = queue.popleft()
            upstreams = self.get_upstream_sources(curr)
            for u in upstreams:
                if u not in visited:
                    visited.add(u)
                    ancestors.append(u)
                    queue.append(u)

        return ancestors

    def detect_circular_lineage(self) -> List[List[str]]:
        """Identify circular loops in data transformation lineage graph.

        Iterative DFS, so a lineage chain of any length is safe (a recursive one raises
        ``RecursionError`` past ~1000 hops). Each distinct loop is reported once.
        """
        adj: Dict[str, List[str]] = defaultdict(list)
        all_nodes: Set[str] = set()

        for e in self.edges:
            adj[e["source"]].append(e["target"])
            all_nodes.add(e["source"])
            all_nodes.add(e["target"])

        cycles: List[List[str]] = []
        seen_cycles: Set[tuple] = set()
        state: Dict[str, int] = {n: 0 for n in all_nodes}

        for root in sorted(all_nodes):
            if state[root] != 0:
                continue
            state[root] = 1
            stack: List[str] = [root]
            iters = [iter(adj.get(root, []))]
            while iters:
                try:
                    nxt = next(iters[-1])
                except StopIteration:
                    state[stack.pop()] = 2
                    iters.pop()
                    continue
                if state.get(nxt, 0) == 1:
                    cycle = stack[stack.index(nxt):] + [nxt]
                    key = tuple(cycle)
                    if key not in seen_cycles:
                        seen_cycles.add(key)
                        cycles.append(cycle)
                elif state.get(nxt, 0) == 0:
                    state[nxt] = 1
                    stack.append(nxt)
                    iters.append(iter(adj.get(nxt, [])))

        return cycles

    def export_to_mermaid(self, direction: str = "LR") -> str:
        """Generate a Mermaid flowchart visualizing data flow lineage.

        Each distinct node gets its own id and an escaped label, so emoji/Arabic names, keywords such
        as ``end`` and names containing quotes neither merge nor break the diagram.
        """
        direction = direction if direction in _MERMAID_DIRECTIONS else "LR"
        lines = [f"flowchart {direction}"]
        if not self.edges:
            lines.append("    empty_lineage[\"No Transformations Recorded\"]")
            return "\n".join(lines)

        ids: Dict[str, str] = {}

        def node(name: str) -> str:
            if name not in ids:
                ids[name] = f"n{len(ids)}"
            return f'{ids[name]}["{_mermaid_label(name)}"]'

        for e in self.edges:
            src = node(e["source"])
            tgt = node(e["target"])
            op = _mermaid_label(e.get("operation", "transforms"))
            lines.append(f'    {src} -->|"{op}"| {tgt}')

        return "\n".join(lines)
