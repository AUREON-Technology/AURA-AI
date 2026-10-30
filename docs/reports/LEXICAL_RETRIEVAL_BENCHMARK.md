# Lexical retrieval benchmark — F-30 / R3.4

Run 2026-10-30, in-process against the in-memory adapters. No Postgres, no Qdrant, no model.
Audit references: `.claude/audits/2026-10-26-full-699f27b/audit-report.md` F-30 (lens AR-13,
AR-14) and F-14; remediation roadmap R2.4 and R3.4.

## Status

| Change | Commit | State |
| --- | --- | --- |
| The relevance threshold is applied to a real score when the reranker is disabled or raises (AR-14) | `2307bc4` | TESTED locally, not CI-run |
| `BM25Index` is now an inverted index sharded by owner; scores are unchanged (AR-13) | the `perf(retrieval)` commit that adds this report | TESTED and benchmarked locally, not CI-run |
| Postgres FTS (R2.4 Option B) | — | Not built. The recommendation is at the end of this report |

CI is not getting runners (CB-44), so none of this has had a CI run.

## 1. The pseudo-score defect (correctness, not scale)

When reranking was disabled or the reranker raised, `KnowledgeEngine._rerank` scored each
candidate `1 - i/n`, a number derived from its rank. With the 0.35 floor this meant:

- the top candidate always scored 1.0 and passed, however irrelevant it was;
- the bottom third was always cut, however relevant it was;
- `searched_but_empty` could never be true on these paths.

**The fix.** The floor is calibrated against `HeuristicReranker` scores. The heuristic is the
default provider, and `CrossEncoderReranker` already degrades to it. So the heuristic now supplies
the score on both paths:

- **Reranking disabled:** the fusion order is kept, and each candidate is gated on its heuristic
  relevance. `rerank_score` stays `None`.
- **The reranker raised:** the heuristic reranks the candidates for that call.
- **The heuristic itself fails:** every candidate scores 0.0, so nothing passes. A pass is never
  fabricated.

Dense cosine was considered as the gate and rejected. Its distribution depends on the embedding
model: production's `bge-small-en-v1.5` puts unrelated passages well above 0.35. The same floor
would therefore mean something different on each deployment.

**Evidence.** `tests/unit/test_retrieval_threshold.py` has 8 cases, parametrised over both paths:

- An unrelated query against a one-document corpus returns `searched_but_empty`.
- A third chunk that is as relevant as the first two is no longer cut for its rank.
- With a raising reranker, the scores equal the heuristic's.

5 of the 8 fail on the code before `2307bc4`, and all 8 pass after it.

## 2. Method

Harness: `aura_backend/tests/benchmarks/bench_lexical.py`, marker `bench`, excluded from the
default run.

```bash
cd aura_backend
AURA_BENCH_LEXICAL_OUT=/tmp/lexical.json pytest tests/benchmarks/bench_lexical.py -m bench -s
# AURA_BENCH_LEXICAL_SIZES=1000,10000,50000,200000 adds the 200k point (several GB of RAM)
```

**Corpus.** Synthetic and seeded:

- a Zipf vocabulary (s = 1.07) of 30k words;
- 150–250 words per chunk, near the 320-token chunk target;
- 20 chunks per document;
- 10 tenants, each owning 10% of the chunks.

**Queries.** 60 queries, each six Zipf-drawn terms plus two rare ones. This matches the shape
the audit measured: an 8-term query from a tenant that owns 10%. Each query runs for 5 rounds at
1k and 10k chunks and 3 rounds at 50k, after a warm-up. `limit = 24`, the engine's
`candidate_k`.

**What each column times:**

| Column | What it times |
| --- | --- |
| `forward_stage` | The lexical stage as it was before the change: one pass over every chunk's owner to build the allow-list, then the forward-scan BM25. The old class is kept verbatim as `tests/unit/_forward_bm25.py`. |
| `inverted_search` | `BM25Index.search` on the caller's and the global groups. |
| `lexical_stage` | `KnowledgeEngine._lexical_search`: what one `retrieve()` now pays for its lexical half. |
| `retrieve` | The whole `retrieve()` with no vectors indexed: lexical stage, active-version check, content resolution (the N+1 chunk fetches), heuristic rerank and threshold. |
| index MiB | The Python heap a fully built index holds, measured with tracemalloc on a separate build. |

**Parity check.** Before any timing, each query's top 24 from the inverted index is asserted
equal to the forward scan's: same documents, same order, the same floats.

**Machine.** Intel Xeon @ 2.10 GHz, 4 vCPUs, 15 GiB RAM, Linux 6.18 (x86_64), CPython 3.13.17.
This is a shared cloud container: other agents ran workloads on the same host during the
measurements. `forward_stage` and `inverted_search` are interleaved query by query in one process,
so the ratio between them holds under that load. Absolute p95 values are noisier than p50 values.

## 3. Results

### Same run, same corpus: forward scan vs inverted index (ms)

| Chunks | `forward_stage` p50 / p95 | `inverted_search` p50 / p95 | `lexical_stage` p50 / p95 | Stage speed-up (p50) |
| ---: | ---: | ---: | ---: | ---: |
| 1,000 | 0.73 / 1.05 | 0.14 / 0.24 | 0.11 / 0.20 | 6.9× |
| 10,000 | 9.78 / 12.91 | 1.20 / 2.06 | 0.94 / 1.81 | 10.4× |
| 50,000 | 54.65 / 67.19 | 6.24 / 10.43 | 4.82 / 9.27 | 11.3× |

### End-to-end `retrieve()` (lexical path, ms)

The "before" figures come from a separate run of the earlier version of this harness at
`2307bc4`. The "after" figures come from the run above.

