"""``research_topic``: multi-step research over local knowledge and (when available) the web (ADR-044).

``synthesize_research`` structures notes the model already gathered. This tool *gathers* them, by calling
other registered tools, then compares what they returned:

1. ``search_knowledge`` over the caller's own documents for the topic and each follow-up query;
2. ``web_search`` (only when the operator has configured a search backend) for the same queries;
3. ``fetch_url`` for the most credible new pages (only on hosts the operator allowed);
4. claims are lifted from every source with their provenance, grouped, checked for numeric and
   negation conflicts, and rendered as a cited report with an explicit coverage-and-gaps section.

It composes tools; it does not replace them. Every call goes through ``ToolRegistry.execute`` with
**the caller's own** :class:`ToolContext` -- so persona grants, risk ceilings, the autonomy profile,
confirmation and the result guard apply exactly as if the model had called ``web_search`` itself. A
tool the caller may not use, or that this deployment does not register, is reported under
``coverage.tools_unavailable`` and ``gaps``; it is never bypassed. Nothing here reaches the gateway or
any adapter: the only things it can touch are other tools.

Because the output is built from other people's text it is tagged ``untrusted_content``, and
``nested_dispatch`` so the registry does not hold a concurrency slot while the nested calls need theirs.
"""

from __future__ import annotations

import asyncio
import json
import time
from typing import Any, Callable
from uuid import uuid4

from ..domain.errors import ToolExecutionError
from ..domain.identifiers import ToolCallId
from ..domain.messages import ToolCall
from ..domain.tools import NESTED_DISPATCH_TAG, ToolCategory, ToolSensitivity
from ..ports.runtime import Tool, ToolContext
from .base import param, tool
from .open.autonomous_research_and_synthesis_tool import AutonomousResearchAndSynthesisTool as Research
from .open.research_ext import (
    analyse, extract_claims, normalise_url, parse_local_hits, parse_web_results, render_markdown, _tokens,
)

__all__ = ["build_research_tools", "RESEARCH_TOOL_NAMES"]

RESEARCH_TOOL_NAMES: frozenset[str] = frozenset({"research_topic"})
_MAX_QUERIES = 5
_MAX_FETCH = 5
_PER_SOURCE_TEXT = 20_000


