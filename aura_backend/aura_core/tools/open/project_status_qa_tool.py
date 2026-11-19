from typing import Any, Dict, List


class ProjectStatusQaTool:
    """Evidence-backed QA helper for querying project execution history, stages, and errors."""

    @staticmethod
    def query_history(
        query: str,
        execution_history: List[Dict[str, Any]],
        limit: int = 5,
    ) -> Dict[str, Any]:
        """Search execution_history records matching terms from query."""
        q_lower = query.strip().lower()
        terms = [t for t in q_lower.split() if len(t) > 1]
        matched: List[Dict[str, Any]] = []

        for record in execution_history:
            stage = str(record.get("stage", "")).lower()
            status = str(record.get("status", "")).lower()
            message = str(record.get("message", "")).lower()
            combined = f"{stage} {status} {message}"

            if q_lower in combined or any(term in combined for term in terms):
                matched.append(dict(record))

        evidence = matched[: max(0, limit)]
        return {
            "query": query,
            "matches_found": len(matched),
            "returned_count": len(evidence),
            "evidence": evidence,
            "direct_answer": f"Found {len(matched)} matching execution records.",
        }