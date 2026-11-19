from typing import Any, Dict, List, Optional


class DataLineageProvenanceTracker:
    """Tracker for recording and querying data transformations, sources, and provenance edges."""

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