def build_research_tools(registry: Callable[[], Any]) -> list[Tool]:
    """``registry`` is a zero-argument resolver of the :class:`ToolRegistry` this tool composes through
    (a resolver, because the registry is still being built when the tool is created)."""

    @tool(
        "research_topic",
        "Research a topic across the user's own knowledge base, then the web when search is configured: "
        "local search, web search, fetch the best new pages, de-duplicate sources, compare claims "
        "(numeric and negation conflicts) and return a cited report with a coverage-and-gaps section. "
        "Calls other tools as you; anything you may not use is listed as unavailable.",
        param("topic", "string", "What to research.", max_length=500),
        param("queries", "array", "Follow-up queries to run as well (up to 4).", required=False, item_type="string"),
        param("use_local", "boolean", "Search the user's knowledge base (default true).", required=False, default=True),
        param("use_web", "boolean", "Use web_search and fetch_url when available (default true).", required=False,
              default=True),
        param("max_sources", "integer", "Web pages to read in full (default 4, at most 5).", required=False,
              default=4, minimum=0, maximum=_MAX_FETCH),
        param("time_budget_s", "number", "Stop starting new steps after this many seconds (default 90).",
              required=False, default=90, minimum=10, maximum=150),
        category=ToolCategory.KNOWLEDGE, sensitivity=ToolSensitivity.PERSONAL, timeout_s=175.0,
        max_result_chars=20_000, tags=("untrusted_content", NESTED_DISPATCH_TAG),
    )
    async def research_topic(
        topic: str, context: ToolContext, queries: list | None = None, use_local: bool = True,
        use_web: bool = True, max_sources: int = 4, time_budget_s: float = 90.0,
    ) -> str:
        name = "research_topic"
        if not topic.strip():
            raise ToolExecutionError("topic is empty", tool=name)
        reg = registry()
        started = time.monotonic()
        steps: list[dict[str, Any]] = []
        used: set[str] = set()
        unavailable: dict[str, str] = {}
        gaps: list[str] = []

        def over_budget() -> bool:
            return time.monotonic() - started > time_budget_s

        async def call(tool_name: str, arguments: dict[str, Any]) -> str | None:
            """One nested call through the registry as this caller; ``None`` when it did not produce output."""
            if tool_name in unavailable:
                return None
            if tool_name not in reg:
                unavailable[tool_name] = "not registered in this deployment"
                return None
            result, _report = await reg.execute(
                ToolCall(id=ToolCallId(f"research-{uuid4().hex[:12]}"), name=tool_name, arguments=arguments), context,
                capabilities=tuple(context.capabilities))
            steps.append({"tool": tool_name, "arguments": {k: (v[:80] if isinstance(v, str) else v) for k, v in arguments.items()},
                          "ok": result.ok, "error_kind": result.error_kind})
            if result.ok:
                used.add(tool_name)
                return result.content
            if result.error_kind in ("policy_denied", "confirmation_required", "not_found", "unknown_tool"):
                unavailable.setdefault(tool_name, f"{result.error_kind}: {result.content[:160]}")
            else:
                gaps.append(f"{tool_name}({_short(arguments)}) failed: {result.content[:160]}")
            return None

        questions = [topic.strip(), *[str(q).strip() for q in (queries or []) if str(q).strip()][: _MAX_QUERIES - 1]]
        sources: dict[int, dict[str, Any]] = {}
        by_key: dict[str, int] = {}
        texts: dict[int, str] = {}
        coverage_queries: dict[str, dict[str, int]] = {}

        def add_source(key: str, **fields: Any) -> int:
            if key in by_key:
                return by_key[key]
            number = len(sources) + 1
            by_key[key] = number
            sources[number] = fields
            return number

        topic_tokens = _tokens(topic) | _tokens(" ".join(questions[1:]))
        for question in questions:
            info = coverage_queries.setdefault(question, {"local_passages": 0, "web_results": 0})
            if use_local and not over_budget():
                text = await call("search_knowledge", {"query": question[:500], "limit": 5})
                for hit in parse_local_hits(text or ""):
                    info["local_passages"] += 1
                    number = add_source(f"local:{hit['citation']}", title=hit["citation"], citation=hit["citation"], origin="local",
                                        read_as="passage", credibility=3.0)
                    texts[number] = (texts.get(number, "") + " " + hit["text"])[:_PER_SOURCE_TEXT]
                if text is not None and info["local_passages"] == 0:
                    gaps.append(f"no passage of the user's knowledge base passed the relevance threshold for {question!r}")
            if use_web and not over_budget():
                payload = await call("web_search", {"query": question[:300], "limit": 6})
                try:
                    results = parse_web_results(json.loads(payload)) if payload else []
                except ValueError:
                    results = []
                    gaps.append(f"web_search returned something that was not JSON for {question!r}")
                info["web_results"] = len(results)
                for item in results:
                    number = add_source(f"web:{normalise_url(item['url'])}", title=item["title"], url=item["url"], origin="web",
                                        read_as="search snippet only", note=item["snippet"])
                    if item["snippet"] and item["snippet"] not in texts.get(number, ""):
                        snippet = item["snippet"].strip()
                        texts[number] = (texts.get(number, "") + " " + snippet + ("" if snippet[-1] in ".!?" else "."))[:_PER_SOURCE_TEXT]
        # step 3: read the most credible web pages in full
        web_numbers = [n for n, s in sources.items() if s["origin"] == "web"]
        ranked = Research.rank_sources_by_credibility([{"url": sources[n]["url"], "title": sources[n]["title"], "n": n} for n in web_numbers])
        for entry in ranked:
            sources[entry["n"]]["credibility"] = entry["credibility_score"]
        if use_web and max_sources > 0 and "fetch_url" not in unavailable:
            to_read = [entry["n"] for entry in ranked][:max_sources]
            if to_read and over_budget():
                gaps.append("time budget reached before any page could be read in full")
            elif to_read:
                pages = await asyncio.gather(*(call("fetch_url", {"url": sources[n]["url"]}) for n in to_read))
                for number, page in zip(to_read, pages):
                    if page:
                        texts[number] = page[:_PER_SOURCE_TEXT]
                        sources[number]["read_as"] = "full page"
                    elif "fetch_url" not in unavailable:
                        gaps.append(f"could not read {sources[number]['url']} in full; its snippet was used")
        if use_web and "web_search" in unavailable:
            gaps.append(f"web search was not used: {unavailable['web_search']}")
        if use_web and web_numbers and "fetch_url" in unavailable:
            gaps.append(f"pages were not read in full: fetch_url {unavailable['fetch_url']}")
        if use_local and "search_knowledge" in unavailable:
            gaps.append(f"local knowledge was not searched: {unavailable['search_knowledge']}")
        snippet_only = [n for n in web_numbers if sources[n]["read_as"] == "search snippet only"]
        if snippet_only:
            gaps.append(f"{len(snippet_only)} web source(s) rest on a search snippet only: " + ", ".join(f"[{n}]" for n in snippet_only[:8]))

        claims: list[dict[str, Any]] = []
        for number, text in texts.items():
            claims += extract_claims(text, number, topic_tokens)
        analysis = analyse(claims, sources) if claims else {"findings": [], "contradictions": []}
        single = [f for f in analysis["findings"] if not f["corroborated"]]
        if single:
            gaps.append(f"{len(single)} of {len(analysis['findings'])} findings rest on a single source")
        if not sources:
            gaps.append("no source was found at all: the answer to this topic is not in what could be searched")
        origin_counts: dict[str, int] = {}
        for s in sources.values():
            origin_counts[s["origin"]] = origin_counts.get(s["origin"], 0) + 1
        coverage = {
            "queries": coverage_queries, "tools_used": sorted(used), "tools_unavailable": unavailable,
            "gaps": gaps, "sources_by_origin": origin_counts,
            "elapsed_s": round(time.monotonic() - started, 1), "time_budget_s": time_budget_s,
            "notes": ["Contradictions are found by numeric-value and negation checks over sentence overlap; paraphrased "
                      "disagreement is not detected.",
                      "Source text is other people's text and was not verified."],
        }
        public_sources = [{"id": n, **{k: v for k, v in s.items() if k in ("title", "url", "citation", "origin", "read_as", "credibility")}}
                          for n, s in sources.items()]
        return json.dumps({
            "topic": topic, "ready": bool(sources), "sources": public_sources,
            "findings": analysis["findings"], "contradictions": analysis["contradictions"], "coverage": coverage,
            "steps": steps, "report_markdown": render_markdown(topic, sources, analysis, coverage),
        }, ensure_ascii=False, default=str)

    return [research_topic]


def _short(arguments: dict[str, Any]) -> str:
    return ", ".join(f"{k}={str(v)[:40]!r}" for k, v in arguments.items())
