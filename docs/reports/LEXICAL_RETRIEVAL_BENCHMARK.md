# Lexical retrieval benchmark — F-30 / R3.4

Run 2026-10-30, in-process against the in-memory adapters. No Postgres, no Qdrant, no model.
Audit references: `.claude/audits/2026-10-26-full-699f27b/audit-report.md` F-30 (lens AR-13,
AR-14) and F-14; remediation roadmap R2.4 and R3.4.

## Status

| Change | Commit | State |
| --- | --- | --- |
| The relevance threshold is applied to a real score when the reranker is disabled or raises (AR-14) | `2307bc4` | TESTED locally, not CI-run |
| `BM25Index` is now an inverted index sharded by owner; scores are unchanged (AR-13) | the `perf(retrieval)` commit that adds this report | TESTED and benchmarked locally, not CI-run |
| F-14 cross-worker sync (R2.4 Option A: a change feed in the shared cache) and active-only `chunks_for` | the `fix(retrieval)` commit that adds section 7 | TESTED locally, including against real Postgres 16 and Redis 7; not CI-run, not deployed |
| Postgres FTS (R2.4 Option B) | — | Not built. The recommendation is in section 6 |

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

*Superseded by section 7, which fixes F-14's cross-worker half. Kept as written for the record.*

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

## 7. F-14 fixed: a cross-worker change feed (R2.4 Option A), run 2026-10-31

Built without a migration, as the owner asked. Option B (section 6) is still the long-term answer
for memory and startup cost. This change fixes only correctness and visibility.

### What changed

1. **`chunks_for` resolves only active versions.** On Postgres it now joins `aura_documents` with
   `status = 'active'`, like `iter_all_chunks`. The in-memory store matches it. The port docstring
   states the contract. A lexical hit from a stale index now resolves to no content on every
   path:
   - in `_resolve_contents`, it is dropped before reranking;
   - in `_chunk_from`, it becomes `None`;
   - before both, `_drop_inactive_lexical` checks it against the shared store.

   Dense hits come with their payload from the vector store (Qdrant or in-memory). Both delete
   paths remove those points before the tombstone or after the supersede, so dense hits are not
   re-checked. Ingest now reads the old version's chunks *before* superseding it, because
   afterwards `chunks_for` hides them and the old vectors would never be retired.
2. **A change feed in the shared `Cache` port** (`aura_core/knowledge/lexical_sync.py`).
   - **Publishing.** Every ingest, new version and delete runs `INCR`s on
     `knowledge:lexical:generation` and writes the changed document ids to
     `knowledge:lexical:change:{n}` with a 1 h TTL.
   - **Catching up.** Each engine keeps a cursor. Before a lexical search it reads the counter,
     at most once every 250 ms. If the counter has moved, it fetches the entries in `MGET`
     batches and *refreshes* each named document: it drops that document's entries and indexes
     the active version's chunks from the shared store.
   - **Why refreshes are safe.** A refresh is idempotent and does not depend on order, so
     replaying an entry, applying it twice, or applying it after a later change all give the
     same result.
   - **Fallbacks.** Three cases trigger a full rebuild:
     - the counter went backwards (Redis was flushed);
     - the worker is more than 5,000 entries behind;
     - an entry is still missing after a 5 s grace (its writer died between `INCR` and `SET`, or
       the entry expired).
   - **Rebuilds.** A rebuild now builds a new index off to the side and swaps it in, so queries
     never read a half-built index. Changes this process makes during the scan are re-applied
     after the swap.
   - **Cache down.** Reads report a miss, so the worker stops catching up. Deletes are still
     enforced on read. The writer's `INCR` raises, so its ids stay pending and are sent with its
     next publish or sync. The write itself never fails.
   - **Pub/sub was not used.** The port has no subscribe, a subscription is a long-lived
     connection that does not fit the lazy-adapter pattern, and a reconnecting subscriber drops
     messages. The counter would still be needed to detect the gap.
3. **Wiring.** `Container` passes `lexical_feed=LexicalChangeFeed(self.cache_backend)`. That is
   Redis in production and the in-memory cache otherwise; a single process works unchanged.
   Celery workers share the same Redis, so documents they ingest now become lexically
   searchable on the gateway workers too. Before this change, nothing ever told the gateway
   workers about them.

### Evidence