| Chunks | Before p50 / p95 | After p50 / p95 |
| ---: | ---: | ---: |
| 1,000 | 2.53 / 2.95 | 2.14 / 3.38 |
| 10,000 | 11.24 / 15.95 | 3.66 / 5.74 |
| 50,000 | 57.95 / 141.59¹ | 9.96 / 15.89 |

¹ That run's p95 was inflated by host load. An earlier "before" run on the same code measured
50k at p50 58.80 / p95 82.83 ms, and 10k at 11.13 / 16.13 ms.

The lens measured the old code at 8.0 ms per query at 10k chunks and 48.4 ms at 50k. These runs
agree with that: 9.4–9.8 ms and 53.8–55.1 ms for the index search.

### Memory and build

| Chunks | Inverted index | Forward index | Build (`rebuild_lexical_index`) |
| ---: | ---: | ---: | ---: |
| 1,000 | 17.6 MiB | 13.7 MiB | 0.15 s |
| 10,000 | 123.9 MiB | 134.0 MiB | 1.62 s |
| 50,000 | 516.6 MiB | 667.6 MiB | 9.40 s (before: 9.98 s) |

At 1k chunks the inverted index is larger, because it pays one small dict per (group, term). From
about 10k chunks it is smaller, because it no longer keeps every chunk's token list. Each gateway
worker holds its own copy, so in production multiply by `--workers` (4) and add each Celery child
that calls `startup()`.

### Retrieval quality is unchanged

The scores are exactly equal: the parity tests and the in-benchmark assertion compare floats.
The phase-a3 corpus (`tests/e2e/test_docs_corpus_gate.py`, 23 cases, k=5) gives identical metrics
at all three points measured:

- at `2071372`, before both commits;
- at `2307bc4`;
- with the inverted index.

The metrics:

- nDCG 0.6082
- recall@5 0.70
- MRR 0.4792
- precision@5 0.3867
- pass rate 0.6522
- empty-correct 0.3333

The default suite has the gate as well.

## 4. What the numbers say

- **Before the change**, the lexical stage cost about 1.1 ms per 1k chunks, linear in the corpus
  size. At 50k chunks that is 55 ms of synchronous CPU per query on the event loop, which stalls
  every other request on that worker.
- **After the change**, the cost grows with the postings of the query's terms inside the tenant.
  For this Zipf corpus that is still roughly linear in size, because the head terms appear in
  most chunks, but about 11× cheaper: 4.8 ms at 50k chunks. Extrapolating linearly gives roughly
  20 ms at 200k chunks. **That point was not measured here**, because building both indexes at
  200k needs roughly 9 GB on a shared host. R2.4's 200k figure is therefore still open; run it
  with `AURA_BENCH_LEXICAL_SIZES=200000` on a host that has the memory.
- **The tenant filter is now free.** The old per-query pass over every chunk's owner is gone,
  because groups are selected instead. `lexical_stage` is no slower than `inverted_search`.
- **The remaining `retrieve()` cost** is 5 ms at 50k chunks. It is spread over the active-version
  check, the N+1 `chunks_for` content resolution and the rerank. Against the in-memory store the
  N+1 is cheap. Against Postgres each lookup is a round trip, and none of that has been measured
  here. A batched `chunks_by_ids` port is the fix for that, and it needs a Postgres adapter change
  verified against a real instance. That is outside this change.

## 5. Effect on F-14 (per-worker divergence)

The change neither fixes F-14 nor makes it worse:

- The index is still per process.
- An upload is still lexically invisible on the other workers until they restart.
- `_drop_inactive_lexical` still checks the shared document store and evicts deleted or
  superseded chunks on read.

The engine's separate chunk-to-owner map (`_lexical_owner`) is gone, because the index now
records each chunk's group. That leaves one structure to keep consistent instead of two.
`tests/unit/test_bm25_inverted_index.py` pins removal, regrouping on re-add, and no empty shard
left behind. The per-worker footprint shrank from about 668 MiB to 517 MiB at 50k chunks.

## 6. Recommendation: Postgres FTS (R2.4 Option B) as the long-term answer, not yet

The inverted index removes the latency reason to move lexical search for the corpus sizes a
single-VPS deployment holds: under 10 ms at 50k chunks per worker. It leaves three reasons, and
only a shared index fixes them:

1. **F-14's other half.** New uploads are not lexically searchable on other workers. Option A,
   Redis pub/sub invalidation, also fixes this, and it is the smaller change.
2. **Memory.** About 0.5 GiB per worker at 50k chunks, times 4 workers. Option A does not fix
   this.
3. **Startup.** The index takes 9.4 s to build at 50k chunks, paid by every worker and every
   Celery child that starts, and growing linearly with the corpus.

Option B removes all three: a `tsvector` column with a GIN index on `aura_chunks`, `owner_id` in
the WHERE clause, and `ts_rank_cd`, fused with dense results by RRF. It is the right long-term
design. It is **not** shipped in this change, for three reasons:

- It needs a migration. The owner has to coordinate that with a production upgrade (see CB-43
  for how the last one went).
- R2.4's decision rule requires recall@k and nDCG on the phase-a3 corpus within 1 point of the
  numbers above (nDCG 0.6082). `ts_rank_cd` does not rank like BM25, so that rule could fail.
- The Arabic tokenization configuration has to be settled first: the `simple` dictionary loses
  stemming.

The suggested order:

1. Option A now, if cross-worker visibility of new uploads matters before Option B lands.
2. Option B behind a flag, evaluated against these numbers: p95 at 10k, 50k and 200k chunks, plus
   phase-a3 nDCG.
3. Retire the in-process index only once Option B passes.
