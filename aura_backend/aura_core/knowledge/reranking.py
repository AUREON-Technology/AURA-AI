"""Self-hosted cross-encoder reranking.

Reranking is where most of ADR-005's precision comes from, and until now the only implementation
was :class:`~aura_core.knowledge.retrieval.HeuristicReranker` -- lexical overlap, term proximity,
and a brevity penalty. That is a genuinely useful floor and it needs no dependencies, but it cannot
score a passage that answers the question in different words, which is the case retrieval most
needs help with.

A cross-encoder can. It reads the query and the passage *together* and scores the pair, so
"how do I get my money back" against a passage titled "Refund procedure" ranks correctly without
sharing a single content word. That joint encoding is also why it cannot be used for first-stage
retrieval: scoring is O(candidates), not a vector lookup, so it only ever runs over the ~24
candidates fusion already selected.

**Runs on our own hardware.** ``sentence-transformers`` loads the weights locally, which is the
same arrangement the embedding provider already uses. The banned thing is a hosted *inference API*;
downloading open weights once and running them ourselves is exactly what the brief asks for. On a
CPU-only box a MiniLM cross-encoder scores 24 candidates in roughly 100-300 ms, which is why this
is opt-in rather than the default -- see ``AURA_KNOWLEDGE_RERANK_PROVIDER``.

**Never fails a retrieval.** Every error path degrades to the heuristic reranker rather than
raising: a reranker that takes down search when its model will not load is worse than a weaker
ranking. The engine has its own fallback too, so this is belt-and-braces on purpose -- the
difference is that degrading *here* keeps a real ranking instead of falling back to fusion order.
"""

from __future__ import annotations

import asyncio
import logging
import math
from typing import Any, Sequence

from .retrieval import HeuristicReranker

logger = logging.getLogger(__name__)

__all__ = ["CrossEncoderReranker", "EitherGateReranker", "build_reranker"]

#: Small enough to run on CPU, and trained for exactly this task. Not a general embedding model:
#: cross-encoders are trained on (query, passage) pairs with a relevance label, which is what makes
#: them better at ranking than a bi-encoder despite being far smaller.
DEFAULT_CROSS_ENCODER = "cross-encoder/ms-marco-MiniLM-L-6-v2"


