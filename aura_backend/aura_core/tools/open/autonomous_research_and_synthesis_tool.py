import re
from typing import Any, Dict, List, Optional


class AutonomousResearchAndSynthesisTool:
    """Helper for structuring research notes, deduplicating claims, ranking evidence credibility, and synthesizing dossiers."""

    HIGH_CREDIBILITY_DOMAINS = [
        "github.com",
        "arxiv.org",
        "docs.python.org",
        "developer.mozilla.org",
        "cloud.google.com",
        "aws.amazon.com",
        "microsoft.com",
        "ieee.org",
        "acm.org",
    ]

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
        """Flag claims sharing the same topic with differing stances or values."""
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

    @classmethod
    def extract_claims_and_evidence(cls, text: str) -> List[Dict[str, Any]]:
        """Extract empirical sentences containing numbers, percentages, benchmark ratios, or citations."""
        sentences = re.split(r"(?<=[.!?])\s+", text.strip())
        empirical_findings: List[Dict[str, Any]] = []

        num_pat = re.compile(r"\b\d+(?:\.\d+)?(?:%|x|ms|s|gb|mb|kb|users|requests)\b", re.I)

        for s in sentences:
            s_clean = s.strip()
            if num_pat.search(s_clean) or any(tok in s_clean.lower() for tok in ("achieved", "benchmarked", "demonstrated", "exceeded", "reduced")):
                empirical_findings.append({
                    "finding": s_clean,
                    "contains_quantifier": bool(num_pat.search(s_clean)),
                })

        return empirical_findings

    @classmethod
    def rank_sources_by_credibility(cls, sources: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """Rank research sources according to domain authority, HTTPS, and citation completeness."""
        scored: List[Dict[str, Any]] = []

        for s in sources:
            url = str(s.get("url", "")).lower()
            score = 1.0

            if url.startswith("https://"):
                score += 0.5
            if any(dom in url for dom in cls.HIGH_CREDIBILITY_DOMAINS):
                score += 2.0
            if any(tld in url for tld in (".edu", ".gov", ".org")):
                score += 1.5
            if s.get("summary"):
                score += 0.5

            item = dict(s)
            item["credibility_score"] = round(score, 2)
            scored.append(item)

        scored.sort(key=lambda x: x["credibility_score"], reverse=True)
        return scored

    @classmethod
    def generate_research_dossier(
        cls,
        topic: str,
        sources: List[Dict[str, Any]],
        key_findings: List[str],
    ) -> str:
        """Render a publication-ready Markdown research dossier."""
        ranked_sources = cls.rank_sources_by_credibility(sources)

        lines = [
            f"# Research Dossier: {topic}",
            "",
            "## Executive Summary",
            "",
            f"This dossier synthesizes findings across {len(sources)} sources regarding **{topic}**.",
            "",
            "## Key Findings & Empirical Evidence",
            "",
        ]

        for kf in key_findings:
            lines.append(f"- :mag: {kf}")
        lines.append("")

        lines.extend([
            "## Source Credibility & Citations",
            "",
            "| Source / Title | URL | Credibility Score |",
            "|---|---|---|",
        ])

        for s in ranked_sources:
            lines.append(f"| {s.get('title', 'Untitled')} | {s.get('url', '-')} | `{s.get('credibility_score', 1.0)}` |")
        lines.append("")

        return "\n".join(lines)
