"""The forward-scan BM25 index the inverted index replaced, kept verbatim as a reference.

Not collected (no ``test_`` prefix). Two users: ``test_bm25_inverted_index.py`` checks that the
inverted index scores exactly what this did, and ``tests/benchmarks/bench_lexical.py`` times both
on the same corpus, so the before/after index numbers in
``docs/reports/LEXICAL_RETRIEVAL_BENCHMARK.md`` come from one run on one machine. Its only change
from ``aura_core/knowledge/retrieval.py`` at the commit before the swap is the class name.
"""

from __future__ import annotations

import math
from collections import Counter
from typing import Iterable

from aura_core.knowledge.retrieval import tokenize


class ForwardScanBM25:
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
