# Phase A4 — First Load Test Against the Real Data Tier, and the Defect It Found

Run 2026-08-23 against `81bc7a7`, in a disposable 4-core / 15 GB Linux container with Docker,
following the same sandbox pattern as A1 and A2. Roadmap reference:
[`docs/AURA_UPGRADE_ROADMAP.md`](../AURA_UPGRADE_ROADMAP.md) Phase A4.

## Status: the numbers are real, and so is the bug they found

`aura_backend/tests/load/test_real_data_tier.py` is new: an opt-in pytest suite (marker `load`,
excluded from the default run exactly like `bench`) that boots the same `Container` the
production compose file boots — real Postgres, real Qdrant, real Redis, real Ollama — and
measures single-turn latency and a concurrency ceiling with an actual model in the request path,
rather than the in-memory adapters or echo provider every other suite uses.

The first run of that suite did not measure retrieval at all: every single query silently failed
with `IndexNotReadyError` and fell back to an empty result. That turned out to be a real,
previously-undetected defect in `IndexIdentity.matches_embedding` — not a harness bug — fixed
before the numbers below were captured. See "The defect" below before the numbers, because it
changes what the numbers mean.

## The defect: retrieval never worked against a real embedding model

`aura_core/adapters/qdrant_store.py`'s `resolve_alias` reconstructs an index's identity by parsing
it back out of the Qdrant collection name (`_parse_physical_name`), because Qdrant only stores the
name, not a separate manifest. That name is built by `IndexIdentity.physical_name`, which slugs its
inputs — lowercases them and strips punctuation — to make a valid collection name. The function's
own docstring already says the round trip is lossy: *"a round trip cannot recover the original
casing or punctuation."*

`IndexManager.resolve_for_read()` then compared that recovered (slugged) identity against the
**raw** id of the currently configured embedder:

```python
def matches_embedding(self, model_id: str, dimension: int) -> bool:
    return self.embedding_model_id == model_id and self.dimension == dimension
```

For `HashEmbeddingProvider`'s id, `"local-embeddings"`, slugging is a no-op — it is already
lowercase with no punctuation — so this comparison happened to hold, and every existing test in
the repository uses that embedder. Production does not. `deploy/docker-compose.prod.yml` sets
`AURA_EMBEDDING_PROVIDER=sentence-transformers`, `AURA_EMBEDDING_MODEL=BAAI/bge-small-en-v1.5` —
an id with uppercase letters, a `/`, and a `.`. Its slug, `baai-bge-small-en-v1-5`, can never equal
the raw id it was derived from. The comparison above was `False` on every call, forever, once
retrieval crossed from a fresh in-process cache into a value read back from Qdrant. The result:
`resolve_for_read()` raised `IndexNotReadyError` on every query, `KnowledgeEngine.retrieve()`
caught it and returned an empty `RetrievalResult` (`aura_core/knowledge/engine.py:399`), and the
turn continued — with no chunks, no citations, silently. Nothing in the pipeline surfaces this to
a caller; `/health/ready` does not check it either. A real deployment on the real embedder
retrieves nothing, ever, and reports itself healthy while doing it.

**Fix** (`aura_core/domain/knowledge.py`): compare slugged forms on both sides instead of raw
forms. `_slug` is idempotent, so this is exact for an already-slugged value (what `resolve_alias`
returns) and correct for a raw one (what the embedder reports) either side of the comparison.
Added a regression test against `QdrantVectorStore`'s fake
(`tests/unit/test_storage_adapter_queries.py`) that round-trips an uppercase/slash/dot model id
through the exact path — it fails on the pre-fix code and passes after. The 20-turn run below was
discarded and re-run after this fix landed; every number in this report is post-fix.

This is exactly the kind of finding Phase A exists to surface: nothing in `tests/`, `bench_pipeline.py`,
or A1's or A3's runs could have caught it, because none of them exercise a real embedder against
real Qdrant. A4 does, because that is what "load test against the real data tier" means.

## The measured numbers

20 sequential turns, then four concurrency levels, against the ingested employee handbook, model
`qwen2.5:3b-instruct-q4_K_M` (same model A1 used), `OLLAMA_NUM_PARALLEL=1`,
`AURA_PROVIDER_LOCAL_MAX_CONCURRENCY=1` — production's own defaults, not loosened for the test.

### Single-turn latency (sequential, n=20)

| | p50 | p95 | max |
| --- | --- | --- | --- |
| Total (model + pipeline) | 56.3 s | 70.5 s | 70.5 s |
| Pipeline overhead only | 244 ms | 19.0 s | 19.0 s |

