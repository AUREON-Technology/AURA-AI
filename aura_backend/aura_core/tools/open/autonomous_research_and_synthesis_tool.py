import re
import unicodedata
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple, Union
from urllib.parse import urlsplit

from ._input import SourceReadError, read_source_file


def _dicts(items: Any) -> Tuple[List[Dict[str, Any]], int]:
    """Return ``(the dict items, how many items were skipped)``.

    ``None`` is an empty list. Anything that is not a list/tuple, and any item that is not a dict,
    is counted as skipped so the caller can say so instead of silently losing it.
    """
    if items is None:
        return [], 0
    if isinstance(items, dict):
        items = [items]
    if not isinstance(items, (list, tuple)):
        return [], 1
    good = [i for i in items if isinstance(i, dict)]
    return good, len(items) - len(good)


def _squash(text: Any) -> str:
    """Whitespace-collapsed, case-folded, NFKC-normalised form used to compare statements."""
    return " ".join(unicodedata.normalize("NFKC", str(text)).casefold().split())


def _escape_cell(text: Any) -> str:
    """Make a value safe inside a Markdown table cell (one line, no raw pipes)."""
    return " ".join(str(text).split()).replace("|", "\\|")


def _host(url: str) -> str:
    try:
        return (urlsplit(url).hostname or "").lower().rstrip(".")
    except ValueError:
        return ""


