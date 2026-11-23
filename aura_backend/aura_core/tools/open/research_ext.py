"""Source handling, claim comparison and report rendering for ``research_topic`` (ADR-044, row 106).

Pure text-in/JSON-out; it never searches or fetches (``tools/research.py`` does that, through the tool
registry). What it adds to ``AutonomousResearchAndSynthesisTool``:

* **Source de-duplication on normalised URLs** (scheme, ``www.``, fragment, tracking parameters and a
  trailing slash do not make a second source), and on citation for local passages.
* **Claims with provenance.** Sentences are lifted from every source text with the id of the source they
  came from, so each statement in the report can be traced back to a numbered source.
* **Corroboration and contradiction.** Claims about the same subject (token overlap) from *different*
  sources are grouped into one finding. A group whose sources agree is corroborated; a pair that gives
  the same quantity different values (more than 10% apart, same unit) or opposite polarity
  ("supports X" vs "does not support X") is reported as a contradiction with both statements and both
  sources. This is lexical, not semantic: it finds numeric and negation conflicts and misses
  paraphrased disagreement, which ``coverage.notes`` says.
* **A report with an explicit coverage and gaps section**: what was searched, what answered, what
  could not be read and why, which findings rest on a single source.
"""

from __future__ import annotations

import re
from typing import Any
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from .autonomous_research_and_synthesis_tool import AutonomousResearchAndSynthesisTool as Research

__all__ = ["normalise_url", "parse_local_hits", "parse_web_results", "extract_claims", "analyse", "render_markdown"]

_TRACKING = re.compile(r"^(utm_|fbclid$|gclid$|mc_|ref$|ref_src$|igshid$)", re.IGNORECASE)
_STOP = frozenset("""a an the of and or to in on for with by from at as is are was were be been being it its this that these those
which who whom what when where how not no nor but if then than so such into over under between about against during
after before above below up down out off again further once only own same too very can will just should now has have
had do does did their there they them we you i he she his her our your""".split())
_NEGATION = re.compile(r"\b(not|no|never|cannot|can't|doesn't|don't|isn't|aren't|won't|without|fails? to|unable)\b", re.IGNORECASE)
_NUMBER = re.compile(r"(?<![\w.])(\d+(?:[.,]\d+)?)\s*(%|percent|ms|milliseconds|seconds|s|gb|mb|kb|x|times|users|requests|hours|days|years|tokens)?\b",
                     re.IGNORECASE)
_SENTENCE = re.compile(r"(?<=[.!?])\s+(?=[A-Z0-9\[(])")
_UNITS = {"percent": "%", "milliseconds": "ms", "seconds": "s", "times": "x"}


def normalise_url(url: str) -> str:
    try:
        parts = urlsplit(str(url).strip())
    except ValueError:
        return str(url).strip().lower()
    host = (parts.hostname or "").lower().removeprefix("www.")
    query = urlencode(sorted((k, v) for k, v in parse_qsl(parts.query) if not _TRACKING.match(k)))
    path = parts.path.rstrip("/") or ""
    port = f":{parts.port}" if parts.port and parts.port not in (80, 443) else ""
    return urlunsplit(("", host + port, path, query, "")).lstrip("/") if host else str(url).strip().lower()


def parse_local_hits(text: str) -> list[dict[str, Any]]:
    """Passages from ``search_knowledge``'s ``[n] citation (score x)\\ncontent`` text."""
    hits: list[dict[str, Any]] = []
    for block in re.split(r"\n\n(?=\[\d+\] )", text.strip()):
        head, _, body = block.partition("\n")
        match = re.match(r"\[(\d+)\]\s+(.*?)\s+\(score ([\d.]+)\)\s*$", head)
        if match and body.strip():
            hits.append({"citation": match.group(2)[:300], "score": float(match.group(3)), "text": body.strip()[:4000]})
    return hits


def parse_web_results(payload: dict[str, Any]) -> list[dict[str, str]]:
    out: list[dict[str, str]] = []
    for item in payload.get("results") or []:
        if isinstance(item, dict) and item.get("url"):
            out.append({"title": str(item.get("title") or item["url"])[:200], "url": str(item["url"]),
                        "snippet": str(item.get("content") or item.get("snippet") or "")[:500]})
    return out


def _tokens(text: str) -> frozenset[str]:
    words = re.findall(r"[A-Za-z][A-Za-z0-9_-]{2,}", text.lower())
    return frozenset(w for w in words if w not in _STOP)


def _numbers(text: str) -> list[tuple[float, str]]:
    found = []
    for match in _NUMBER.finditer(text):
        raw, unit = match.group(1), (match.group(2) or "").lower()
        try:
            value = float(raw.replace(",", "."))
        except ValueError:
            continue
        found.append((value, _UNITS.get(unit, unit)))
    return found


def extract_claims(text: str, source_id: int, topic_tokens: frozenset[str], limit: int = 12) -> list[dict[str, Any]]:
    claims: list[dict[str, Any]] = []
    for sentence in _SENTENCE.split(" ".join(text.split())):
        s = sentence.strip()
        if not 40 <= len(s) <= 320:
            continue
        tokens = _tokens(s)
        overlap = len(tokens & topic_tokens)
        numeric = bool(_numbers(s))
        if overlap == 0 and not (numeric and len(tokens) >= 4):
            continue
        claims.append({"text": s, "source": source_id, "tokens": tokens, "numbers": _numbers(s),
                       "negated": bool(_NEGATION.search(s)), "relevance": overlap + (1 if numeric else 0)})
    claims.sort(key=lambda c: -c["relevance"])
    return claims[:limit]