The total is dominated by CPU inference, as expected on a 3-core allocation with no GPU — this is
not a regression against A1's `~5.3 s warm` figure, which measured 2 output tokens; this run
generates full free-text answers. The pipeline-overhead p95 spike (19 s against a 244 ms median)
is the more interesting number: it is not the ADR-000 pipeline-overhead target being missed by
30,000% so much as CPU contention on a 4-core sandbox where Ollama, Postgres, Qdrant, and the
retrieval/embedding call all compete for the same cores — a real effect of testing on constrained
hardware, and a reason not to over-read this particular figure as "the" overhead number. It is
reported as measured, not adjusted.

### Concurrency (OLLAMA_NUM_PARALLEL=1)

| Concurrency | Wall time | Throughput | Per-turn p50 | Per-turn max |
| --- | --- | --- | --- | --- |
| 1 | 28.1 s | 0.04 turns/s | 28.1 s | 28.1 s |
| 2 | 36.0 s | 0.06 turns/s | 27.3 s | 36.0 s |
| 4 | 81.5 s | 0.05 turns/s | 48.1 s | 81.5 s |
| 8 | 143.9 s | 0.06 turns/s | 85.2 s | 143.9 s |

Throughput is flat across every concurrency level tested — it does **not** rise with concurrency,
because `OLLAMA_NUM_PARALLEL=1` means the model serves exactly one generation at a time regardless
of how many requests arrive. Wall time grows roughly with the request count (8 concurrent requests
took ~5× as long as 1, not 1×), confirming the roadmap's own comment on this setting
(`deploy/docker-compose.prod.yml`: *"two concurrent CPU jobs on this box time-share the model and
both miss the chat timeout"*) as measured fact rather than a design assumption. **`OLLAMA_NUM_PARALLEL`
is the concurrency ceiling** — this deployment currently serves one AI request at a time, full
stop, independent of `AURA_WORKERS` or how many uvicorn workers accept the HTTP connection.

### An unplanned finding: safety-guard activity under real generation

3 of the 15 concurrency-test turns were blocked outright (`guard=system_prompt_leak`,
confidence 0.99) and 9 were flagged but allowed (`guard=groundedness`, `reason=low_groundedness`).
None of the 20 sequential-latency-test turns triggered either guard. Every prior test of the safety
pipeline in this repository runs against `EchoProvider`, which cannot leak a system prompt or
produce an ungrounded claim because it only echoes the caller's own message — this is the first
time these guards have ever fired against real model output. Whether the `system_prompt_leak`
blocks are correct (the small model genuinely reproduced prompt text) or false positives, and
whether the groundedness flags reflect a real hallucination-under-concurrency effect or just this
run's particular question wording, is **not established here** — flagged as an open question for
whoever picks up the answer/groundedness gating this report's "still open" section below names,
not investigated further in this pass.

## What this closes, and what is still open

Closes the "load test against the real data tier" scope of the roadmap's A4 row, plus finds and
fixes the retrieval-identity defect above — arguably A4's most consequential result, since it
means retrieval over the real embedding configuration had never actually been exercised before
this phase.

- **A real p95 with a model in the path**: 70.5 s, on this hardware, for this model. Not
  comparable to a GPU deployment; comparable to nothing else in this repo, because nothing else
  has measured it.
- **A documented concurrency ceiling naming `OLLAMA_NUM_PARALLEL` as the binding constraint**:
  done, with throughput data showing zero scaling from 1 to 8 concurrent requests.
- **Still open**: per-RAG-strategy comparison (this suite exercises the default hybrid path only,
  same limitation A3 already named); a load test with `OLLAMA_NUM_PARALLEL` raised, to find the
  point where CPU contention rather than the semaphore becomes the limiter; investigating whether
  the safety-guard activity above is a false-positive rate worth tuning or a real signal.

## Environment note

Same scratch-venv workaround as A1–A3
(`pip install -r requirements.lock -r requirements-dev.txt`). The data tier ran as
`postgres`/`redis`/`qdrant`/`ollama` from `deploy/docker-compose.prod.yml` with throwaway secrets
from `bootstrap-secrets.sh`, ports published to `localhost` only for this sandbox run (production
never does this — SEC-12 keeps those ports off the host) via an uncommitted local override file.
Qdrant/Postgres/Redis were reachable only after also attaching them to the `edge` network: `data`
is `internal: true` by design, which also blocks Docker's host port-forwarding for a
container with no other route out. The Ollama model pull needed one temporary
`network_mode: host` recreate to reach the sandbox's own egress proxy, then was recreated back
onto the normal bridge network before the timed runs — the pull itself is not part of any
measurement above.
