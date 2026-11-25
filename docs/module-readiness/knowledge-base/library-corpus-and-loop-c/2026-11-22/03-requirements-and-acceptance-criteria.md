# Knowledge-Base System: requirements and acceptance criteria (2026-11-22)

Sources: `D#` = owner decision in `02-…`; `ADR-n` = approved architecture record; `R#` = recommendation
(not yet approved); `EXISTING` = behaviour already built and tested that must be kept.
Priority: P0 release blocker, P1 blocker for general availability, P2 important, P3 later.

## Business rules

| ID | Requirement | Acceptance criteria | Source | Pri |
|---|---|---|---|---|
| BR-001 | A user's or team's documents MUST be visible only to their owner or team members; global documents MUST be read-only to users. | Cross-user list, search, delete, overwrite and erase attempts return 404/409 or an empty set, on the in-memory, qdrant and Postgres stores. | EXISTING, ADR-034 | P0 |
| BR-002 | When nothing relevant is found, retrieval MUST return nothing and the assistant MUST say it has no grounded information; it MUST NOT label noise as context. | Below-floor candidates are never returned; `searched_but_empty` is set. | EXISTING, ADR-005 | P0 |
| BR-003 | The global corpus MUST accept only CC0, CC BY and permissive licences, and MUST NOT carry content of unknown or restricted licence. | A global source without an approved licence is never fetched; the live corpus contains no document from a non-approved source (inventory evidence). | D-001, D3 | P0 |
| BR-004 | Loop C evidence MUST meet BR-003: only pages from a licence-approved source count; SearxNG results MUST NOT be cited or quoted unless their domain is licence-approved; otherwise the proposal ends `NO_SOURCES`. | A proposal built only from an unapproved domain is `NO_SOURCES` and has zero evidence rows; a fitness test pins the draft prompt's inputs. | D2 | P1 |
| BR-005 | Every Loop C change to the global corpus MUST wait for a platform admin's recorded, digest-bound approval; nothing changes before it. | Applying an unapproved or stale-base proposal fails closed (`stale_base`); editing a proposal voids its approval. | ADR-035 | P1 |
| BR-006 | Answers grounded in a CC BY source MUST credit that source to the user. | A response citing a CC BY chunk carries source name, licence id and a link; the client shows them. | D-001, R4 | P0 |

## Functional

| ID | Requirement | Acceptance criteria | Source | Pri |
|---|---|---|---|---|
| FR-001 | Upload MUST accept PDF, TXT, MD, HTML and DOCX, validate the type by extension and magic bytes, and answer 415 with the supported list otherwise. | One test per type ingests; a renamed `.exe` and a mismatched extension get 415; the client picker lists the five types. | D4 | P1 |
| FR-002 | Search results MUST carry a structured citation (document id, version, title, location, source name, publisher, licence, attribution, URL) and keep the legacy `citation` string. | API schema test; legacy field unchanged; the client chip opens the source. | R4 | P1 |
| FR-003 | Deleting a document MUST remove it from vectors, lexical shards, graph facts and CAG, and report the chunks removed. | Delete then retrieve returns nothing; graph facts gone; on a real Qdrant and real Postgres as well. | EXISTING | P0 |
| FR-004 | Team writes MUST be Owner or Admin only, and the client MUST hide or disable write controls for other members with a clear message. | Member sees no upload, delete or pin; a forced call returns 403 and a readable message. | ADR-034, R6 | P2 |
| FR-005 | Retrieval MUST be available whenever the published index matches the embedder; an Admin MUST be able to rebuild and promote with a gate report. | Live `/health/ready` shows `matches_embedder: true`; a retrieval smoke returns a known chunk; a report is committed. | ADR-007 | P0 |
| FR-006 | The refresh MUST be able to build and promote an up-to-date index even when no source is enabled. | With zero sources and a mismatched index, a refresh run promotes a gate-passing index. | R1 | P1 |
| FR-007 | An ingest response MUST include `searchable` (false while the index is not ready) and the client MUST say so. | Test with a mismatched index: `searchable:false`; client shows the notice. | R3 | P1 |
| FR-008 | Registering a global source MUST capture licence and provenance; the console form MUST offer the fields and show whether the source is ingestible. | Register without a licence shows "inert, licence missing"; with an allowed SPDX id it becomes ingestible. | D-001, R5 | P1 |
| FR-009 | The nightly refresh MUST be incremental (ETag, Last-Modified), MUST skip non-approved global sources before any network call, and MUST NOT advance the cursor on partial failure. | Existing `test_source_fetch_batches.py` and `test_source_licence.py` pass. | EXISTING (uncommitted) | P1 |
| FR-010 | Deleting a global source MUST ask "also remove its N documents?" and, when confirmed, retire them through the normal delete path. | Confirmed delete leaves zero retrievable documents with that `source_id`; declined delete leaves them. | D3 | P1 |
| FR-011 | A Super Admin takedown MUST retire the source's documents and keep the source registered as `restricted`. | After takedown the source is `restricted`, not fetched, and has no live documents; one audit event per step. | D3 | P1 |
| FR-012 | The provenance route MUST be able to set a source to `restricted`. | `PUT …/provenance` with a restricted marker is accepted and persists; a restricted source is never ingestible. | D3 | P1 |
| FR-013 | Loop C MUST implement gap capture, explicit requests, corrections, admin new versions and staleness, then research, draft, queue, apply and revert (plan phases C0 to C5). | Each phase meets its exit criteria in plan §9. | ADR-035, D1 | P1 |

