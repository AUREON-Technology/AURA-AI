"""Reasoning engine: strategy selection, deliberation, verification, confidence.

Four strategies with genuinely different cost/quality profiles, selected per turn rather than
applied uniformly:

``DIRECT``            one generation. The right answer for most turns.
``CHAIN_OF_THOUGHT``  one generation with an internal reasoning instruction.
``SELF_CONSISTENCY``  N sampled generations, majority answer. Good for arithmetic and
                      classification, where independent errors do not correlate.
``TREE_OF_THOUGHTS``  bounded best-first search over partial reasoning paths. Expensive; used
                      only where branching genuinely pays.

Cost discipline is the point. Applying tree-of-thoughts to "what's my leave balance?" would
multiply the platform's bill by six for no quality gain, so :meth:`select_strategy` defaults to
``DIRECT`` and escalates only on evidence.

Reasoning artefacts are **internal**. The kernel returns an answer and a confidence assessment;
the trace goes to telemetry. Whether to surface deliberation to an end user is a product
decision this layer does not make for the caller.
"""

from __future__ import annotations

import asyncio
import json
import re
import time
from collections import Counter
from typing import Any, Mapping, Sequence

from ..domain.llm import CompletionRequest, SamplingParams
from ..domain.messages import Conversation, Message
from ..domain.reasoning import (
    ConfidenceAssessment,
    ReasoningStrategy,
    ReasoningTrace,
    Thought,
    ThoughtNode,
    Verification,
    VerificationOutcome,
    normalise_answer,
)
from ..observability.logging import get_logger

__all__ = ["ReasoningEngine", "ReasoningRequest", "ReasoningResult"]

logger = get_logger("reasoning")

_ARITHMETIC = re.compile(r"\d+\s*[-+*/%^]\s*\d+|\bhow (?:much|many)\b|\bcalculate\b|\btotal\b", re.I)
_COMPARATIVE = re.compile(r"\b(?:compare|versus|vs\.?|better|best|worse|trade-?off|pros and cons)\b", re.I)
_OPEN_ENDED = re.compile(r"\b(?:design|strategy|plan for|approach to|how (?:should|could|would) (?:we|i))\b", re.I)
_FACTUAL = re.compile(r"^(?:what|who|when|where|which)\b.{0,80}\?$", re.I | re.S)


class ReasoningRequest:
    __slots__ = ("question", "conversation", "criteria", "citations", "retrieval_strength",
                 "strategy", "max_output_tokens", "require_local")

    def __init__(
        self,
        *,
        question: str,
        conversation: Conversation,
        criteria: Sequence[str] = (),
        citations: Sequence[str] = (),
        retrieval_strength: float | None = None,
        strategy: ReasoningStrategy | None = None,
        max_output_tokens: int = 1024,
        require_local: bool = False,
    ) -> None:
        self.question = question
        self.conversation = conversation
        self.criteria = tuple(criteria)
        self.citations = tuple(citations)
        self.retrieval_strength = retrieval_strength
        self.strategy = strategy
        self.max_output_tokens = max_output_tokens
        #: Carried onto every generation this request makes. The engine builds its own
        #: ``CompletionRequest``s, so before this field existed a turn's ``require_local`` was
        #: silently dropped on the non-streaming path -- the streaming path honoured it.
        self.require_local = require_local


class ReasoningResult:
    __slots__ = ("text", "trace", "provider", "model", "usage", "generations")

    def __init__(
        self, *, text: str, trace: ReasoningTrace, provider: str | None = None,
        model: str | None = None, usage: Any = None, generations: int = 1,
    ) -> None:
        self.text = text
        self.trace = trace
        self.provider = provider
        self.model = model
        self.usage = usage
        self.generations = generations

    @property
    def confidence(self) -> ConfidenceAssessment | None:
        return self.trace.confidence