class CrossEncoderReranker:
    """Scores (query, passage) pairs with a locally-loaded cross-encoder."""

    name = "cross_encoder"

    def __init__(
        self,
        model_name: str = DEFAULT_CROSS_ENCODER,
        *,
        batch_size: int = 16,
        max_candidates: int = 64,
        timeout_s: float = 10.0,
        fallback: Any = None,
        metrics: Any = None,
        revision: str = "",
    ) -> None:
        self._model_name = model_name
        self._revision = revision  # Hub commit of the weights (audit FSA-110); empty = repo head
        self._batch_size = batch_size
        # Hard ceiling on how many pairs reach the model. Scoring is linear in candidates, so an
        # unbounded list turns a caller's mistake into a multi-second stall in the request path.
        # Candidates beyond this keep their fusion order below the reranked ones.
        self._max_candidates = max_candidates
        self._timeout = timeout_s
        self._fallback = fallback or HeuristicReranker()
        self._metrics = metrics
        self._model: Any = None
        self._load_lock = asyncio.Lock()
        self._load_failed = False

    async def _ensure_model(self) -> Any:
        """Load once, lazily, off the event loop.

        The lock stops N concurrent first-requests each loading their own copy of the weights --
        the same reason the embedding provider holds one. ``_load_failed`` latches so a deployment
        without sentence-transformers installed does not retry the import on every single query;
        the first failure is logged and the rest degrade silently.
        """
        if self._model is not None:
            return self._model
        if self._load_failed:
            return None

        async with self._load_lock:
            if self._model is not None:
                return self._model
            if self._load_failed:
                return None
            try:
                from sentence_transformers import CrossEncoder
            except ImportError:
                logger.warning(
                    "cross-encoder reranking requested but sentence-transformers is not "
                    "installed; falling back to the heuristic reranker",
                    extra={"model": self._model_name},
                )
                self._load_failed = True
                return None
            try:
                # to_thread because loading reads weights from disk and builds a torch graph;
                # doing that on the event loop stalls every other in-flight request.
                self._model = await asyncio.to_thread(
                    CrossEncoder, self._model_name, revision=self._revision or None
                )
                logger.info(
                    "cross-encoder loaded",
                    extra={"model": self._model_name, "revision": self._revision or None},
                )
            except Exception as exc:
                logger.warning(
                    "cross-encoder failed to load; falling back to the heuristic reranker",
                    extra={"model": self._model_name, "error": type(exc).__name__},
                )
                self._load_failed = True
                return None
        return self._model

    async def logits(self, query: str, documents: Sequence[str]) -> list[float] | None:
        """The model's raw score for each of the first ``max_candidates`` documents, or ``None``
        when it cannot score them (not installed, failed to load, timed out, raised, or returned
        the wrong shape). ``None`` is the caller's cue to degrade; this never substitutes another
        scorer's numbers, so a combined gate (:class:`EitherGateReranker`) can tell them apart."""
        if not documents:
            return []
        model = await self._ensure_model()
        if model is None:
            return None
        # Only the first `max_candidates` are scored by the model.
        pairs = [(query, document) for document in documents[: self._max_candidates]]
        try:
            scores = await asyncio.wait_for(
                asyncio.to_thread(self._predict, model, pairs),
                timeout=self._timeout,
            )
        except asyncio.TimeoutError:
            logger.warning(
                "cross-encoder timed out; falling back to the heuristic reranker",
                extra={"candidates": len(pairs), "timeout_s": self._timeout},
            )
            return None
        except Exception as exc:
            logger.warning(
                "cross-encoder scoring failed; falling back to the heuristic reranker",
                extra={"error": type(exc).__name__},
            )
            return None
        if len(scores) != len(pairs):
            # A shape mismatch means the model is not the pairwise scorer we assume. Ranking on it
            # would silently mis-order results, which is worse than not reranking.
            logger.warning(
                "cross-encoder returned %d scores for %d pairs; falling back",
                len(scores), len(pairs),
            )
            return None
        return [float(score) for score in scores]

    async def rerank(
        self, query: str, documents: Sequence[str], *, top_k: int | None = None
    ) -> list[tuple[int, float]]:
        if not documents:
            return []

        scores = await self.logits(query, documents)
        if scores is None:
            return await self._degrade(query, documents, top_k, reason="model could not score")
        # The rest are appended in their incoming order, below everything scored, so nothing is
        # silently dropped.
        tail_start = len(scores)

        ranked = sorted(
            ((index, float(score)) for index, score in enumerate(scores)),
            key=lambda item: item[1],
            reverse=True,
        )
        # Unscored tail keeps its relative order, placed after every scored candidate. Scores here
        # are logits and can be negative, so a sentinel below the minimum is derived rather than
        # assumed to be zero.
        if tail_start < len(documents):
            floor = min((score for _i, score in ranked), default=0.0) - 1.0
            ranked.extend(
                (index, floor) for index in range(tail_start, len(documents))
            )

        if self._metrics is not None:
            counter = getattr(self._metrics, "rerank_total", None)
            if counter is not None:
                counter.labels(reranker=self.name, outcome="scored").inc()

        return ranked[:top_k] if top_k else ranked

    @staticmethod
    def _predict(model: Any, pairs: Sequence[tuple[str, str]]) -> Sequence[float]:
        """Synchronous scoring, called in a worker thread."""
        raw = model.predict(list(pairs))
        # numpy array, list, or scalar depending on version and input size.
        if hasattr(raw, "tolist"):
            raw = raw.tolist()
        if isinstance(raw, (int, float)):
            return [float(raw)]
        return [float(value) for value in raw]

    async def _degrade(
        self, query: str, documents: Sequence[str], top_k: int | None, *, reason: str
    ) -> list[tuple[int, float]]:
        if self._metrics is not None:
            counter = getattr(self._metrics, "rerank_total", None)
            if counter is not None:
                counter.labels(reranker=self.name, outcome="degraded").inc()
        logger.debug("reranker degraded", extra={"reason": reason})
        return list(await self._fallback.rerank(query, documents, top_k=top_k))


