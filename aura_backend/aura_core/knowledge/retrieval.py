"""Hybrid retrieval: BM25, reciprocal rank fusion, reranking, MMR (ADR-005).

Dense-only retrieval fails exactly where an assistant needs strength: identifiers, error codes,
product names, acronyms. A query for ``AURA-4419`` retrieves text that is semantically "about
tickets" rather than that ticket. Lexical search handles those; dense search handles paraphrase.
Neither is sufficient alone.

Four stages, each independently disable-able:

1. **Retrieve** dense and lexical candidate lists (the engine runs them concurrently).
2. **Fuse** with RRF -- rank-based, so it needs no per-corpus score normalisation and stays
   robust when one retriever returns noise.
3. **Rerank** with a cross-encoder or the zero-dependency heuristic below.
4. **Threshold and diversify** -- a score floor that can return *nothing*, then MMR so three
   chunks of the same paragraph do not fill the budget.
"""

from __future__ import annotations

import math
import re
from collections import Counter
from dataclasses import dataclass
from typing import Iterable, Mapping, Sequence

__all__ = [
    "BM25Index",
    "reciprocal_rank_fusion",
    "HeuristicReranker",
    "maximal_marginal_relevance",
    "tokenize",
]

_TOKEN = re.compile(r"[a-z0-9]+(?:[-_.][a-z0-9]+)*")

# Deliberately short. An aggressive stopword list removes the function words that distinguish
# "leave for staff" from "leave of staff", and BM25's IDF term already discounts common words.
_STOPWORDS = frozenset(
    "a an the and or of to in on at is are was were be been for with by as it its this that "
    "these those from".split()
)


def tokenize(text: str, *, keep_stopwords: bool = False) -> list[str]:
    """Lowercase word tokens, preserving identifier punctuation.

    ``AURA-4419`` and ``v2.1.0`` stay whole: they are precisely the tokens lexical search exists
    to match, and splitting them on punctuation would defeat the point of having a lexical
    retriever at all.
    """
    tokens = _TOKEN.findall(text.lower())
    if keep_stopwords:
        return tokens
    return [t for t in tokens if t not in _STOPWORDS]


class BM25Index:
    """Okapi BM25 over an in-process corpus.

    ~120 lines, no Elasticsearch. At the corpus size a single-VPS deployment holds (tens of
    thousands of chunks) an in-process inverted index is milliseconds per query, and adding a
    second heavyweight datastore for that would be a poor trade (ADR-005). Postgres full-text
    search is the documented drop-in for Stage 2.

    ``k1`` controls term-frequency saturation and ``b`` length normalisation; the values here
    are the standard defaults, which are well behaved across corpora.
    """

    def __init__(self, *, k1: float = 1.5, b: float = 0.75) -> None:
        self._k1 = k1
        self._b = b
        self._documents: dict[str, list[str]] = {}
        self._frequencies: dict[str, Counter[str]] = {}
        self._document_frequency: Counter[str] = Counter()
        self._length_sum = 0

    def add(self, doc_id: str, text: str) -> None:
        if doc_id in self._documents:
            self.remove(doc_id)
        tokens = tokenize(text)
        if not tokens:
            return
        counts = Counter(tokens)
        self._documents[doc_id] = tokens
        self._frequencies[doc_id] = counts
        for term in counts:
            self._document_frequency[term] += 1
        self._length_sum += len(tokens)

    def add_many(self, items: Iterable[tuple[str, str]]) -> None:
        for doc_id, text in items:
            self.add(doc_id, text)

    def remove(self, doc_id: str) -> None:
        tokens = self._documents.pop(doc_id, None)
        if tokens is None:
            return
        counts = self._frequencies.pop(doc_id, Counter())
        for term in counts:
            self._document_frequency[term] -= 1
            if self._document_frequency[term] <= 0:
                del self._document_frequency[term]
        self._length_sum -= len(tokens)

    @property
    def size(self) -> int:
        return len(self._documents)

    @property
    def average_length(self) -> float:
        return self._length_sum / len(self._documents) if self._documents else 0.0

    def _idf(self, term: str) -> float:
        """Robertson-Sparck-Jones IDF with the standard +0.5 smoothing.

        The ``max(..., 0)`` floor matters: without it, a term appearing in more than half the
        corpus gets a *negative* weight, so documents containing it score lower than documents
        that do not -- which is the opposite of what a search for that term should do.
        """
        n = len(self._documents)
        df = self._document_frequency.get(term, 0)
        return max(math.log((n - df + 0.5) / (df + 0.5) + 1.0), 0.0)

    def search(self, query: str, *, limit: int = 20, allowed_ids: set[str] | None = None
               ) -> list[tuple[str, float]]:
        terms = tokenize(query)
        if not terms or not self._documents:
            return []

        average = self.average_length or 1.0
        scores: dict[str, float] = {}
        for term in set(terms):
            idf = self._idf(term)
            if idf <= 0.0:
                continue
            for doc_id, counts in self._frequencies.items():
                if allowed_ids is not None and doc_id not in allowed_ids:
                    continue
                frequency = counts.get(term, 0)
                if not frequency:
                    continue
                length = len(self._documents[doc_id])
                denominator = frequency + self._k1 * (1 - self._b + self._b * length / average)
                scores[doc_id] = scores.get(doc_id, 0.0) + idf * (frequency * (self._k1 + 1)) / denominator

        ranked = sorted(scores.items(), key=lambda item: item[1], reverse=True)
        return ranked[:limit]

    def clear(self) -> None:
        self._documents.clear()
        self._frequencies.clear()
        self._document_frequency.clear()
        self._length_sum = 0


