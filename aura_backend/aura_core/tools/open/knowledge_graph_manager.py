from typing import Any, Dict, List, Optional


class KnowledgeGraphManager:
    """Lightweight in-memory knowledge graph manager for triples (entity, relation, target)."""

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