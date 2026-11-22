# Requirements — Knowledge source registry v2 (PLAN, 2026-11-22)

Source of every requirement: **U** = user decision (D-001), **R** = recommendation not yet approved, **E** =
existing behaviour that must be kept. Priority: B = blocks release of the capability, G = needed before
general availability, L = later.

| ID | Requirement | Acceptance criterion | Src | Pri |
|---|---|---|---|---|
| BR-001 | The global corpus MUST accept only CC0, CC BY and permissive-licensed content. | Registering a source whose `license_status` is not `approved`, or whose SPDX id is outside the configured allow-list, returns 422; the refresh skips it. A test covers CC BY-SA, CC BY-NC-SA and unknown. | U | B |
| BR-002 | Content under CC BY MUST be attributed wherever it is shown. | A chat citation for such a chunk includes publisher, title, URL and licence id. | U | B |
| DATA-001 | Every document and chunk MUST carry its source id, publisher, URL, licence id, language and domain. | After ingest, `GET /knowledge/documents/{id}` and the vector payload contain them; deleting the source's documents removes them. | R | B |
| DATA-002 | The registry MUST store: name, publisher, domains, language, `license_spdx`, `license_status`, attribution text, commercial-use flag, source version. | Migration `0032` adds them with a working `downgrade()`; `tests/load/test_migration_roundtrip_postgres.py` passes. | R | B |
| FR-001 | A source type MUST be registrable only if a fetcher for it exists. | `POST /admin/sources` with an unimplemented type returns 422 (today `repository`/`drive` are accepted and fetched as HTML). | R | B |
| FR-002 | A fetcher MUST be able to return many documents with stable ids and a source version, and MUST support conditional fetch. | A source whose upstream is unchanged downloads nothing (304) and reports `skipped_unchanged`. | R | G |
| FR-003 | Each source MUST refresh on its own schedule. | A source with `0 3 * * 1` runs only then; a source due is run once per slot, across workers. | R | G |
| FR-004 | Retrieval MUST be filterable by domain, source and language. | A turn routed to domain X returns no chunk from domain Y; the filter field is payload-indexed. | R | G |
| FR-005 | When the published index was built by another embedder, readiness MUST say so. | `/health/ready` reports `knowledge_index.published`, `.required`, `.matches_embedder` (the existing `index` string is unchanged), sets gauge `aura_knowledge_index_mismatch`, and an alert fires after 30 minutes. Readiness stays informational: it does not turn `healthy` false. **Built on branch `anas/knowledge-wp1-index-mismatch`, not merged.** | R | B |
| FR-006 | A first publish MUST be possible on an empty or small corpus. | Rebuild on a 0-chunk corpus repoints the alias; the count-delta gate is skipped when the baseline index was built by another embedder. | R | B |
| SEC-001 | External content MUST stay untrusted DATA at every step. | Existing tests keep passing (`test_history_summary_taint.py`, `test_prompt_injection.py`, `test_ssrf.py`); new fetchers use the shared SSRF policy and add tests. | E | B |
| SEC-002 | Registering a global source SHOULD need a second approver. | A source registered by one SuperAdmin stays `pending` until a different admin approves (`ApprovalKind.SOURCE_REGISTRATION`). | R | G |
| NFR-001 | A refresh MUST NOT modify the serving index until gates pass, and MUST gate on recall. | A build is named by generation; a candidate with recall@5 more than 0.02 below baseline is not promoted (test). | E/R | G |
| NFR-002 | The model-visible knowledge budget MUST be sized to the deployed context window. | Defaults documented per provider; an answer-quality suite runs on the rendered prompt and is committed with numbers. | R | B |
| NFR-003 | Corpus growth beyond 50,000 chunks MUST NOT start without a measured plan. | A capacity note with measured RAM and boot time at the chosen ceiling; Postgres FTS adapter exists before 200,000. | R | L |
| UX-001 | The console MUST show licence status, last result and next run per source. | The Knowledge page lists them; a failed source is visibly red. | R | G |

## Out of scope (recorded, not decided)

Share-alike content in private or per-workspace corpora; speech datasets (Common Voice) for STT training;
Egyptian legal sources until their reuse terms are read; any source needing a full-scale (millions of chunks)
build.
