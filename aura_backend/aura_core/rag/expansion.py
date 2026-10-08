"""Strategies that change the *query* before retrieving.

Both address the same weakness: a user's phrasing and the corpus's phrasing often do not overlap,
and a single embedding of a short question is a narrow probe.

* :class:`MultiQueryRAG` -- ask the same thing several ways, retrieve each, fuse the rankings.
* :class:`HyDERAG` -- write a hypothetical answer and retrieve with *that*, because an answer looks
  more like the passage that contains it than the question does.

Both cost a generation, which on a 7B model on CPU is seconds rather than milliseconds. They are
therefore opt-in, and both degrade to their inner strategy when the generation budget is zero or
the model is unavailable -- losing the enhancement, never the retrieval.
"""

from __future__ import annotations

import re
from dataclasses import replace
from typing import Any, Sequence

from ..domain.llm import CompletionRequest, SamplingParams
from ..domain.messages import Conversation, Message
from .base import (
    BaseStrategy,
    RetrievalOutcome,
    RetrievalRequest,
    StrategyTrace,
    fuse_results,
)

__all__ = ["MultiQueryRAG", "HyDERAG", "QueryRewriter"]


async def _generate(router: Any, prompt: str, *, max_tokens: int, temperature: float) -> str:
    """One short, self-contained generation.

    A bare conversation with no history on purpose: these prompts are mechanical transformations of
    a query, and including the dialogue would let earlier turns steer the rewrite -- which is both a
    quality problem and, when the history contains retrieved content, a prompt-injection path into
    the query itself.
    """
    response = await router.complete(
        CompletionRequest(
            conversation=Conversation.of([Message.user(prompt)]),
            sampling=SamplingParams(temperature=temperature, max_output_tokens=max_tokens),
        )
    )
    return response.text or ""


def _split_lines(text: str, *, limit: int) -> list[str]:
    """Pull a list out of a model's reply without demanding it format perfectly.

    Local models add preambles, number their lines inconsistently, and sometimes wrap output in a
    code fence. Insisting on strict JSON here would make the strategy fail on presentation rather
    than on substance, and the fallback for a parse failure is silently losing the expansion.
    """
    lines = []
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("```"):
            continue
        # Strip "1.", "1)", "-", "*" and a stray leading quote.
        line = re.sub(r'^\s*(?:\d+[.)]|[-*•])\s*', "", line).strip().strip('"')
        # Drop obvious preamble ("Here are three versions:") -- it ends in a colon and contains no
        # question content worth retrieving on.
        if line.endswith(":") and len(line.split()) <= 8:
            continue
        if line:
            lines.append(line)
        if len(lines) >= limit:
            break
    return lines


class QueryRewriter:
    """Turns a follow-up into a standalone query.

    Not a retrieval strategy -- a collaborator the strategies use. "And the second one?" retrieves
    nothing on its own, however good the index is, because the referent is in the previous turn. The
    kernel's context assembly resolves this for *generation*; retrieval needs it too and happens
    first.

    Returns the original query unchanged on any failure. A rewrite that silently loses the user's
    actual words is worse than not rewriting.
    """

    def __init__(self, router: Any, *, max_history: int = 6) -> None:
        self._router = router
        self._max_history = max_history

    async def rewrite(self, request: RetrievalRequest) -> tuple[str, bool]:
        """Return ``(query, rewritten)``."""
        if not request.history or request.max_generations <= 0:
            return request.query, False

        # Only bother when the query looks dependent on context. Rewriting "what is our refund
        # policy" wastes a generation and can only make it worse.
        if not self._looks_dependent(request.query):
            return request.query, False

        history = request.history[-self._max_history :]
        transcript = "\n".join(f"{role}: {content}" for role, content in history)
        prompt = (
            "Rewrite the final question so it can be understood on its own, without the "
            "conversation. Resolve pronouns and references to earlier turns. Keep it short and "
            "keep the user's meaning exactly. Reply with the rewritten question and nothing else."
            f"\n\nConversation:\n{transcript}\n\nFinal question: {request.query}"
        )
        try:
            text = await _generate(self._router, prompt, max_tokens=80, temperature=0.0)
        except Exception:
            return request.query, False

        candidate = _split_lines(text, limit=1)
        if not candidate:
            return request.query, False
        rewritten = candidate[0]
        # Guard against a model that "helpfully" answers the question instead of rewriting it, or
        # returns something far longer than the input.
        if len(rewritten) > max(120, len(request.query) * 4):
            return request.query, False
        return rewritten, True

    @staticmethod
    def _looks_dependent(query: str) -> bool:
        lowered = query.lower().strip()
        if len(lowered.split()) <= 3:
            return True
        pronouns = (
            " it", " that", " this", " those", " these", " they", " them", " he ", " she ",
            "the second", "the first", "the other", "the same",
        )
        return lowered.startswith(("and ", "what about", "why", "how about")) or any(
            p in f" {lowered}" for p in pronouns
        )


