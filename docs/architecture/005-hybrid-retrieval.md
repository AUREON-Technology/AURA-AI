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
Default adapter is a heuristic lexical-overlap + position reranker with zero dependencies. A
self-hosted cross-encoder (`CrossEncoderReranker`, ms-marco-MiniLM-L-6-v2 via
sentence-transformers) implements the same port and is selected with
`AURA_KNOWLEDGE_RERANK_PROVIDER=cross_encoder`. It ranks materially better where the corpus
and the question use different vocabulary -- the case a lexical reranker cannot help with at
all -- at 100-300 ms per retrieval on CPU. Every failure path in it degrades to the heuristic
rather than raising, so enabling it cannot take search down.

A larger cross-encoder (BGE-reranker) or an LLM-as-reranker for small candidate sets would both
implement the same port; neither is wired today. Reranking is where most of the precision gain
comes from, and it is the stage most worth spending latency on.

Note what the cross-encoder's joint encoding costs: scoring is O(candidates), not a vector lookup,
which is why it runs only over the ~24 candidates fusion has already selected and can never serve
first-stage retrieval.

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

**Extraction** (`knowledge/documents.py`) runs before chunking and keeps page provenance. A PDF is
read page by page; each block carries a `modality` (`pdf_page`, `table`, `image`) and a page number
into chunk metadata and then into the vector payload, which is what lets `MultimodalRAG` filter on
modality and lets `Chunk.citation()` name a page. Each block is chunked separately, so no chunk
straddles a page boundary and then reports one page number for both halves. A page with images and no
text layer is indexed as a block *stating* that it could not be read — retrievable, and honest about
why. The legacy path concatenated every page into one string, which destroyed page numbers before
retrieval ever saw them; no amount of retrieval quality can recover information discarded at
ingestion.

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

## Knowledge graph (2026-09-25)

The graph is a third retrieval signal beside BM25 and dense vectors, stored in Postgres; no
separate graph database was introduced. Entities, relations, aliases and a type vocabulary live
in owner-scoped `aura_graph_*` tables (Alembic `0007`, mirrored by `PostgresGraphStore`'s
`IF NOT EXISTS` bootstrap), with an equivalent in-memory adapter for the default test run.

**No fact enters long-term knowledge without its evidence.** Every ingest runs extract → normalize
(case, whitespace, aliases) → validate against `DEFAULT_GRAPH_SCHEMA`'s allowed entity and
relation types → attach provenance → combine confidence → merge duplicates → insert. The default
extractor is the deterministic `RuleEntityExtractor`. `LLMEntityExtractor` exists behind the
`EntityExtractor` port, is off by default, and its output passes the same validation as data,
never as instructions.

**Provenance drives lifecycle.** Each fact keeps one `FactProvenance` per source (document id and
version) that asserted it, and its confidence is recomputed from the *live* sources, so it rises
when another document corroborates it and falls when a source is withdrawn. Deleting a document
removes the facts only it supported; superseding one expires its version's facts (`valid_to`), so
the graph never keeps answering from a document the user has replaced.

**Retrieval.** The adaptive router classifies relational questions and composes `GraphRAG` with
text retrieval rather than replacing it. Owner-scoped `GET /api/v3/knowledge/graph` and
`/api/v3/knowledge/graph/entities/{entity_key}` expose a user's graph (route-auth and IDOR tested).

**Proven so far:** the pipeline, lifecycle and cross-user isolation on the in-memory substrate,
and store, lifecycle and migration round-trip on a real Postgres 16 (opt-in
`tests/load/test_knowledge_graph_postgres.py`). **Not proven:** extraction quality on real
documents, and any client view of the graph.