def reciprocal_rank_fusion(
    rankings: Sequence[Sequence[str]], *, k: int = 60, weights: Sequence[float] | None = None
) -> list[tuple[str, float]]:
    """Fuse ranked lists by reciprocal rank.

        RRF(d) = Σ_r  w_r / (k + rank_r(d))

    Rank-based rather than score-based because BM25 scores and cosine similarities are not
    comparable and their distributions shift per corpus. RRF needs no normalisation, no per-
    deployment tuning, and degrades gracefully when one retriever returns garbage -- a garbage
    list simply contributes small, evenly-spread increments.

    ``k=60`` is the value from the original TREC work; it damps the influence of the very top
    ranks enough that a single retriever cannot dominate the fused ordering.
    """
    if weights is None:
        weights = [1.0] * len(rankings)
    if len(weights) != len(rankings):
        raise ValueError("weights must match the number of rankings")

    scores: dict[str, float] = {}
    for ranking, weight in zip(rankings, weights):
        for position, doc_id in enumerate(ranking, start=1):
            scores[doc_id] = scores.get(doc_id, 0.0) + weight / (k + position)
    return sorted(scores.items(), key=lambda item: item[1], reverse=True)


class HeuristicReranker:
    """Zero-dependency reranker: lexical overlap, coverage, and proximity.

    Not as good as a cross-encoder, and does not pretend to be. It exists so the reranking stage
    is always present -- a pipeline where the stage is optional tends to be deployed without it,
    and reranking is where most of the precision gain in ADR-005 comes from. The cross-encoder
    adapter implements the same port and is enabled by configuration.

    Signals: proportion of query terms present (coverage), how tightly they cluster in the text
    (proximity), and a mild penalty for very long passages, which trivially contain more terms.
    """

    name = "heuristic"

    def __init__(self, *, coverage_weight: float = 0.6, proximity_weight: float = 0.3,
                 brevity_weight: float = 0.1) -> None:
        self._coverage = coverage_weight
        self._proximity = proximity_weight
        self._brevity = brevity_weight

    async def rerank(
        self, query: str, documents: Sequence[str], *, top_k: int | None = None
    ) -> list[tuple[int, float]]:
        query_terms = set(tokenize(query))
        if not query_terms:
            return [(i, 0.0) for i in range(len(documents))][: top_k or len(documents)]

        scored: list[tuple[int, float]] = []
        for index, document in enumerate(documents):
            tokens = tokenize(document)
            if not tokens:
                scored.append((index, 0.0))
                continue

            present = query_terms & set(tokens)
            coverage = len(present) / len(query_terms)

            proximity = 0.0
            if len(present) > 1:
                positions = [i for i, t in enumerate(tokens) if t in present]
                span = max(positions) - min(positions) + 1
                # A tight cluster of query terms means the passage is about the query, not
                # merely mentioning its words in separate paragraphs.
                proximity = len(present) / span
            elif present:
                proximity = 0.5

            brevity = 1.0 / (1.0 + math.log1p(len(tokens) / 100.0))
            scored.append(
                (
                    index,
                    self._coverage * coverage
                    + self._proximity * min(proximity, 1.0)
                    + self._brevity * brevity,
                )
            )

        scored.sort(key=lambda item: item[1], reverse=True)
        return scored[: top_k] if top_k else scored


def maximal_marginal_relevance(
    candidates: Sequence[tuple[str, float, Sequence[float]]],
    *,
    limit: int,
    lambda_: float = 0.7,
    similarity=None,
) -> list[str]:
    """Select a relevant but non-redundant subset.

        MMR = λ · relevance(d) − (1 − λ) · max_similarity(d, already_selected)

    Without this, retrieval commonly returns three overlapping chunks from the same page, which
    fills the context budget with one fact stated three times. ``λ = 0.7`` favours relevance
    while still breaking up near-duplicates.
    """
    if not candidates:
        return []
    if similarity is None:
        from ..llm.embeddings import cosine_similarity as similarity

    remaining = list(candidates)
    remaining.sort(key=lambda item: item[1], reverse=True)
    selected: list[tuple[str, float, Sequence[float]]] = [remaining.pop(0)]

    while remaining and len(selected) < limit:
        best_index, best_score = 0, float("-inf")
        for index, (_doc_id, relevance, vector) in enumerate(remaining):
            redundancy = max(
                (similarity(vector, chosen[2]) for chosen in selected if chosen[2] and vector),
                default=0.0,
            )
            score = lambda_ * relevance - (1.0 - lambda_) * redundancy
            if score > best_score:
                best_index, best_score = index, score
        selected.append(remaining.pop(best_index))

    return [doc_id for doc_id, _score, _vector in selected]


@dataclass(frozen=True, slots=True)
class FusionInput:
    """Named container for one retriever's output, so call sites stay readable."""

    name: str
    ranking: tuple[str, ...]
    scores: Mapping[str, float]
    weight: float = 1.0
