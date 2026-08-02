# ADR-005 — Hybrid retrieval: BM25 + dense, fused by RRF, then reranked

**Status:** Accepted · **Date:** 2026-08-02

## Context

Current retrieval is dense-only top-k with no score floor (`services/rag.py:229`). Dense embeddings
are weak exactly where an assistant needs strength: exact identifiers, error codes, product names,
rare tokens, and acronyms. A query for `AURA-4419` retrieves semantically "similar" text about
tickets rather than the ticket. And because there is no score floor, an empty or irrelevant corpus
still returns three chunks, which the prompt labels "Relevant Context" — the model then grounds a
confident answer in noise.

## Decision

Four stages, each independently disable-able by configuration:

**1. Retrieve, twice, concurrently.**
- Dense: embedding cosine over the versioned vector index.
- Lexical: BM25 over the same corpus. Implemented in `knowledge/bm25.py` (stdlib, ~120 lines) so it
  works with any vector store and needs no Elasticsearch. Postgres full-text search is the
  drop-in adapter for larger corpora.

**2. Fuse with Reciprocal Rank Fusion.**

```
RRF(d) = Σ_over_retrievers  1 / (k + rank_r(d))        k = 60
```

RRF is chosen over score normalisation because BM25 scores and cosine similarities are not
comparable and their distributions shift per corpus; RRF uses only ranks, so it needs no tuning per
deployment and is robust when one retriever returns garbage.

**3. Rerank.** A cross-encoder reranker port (`ports/reranker.py`) scores (query, chunk) pairs.
Default adapter is a heuristic lexical-overlap + position reranker with zero dependencies; the
cross-encoder adapter (BGE-reranker, or an LLM-as-reranker for small candidate sets) is enabled by
configuration. Reranking is where most of the precision gain comes from, and it is the stage most
worth spending latency on.

**4. Threshold, diversify, cite.**
- A **score floor** — below it, retrieval returns *nothing*, and the kernel says it has no grounded
  information rather than inventing context. This is the single most important correctness change.
- Maximal Marginal Relevance for diversity, so three chunks of the same paragraph don't fill the
  budget.
- Every surviving chunk carries `document_id`, `version`, `chunk_index`, and `score` so the answer
  can cite, and so groundedness can be evaluated.

**Chunking** (`knowledge/chunking.py`) is structure-aware: split on headings → paragraphs →
sentences, merge to a target token count with overlap measured in sentences, never mid-word. Fixed
character slicing is retained only as a fallback for unstructured blobs.

## Alternatives rejected

| Alternative | Why rejected |
| --- | --- |
| Dense-only (status quo) | Fails on identifiers, codes, and rare terms — a large share of real assistant queries. |
| Weighted score blend (`α·dense + (1-α)·bm25`) | Requires per-corpus tuning of α and normalisation; brittle as the corpus grows. RRF needs neither. |
| Elasticsearch/OpenSearch for lexical | A second heavyweight datastore on a single VPS for a feature that is 120 lines of code at this corpus size. Kept as a documented adapter for Stage 2. |
| Rerank with the main LLM on every query | 200–800 ms and real cost per query. Available as an adapter, off by default. |
| No score floor | Guarantees fabricated grounding when the corpus lacks an answer. This is the defect being fixed. |

## Consequences

**Positive** — large recall gain on keyword-shaped queries; precision gain from reranking; the model
can honestly say "I don't have that"; citations become possible, and with them, groundedness
measurement (`evaluation/rag_metrics.py`).

**Negative** — two indexes to keep in sync (handled by the ingestion pipeline writing both in one
transaction-like unit with a repair job), and reranking adds 20–150 ms depending on adapter.

**Impact:** answer quality is the primary win; token cost *drops* because fewer, better chunks are
sent; scalability is unaffected at Stage 0 and has a documented path (Postgres FTS → OpenSearch) for
Stage 2.