class ReasoningEngine:
    def __init__(
        self,
        *,
        router: Any,
        prompts: Any,
        default_strategy: ReasoningStrategy = ReasoningStrategy.DIRECT,
        auto_select: bool = True,
        self_consistency_samples: int = 3,
        tot_breadth: int = 3,
        tot_depth: int = 3,
        tot_max_nodes: int = 12,
        verification_enabled: bool = True,
        metrics: Any = None,
    ) -> None:
        self._router = router
        self._prompts = prompts
        self._default = default_strategy
        self._auto = auto_select
        self._samples = self_consistency_samples
        self._breadth = tot_breadth
        self._depth = tot_depth
        self._max_nodes = tot_max_nodes
        self._verify_enabled = verification_enabled
        self._metrics = metrics

    # -- strategy selection ------------------------------------------------------------

    def select_strategy(self, request: ReasoningRequest) -> ReasoningStrategy:
        """Pick a strategy from question features.

        Escalation is evidence-driven and conservative. A short factual question with strong
        retrieval gets ``DIRECT``: the answer is in the context, and deliberating about it adds
        cost and latency without adding accuracy.
        """
        if request.strategy is not None:
            return request.strategy
        if not self._auto:
            return self._default

        question = request.question.strip()

        # Well-grounded factual lookup: the work was done by retrieval.
        if request.retrieval_strength is not None and request.retrieval_strength >= 0.6:
            if _FACTUAL.match(question) or len(question) < 80:
                return ReasoningStrategy.DIRECT

        if _ARITHMETIC.search(question):
            # Independent samples rarely make the *same* arithmetic slip, so majority voting is
            # unusually effective here.
            return ReasoningStrategy.SELF_CONSISTENCY

        if _OPEN_ENDED.search(question) and len(question) > 60:
            return ReasoningStrategy.TREE_OF_THOUGHTS

        if _COMPARATIVE.search(question) or len(question) > 200:
            return ReasoningStrategy.CHAIN_OF_THOUGHT

        return self._default

    # -- execution ---------------------------------------------------------------------

    async def reason(self, request: ReasoningRequest) -> ReasoningResult:
        started = time.perf_counter()
        strategy = self.select_strategy(request)
        if self._metrics is not None:
            self._metrics.reasoning_strategy_total.labels(strategy=str(strategy)).inc()

        if strategy is ReasoningStrategy.SELF_CONSISTENCY:
            result = await self._self_consistency(request, strategy)
        elif strategy is ReasoningStrategy.TREE_OF_THOUGHTS:
            result = await self._tree_of_thoughts(request, strategy)
        elif strategy is ReasoningStrategy.CHAIN_OF_THOUGHT:
            result = await self._chain_of_thought(request, strategy)
        else:
            result = await self._direct(request, strategy)

        verification = None
        if self._verify_enabled and strategy is not ReasoningStrategy.DIRECT:
            # Verification costs a generation. Spending it on a turn that already chose a cheap
            # strategy would double that turn's cost for the least uncertain answers.
            verification = await self._verify(request, result.text)

        confidence = ConfidenceAssessment.from_signals(
            retrieval_strength=request.retrieval_strength,
            verification_rate=verification.pass_rate if verification else None,
            self_consistency=result.trace.agreement,
        )

        trace = ReasoningTrace(
            strategy=result.trace.strategy,
            thoughts=result.trace.thoughts,
            nodes=result.trace.nodes,
            candidates=result.trace.candidates,
            selected_index=result.trace.selected_index,
            verification=verification,
            confidence=confidence,
            generations_used=result.generations,
            duration_ms=(time.perf_counter() - started) * 1000.0,
        )

        if self._metrics is not None:
            self._metrics.confidence_score.labels(strategy=str(strategy)).observe(confidence.score)
            if verification is not None:
                self._metrics.verification_total.labels(outcome=str(verification.outcome)).inc()

        return ReasoningResult(
            text=result.text,
            trace=trace,
            provider=result.provider,
            model=result.model,
            usage=result.usage,
            generations=result.generations + (1 if verification else 0),
        )

    # -- strategies --------------------------------------------------------------------

    async def _direct(self, request: ReasoningRequest, strategy: ReasoningStrategy) -> ReasoningResult:
        response = await self._router.complete(
            CompletionRequest(
                conversation=request.conversation,
                sampling=SamplingParams(temperature=0.3, max_output_tokens=request.max_output_tokens),
                require_local=request.require_local,
            )
        )
        return ReasoningResult(
            text=response.text,
            trace=ReasoningTrace(strategy=strategy),
            provider=response.provider,
            model=response.model,
            usage=response.usage,
        )

    async def _chain_of_thought(
        self, request: ReasoningRequest, strategy: ReasoningStrategy
    ) -> ReasoningResult:
        """One generation with an internal reasoning instruction.

        The instruction asks the model to work through the problem before answering and to
        present only the conclusion. Reasoning that leaks into the answer is a presentation
        defect, not a feature -- the user asked a question, not for a transcript.
        """
        conversation = request.conversation.append(
            Message.system(
                "Work through this carefully before answering: identify what is being asked, "
                "what the reference material supports, and what follows from it. Present only "
                "your conclusion and the reasoning a reader needs to check it -- not your full "
                "deliberation."
            )
        )
        response = await self._router.complete(
            CompletionRequest(
                conversation=conversation,
                sampling=SamplingParams(temperature=0.3, max_output_tokens=request.max_output_tokens),
                require_local=request.require_local,
            )
        )
        return ReasoningResult(
            text=response.text,
            trace=ReasoningTrace(
                strategy=strategy,
                thoughts=(Thought(content="chain-of-thought instruction applied", kind="analysis"),),
            ),
            provider=response.provider,
            model=response.model,
            usage=response.usage,
        )

    async def _self_consistency(
        self, request: ReasoningRequest, strategy: ReasoningStrategy
    ) -> ReasoningResult:
        """Sample N answers and take the majority.

        Samples run concurrently -- they are independent by construction, so serialising them
        would multiply latency by N for no benefit. Temperature must be non-zero or the samples
        are identical and the vote is meaningless.
        """
        requests = [
            CompletionRequest(
                conversation=request.conversation,
                sampling=SamplingParams(temperature=0.7, max_output_tokens=request.max_output_tokens),
                require_local=request.require_local,
            )
            for _ in range(self._samples)
        ]
        responses = await asyncio.gather(
            *(self._router.complete(r) for r in requests), return_exceptions=True
        )
        successful = [r for r in responses if not isinstance(r, BaseException)]
        if not successful:
            # Every sample failed; fall back to one deterministic attempt so the turn still has
            # a chance to produce an answer.
            return await self._direct(request, strategy)

        candidates = [r.text for r in successful]
        normalised = [_normalise_answer(t) for t in candidates]
        modal, _count = Counter(normalised).most_common(1)[0]
        selected_index = normalised.index(modal)

        return ReasoningResult(
            text=candidates[selected_index],
            trace=ReasoningTrace(
                strategy=strategy,
                candidates=tuple(candidates),
                selected_index=selected_index,
            ),
            provider=successful[selected_index].provider,
            model=successful[selected_index].model,
            usage=successful[selected_index].usage,
            generations=len(successful),
        )

    async def _tree_of_thoughts(
        self, request: ReasoningRequest, strategy: ReasoningStrategy
    ) -> ReasoningResult:
        """Bounded best-first search over partial reasoning paths.

        Expansion is capped by ``tot_max_nodes`` in addition to breadth and depth. Without a
        hard node budget the search cost is ``breadth^depth``, which is how a tree-of-thoughts
        implementation quietly becomes the most expensive thing in a system.
        """
        nodes: list[ThoughtNode] = []
        root = ThoughtNode(id="n0", content=request.question, depth=0, value=0.5)
        nodes.append(root)
        frontier = [root]
        generations = 0

        for depth in range(1, self._depth + 1):
            if not frontier or len(nodes) >= self._max_nodes:
                break

            # Expand only the single most promising frontier node per level: expanding the whole
            # frontier is what turns the cost exponential.
            parent = max(frontier, key=lambda n: n.value)
            expansions = await self._expand(request, parent, self._breadth)
            generations += 1
            if not expansions:
                break

            children: list[ThoughtNode] = []
            for index, content in enumerate(expansions):
                if len(nodes) >= self._max_nodes:
                    break
                node = ThoughtNode(
                    id=f"n{len(nodes)}", content=content, parent_id=parent.id, depth=depth
                )
                score = await self._evaluate_path(request, node, nodes)
                generations += 1
                node = ThoughtNode(
                    id=node.id, content=node.content, parent_id=node.parent_id,
                    depth=depth, value=score, terminal=depth >= self._depth,
                )
                nodes.append(node)
                children.append(node)

            frontier = children

        best = max(nodes[1:], key=lambda n: n.value) if len(nodes) > 1 else root
        path = _path_to(nodes, best)
        conversation = request.conversation.append(
            Message.system(
                "You explored this reasoning path:\n"
                + "\n".join(f"- {n.content}" for n in path[1:])
                + "\n\nWrite the final answer it supports. Do not describe the exploration."
            )
        )
        response = await self._router.complete(
            CompletionRequest(
                conversation=conversation,
                sampling=SamplingParams(temperature=0.3, max_output_tokens=request.max_output_tokens),
                require_local=request.require_local,
            )
        )
        generations += 1

        return ReasoningResult(
            text=response.text,
            trace=ReasoningTrace(
                strategy=strategy,
                nodes=tuple(nodes),
                thoughts=tuple(Thought(content=n.content, kind="hypothesis") for n in path[1:]),
            ),
            provider=response.provider,
            model=response.model,
            usage=response.usage,
            generations=generations,
        )

    async def _expand(
        self, request: ReasoningRequest, parent: ThoughtNode, breadth: int
    ) -> list[str]:
        prompt = (
            f"Question: {request.question}\n\n"
            f"Reasoning so far: {parent.content}\n\n"
            f"Propose {breadth} distinct next steps in the reasoning. Each on its own line, "
            f"one sentence, no numbering, materially different from each other."
        )
        try:
            response = await self._router.complete(
                CompletionRequest(
                    conversation=Conversation.of([Message.user(prompt)]),
                    sampling=SamplingParams(temperature=0.8, max_output_tokens=400),
                    require_local=request.require_local,
                )
            )
        except Exception:
            return []
        lines = [
            re.sub(r"^\s*(?:[-*]|\d+[.)])\s*", "", line).strip()
            for line in response.text.splitlines()
            if line.strip()
        ]
        return [line for line in lines if len(line) > 10][:breadth]

    async def _evaluate_path(
        self, request: ReasoningRequest, node: ThoughtNode, nodes: Sequence[ThoughtNode]
    ) -> float:
        rendered = self._prompts.render(
            "task.thought_evaluation",
            {
                "goal": request.question,
                "path": "\n".join(n.content for n in _path_to(nodes, node)),
            },
        )
        try:
            response = await self._router.complete(
                CompletionRequest(
                    conversation=Conversation.of([Message.user(rendered.text)]),
                    sampling=SamplingParams(temperature=0.0, max_output_tokens=120),
                    require_local=request.require_local,
                )
            )
            payload = _parse_json_object(response.text)
            return max(0.0, min(1.0, float(payload.get("score", 0.5))))
        except Exception:
            # A neutral score keeps the search running rather than collapsing it toward whichever
            # branch happened to be evaluated successfully.
            return 0.5

    # -- verification ------------------------------------------------------------------

    async def _verify(self, request: ReasoningRequest, answer: str) -> Verification:
        started = time.perf_counter()
        rendered = self._prompts.render(
            "task.verification",
            {
                "question": request.question,
                "answer": answer,
                "criteria": list(request.criteria),
                "sources": list(request.citations),
            },
        )
        try:
            response = await self._router.complete(
                CompletionRequest(
                    conversation=Conversation.of([Message.user(rendered.text)]),
                    sampling=SamplingParams(temperature=0.0, max_output_tokens=600),
                    require_local=request.require_local,
                )
            )
            payload = _parse_json_object(response.text)
        except Exception:
            # An unavailable verifier means "we don't know", not "it passed". Reporting
            # INCONCLUSIVE keeps confidence honest instead of inflating it.
            return Verification(
                outcome=VerificationOutcome.INCONCLUSIVE,
                notes="verification unavailable",
                duration_ms=(time.perf_counter() - started) * 1000.0,
            )

        checks = tuple(
            (
                str(c.get("criterion", ""))[:200],
                bool(c.get("passed", False)),
                str(c.get("note", ""))[:300],
            )
            for c in (payload.get("checks") or [])
            if isinstance(c, Mapping)
        )
        raw_verdict = str(payload.get("verdict", "inconclusive")).lower()
        outcome = {
            "passed": VerificationOutcome.PASSED,
            "failed": VerificationOutcome.FAILED,
        }.get(raw_verdict, VerificationOutcome.INCONCLUSIVE)

        return Verification(
            outcome=outcome,
            checks=checks,
            duration_ms=(time.perf_counter() - started) * 1000.0,
        )