class EitherGateReranker:
    """A candidate is relevant if *either* the lexical heuristic or a cross-encoder accepts it
    (docs/plans/arabic-language_plan.md, stage 4, owner decision 17).

    The two fail in opposite places. :class:`HeuristicReranker` measures word overlap, so an
    Arabic question about an English document can never pass it, whatever the retrievers
    found; a multilingual cross-encoder (``cross-encoder/mmarco-mMiniLMv2-L12-H384-v1``) reads
    the pair across languages but measured too strict on Arabic-only documents. Measured on the
    retrieval suites (``evaluation/multilingual_retrieval_eval.py``): heuristic alone 0.70 / 0.10
    / 0.60 recall@5 on English / Arabic-asks-English / Arabic; the cross-encoder alone
    0.85 / 0.75 / 0.37.

    Each candidate scores ``max(heuristic, sigmoid(logit))``: both are on ``[0, 1]`` and share
    the engine's one relevance threshold, so the gate passes what either side would. Ordering
    follows the same combined score. When the cross-encoder cannot score (missing, timed out,
    failed) only the heuristic is used, exactly as ``reranker="heuristic"`` would be.
    """

    name = "either"

    def __init__(self, cross_encoder: CrossEncoderReranker, *, heuristic: Any = None,
                 metrics: Any = None) -> None:
        self._cross_encoder = cross_encoder
        self._heuristic = heuristic or HeuristicReranker()
        self._metrics = metrics

    async def rerank(
        self, query: str, documents: Sequence[str], *, top_k: int | None = None
    ) -> list[tuple[int, float]]:
        if not documents:
            return []
        lexical = dict(await self._heuristic.rerank(query, documents))
        logits = await self._cross_encoder.logits(query, documents)
        outcome = "degraded" if logits is None else "scored"
        combined: dict[int, float] = {}
        for index in range(len(documents)):
            score = lexical.get(index, 0.0)
            if logits is not None and index < len(logits):
                score = max(score, 1.0 / (1.0 + math.exp(-logits[index])))
            combined[index] = score
        if self._metrics is not None:
            counter = getattr(self._metrics, "rerank_total", None)
            if counter is not None:
                counter.labels(reranker=self.name, outcome=outcome).inc()
        ranked = sorted(combined.items(), key=lambda item: item[1], reverse=True)
        return ranked[:top_k] if top_k else ranked


def build_reranker(
    provider: str,
    *,
    model: str = DEFAULT_CROSS_ENCODER,
    metrics: Any = None,
    revision: str = "",
) -> Any:
    """Construct the configured reranker.

    ``heuristic`` is the default because it is the only one with bounded, predictable latency and
    no dependency: on CPU the cross-encoder adds 100-300 ms to every retrieval, which is a real
    cost on a box that is also running a 7B generator. Deployments that have the headroom get a
    meaningful precision gain from switching.

    An unknown name raises rather than silently choosing a default -- a typo in
    ``AURA_KNOWLEDGE_RERANK_PROVIDER`` should fail at startup, not quietly downgrade ranking
    quality in a way nobody notices for a month.
    """
    from ..domain.errors import ConfigurationError

    normalised = (provider or "heuristic").strip().lower()
    if normalised in {"heuristic", "", "none"}:
        return HeuristicReranker()
    if normalised in {"cross_encoder", "cross-encoder", "crossencoder"}:
        return CrossEncoderReranker(model, metrics=metrics, revision=revision)
    if normalised in {"either", "cross_encoder_or_heuristic", "cross-encoder-or-heuristic"}:
        return EitherGateReranker(
            CrossEncoderReranker(model, metrics=metrics, revision=revision), metrics=metrics,
        )
    raise ConfigurationError(
        f"unknown rerank provider {provider!r}",
        details={"supported": ["heuristic", "cross_encoder", "either"]},
    )
