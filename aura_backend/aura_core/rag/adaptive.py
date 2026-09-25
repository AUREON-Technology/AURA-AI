"""The adaptive router: choose the retrieval strategy from the query.

Ten strategies are only useful if something picks the right one. Running them all would multiply
latency by ten and local-model load by more, since several cost a generation each.

**Classification is lexical, not a model call.** This is the decision that gets made on *every*
turn, and spending a generation to decide how to spend generations is a poor trade on a 7B model on
CPU -- it would add seconds to every request including the simple ones the classifier exists to
keep fast. A rule set is also inspectable and testable, which a prompt is not: when routing goes
wrong you can see exactly which signal fired.

The cost of that choice is real and worth stating: a lexical classifier misroutes phrasings it has
no rule for, and it does not generalise across languages. It is a floor, not a ceiling --
:class:`AdaptiveRouter` takes a ``classifier`` argument, so a trained classifier or an LLM-based one
can replace the default without touching any strategy. ``aura_core/ml/nn.py`` already declares an
intent-classifier architecture for exactly this.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field, replace
from typing import Any, Callable, Mapping, Sequence

from .base import (
    BaseStrategy,
    RetrievalOutcome,
    RetrievalRequest,
    StrategyTrace,
    fuse_results,
)

__all__ = ["QueryClass", "QueryClassification", "classify_query", "AdaptiveRouter"]


class QueryClass:
    """What kind of retrieval a question wants.

    Not mutually exclusive by nature -- "what did I say about how these services connect" is both
    personal and relational -- so the classifier returns an ordered set and the router composes.
    """

    FACTUAL = "factual"          # straightforward lookup
    PERSONAL = "personal"        # about the user, or something they said
    RELATIONAL = "relational"    # about connections between things
    COMPARATIVE = "comparative"  # needs several lookups and a comparison
    MEDIA = "media"              # about an image, table, or diagram
    SELF_CONTAINED = "self_contained"  # needs no corpus at all
    VERIFICATION = "verification"      # asks whether something is supported


@dataclass(frozen=True, slots=True)
class QueryClassification:
    classes: tuple[str, ...]
    signals: tuple[str, ...] = ()

    @property
    def primary(self) -> str:
        return self.classes[0] if self.classes else QueryClass.FACTUAL

    def __contains__(self, item: str) -> bool:
        return item in self.classes


#: Ordered so that a more specific class wins the `primary` slot. SELF_CONTAINED is checked first
#: because it short-circuits retrieval entirely; MEDIA before RELATIONAL because "how does this
#: diagram relate to..." is better served by finding the diagram.
_PATTERNS: tuple[tuple[str, tuple[str, ...]], ...] = (
    (
        QueryClass.SELF_CONTAINED,
        (
            r"\b(rewrite|reword|rephrase|translate|proofread|shorten|capitali[sz]e)\b",
            r"^\s*(summari[sz]e|format|convert)\s+(this|the following)\b",
            r"^[\d\s+\-*/().^%=]+\??$",
        ),
    ),
    (
        QueryClass.VERIFICATION,
        (
            # `\w+\s+` between the pronoun and the adjective allows an intervening adverb --
            # "is that actually supported", "is this really correct" -- which a rigid sequence
            # misses. Bounded to one word so it cannot span a clause.
            r"\bis (that|this|it)( \w+)? (true|correct|right|supported|accurate|verified)\b",
            r"\b(are you sure|can you verify|is there evidence|according to what)\b",
            r"\b(cite|source for|where does it say|back(ed)? up by)\b",
        ),
    ),
    (
        QueryClass.MEDIA,
        (
            r"\b(image|picture|photo|screenshot|diagram|chart|graph|figure|table|spreadsheet)\b",
            r"\bon page \d+\b",
            r"\b(scanned|scan of)\b",
        ),
    ),
    (
        QueryClass.COMPARATIVE,
        (
            r"\b(compare|comparison|versus|vs\.?|difference between|differences between)\b",
            r"\b(which (is|one) (better|stricter|cheaper|faster|safer))\b",
            r"\b(both|all three|each of)\b",
        ),
    ),
    (
        QueryClass.RELATIONAL,
        (
            r"\b(depend|depends|dependency|dependencies|related to|relationship|connected)\b",
            r"\b(who owns|what contains|which .* uses|upstream|downstream)\b",
            r"\b(impact of changing|what breaks if)\b",
            # The graph's own predicates (domain/graph.py). Without these the router only sent
            # dependency questions to the graph, so "who does Sarah report to" -- the question the
            # extracted `reports_to` edge exists to answer -- never reached it.
            r"\b(who|where) (does|do|did) .{1,60}\b(work|report|live)\b",
            r"\b(reports? to|works? (at|for)|(located|based|headquartered) in)\b",
            r"\bwho (runs|leads|heads|manages|owns|founded|acquired|works)\b",
            r"\b(ceo|cto|cfo|coo|founder|head|director|manager|lead) of\b",
            r"\b(acquired|bought) (by|whom|what|which)\b",
            r"\b(is|are|was|a) (part|member|subsidiary) of\b",
        ),
    ),
    (
        QueryClass.PERSONAL,
        (
            # Present and past tense both: "what did I say" is as common as "what I told you", and
            # only matching the past tense misses half of real phrasings.
            r"\bi (told|said|say|mentioned|mention|asked|ask)\b",
            r"\b(my|mine)\b",
            r"\b(do you remember|last (week|time)|earlier|previously)\b",
            r"\bwe (discussed|talked about|agreed)\b",
        ),
    ),
)


def classify_query(query: str) -> QueryClassification:
    """Assign zero or more classes to a query.

    Always returns at least FACTUAL, so the router always has a strategy. A question matching
    nothing is a plain lookup, which is the correct default and the cheapest path.
    """
    lowered = query.lower().strip()
    if not lowered:
        return QueryClassification(classes=(QueryClass.FACTUAL,), signals=("empty",))

    classes: list[str] = []
    signals: list[str] = []
    for name, patterns in _PATTERNS:
        for pattern in patterns:
            if re.search(pattern, lowered):
                classes.append(name)
                signals.append(f"{name}:{pattern[:24]}")
                break

    if QueryClass.FACTUAL not in classes:
        classes.append(QueryClass.FACTUAL)
    return QueryClassification(classes=tuple(classes), signals=tuple(signals))


class AdaptiveRouter(BaseStrategy):
    """Select and compose strategies per query.

    Wiring, from the container:

    ==================  ==========================================================
    Query class         Strategy
    ==================  ==========================================================
    SELF_CONTAINED      ``self_rag`` -- which skips retrieval and says why
    VERIFICATION        ``corrective`` -- the question is about evidence quality
    MEDIA               ``multimodal``
    COMPARATIVE         ``agentic`` -- decomposes into several lookups
    RELATIONAL          ``graph``
    PERSONAL            ``memory``
    FACTUAL             ``simple``
    ==================  ==========================================================

    When a query carries several classes the router runs the strategies for each and fuses the
    results with RRF, capped by ``max_parallel``. The cap exists because each additional strategy is
    at least one more retrieval and possibly a generation: two is a reasonable spend on an ambiguous
    query, five is not.

    Falls back to ``default`` whenever a selected strategy is not configured, so a partially wired
    container degrades to plain retrieval instead of raising.
    """

    name = "adaptive"

    DEFAULT_ROUTES: Mapping[str, str] = {
        QueryClass.SELF_CONTAINED: "self_rag",
        QueryClass.VERIFICATION: "corrective",
        QueryClass.MEDIA: "multimodal",
        QueryClass.COMPARATIVE: "agentic",
        QueryClass.RELATIONAL: "graph",
        QueryClass.PERSONAL: "memory",
        QueryClass.FACTUAL: "simple",
    }

    def __init__(
        self,
        strategies: Mapping[str, Any],
        *,
        default: str = "simple",
        routes: Mapping[str, str] | None = None,
        classifier: Callable[[str], QueryClassification] | None = None,
        max_parallel: int = 2,
        metrics: Any = None,
    ) -> None:
        super().__init__(metrics=metrics)
        if default not in strategies:
            raise ValueError(
                f"default strategy {default!r} is not among {sorted(strategies)}"
            )
        self._strategies = dict(strategies)
        self._default = default
        self._routes = dict(routes or self.DEFAULT_ROUTES)
        self._classify = classifier or classify_query
        self._max_parallel = max(1, max_parallel)

    def plan(self, query: str) -> tuple[QueryClassification, list[str]]:
        """Expose the routing decision without running it.

        Separate from ``retrieve`` so the decision is testable and so an admin endpoint can answer
        "why did this query route there?" -- the question every routing layer eventually has to
        answer.
        """
        classification = self._classify(query)
        chosen: list[str] = []
        for cls in classification.classes:
            name = self._routes.get(cls)
            if name and name in self._strategies and name not in chosen:
                chosen.append(name)
            if len(chosen) >= self._max_parallel:
                break
        if not chosen:
            chosen = [self._default]
        return classification, chosen

    async def _run(self, request: RetrievalRequest) -> RetrievalOutcome:
        classification, names = self.plan(request.query)
        notes = [f"classes={','.join(classification.classes)}", f"ran={','.join(names)}"]
        notes.extend(classification.signals[:3])

        outcomes = []
        for name in names:
            strategy = self._strategies[name]
            try:
                outcomes.append(await strategy.retrieve(request))
            except Exception as exc:
                # One strategy failing must not lose the others. A graph store being down should
                # not stop a relational question getting a text answer.
                notes.append(f"{name} failed: {type(exc).__name__}")

        if not outcomes:
            notes.append("all strategies failed; using default")
            outcomes.append(await self._strategies[self._default].retrieve(request))

        if len(outcomes) == 1:
            best = outcomes[0]
            return RetrievalOutcome(
                result=replace(best.result, strategy=self.name),
                memories=best.memories,
                graph_paths=best.graph_paths,
                trace=StrategyTrace(
                    name=self.name,
                    candidates=best.trace.candidates,
                    returned=best.trace.returned,
                    notes=tuple(notes),
                    children=(best.trace,),
                ),
            )

        fused = fuse_results([o.result for o in outcomes], limit=request.limit)
        fused = replace(fused, query=request.query, strategy=self.name)
        return RetrievalOutcome(
            result=fused,
            memories=tuple(m for o in outcomes for m in o.memories),
            graph_paths=tuple(p for o in outcomes for p in o.graph_paths),
            trace=StrategyTrace(
                name=self.name,
                candidates=sum(o.trace.candidates for o in outcomes),
                returned=len(fused.chunks),
                notes=tuple(notes),
                children=tuple(o.trace for o in outcomes),
            ),
        )
