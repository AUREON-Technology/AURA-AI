# ADR-007 — Index identity includes the embedding model; changes go through an alias switch

**Status:** Accepted · **Date:** 2026-08-02

## Context

SEC-03 / TD-04: `services/rag.py:69-72` deletes the entire Qdrant collection when the embedding
dimension it computes at boot differs from the collection's. Since the embedding backend is chosen by
a runtime fallback chain (Gemini 768 → MiniLM 384 → Ollama → zero vector), losing the Gemini key is
sufficient to destroy every user's knowledge base on the next restart.

More generally: embeddings are not interchangeable. Vectors written by model A are meaningless when
searched with model B. A collection is only valid for the exact model that produced it.

## Decision

**1. Index identity is a value object.**

```
IndexIdentity(logical_name, schema_version, embedding_model_id, dimension, distance)
  → physical_name = "{logical_name}__v{schema_version}__{slug(model_id)}__{dimension}"
```

Example: `aura_knowledge__v3__bge-small-en-v15__384`.

**2. Clients address an alias, never a physical index.** `aura_knowledge` is an alias pointing at one
physical index. Reads and writes both resolve through the alias.

**3. A mismatch never deletes.** If the required identity has no physical index, one is **created**.
The old index is left intact. Deleting a physical index is an explicit admin operation, audited, and
refused while any alias points at it.

**4. Reindexing is a job, not a side effect** (`knowledge/index_manager.py`):

```
build   → create target index; stream source documents; embed in batches; write
verify  → compare document count; run the retrieval eval set against both indexes;
          require target recall@5 ≥ source recall@5 − tolerance
switch  → atomically repoint the alias
observe → keep the previous index for `retention_days`
rollback→ repoint the alias back; O(1), no data movement
```

**5. Document versioning.** Each document has an immutable `(document_id, version)`; ingesting a
changed document creates version n+1 and marks n `superseded`. Chunk ids embed the version, so
retrieval can be pinned to a version and stale chunks are never silently mixed with fresh ones.
Deletion is a tombstone plus a purge job, so "delete my data" is verifiable.

**6. Embedding failures are errors.** The zero-vector fallback is removed. `EmbeddingError`
propagates; ingestion retries with backoff and dead-letters after N attempts. A document is never
stored with a synthetic vector.

## Alternatives rejected

| Alternative | Why rejected |
| --- | --- |
| Auto-recreate on mismatch (status quo) | Data loss on a routine dependency failure. |
| Pad/truncate vectors to a common dimension | Produces geometrically meaningless vectors; silently wrong retrieval is worse than an error. |
| Store the model id per point and filter at query time | Doesn't work — the query vector still comes from one model, and a single ANN graph cannot mix embedding spaces. |
| Blue/green whole-database copies | Far more storage and operational weight than an alias switch, for the same guarantee. |

## Consequences

**Positive** — changing embedding models becomes a routine, reversible, zero-downtime operation with
a quality gate; the "restart destroyed the knowledge base" class of incident is eliminated; document
versioning makes GDPR-style deletion and citation-by-version possible.

**Negative** — both indexes exist during a reindex (roughly 2× vector storage, transient), and the
alias indirection is one more concept for operators. Documented in
`docs/guides/administrator-guide.md` §Reindexing.

**Impact:** availability and data safety improve substantially; storage cost rises transiently during
reindex; scalability improves because reindexing no longer requires downtime.
