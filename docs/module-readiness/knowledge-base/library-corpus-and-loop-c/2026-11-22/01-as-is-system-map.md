# Knowledge-Base System: as-is system map (2026-11-22)

## Detected stack and layer map

| Layer | Path | Stack | Commands used |
|---|---|---|---|
| Backend | `aura_backend/` | Python 3.11, FastAPI, hexagonal; Postgres (Alembic head `0032`), Qdrant, Redis, Celery | `.venv/Scripts/python.exe -m pytest <files>` |
| Client | `aura_client/` | Flutter, Riverpod, go_router, Drift | not run (no SDK in this session) |
| Console | `aura_admin/` | Next.js, Playwright e2e on a mock gateway | not run |
| Daemon | `aura_hostd/` | Dart | out of scope |

## Components and flow

```
 Flutter library page ─┐                           ┌─ Console Knowledge page (7 calls)
                       ▼                           ▼
        /api/v3/knowledge/*  (v3.py)        /api/v3/admin/sources|indexes|index/* (admin.py)
          Principal.knowledge_owner (token subject or X-Aura-Workspace, membership checked)
                       ▼
 KnowledgeEngine.ingest ── extract (PDF, OCR) ─ chunk (headings→paragraphs→sentences)
   ├ content-hash idempotency + per-hash and per-document locks
   ├ SafetyPipeline.guard_context_items (quarantine instruction-shaped chunks)
   ├ embed in batches → VectorStore (Qdrant, versioned index)  +  BM25 (per owner shard)
   ├ DocumentStore.put / supersede (immutable versions)
   └ KnowledgeGraphPipeline (facts with provenance)
 KnowledgeEngine.retrieve ── dense ∥ BM25 → RRF → rerank → score floor → MMR → liveness check vs store
 CAG: pinned documents → byte-stable prefix (invalidated by 3 v3 routes)
 Loop A: KnowledgeRefreshPipeline (nightly) ── registry (aura_knowledge_sources) ── CompositeFetcher
   (WIP: web + connector only) ── licence gate (D-001) ── KnowledgeEngine.ingest ── _promote (gates)
 Loop C: ADR-035 / docs/plans/adaptive-knowledge-update-system.md: NOT BUILT
```

## Surfaces

| Surface | Routes | Caller |
|---|---|---|
| User library | POST `/knowledge/documents`, POST `/documents/upload` (PDF only), POST `/knowledge/search`, GET `/knowledge/documents`, DELETE `/documents/{id}`, POST/DELETE `.../cag-pin`, GET `/knowledge/cag` | Flutter: all called |
| Graph | GET `/knowledge/graph`, `/graph/entities/{key}` | **none** ("wave 2, no view yet") |
| Global corpus admin | `/admin/sources` CRUD, `PUT /admin/sources/{id}/provenance`, admin cag-pin, `/admin/indexes`, `/admin/index/rebuild|rollback`, `/admin/refresh/latest`, `/admin/scorecard` | Console calls 7; **no caller**: provenance, admin cag-pin, refresh/latest, scorecard |
| Loop C | `POST /knowledge/suggestions`, proposal and gap routes | none exist |

## Authorization and tenancy (ASSESSED)

Every knowledge route depends on `principal` (bearer token). The owner is never read from a body.
Team writes need `manage_knowledge` (Owner or Admin). Admin routes call `require_admin` or
`require_super_admin` inside each handler, not at router level. Global documents cannot be deleted
or overwritten by a user (404/409). Source registration, provenance and deletion are Super Admin.

## Live state (probe 2026-11-22, `/health/ready`)

Index `aura-knowledge__v1__hash-embed-v1__384` published; required
`...multilingual-e5-small-doc-prefix-707ad539__384`; `matches_embedder: false`, `up_to_date: false`.
Schema `0032`, `healthy: true`, vectors on Qdrant with `payload_content: false`, embedder
sentence-transformers e5-small. Providers `hosted1` and `claude1` healthy, `hfspace` 503.

## Dimension assessment (section 5)

| Dimension | Result |
|---|---|
| Actors, roles, ownership | ASSESSED: server-side enforced; client shows write controls to every member |
| Lifecycle and transitions | ASSESSED: immutable versions, supersede, tombstone; source lifecycle lacks takedown (KB-003) |
| Business rules | ASSESSED: score floor, licence allow-list; attribution rule unmet (KB-005) |
| Validation, exceptions | ASSESSED: byte cap, 500-page cap, quarantine; silent `unavailable` (KB-001/002) |
| UX and accessibility | PARTIAL: client agent audit; no widget tests; accessibility UNKNOWN |
| Data integrity, privacy | ASSESSED: erasure reaches vectors, lexical, graph; payload content off off-host |
| Integrations | ASSESSED: HTTP fetcher SSRF-guarded, no redirects; connectors not read |
| Performance, operations | PARTIAL: lexical benchmark 50k chunks 4.8 ms p50; no live figures; alerts gap (KB-002) |
| Product completeness | GAP: one-page `web` sources only, no formats beyond PDF, no graph view |
| Stack-specific | ASSESSED: lazy adapters and hexagonal rules pinned by architecture tests |

## Evidence map

| Area | Evidence inspected | Confidence |
|---|---|---|
| Engine, index manager, CAG | read in full or in the relevant ranges | High |
| Loop A, sources, admin routes | read; uncommitted diff read | High |
| Client | subagent audit (grep and reads), nothing run | Medium-High |
| Console | subagent audit | High |
| Tests | inventory by subagent plus a local run of 11 files | High (inventory), Medium (bodies not all read) |
| Live | one read-only GET of `/health/ready` | High for what it shows |