def _jaccard(a: frozenset[str], b: frozenset[str]) -> float:
    return len(a & b) / len(a | b) if a and b else 0.0


def _numeric_conflict(a: dict[str, Any], b: dict[str, Any]) -> str | None:
    for va, ua in a["numbers"]:
        for vb, ub in b["numbers"]:
            if ua == ub and max(abs(va), abs(vb)) > 0 and abs(va - vb) / max(abs(va), abs(vb)) > 0.10 \
                    and (ua or abs(va) >= 10 or abs(vb) >= 10):
                return f"{va:g}{ua} vs {vb:g}{ub}"
    return None


def analyse(claims: list[dict[str, Any]], sources: dict[int, dict[str, Any]]) -> dict[str, Any]:
    """Group claims into findings and find contradictions between different sources."""
    ordered = sorted(claims, key=lambda c: (-sources[c["source"]].get("credibility", 1.0), -c["relevance"]))
    groups: list[list[dict[str, Any]]] = []
    for claim in ordered:
        for group in groups:
            if _jaccard(group[0]["tokens"], claim["tokens"]) >= 0.45:
                group.append(claim)
                break
        else:
            groups.append([claim])
    findings: list[dict[str, Any]] = []
    for group in groups:
        ids = sorted({c["source"] for c in group})
        findings.append({"statement": group[0]["text"], "sources": ids, "corroborated": len(ids) >= 2,
                         "also_stated_as": [c["text"] for c in group[1:3] if c["text"] != group[0]["text"]]})
    findings.sort(key=lambda f: (-len(f["sources"]), f["statement"]))
    contradictions: list[dict[str, Any]] = []
    for i, a in enumerate(ordered):
        for b in ordered[i + 1:]:
            if a["source"] == b["source"]:
                continue
            similarity = _jaccard(a["tokens"], b["tokens"])
            if similarity < 0.4:
                continue
            number = _numeric_conflict(a, b)
            polarity = a["negated"] != b["negated"] and similarity >= 0.55
            if number or polarity:
                contradictions.append({
                    "kind": "different values" if number else "opposite polarity", "detail": number or "one statement negates the other",
                    "a": {"statement": a["text"], "source": a["source"]}, "b": {"statement": b["text"], "source": b["source"]},
                    "similarity": round(similarity, 2)})
    seen: set[tuple] = set()
    unique = []
    for c in contradictions:
        # one entry per pair of sources and conflicting figures, however many sentences repeat it
        key = (tuple(sorted((c["a"]["source"], c["b"]["source"]))), c["detail"])
        if key not in seen:
            seen.add(key)
            unique.append(c)
    return {"findings": findings[:25], "contradictions": unique[:15]}


def _refs(ids: list[int]) -> str:
    return "".join(f"[{i}]" for i in ids)


def render_markdown(
    topic: str, sources: dict[int, dict[str, Any]], analysis: dict[str, Any], coverage: dict[str, Any],
) -> str:
    ranked = Research.rank_sources_by_credibility([{"url": s.get("url"), "title": s["title"], "summary": s.get("note")} for s in sources.values()])
    lines = [f"# Research report: {' '.join(topic.split())}", "", "## Summary", "",
             f"{len(sources)} distinct source(s) ({coverage['sources_by_origin']}); {len(analysis['findings'])} finding(s), "
             f"{sum(1 for f in analysis['findings'] if f['corroborated'])} corroborated by more than one source; "
             f"{len(analysis['contradictions'])} contradiction(s) flagged.", "", "## Findings", ""]
    if not analysis["findings"]:
        lines.append("_No statements relevant to the topic could be extracted from the sources that were read._")
    for finding in analysis["findings"]:
        suffix = "" if finding["corroborated"] else " _(single source)_"
        lines.append(f"- {finding['statement']} {_refs(finding['sources'])}{suffix}")
    lines += ["", "## Contradictions", ""]
    if not analysis["contradictions"]:
        lines.append("_None found by the numeric and negation checks (paraphrased disagreement is not detected)._")
    for c in analysis["contradictions"]:
        lines += [f"- **{c['kind']}** ({c['detail']}):", f"  - {c['a']['statement']} [{c['a']['source']}]",
                  f"  - {c['b']['statement']} [{c['b']['source']}]"]
    lines += ["", "## Sources", "", "| # | Source | Origin | Read as | Credibility |", "|---|---|---|---|---|"]
    credibility = {(r.get("url") or r.get("title")): r.get("credibility_score") for r in ranked}
    for number, s in sorted(sources.items()):
        where = (s.get("url") or s.get("citation") or "-").replace("|", "\\|")
        title = " ".join(str(s["title"]).split()).replace("|", "\\|")
        score = s.get("credibility") if s["origin"] == "local" else credibility.get(s.get("url") or s["title"], s.get("credibility", "-"))
        lines.append(f"| {number} | {title} - {where} | {s['origin']} | {s['read_as']} | {score} |")
    lines += ["", "## Coverage and gaps", ""]
    for question, info in coverage["queries"].items():
        lines.append(f"- Query `{question}`: {info['local_passages']} local passage(s), {info['web_results']} web result(s).")
    lines.append(f"- Tools: used {coverage['tools_used'] or 'none'}; unavailable {coverage['tools_unavailable'] or 'none'}.")
    for gap in coverage["gaps"]:
        lines.append(f"- GAP: {gap}")
    for note in coverage["notes"]:
        lines.append(f"- Note: {note}")
    return "\n".join(lines)
