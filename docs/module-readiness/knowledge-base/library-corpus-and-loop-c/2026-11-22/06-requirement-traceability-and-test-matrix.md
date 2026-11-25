# Knowledge-Base System: requirement traceability and test matrix (2026-11-22)

Statuses: `PASS`, `GAP`, `PARTIAL`, `UNVERIFIABLE`. **Every PASS here is local evidence** (code read
plus tests; the core files ran 187 passed, 1 skipped on 2026-11-22). None is proven live or in CI.

| Req | Expected behaviour | Current implementation evidence | Status | Gap / risk | Verification |
|---|---|---|---|---|---|
| BR-001 | Tenant and team isolation | `Principal.knowledge_owner`; `engine.py:286-295, 508-514`; `tests/security/test_idor.py`, `test_vector_owner_isolation.py`, `integration/test_workspace_isolation.py`, `test_document_delete_ownership.py` | PASS (local) | Real Qdrant server untested | tests above; Qdrant run in WP-7 |
| BR-002 | Score floor, honest empty | `engine.py:648-667`; `test_retrieval_threshold.py`, `test_either_gate_reranker.py` | PASS (local) | Unavailable index is not "empty" (KB-002) | tests above |
| BR-003 | Only D-001 licences in the global corpus | `domain/knowledge.py:95, 499-510`; `knowledge_refresh.py:235-243`; `test_source_licence.py` (22) | PARTIAL | Existing documents unknown (KB-004); no takedown (KB-003) | inventory (O6); WP-2 tests |
| BR-004 | Loop C evidence meets D-001 | none (Loop C unbuilt) | GAP | KB-007 | WP-8 gate tests |
| BR-005 | Loop C approval gate | none; `ApprovalService` exists (`learning/approval.py`) | GAP | KB-007 | WP-8 C3 cycle |
| BR-006 | Credit CC BY sources | `Chunk.citation()` has no source or licence | GAP | KB-005 | WP-3 tests |
| FR-001 | PDF, TXT, MD, HTML, DOCX upload | PDF only: `v3.py:1044-1050` | GAP | KB-009 | WP-5 per-type tests |
| FR-002 | Structured citations | legacy string only | GAP | KB-005 | WP-3 |
| FR-003 | Delete reaches every store | `engine.py:497-525`; `test_knowledge_deletion_resilience.py` (6), `test_knowledge_lifecycle.py`; load suite on Postgres | PASS (local) / UNVERIFIABLE (Qdrant) | KB-014, KB-016 | WP-7 |
| FR-004 | Role-aware team writes | backend `require_knowledge_write` PASS; client shows controls to all | PARTIAL | KB-008 | WP-6 widget tests |
| FR-005 | Retrieval available when the index matches | live `matches_embedder:false` | GAP | KB-001 | WP-0 live probe |
| FR-006 | Refresh promotes with zero sources | `knowledge_refresh.py:164-172` returns early | GAP | KB-001 | WP-1 test |
| FR-007 | `searchable` on ingest | absent | GAP | KB-002 | WP-1 |
| FR-008 | Console captures licence | `page.tsx:143-146` | GAP | KB-006 | WP-4 e2e |
| FR-009 | Incremental, licence-gated, cursor-safe refresh | `sources.py` (uncommitted), `knowledge_refresh.py`; `test_source_fetch_batches.py` (18), `test_source_licence.py` | PASS (local, uncommitted) | KB-010, KB-017 | tests above |
| FR-010 | Confirm-and-retire on source delete | `admin.py:335-342` | GAP | KB-003 | WP-2 |
| FR-011 | Takedown | absent | GAP | KB-003 | WP-2 |
| FR-012 | Set `restricted` | `_checked_licence` refuses it | GAP | KB-003 | WP-2 |
| FR-013 | Loop C C0 to C5 | none built | GAP | KB-007 | WP-8 |
| SEC-001 | Quarantine and taint | `engine.py:311-350`; `tests/security/test_prompt_injection.py` (26), `test_injection_end_to_end.py` (3), `test_knowledge_graph_pipeline.py` | PASS (local) | none found | tests above |
| SEC-002 | SSRF-safe fetch | `HttpSourceFetcher` (public address, no redirects, https); `test_integration/test_learning_pipeline.py` | PASS (local) | Loop C shared fetcher unbuilt | tests above |
| SEC-003 | Redacted SearxNG queries | none | GAP | Loop C unbuilt | WP-8 C1/C2 |
| SEC-004 | Admin route hygiene | per-handler guards; no limiter | PARTIAL | KB-012 | WP route list |
| SEC-005 | DOCX hardening | no DOCX path | GAP | KB-009 | WP-5 fixtures |
| DATA-001 | Immutable versions, idempotent ingest | `engine.py:273-305`; `test_ingestion_idempotency.py` (11), `test_knowledge.py` (33) | PASS (local) | none found | tests above |
| DATA-002 | Traceable and retirable by source | `source_id` in metadata (`_provenance`); no retire | PARTIAL | KB-003 | WP-2 |
| DATA-003 | Migrations with downgrade, Loop C after 0032 | migration `0032` has `downgrade()`; plan names `0031` | PARTIAL | KB-007; round trip on Postgres unconfirmed for 0032 | `tests/load/test_migration_roundtrip_postgres.py` |
| INT-001 | Client shows extraction fields | dropped (`v3.py:1078-1081` sends; client ignores) | GAP | KB-008 | WP-6 |
| UX-001 | Safe destructive and error states | missing | GAP | KB-008 | WP-6 widget tests |
| UX-002 | Unavailable notice | none | GAP | KB-002 | WP-1 |
| UX-003 | Reload on workspace switch | not traced | UNVERIFIABLE | KB-008 | WP-6 test |
| NFR-001 | Retrieval budget | `docs/reports/LEXICAL_RETRIEVAL_BENCHMARK.md` (4.8 ms p50 at 50k chunks, local) | PARTIAL | no live latency | live measurement in WP-7 |
| NFR-002 | Quality on the live embedder | e2e corpus gate `tests/e2e/test_docs_corpus_gate.py` runs on the test embedder; live e5 never measured | UNVERIFIABLE | KB-015 | WP-0 report |
| NFR-003 | Alerts | `aura-alerts.yml:122-144` | PARTIAL | KB-002 | WP-1 drill |
| NFR-004 | CI runs the suites | no Backend CI trigger since 2026-10-31 | UNVERIFIABLE | KB-014 | linked CI run |

## Test baseline (this discovery)

`pytest tests/unit/test_knowledge.py test_knowledge_lifecycle.py test_knowledge_deletion_resilience.py
test_ingestion_idempotency.py test_source_licence.py test_source_fetch_batches.py
test_adaptation_connector_sources.py test_document_delete_ownership.py test_cag.py
test_retrieval_threshold.py test_knowledge_upload_endpoint.py` → **187 passed, 1 skipped** (exit 0),
`.venv` Python 3.11.16, working tree with uncommitted WP4 changes, temp directory on `E:`. Not run:
the full backend suite, the legacy `unittest` tree, `tests/architecture`, `tests/load`, the Flutter
suite and the console e2e. A pre-existing failure baseline was not recorded for them.
