# Knowledge-Base System: implementation plan (2026-11-22)

**Status: PLAN, awaiting the owner's go-ahead to implement.** Decisions D1 to D4 and
recommendations R1 to R9 are owner-approved (2026-11-22); the plan itself is not yet approved for
implementation. Estimates are not given: none is supportable before the live inventory (O6, O7).
File lists are estimates until checked during implementation.

**Before any code:** WP-0 needs no code and unblocks the P0. O6 and O7 (the live source and
document inventory) need an authenticated admin call and gate WP-2 and WP-4.

## Sequencing

```
WP-0 (owner action, no code) ─► WP-1 ─► WP-2 ─┬► WP-4 ─► WP-7 ─► release of parts 1+2
                                  └► WP-3 ────┘
WP-5, WP-6 run beside WP-3/4 (client and upload).   WP-8 (Loop C) after WP-2/3, behind a flag.
```

## 1. Release blockers (P0 and P1)

| WP | Scope | Findings / reqs | Layers and files (estimated) | Depends on | Acceptance and tests | Blocks production |
|---|---|---|---|---|---|---|
| **WP-0** | An Admin rebuilds and promotes the index: `POST /admin/index/rebuild`, or `python scripts/rebuild_index.py` over `railway ssh`. Then run a retrieval smoke and an eval on the live embedder and commit a report. **No code. An owner action; I did not run it.** | KB-001, KB-015; FR-005, NFR-002 | live ops; `docs/reports/` | none | `/health/ready` shows `matches_embedder:true`; a known chunk is retrieved; recall@5 and Arabic eval recorded against baselines | **Yes (P0)** |
| WP-1 | Make "unavailable" visible: kernel degraded reason, `aura_retrieval_total{result="unavailable"}`, alert, `knowledge.degraded` on readiness, `searchable` on ingest (R2/R3, approved); let the refresh promote with zero sources (R1). | KB-001, KB-002; FR-006, FR-007, NFR-003, UX-002 | `knowledge/engine.py`, `kernel/kernel.py`, `observability/metrics.py`, `control/assets/aura-alerts.yml`, `learning/knowledge_refresh.py`, `api/v3.py`, health route, client notice | WP-0 | Unit tests per signal; refresh-with-zero-sources test; alert drill | Yes |
| WP-2 | Source takedown and retire-by-source: confirm-and-retire on delete, Super Admin takedown, `restricted` status, audit; inventory script for pre-0032 sources; decide the second approver (KB-019). | KB-003, KB-004, KB-019; FR-010 to FR-012, DATA-002, BR-003 | `api/admin.py`, `knowledge/engine.py` (retire by metadata `source_id`), `adapters/postgres_sources.py`, source registry port, a migration only if a status column is needed | O6 | Retire-by-source test across vectors, lexical, graph; takedown audit; real-Postgres test | Yes |
| WP-3 | Structured citations and attribution (R4): resolve source name, licence, attribution and URL from the document store at response time; keep `citation`; client chips link out. | KB-005; BR-006, FR-002, UX-002 | `domain/knowledge.py`, `knowledge/engine.py`, `api/v3.py` search and chat payloads, `kernel`, client `user_facing_citations.dart`, chip widget | none | API schema test; a CC BY chunk shows credit; widget test; legacy string unchanged | Yes (legal) |
| WP-4 | Console: licence and provenance fields, `ingestible` badge, provenance edit, takedown and delete-with-count dialogs. Merge the in-flight WP4 fetcher work after the O7 inventory and a disable-with-reason step for unsupported source types. | KB-006, KB-010; FR-008, FR-009 | `aura_admin/app/(console)/knowledge/page.tsx`, `src/lib/types.ts`, Playwright mock gateway; backend WIP files | WP-2, O7 | Playwright e2e for register, badge, takedown; backend suites green | Yes |

## 2. Required before general availability (P2)

| WP | Scope | Findings / reqs | Acceptance |
|---|---|---|---|
| WP-5 | Upload TXT, MD, HTML and **DOCX** (D4): route dispatch to `extract_document`, magic-byte and extension check, 415, a DOCX parser with decompressed-size and entry caps, macro and external-reference refusal, off-loop extraction, client picker; pin the new dependency in both lockfiles with hashes. | KB-009; FR-001, SEC-005 | Per-type ingest tests; zip-bomb and macro fixtures refused; `pip install --require-hashes` clean |
| WP-6 | Client UX (R6): delete confirmation, keep text on failure, search error vs empty, retry, role-aware controls, workspace-switch reload, pin failure local, show unreadable pages; decide desktop screen and graph view scope. | KB-008, KB-013, KB-018; UX-001, UX-003, INT-001, FR-004 | Widget tests for every state; `flutter analyze` and `flutter test` counts quoted |
| WP-7 | Verification: e5 retrieval eval committed; real Qdrant tests (delete, erase, swap); real-Postgres suite run; CAG after refresh test; mid-ingest and graph-purge failure tests; full build, promote, rollback, prune; link a CI run. Investigate R8 (refresh in place). | KB-014 to KB-017; NFR-004 | A report in `docs/reports/`; CI run URL |

## 3. Loop C (D1): large, sequenced last

| WP | Scope | Acceptance |
|---|---|---|
| WP-8 | Amend ADR-035 and the plan first (R7): migration `0033`, the D2 evidence gate, SearxNG as a Railway service (O1), asker threshold (O2). Then phases **C0 to C5** exactly as plan §9, each ending with its evidence (migration round trip, gate leakage test, compose parity, a live dev run, one approve-apply-retrieve-revert cycle with citations `id@vN`, client counts, staleness run, alert drill). Ship behind `AURA_KNOWLEDGE_ADAPTIVE_ENABLED=false` (R9). | GAP-065 moves to `built_not_proven` after C3 and to `proven` only when the C3 and C5 reports are committed |

## 4. After launch

Sitemap, RSS and repository fetchers with the licence gate (KB-011); notify an explicit requester (O5);
Arabic research (O4); admin route hygiene (KB-012); a CAG event subscriber (KB-016).

## Validation commands (backend, local)

```powershell
cd aura_backend
.venv\Scripts\python.exe -m pytest tests/unit/test_knowledge.py tests/unit/test_knowledge_lifecycle.py tests/unit/test_source_licence.py tests/unit/test_source_fetch_batches.py -q
.venv\Scripts\python.exe -m pytest tests/architecture -q
```

Client and console commands are the ones in `CLAUDE.md` (Flutter SDK fetched per its recipe;
`npm run lint`, `npm run build`, `npm run test:e2e` in `aura_admin/`).