## Security and privacy

| ID | Requirement | Acceptance criteria | Source | Pri |
|---|---|---|---|---|
| SEC-001 | Instruction-shaped content MUST be quarantined before indexing and before graph extraction, and a poisoned retrieval MUST taint the turn. | Existing injection suites pass. | EXISTING, ADR-006 | P0 |
| SEC-002 | Source fetching MUST resolve to public addresses, refuse redirects, use HTTPS and cap bytes and time. | Existing SSRF tests pass; Loop C reuses one shared fetcher. | EXISTING, ADR-035 | P0 |
| SEC-003 | SearxNG MUST receive only redacted topic queries and admins MUST see topics, never askers. | Leakage test ("Project Falcon" private entity) fails closed. | ADR-035 | P1 |
| SEC-004 | Admin knowledge routes SHOULD share a router-level guard and a rate limit, and an unknown source id MUST answer 404. | Route-guard certification test lists them; `PATCH` unknown returns 404. | R-low | P3 |
| SEC-005 | DOCX handling MUST cap decompressed size and entry count, refuse macros and external references, and run off the event loop. | Zip-bomb fixture refused; macro-enabled file refused; fuzz fixture does not hang. | D4 | P1 |

## Data

| ID | Requirement | Acceptance criteria | Source | Pri |
|---|---|---|---|---|
| DATA-001 | Document versions MUST be immutable; identical content MUST be a no-op; concurrent submissions MUST index once. | Existing idempotency tests pass. | EXISTING, ADR-007 | P0 |
| DATA-002 | A document MUST be traceable to its source (`source_id`, licence, publisher, attribution) and retirable by source. | A query by `source_id` lists a source's documents; retire-by-source test. | D3 | P1 |
| DATA-003 | Every schema change MUST be an Alembic migration with a working `downgrade()`; Loop C's migration MUST be numbered after `0032`. | `tests/load/test_migration_roundtrip_postgres.py` green; the plan names `0033`. | CB-20 | P1 |

## Integration, UX and non-functional

| ID | Requirement | Acceptance criteria | Source | Pri |
|---|---|---|---|---|
| INT-001 | The client MUST parse and show `pages`, `unreadable_pages`, `blank_pages` and `ocr_used` from the upload response. | A scanned PDF with no OCR shows a warning, not "Uploaded". | R6 | P2 |
| UX-001 | Delete MUST ask for confirmation; a failed save MUST keep the user's text; a search failure MUST NOT look like "no results"; a load error MUST offer retry; a pin failure MUST NOT replace the library. | Widget tests for each state. | R6 | P2 |
| UX-002 | When retrieval is unavailable the client MUST show a notice ("knowledge search is being updated"). | Widget test on the `knowledge_unavailable` degraded reason. | R2 | P1 |
| UX-003 | The library MUST refresh when the active workspace changes. | Switch test shows the new scope's list. | R6 | P2 |
| NFR-001 | Retrieval MUST stay inside a stated budget at corpus scale. | Lexical stage at 50k chunks p50 ≤ 10 ms (measured 4.8 ms locally); end-to-end retrieve p95 measured on the live stack and recorded. | EXISTING | P2 |
| NFR-002 | Retrieval quality MUST be measured on the live embedder and meet or beat the committed baseline. | Report in `docs/reports/` after rebuild: recall@5 and the Arabic eval, compared with the baselines in `evaluation/`. | ADR-005 | P0 |
| NFR-003 | An unavailable index and a stale refresh MUST raise alerts. | Alert fires in a drill; `AuraKnowledgeStale` fires when the metric is absent too. | R2 | P1 |
| NFR-004 | Knowledge suites MUST run in CI and pass. | A Backend CI run that executed them is linked. | CLAUDE.md | P1 |
