# Knowledge module — source registry and how AURA uses it (DISCOVER)

**Date:** 2026-11-22 · **Mode:** DISCOVER (read-only; no application code changed) ·
**Repo:** `E:\AURA AI`, branch `main` at `6ac85900`, working tree has only unrelated generated-plugin
changes · **Live target probed:** `https://api.aureon-ai.tech` (release `c346d529fb88`)

## Verdict

**NO-SHIP for "AURA answers from its knowledge base in production", and NO-SHIP for the
multi-domain expansion in the strategy report as written.** One P1 is live today and four P1/P2
structural gaps sit between the registry and what the report asks it to hold.

| Question | Answer |
|---|---|
| Does retrieval work in production today? | **No.** The published index is `aura-knowledge__v1__hash-embed-v1__384`; the running embedder is `intfloat/multilingual-e5-small#doc-prefix-707ad539`. Reads refuse that mismatch (`index_manager.py:196`) and retrieval returns `strategy="unavailable"` (`engine.py:600-603`). (KM-01) |
| Which sources are registered in production? | **Unknown.** Nothing in the repo seeds any, and the listing needs an admin token (`GET /api/v3/admin/sources`). With none registered the nightly refresh does nothing. (KM-02) |
| Can the registry hold the report's sources? | **Almost none of them as designed.** It fetches one HTTPS page per source and strips tags. No dump, API-paging, OAI-PMH, E-utilities or repository adapter exists. (KM-03) |
| Is the report's trust-channel warning (section 11) still true? | **No, it is already fixed** (KAN-101). The doc it was built from is stale. (KM-14) |

## Scope and what was and was not verified

In scope: the source registry (`learning/sources.py`, `adapters/postgres_sources.py`, admin routes),
the nightly refresh (`learning/knowledge_refresh.py`), ingestion and retrieval
(`knowledge/engine.py`, `index_manager.py`), how retrieved text reaches the model
(`context/engine.py`, `kernel/kernel.py`), and the strategy report you supplied.

| Evidence | Result |
|---|---|
| Code read of the files above, with line references in the register | done |
| `pytest` on 11 knowledge/refresh/SSRF/taint test files (local, in-memory substrate, venv) | **259 passed, 0 failed, 0 skipped** — log `E:\aura_audit_tmp\knowledge-pytest.log` |
| Live `GET /health/live` and `/health/ready` on `api.aureon-ai.tech` | done (unauthenticated) |
| Authenticated live calls (source list, search, refresh report) | **not run** — no admin token was used |
| Real Qdrant / Postgres adapters, CI, latency on the deploy target | **not run** |
| External licence and size claims in the report | only OpenStax checked (matches its cited page); Stack Exchange terms could not be fetched |

## What exists (verified)

```
POST /admin/sources (SuperAdmin) ──► PostgresSourceRegistry (aura_knowledge_sources)
                                              │
Celery beat 02:00, queue "learning" ──► KnowledgeRefreshPipeline.run()
   for each enabled source (3 at a time, 5,000-doc run cap):
     CompositeFetcher ─► HttpSourceFetcher | ConnectorSourceFetcher
     validate (size/markup) → exact + near-duplicate drop
     KnowledgeEngine.ingest: content-hash idempotency → chunk → context guard
        (quarantine instruction-shaped chunks) → embed → vector + BM25 → graph
   then build candidate index → gates (min chunks, error rate, count delta) → promote alias
Chat turn: AIKernel → KnowledgeEngine.retrieve (dense ∥ BM25 → RRF → rerank → 0.35 floor → MMR)
   → top 2 chunks, each clipped to 280 chars → fenced DATA with a per-turn nonce
```

Strengths worth keeping (all backed by passing tests): SSRF policy on fetch (https only, no private
ranges, DNS re-check, no redirects); per-source ceilings; content-hash idempotency; versioned
documents with chunk retirement; tombstone-checked retrieval; owner/global isolation; instruction
quarantine at ingest; retrieved text is DATA, never SYSTEM; the score floor returns nothing rather
than noise; Arabic folding and light stemming in lexical search; multilingual embedder plus an
"either" reranker gate evaluated offline.

## Where the details are

- `04-gap-and-risk-register.md` — 16 findings with evidence, severity and the fix direction.
- `10-strategy-report-assessment.md` — the report checked claim by claim, source by source.

## Decisions

D-001 (confirmed 2026-11-22): the global corpus accepts **CC0, CC BY and permissive licences only**; no
share-alike, no non-commercial. See `02-business-interview-and-decisions.md` for the effect on each source
(Wikipedia, Stack Exchange and OpenStax are out).
