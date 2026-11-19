from typing import Any, Dict, List, Optional


class AutonomousResearchAndSynthesisTool:
    """Helper for structuring research notes, deduplicating claims, and synthesizing findings."""

    @staticmethod
    def normalize_sources(sources: List[Dict[str, Any]]) -> Dict[str, Any]:
        """Deduplicate and normalize research source entries by title or URL."""
        seen_keys = set()
        unique_sources: List[Dict[str, Any]] = []

        for src in sources:
            title = str(src.get("title", "")).strip()
            url = str(src.get("url", "")).strip()
            key = url.lower() or title.lower()
            if not key or key in seen_keys:
                continue
            seen_keys.add(key)
            unique_sources.append({
                "title": title or "Untitled Source",
                "url": url,
                "summary": str(src.get("summary", "")).strip(),
                "confidence": src.get("confidence", "medium"),
            })

        return {
            "input_count": len(sources),
            "unique_count": len(unique_sources),
            "sources": unique_sources,
        }

    @staticmethod
    def find_conflicting_claims(claims: List[Dict[str, str]]) -> List[Dict[str, Any]]:
        """
        Flag claims sharing the same topic with differing stances or values.
        Each claim dict may contain: topic, statement, source.
        """
        by_topic: Dict[str, List[Dict[str, str]]] = {}
        for c in claims:
            topic = str(c.get("topic", "general")).strip().lower()
            by_topic.setdefault(topic, []).append(c)

        conflicts: List[Dict[str, Any]] = []
        for topic, items in by_topic.items():
            statements = {str(item.get("statement", "")).strip() for item in items if item.get("statement")}
            if len(statements) > 1:
                conflicts.append({
                    "topic": topic,
                    "statements": sorted(statements),
                    "sources": [str(item.get("source", "")) for item in items],
                })
        return conflicts

    @classmethod
    def synthesize_report(
        cls,
        research_topic: str,
        sources: Optional[List[Dict[str, Any]]] = None,
        claims: Optional[List[Dict[str, str]]] = None,
    ) -> Dict[str, Any]:
        """Build a structured synthesis payload from sources and claims."""
        norm_src = cls.normalize_sources(sources or [])
        conflicts = cls.find_conflicting_claims(claims or [])

        key_points = [
            f"[{s['title']}] {s['summary']}" if s["summary"] else f"[{s['title']}]"
            for s in norm_src["sources"]
        ]

        return {
            "topic": research_topic,
            "sources_count": norm_src["unique_count"],
            "key_points": key_points,
            "conflicts_count": len(conflicts),
            "conflicts": conflicts,
            "ready": norm_src["unique_count"] > 0,
        }