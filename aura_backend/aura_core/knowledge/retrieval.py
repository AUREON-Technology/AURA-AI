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

import heapq
import math
import re
from collections import Counter
from dataclasses import dataclass
from typing import Container, Iterable, Mapping, Sequence

from ..text import ARABIC_STOPWORDS, fold, is_arabic_token, light_stem

__all__ = [
    "BM25Index",
    "reciprocal_rank_fusion",
    "HeuristicReranker",
    "maximal_marginal_relevance",
    "tokenize",
]

#: Latin identifiers keep their punctuation (below); Arabic words are runs of Arabic letters. The
#: Arabic alternative matters: without it an Arabic document produced no tokens at all and was
#: invisible to lexical search (docs/plans/arabic-language_plan.md, stage 1).
_TOKEN = re.compile(r"[a-z0-9]+(?:[-_.][a-z0-9]+)*|[\u0621-\u064A]+")

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

    Arabic is folded first (diacritics and tatweel dropped, hamza/alef-maqsura/teh-marbuta
    variants unified, Arabic-Indic digits made ASCII) and each Arabic word light-stemmed, so
    "الطلاب" and "طلاب" are one term in documents and queries alike (``aura_core/text``).
    """
    tokens = _TOKEN.findall(fold(text.lower()))
    result: list[str] = []
    for token in tokens:
        if is_arabic_token(token):
            if not keep_stopwords and token in ARABIC_STOPWORDS:
                continue
            result.append(light_stem(token))
        elif keep_stopwords or token not in _STOPWORDS:
            result.append(token)
    return result


class BM25Index:
    """Okapi BM25 over an in-process corpus, as an inverted index sharded by group.

    No Elasticsearch: at the corpus size a single-VPS deployment holds (tens of thousands of
    chunks) an in-process index is fine, and adding a second heavyweight datastore for it would
    be a poor trade (ADR-005). Postgres full-text search is the documented drop-in for Stage 2;
    ``docs/reports/LEXICAL_RETRIEVAL_BENCHMARK.md`` has the measurements behind that decision.

    The postings are ``group -> term -> {doc_id: term frequency}``. A query therefore touches
    only the postings of its own terms inside the groups it may read -- ``O(Σ postings(q))``
    within the caller's tenant -- rather than every document's term counts, which is what the
    forward index this replaced did on every query (audit F-30 / AR-13: ~50 ms per query at 50k
    chunks, synchronous on the event loop). A group is the knowledge engine's owner key, so the
    tenant filter is a choice of shards, not a pass over the corpus.

    Document frequencies, the document count and the average length stay **corpus-wide**, not
    per group, so scores are exactly what the forward index produced: grouping prunes which
    documents are scored, never how.

    ``k1`` controls term-frequency saturation and ``b`` length normalisation; the values here
    are the standard defaults, which are well behaved across corpora.
    """

    def __init__(self, *, k1: float = 1.5, b: float = 0.75) -> None:
        self._k1 = k1
        self._b = b
        # group -> term -> doc_id -> term frequency
        self._postings: dict[str, dict[str, dict[str, int]]] = {}
        # doc_id -> (group, its distinct terms, token count): what removal and scoring need.
        # The token lists themselves are not kept; scoring never needed them.
        self._documents: dict[str, tuple[str, tuple[str, ...], int]] = {}
        # group -> every doc id added under it, including the ones with no indexable tokens:
        # what a per-group refresh diffs against the document store. Without the tokenless ones
        # (a chunk in a script the tokenizer does not cover, say), every refresh would see them
        # as missing and fetch them again.
        self._members: dict[str, set[str]] = {}
        # doc_id -> group, for documents added with no tokens. Not in `_documents`: they must not
        # count towards N or the average length, or scoring would change.
        self._tokenless: dict[str, str] = {}
        self._document_frequency: Counter[str] = Counter()
        self._length_sum = 0

    def add(self, doc_id: str, text: str, *, group: str = "") -> None:
        if doc_id in self._documents or doc_id in self._tokenless:
            self.remove(doc_id)
        tokens = tokenize(text)
        if not tokens:
            self._tokenless[doc_id] = group
            self._members.setdefault(group, set()).add(doc_id)
            return
        counts = Counter(tokens)
        shard = self._postings.setdefault(group, {})
        for term, frequency in counts.items():
            shard.setdefault(term, {})[doc_id] = frequency
            self._document_frequency[term] += 1
        self._documents[doc_id] = (group, tuple(counts), len(tokens))
        self._members.setdefault(group, set()).add(doc_id)
        self._length_sum += len(tokens)

    def add_many(self, items: Iterable[tuple[str, str]], *, group: str = "") -> None:
        for doc_id, text in items:
            self.add(doc_id, text, group=group)

    def _forget_member(self, group: str, doc_id: str) -> None:
        members = self._members.get(group)
        if members is not None:
            members.discard(doc_id)
            if not members:
                del self._members[group]

    def remove(self, doc_id: str) -> None:
        tokenless_group = self._tokenless.pop(doc_id, None)
        if tokenless_group is not None:
            self._forget_member(tokenless_group, doc_id)
            return
        entry = self._documents.pop(doc_id, None)
        if entry is None:
            return
        group, terms, length = entry
        shard = self._postings.get(group, {})
        for term in terms:
            postings = shard.get(term)
            if postings is not None:
                postings.pop(doc_id, None)
                if not postings:
                    del shard[term]
            self._document_frequency[term] -= 1
            if self._document_frequency[term] <= 0:
                del self._document_frequency[term]
        if not shard:
            self._postings.pop(group, None)
        self._forget_member(group, doc_id)
        self._length_sum -= length

    @property
    def size(self) -> int:
        return len(self._documents)

    @property
    def average_length(self) -> float:
        return self._length_sum / len(self._documents) if self._documents else 0.0

    def ids_in(self, group: str) -> frozenset[str]:
        """Every doc id added under ``group`` and not removed since, tokenless ones included.

        A snapshot, so the caller may hold it across an ``await`` while the index changes."""
        return frozenset(self._members.get(group, ()))

    def group_of(self, doc_id: str) -> str | None:
        """The group ``doc_id`` was added under, or ``None`` if it is not indexed."""
        entry = self._documents.get(doc_id)
        return entry[0] if entry is not None else None

    def _idf(self, term: str) -> float:
        """Robertson-Sparck-Jones IDF with the standard +0.5 smoothing.

        The ``max(..., 0)`` floor matters: without it, a term appearing in more than half the
        corpus gets a *negative* weight, so documents containing it score lower than documents
        that do not -- which is the opposite of what a search for that term should do.
        """
        n = len(self._documents)
        df = self._document_frequency.get(term, 0)
        return max(math.log((n - df + 0.5) / (df + 0.5) + 1.0), 0.0)

    def search(
        self,
        query: str,
        *,
        limit: int = 20,
        allowed_ids: Container[str] | None = None,
        groups: Iterable[str] | None = None,
    ) -> list[tuple[str, float]]:
        """Top ``limit`` documents by BM25, best first; equal scores are ordered by ``doc_id``.

        ``groups`` restricts the search to those shards (``None`` searches every shard), and
        ``allowed_ids`` further restricts it to an explicit id set. Both only prune: neither
        changes the score of a document that is searched.
        """
        terms = tokenize(query)
        if not terms or not self._documents or limit <= 0:
            return []

        shards = (
            list(self._postings.values())
            if groups is None
            else [self._postings[g] for g in dict.fromkeys(groups) if g in self._postings]
        )
        if not shards:
            return []

        k1, b = self._k1, self._b
        average = self.average_length or 1.0
        documents = self._documents
        scores: dict[str, float] = {}
        for term in set(terms):
            idf = self._idf(term)
            if idf <= 0.0:
                continue
            for shard in shards:
                postings = shard.get(term)
                if not postings:
                    continue
                for doc_id, frequency in postings.items():
                    if allowed_ids is not None and doc_id not in allowed_ids:
                        continue
                    length = documents[doc_id][2]
                    denominator = frequency + k1 * (1 - b + b * length / average)
                    scores[doc_id] = scores.get(doc_id, 0.0) + idf * (frequency * (k1 + 1)) / denominator

        # Partial selection, O(matches · log limit), instead of sorting every match. The doc_id
        # tie-break makes the order independent of insertion and shard order.
        return heapq.nsmallest(limit, scores.items(), key=lambda item: (-item[1], item[0]))

    def clear(self) -> None:
        self._postings.clear()
        self._documents.clear()
        self._members.clear()
        self._tokenless.clear()
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
