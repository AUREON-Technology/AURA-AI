"""Superseded -- deliberately NOT registered as a tool (ADR-036). The platform already has per-fact
provenance in `aura_core/domain/graph.py` (`FactProvenance`) and `domain/ausm.py` (`Provenance`).
This tracker keeps its edges in one process-wide in-memory object, so registering it would share
lineage between users and lose it on restart.

Kept for reference only: nothing under ``aura_core`` may import it
(``tests/architecture/test_superseded_open_tools_unused.py``).
"""

from typing import Any, Dict, List, Optional
from collections import defaultdict, deque
from typing import Any, Dict, List, Optional, Set


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
        """Record a directional lineage edge from a source dataset/node to a target node."""
        edge = {
            "source": source,
            "target": target,
            "operation": operation,
            "metadata": dict(metadata) if metadata else {},
        }
        self.edges.append(edge)
        return {"recorded": True, "edge": edge, "total_edges": len(self.edges)}

    def get_upstream_sources(self, target: str) -> List[str]:
        """Return direct upstream sources feeding into target."""
        return [e["source"] for e in self.edges if e["target"] == target]

    def get_downstream_targets(self, source: str) -> List[str]:
        """Return direct downstream targets fed by source."""
        return [e["target"] for e in self.edges if e["source"] == source]

    def trace_path(self, start_node: str, max_hops: int = 20) -> List[str]:
        """Trace a linear path starting from start_node without revisiting nodes."""
        path: List[str] = [start_node]
        visited = {start_node}
        current = start_node

        for _ in range(max_hops):
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
        pipeline_stages = list(stages) if stages is not None else ["ingestion", "normalization", "indexing"]
        lineage_path = [data_source] + pipeline_stages + [target_node]
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
        """Identify circular loops in data transformation lineage graph."""
        adj: Dict[str, List[str]] = defaultdict(list)
        all_nodes: Set[str] = set()

        for e in self.edges:
            adj[e["source"]].append(e["target"])
            all_nodes.add(e["source"])
            all_nodes.add(e["target"])

        cycles: List[List[str]] = []
        state: Dict[str, int] = {n: 0 for n in all_nodes}
        stack: List[str] = []

        def dfs(node: str) -> None:
            state[node] = 1
            stack.append(node)
            for nxt in adj.get(node, []):
                if state.get(nxt, 0) == 1:
                    idx = stack.index(nxt)
                    cycles.append(stack[idx:] + [nxt])
                elif state.get(nxt, 0) == 0:
                    dfs(nxt)
            stack.pop()
            state[node] = 2

        for n in sorted(all_nodes):
            if state[n] == 0:
                dfs(n)

        return cycles

    def export_to_mermaid(self, direction: str = "LR") -> str:
        """Generate a Mermaid flowchart visualizing data flow lineage."""
        lines = [f"flowchart {direction}"]
        if not self.edges:
            lines.append("    empty_lineage[\"No Transformations Recorded\"]")
            return "\n".join(lines)

        def sanitize(text: str) -> str:
            return "".join(c if c.isalnum() else "_" for c in text)

        for e in self.edges:
            src_id = sanitize(e["source"])
            tgt_id = sanitize(e["target"])
            op = e.get("operation", "transforms")
            lines.append(f'    {src_id}["{e["source"]}"] -->|"{op}"| {tgt_id}["{e["target"]}"]')

        return "\n".join(lines)
