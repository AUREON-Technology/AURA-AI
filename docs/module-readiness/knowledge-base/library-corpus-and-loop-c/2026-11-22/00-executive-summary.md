# Knowledge-Base System: executive summary (DISCOVER, 2026-11-22)

**Mode:** DISCOVER. No application code was changed. **Branch:** `anas/knowledge-wp4-fetchers` at
`0438bfd6`, with uncommitted WP4 work (`learning/sources.py`, `knowledge_refresh.py`, `container.py`,
`api/admin.py`, one test edit, `tests/unit/test_source_fetch_batches.py`). **Live revision probed:**
`93635069` (`/health/ready`, 2026-11-22).

## Scope (confirmed by the owner, decision D1)

All three parts of the Knowledge-Base System must reach production readiness:

1. **User library**: personal and team documents, upload, search, delete, CAG pins, chat RAG with citations.
2. **Global corpus**: admin-registered sources, nightly refresh (Loop A), licence gate (owner decision D-001), the console Knowledge page.
3. **Loop C**: adaptive, reviewed knowledge updates (ADR-035, GAP-065). **Designed, nothing built.**

## Readiness status: NO-SHIP

| # | Finding | Sev | Status |
|---|---|---|---|
| KB-001 | **Retrieval is off in production.** Live `/health/ready` shows the published index `hash-embed-v1` and the required one `multilingual-e5-small`; `matches_embedder: false`. Every retrieval returns `strategy="unavailable"`, so no user's library or the global corpus reaches chat. Uploads are acknowledged but unsearchable. Same state recorded on 2026-11-17. | P0 | VERIFIED live + code |
| KB-002 | The broken state is invisible: no degraded reason, no metric, no alert; `healthy: true`. | P1 | VERIFIED (code) |
| KB-003 | Deleting or withdrawing a global source leaves its documents live. A source cannot be marked `restricted` through the API. | P1 | VERIFIED (code) |
| KB-005 | CC BY attribution (D-001) is never shown: a citation is `doc-id@vN (location)`, no source, licence or link. | P1 | VERIFIED (code) |
| KB-006 | A source registered from the console is silently inert: the form sends no licence and the provenance route has no console caller. | P1 | VERIFIED (code) |
| KB-007 | Loop C is unbuilt, was designed before D-001, and its plan reserves migration `0031`, which is taken (head is `0032`). | P1 | VERIFIED |
| KB-004 | Global documents from sources registered before migration 0032 have unknown licence and stay retrievable. | P1 | UNVERIFIABLE (needs admin access) |

Full register: `04-gap-and-risk-register.md` (19 findings).

## What is solid (verified by reading and local tests)

The retrieval engine is mature: hybrid BM25 + dense with RRF, rerank, score floor, MMR; content-hash
idempotent ingest under per-key locks; per-owner lexical shards converged across workers; delete and
erasure that survive an embedder change; instruction-shaped chunks quarantined before indexing or
graph extraction; owner and workspace isolation tested on in-memory, qdrant `:memory:` and real
Postgres; route-level IDOR tests. A local run of 11 core knowledge test files gave **187 passed, 1
skipped** (`.venv`, Python 3.11.16, working tree including the uncommitted WP4 work). **Not run in
CI** (Backend CI has had no trigger since 2026-10-31).

## Decisions confirmed by the owner

D1 scope = all three parts. D2 Loop C evidence must meet D-001. D3 retire a source's documents on
removal, with confirmation, plus a Super Admin takedown. D4 upload accepts PDF, TXT/MD/HTML **and
.docx**. Detail: `02-business-interview-and-decisions.md`.

## Path to release

`05-implementation-plan.md`: WP-0 (run the index rebuild, an owner action, no code) unblocks the P0
the same day. WP-1 to WP-4 close the other release blockers. Loop C (WP-8) is the largest item and
is sequenced last behind `AURA_KNOWLEDGE_ADAPTIVE_ENABLED=false`. Nine recommendations (R1 to R9)
were approved by the owner on 2026-11-22. The report is now in PLAN; implementation awaits a
go-ahead on the plan.

## Not verified (limits of this discovery)

No authenticated call to the live admin API (the live source and document inventory is unknown); no
real Qdrant server; no real-Postgres load suite run; no device or screen-reader testing; `admin_adaptation.py`
connector routes and service-key allow-lists not read; the desktop `/knowledge` redirect read only
through a test.