class MultiQueryRAG(BaseStrategy):
    """Retrieve for several paraphrases of the query and fuse the rankings.

    Helps most where the corpus uses different vocabulary than the user: "how do I get my money
    back" against a document titled "Refund procedure". One embedding of one phrasing is a single
    point in the space; several phrasings cover more of the neighbourhood.

    Fused with RRF rather than by score, because scores from different queries are not comparable --
    a chunk can score 0.8 for a narrow paraphrase and 0.5 for a broad one without being less
    relevant. Rank agreement across paraphrases is the signal worth trusting.

    Costs exactly one generation regardless of ``variants``: the variants come from a single reply.
    """

    name = "multi_query"

    def __init__(
        self,
        inner: Any,
        router: Any,
        *,
        variants: int = 3,
        metrics: Any = None,
    ) -> None:
        super().__init__(metrics=metrics)
        self._inner = inner
        self._router = router
        self._variants = variants

    async def _run(self, request: RetrievalRequest) -> RetrievalOutcome:
        notes: list[str] = []
        generations = 0
        queries = [request.query]

        if request.max_generations <= 0:
            notes.append("degraded: no generation budget")
        else:
            prompt = (
                f"Write {self._variants} alternative phrasings of this question that a search "
                "engine might match against different wordings in a document. Vary the vocabulary, "
                "keep the meaning identical. One per line, no numbering, no commentary."
                f"\n\nQuestion: {request.query}"
            )
            try:
                text = await _generate(self._router, prompt, max_tokens=200, temperature=0.7)
                generations = 1
                extra = _split_lines(text, limit=self._variants)
                # Deduplicate case-insensitively, keeping the original first: a model asked for
                # paraphrases often returns the input as one of them.
                seen = {request.query.lower()}
                for variant in extra:
                    if variant.lower() not in seen:
                        queries.append(variant)
                        seen.add(variant.lower())
                if len(queries) == 1:
                    notes.append("no usable variants")
            except Exception as exc:
                notes.append(f"expansion failed: {type(exc).__name__}")

        outcomes = []
        for query in queries:
            outcomes.append(await self._inner.retrieve(request.with_query(query)))

        fused = fuse_results(
            [o.result for o in outcomes],
            limit=request.limit,
        )
        # The fused result answers the *original* question, whatever phrasings found it.
        fused = replace(fused, query=request.query, strategy=self.name)

        return RetrievalOutcome(
            result=fused,
            memories=tuple(m for o in outcomes for m in o.memories),
            graph_paths=tuple(p for o in outcomes for p in o.graph_paths),
            trace=StrategyTrace(
                name=self.name,
                generations=generations,
                candidates=fused.candidates_considered,
                returned=len(fused.chunks),
                notes=tuple(notes) + (f"queries={len(queries)}",),
                children=tuple(o.trace for o in outcomes),
            ),
        )


class HyDERAG(BaseStrategy):
    """Retrieve using a hypothetical answer rather than the question.

    The premise: in embedding space a *passage answering* a question sits closer to the real passage
    than the question does, because questions and statements are phrased differently. So the model
    writes a plausible answer -- factually wrong is fine, it is never shown to anyone -- and that
    text becomes the query.

    Strongest on questions whose answer has predictable vocabulary ("what is the escalation
    procedure"). Weakest on questions about specific named entities, where a fabricated answer
    invents names that pull retrieval toward nothing. Because of that asymmetry this is not a good
    default; the adaptive router selects it deliberately.

    The hypothetical document is retained in the trace notes only as a length, never as text. It is
    model-generated content derived from a user query and has no place in logs.
    """

    name = "hyde"

    def __init__(
        self,
        inner: Any,
        router: Any,
        *,
        include_original: bool = True,
        metrics: Any = None,
    ) -> None:
        super().__init__(metrics=metrics)
        self._inner = inner
        self._router = router
        self._include_original = include_original

    async def _run(self, request: RetrievalRequest) -> RetrievalOutcome:
        notes: list[str] = []
        generations = 0
        queries: list[str] = []

        if request.max_generations <= 0:
            notes.append("degraded: no generation budget")
        else:
            prompt = (
                "Write a short factual paragraph that would answer this question, as if it were "
                "an extract from internal documentation. Do not hedge, do not say you are unsure, "
                "and do not mention the question. Two or three sentences."
                f"\n\nQuestion: {request.query}"
            )
            try:
                hypothetical = await _generate(
                    self._router, prompt, max_tokens=220, temperature=0.3
                )
                generations = 1
                hypothetical = hypothetical.strip()
                if hypothetical:
                    queries.append(hypothetical)
                    notes.append(f"hypothetical={len(hypothetical)}ch")
                else:
                    notes.append("empty hypothetical")
            except Exception as exc:
                notes.append(f"hyde failed: {type(exc).__name__}")

        # Keeping the original query alongside is what makes this safe to use by default where the
        # router chooses it: if the hypothetical drifted off-topic, the original still retrieves.
        if self._include_original or not queries:
            queries.append(request.query)

        outcomes = [await self._inner.retrieve(request.with_query(q)) for q in queries]
        fused = fuse_results([o.result for o in outcomes], limit=request.limit)
        fused = replace(fused, query=request.query, strategy=self.name)

        return RetrievalOutcome(
            result=fused,
            memories=tuple(m for o in outcomes for m in o.memories),
            graph_paths=tuple(p for o in outcomes for p in o.graph_paths),
            trace=StrategyTrace(
                name=self.name,
                generations=generations,
                candidates=fused.candidates_considered,
                returned=len(fused.chunks),
                notes=tuple(notes),
                children=tuple(o.trace for o in outcomes),
            ),
        )