def _path_to(nodes: Sequence[ThoughtNode], node: ThoughtNode) -> list[ThoughtNode]:
    by_id = {n.id: n for n in nodes}
    path = [node]
    while path[-1].parent_id is not None and path[-1].parent_id in by_id:
        path.append(by_id[path[-1].parent_id])
    return list(reversed(path))


# The vote and ``ReasoningTrace.agreement`` must use the same notion of sameness, so the
# normalisation lives in the domain and both call it.
_normalise_answer = normalise_answer


def _parse_json_object(text: str) -> dict[str, Any]:
    stripped = text.strip()
    if stripped.startswith("```"):
        stripped = re.sub(r"^```(?:json)?\s*|\s*```$", "", stripped, flags=re.S).strip()
    try:
        parsed = json.loads(stripped)
        return parsed if isinstance(parsed, dict) else {}
    except json.JSONDecodeError:
        pass
    start = stripped.find("{")
    if start < 0:
        return {}
    depth = 0
    for index in range(start, len(stripped)):
        if stripped[index] == "{":
            depth += 1
        elif stripped[index] == "}":
            depth -= 1
            if depth == 0:
                try:
                    parsed = json.loads(stripped[start : index + 1])
                    return parsed if isinstance(parsed, dict) else {}
                except json.JSONDecodeError:
                    return {}
    return {}