- **Default suite.** `tests/unit/test_lexical_cross_worker.py` has 18 cases. Two engines over
  one document store, one vector store and one cache stand in for two workers. The cases cover:
  - the audit's `repro_lexical.py` scenario (delete on A: B's results *and* B's index are empty);
  - an upload on A found lexically on B, with a control case: without the feed, B never finds
    it;
  - a new version, tenant scoping and erasure;
  - the check interval;
  - a reset counter, a lost entry with its grace period, entries after a gap, falling too far
    behind, an `INCR` that raises (write succeeds, then a retry), and deletes with the cache
    fully down;
  - an ingest during a rebuild not being lost, and queries during a rebuild reading the old
    index;
  - the container wiring;
  - active-only `chunks_for`, and old vectors still retired.
- **Real Postgres 16 + Redis 7** (Docker `postgres:16-alpine` and `redis:7-alpine`, locally):
  - `tests/load/test_lexical_cross_worker_postgres_redis.py` (opt-in `-m load`, with
    `AURA_TEST_DATABASE_URL` and `AURA_TEST_REDIS_URL`) passes 4 of 4. It covers: the audit
    scenario, including R2.4's acceptance check that the upload is visible on worker B within
    1 s; a new version replacing the old one on B; `chunks_for` returning nothing for a
    superseded or tombstoned version, with the old vectors retired; and a `FLUSHDB` forcing a
    rebuild. `tests/load/test_erasure_postgres.py` still passes.
  - **Two OS processes**, each with its own asyncpg pool and Redis client, at production
    defaults (250 ms interval):
    - B saw A's upload lexically about 19 ms after A started the ingest.
    - B's index dropped the document about 49 ms after A started the delete.
    - B's index ended empty.

### Overhead (`bench_lexical.py`, new `feed_check` and `lexical_stage_feed` columns, ms)

`feed_check` is the worst case: the interval forced to 0, so every query reads the counter and
nothing has changed. `lexical_stage_feed` is the lexical stage with that check included.

| Chunks | Feed on | `feed_check` p50 / p95 | `lexical_stage` p50 / p95 | `lexical_stage_feed` p50 / p95 |
| ---: | --- | ---: | ---: | ---: |
| 1,000 | in-memory cache | 0.007 / 0.011 | 0.095 / 0.182 | 0.112 / 0.210 |
| 10,000 | in-memory cache | 0.010 / 0.015 | 0.776 / 1.339 | 0.865 / 1.458 |
| 1,000 | Redis 7 (Docker, loopback) | 0.422 / 0.667 | 0.141 / 0.257 | 0.480 / 0.724 |
| 10,000 | Redis 7 | 0.524 / 0.855 | 0.773 / 1.354 | 1.235 / 1.894 |
| 50,000 | Redis 7 | 0.673 / 0.976 | 4.849 / 7.805 | 5.078 / 8.094 |

What this means in production:

- **Worst case.** Every query pays one Redis `GET`: about 0.4–0.7 ms p50, under 1 ms p95.
- **At the shipped 250 ms interval.** Each worker does at most 4 `GET`s a second. Every other
  query pays a clock read (about 0.01 ms). The check also runs inside the lexical task, which
  overlaps the dense search, so the `GET` mostly hides behind the query embedding.
- **Catching up.** Measured on real Postgres and Redis (scratch script, 40 documents of 41
  chunks each, while the full test suites ran on the same host): an idle check took 0.45 / 1.58
  ms (p50 / p95), and applying one changed document took 6.8 / 13.7 ms. That cost is two queries
  (`get` and `chunks_for`) plus re-indexing the document, and only the first query after a
  change pays it.

The other columns of this run match section 3: inverted 50k `lexical_stage` p50 4.85 ms, build
9.27 s, 516.6 MiB. Same machine, and it was shared with other workloads during the run.

### Still open

- **Memory and startup cost per worker.** Option B is what removes these.
- **No CI run (CB-44), and not deployed.** Production proof is an upload made through one worker
  that is lexically searchable from the others, and a delete that disappears from all of them.
  In the gateway, that means requests landing on different `--workers`.
- **Dense hits are trusted, not re-checked.** On a new version, the old version's vectors are
  deleted *after* the supersede. If that delete fails, those points stay searchable until a
  reindex. That is unchanged by this work, and it is narrower than F-14: the ingest raises, so
  the caller sees it. A document delete removes its vectors *before* the tombstone, so a failure
  there leaves the document active and the delete can be retried.