class AutonomousResearchAndSynthesisTool:
    """Helper for structuring research notes, deduplicating claims, ranking evidence credibility, and synthesizing dossiers.

    It never searches or fetches anything. The one file entry point, :meth:`extract_claims_from_file`,
    reads a text file or a PDF (every page) through the shared ``_input`` reader.
    """

    HIGH_CREDIBILITY_DOMAINS = (
        "github.com",
        "arxiv.org",
        "docs.python.org",
        "developer.mozilla.org",
        "cloud.google.com",
        "aws.amazon.com",
        "microsoft.com",
        "ieee.org",
        "acm.org",
    )

    # ``%`` / Arabic percent sign carry no trailing word boundary (``50% faster`` has none).
    _NUM_PAT = re.compile(
        r"(?<![\w.])\d+(?:[.,]\d+)?\s*[%٪]"
        r"|(?<![\w.])\d+(?:[.,]\d+)?\s?(?:ms|gb|mb|kb|users|requests)\b"
        r"|(?<![\w.])\d+(?:[.,]\d+)?(?:x|s)\b",
        re.I,
    )
    _KEYWORDS = (
        "achieved", "benchmarked", "demonstrated", "exceeded", "reduced",
        "حقق", "حققت", "أثبت", "أثبتت", "تجاوز", "تجاوزت", "خفّض", "خفض", "قلّل", "قلل",
    )
    # Sentence ends: Latin, Arabic question mark, full stop variants, Arabic full stop.
    _SENTENCE_END = re.compile(r"(?<=[.!?؟۔。！？])\s+")

    @staticmethod
    def normalize_sources(sources: List[Dict[str, Any]]) -> Dict[str, Any]:
        """Deduplicate and normalize research source entries by title or URL."""
        items, skipped = _dicts(sources)
        seen_keys = set()
        unique_sources: List[Dict[str, Any]] = []

        for src in items:
            title = str(src.get("title") or "").strip()
            url = str(src.get("url") or "").strip()
            key = url.lower() or title.lower()
            if not key or key in seen_keys:
                continue
            seen_keys.add(key)
            unique_sources.append({
                "title": title or "Untitled Source",
                "url": url,
                "summary": str(src.get("summary") or "").strip(),
                "confidence": src.get("confidence", "medium"),
            })

        result: Dict[str, Any] = {
            "input_count": len(items) + skipped,
            "unique_count": len(unique_sources),
            "sources": unique_sources,
        }
        if skipped:
            result["skipped_invalid"] = skipped
        return result

    @staticmethod
    def find_conflicting_claims(claims: List[Dict[str, str]]) -> List[Dict[str, Any]]:
        """Flag claims sharing the same topic with differing stances or values.

        Statements that differ only in case, spacing or Unicode form are the same statement.
        """
        items, _ = _dicts(claims)
        by_topic: Dict[str, List[Dict[str, str]]] = {}
        for c in items:
            topic = _squash(c.get("topic") or "general") or "general"
            by_topic.setdefault(topic, []).append(c)

        conflicts: List[Dict[str, Any]] = []
        for topic, group in by_topic.items():
            distinct: Dict[str, str] = {}
            for item in group:
                raw = str(item.get("statement") or "").strip()
                if raw:
                    distinct.setdefault(_squash(raw), raw)
            if len(distinct) > 1:
                conflicts.append({
                    "topic": topic,
                    "statements": sorted(distinct.values()),
                    "sources": [str(item.get("source", "")) for item in group],
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

        result: Dict[str, Any] = {
            "topic": research_topic,
            "sources_count": norm_src["unique_count"],
            "key_points": key_points,
            "conflicts_count": len(conflicts),
            "conflicts": conflicts,
            "ready": norm_src["unique_count"] > 0,
        }
        _, bad_claims = _dicts(claims)
        skipped = norm_src.get("skipped_invalid", 0)
        if skipped or bad_claims:
            result["warnings"] = [
                f"{skipped} source item(s) and {bad_claims} claim item(s) were not objects and were ignored."
            ]
        return result

    @classmethod
    def extract_claims_and_evidence(cls, text: str) -> List[Dict[str, Any]]:
        """Extract empirical sentences containing numbers, percentages, benchmark ratios, or citations."""
        if text is None:
            return []
        sentences = cls._SENTENCE_END.split(str(text).strip())
        empirical_findings: List[Dict[str, Any]] = []

        for s in sentences:
            s_clean = " ".join(s.split())  # one line, even when a PDF wrapped the sentence
            if not s_clean:
                continue
            has_number = bool(cls._NUM_PAT.search(s_clean))
            lowered = s_clean.lower()
            if has_number or any(tok in lowered for tok in cls._KEYWORDS):
                empirical_findings.append({
                    "finding": s_clean,
                    "contains_quantifier": has_number,
                })

        return empirical_findings

    @classmethod
    def extract_claims_from_file(
        cls,
        file_path: Union[str, Path],
        base_dir: Union[str, Path, None] = None,
    ) -> Dict[str, Any]:
        """Extract empirical claims from a text file or a PDF, reading the whole document.

        A PDF is read through every page (``input.pages`` / ``input.pages_read`` say so). Scanned
        image pages cannot be read without OCR; they are named in ``warnings`` and ``complete`` is
        ``False``, so "no findings" is never mistaken for "nothing there".
        """
        try:
            read = read_source_file(file_path, base_dir=base_dir)
        except SourceReadError as exc:
            return {"findings": [], "findings_count": 0, "complete": False, "error": str(exc)}

        findings = cls.extract_claims_and_evidence(read.text)
        result: Dict[str, Any] = {
            "file": str(file_path),
            "findings": findings,
            "findings_count": len(findings),
            "complete": read.complete,
            "input": read.describe(),
        }
        if read.warnings():
            result["warnings"] = read.warnings()
        return result

    @classmethod
    def rank_sources_by_credibility(cls, sources: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """Rank research sources according to domain authority, HTTPS, and citation completeness.

        The domain is judged from the URL's *host* only: ``https://evil.io/?u=github.com`` or
        ``https://evil.com/x.org/`` earn nothing for naming a trusted domain in the path or query.
        """
        items, _ = _dicts(sources)
        scored: List[Dict[str, Any]] = []

        for s in items:
            url = str(s.get("url") or "").strip().lower()
            host = _host(url)
            score = 1.0

            if url.startswith("https://"):
                score += 0.5
            if any(host == dom or host.endswith("." + dom) for dom in cls.HIGH_CREDIBILITY_DOMAINS):
                score += 2.0
            if host.endswith((".edu", ".gov", ".org")):
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
        seen: set = set()
        unique_ranked: List[Dict[str, Any]] = []
        for s in ranked_sources:
            key = str(s.get("url") or "").strip().lower() or str(s.get("title") or "").strip().lower()
            if key and key in seen:
                continue
            seen.add(key)
            unique_ranked.append(s)

        lines = [
            f"# Research Dossier: {' '.join(str(topic).split())}",
            "",
            "## Executive Summary",
            "",
            f"This dossier synthesizes findings across {len(unique_ranked)} sources regarding **{' '.join(str(topic).split())}**.",
            "",
            "## Key Findings & Empirical Evidence",
            "",
        ]

        if isinstance(key_findings, str):
            key_findings = [key_findings]
        for kf in key_findings or []:
            lines.append(f"- :mag: {' '.join(str(kf).split())}")
        lines.append("")

        lines.extend([
            "## Source Credibility & Citations",
            "",
            "| Source / Title | URL | Credibility Score |",
            "|---|---|---|",
        ])

        for s in unique_ranked:
            lines.append(
                f"| {_escape_cell(s.get('title') or 'Untitled')} | {_escape_cell(s.get('url') or '-')} "
                f"| `{s.get('credibility_score', 1.0)}` |"
            )
        lines.append("")

        return "\n".join(lines)
