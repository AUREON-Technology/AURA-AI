# Gap and risk register — Knowledge module (2026-11-22)

Status vocabulary: `GAP`, `PARTIAL`, `UNVERIFIABLE`, `PASS`. Evidence types: **CODE** (read at `6ac85900`),
**TEST** (ran here, 259 passed), **LIVE** (unauthenticated probe of `api.aureon-ai.tech`, release
`c346d529fb88`, 2026-11-22). Paths are relative to `aura_backend/` unless they start with `docs/`.
Earlier audit IDs (`D-xx`) are from `docs/reports/audit/full-system-audit/rag_and_knowledge.md`
(2026-10-31); each one I re-checked is marked *still open* or *fixed*.

## Summary

| ID | Sev | Status | Title |
|---|---|---|---|
| KM-01 | **P1** | GAP (live) | Production retrieval is off: published index built by a different embedder |
| KM-02 | P1 | UNVERIFIABLE | Nobody can say from the repo which sources are registered in production |
| KM-03 | P1 | GAP | The fetcher can only read one HTTPS page per source |
| KM-04 | P1 | GAP | No licence, publisher, domain or attribution data anywhere; citations carry none |
| KM-05 | P1 | GAP (for scale) | Corpus size ceilings: 5,000 docs/run, full-corpus nightly re-embed, in-process BM25 |
| KM-06 | P2 | GAP | `trust_tier` does not change guard strictness or ranking, despite the docs |
| KM-07 | P2 | GAP | Per-source `schedule_cron` is stored and never read |
| KM-08 | P2 | GAP | No incremental sync (cursor always `None`, no ETag) |
| KM-09 | P2 | GAP | Promotion is not recall-gated (`D-02`, still open) |
| KM-10 | P2 | GAP | Retrieval cannot filter by domain, source, language or licence; strategies unreachable (`D-01`, still open) |
| KM-11 | P2 | GAP | The model sees at most 2 chunks of 280 characters (`D-05`, still open) |
| KM-12 | P2 | PARTIAL | Arabic support is real but narrow, and measured only offline |
| KM-13 | P3 | GAP | `last_success_at` is recorded even when every document failed |
| KM-14 | P3 | GAP (docs) | Stale docs: the "known gap" diagram, the admin guide, the strategy report's section 11 |
| KM-15 | P3 | PARTIAL | Global-source registration has no second approver |
| KM-16 | P3 | PARTIAL | Evaluation covers 3 small suites about AURA's own docs and authored Arabic text |

## Findings

### KM-01 — Production retrieval is off (P1, LIVE + CODE)

- **Evidence.** LIVE `/health/ready`: `"index":"aura-knowledge__v1__hash-embed-v1__384"` and
  `"embedding":{"provider":"sentence-transformers","model":"intfloat/multilingual-e5-small#doc-prefix-707ad539","semantic":true}`.
  CODE `knowledge/index_manager.py:196-201` raises `IndexNotReadyError` when the published index was
  built by another embedder; `knowledge/engine.py:600-603` turns that into
  `RetrievalResult(strategy="unavailable")` with a warning log and no error to the user.
  `docs/status/BACKEND_STATUS.md:25-30` recorded the same state on 2026-11-17; it is five days old and unchanged.
- **Impact.** Every chat turn runs without knowledge. A user upload still succeeds, because
  `ensure_ready()` (`index_manager.py:204-216`) creates and writes the e5 index but never publishes it, so
  uploads sit in an index that reads refuse. Nothing tells the user or the operator.
- **Why the obvious fix may stall.** `POST /admin/index/rebuild` (`api/v3.py:2053-2081`) has no `force`.
  Its gate (`index_manager.py:74-119`) needs `min_chunks=1`, so an empty corpus cannot be promoted, and it
  fails when the chunk count differs by more than 40% from the old index. Both are plausible here
  (UNVERIFIABLE until someone lists the corpus).
- **Fix direction.** Owner: run the rebuild (`scripts/rebuild_index.py` or the endpoint) and read the
  response's `gate_failures`. Code: make the mismatch loud (`/health/ready` should report
  `index.matches_embedder`), and make first publish possible on an empty or small corpus.
- **Verify.** After the rebuild, `/health/ready` shows an index name containing the e5 slug, and an
  authenticated `GET /api/v3/knowledge/search` for a known document returns `strategy="hybrid"`.

### KM-02 — Registered sources in production are unknown (P1, UNVERIFIABLE)

- **Evidence.** CODE: no seed, migration or script registers a source (`KnowledgeSource(` appears only in
  the admin route, the Postgres adapter and the AP compiler). `learning/knowledge_refresh.py:159-167` returns
  early on an empty registry, **before** the promotion step, so an empty registry also cannot repair KM-01.
  The 2026-10-31 audit could not see production either.
- **Needed from the owner.** `GET /api/v3/admin/sources` and `GET /api/v3/admin/refresh/latest` with an admin
  token, or the console's Knowledge page.

### KM-03 — One page per source (P1, CODE + TEST)

- **Evidence.** `learning/sources.py:203-238`: a single GET, `response.text[:4 MiB]`, regex tag stripping,
  one `FetchedDocument` with id `<source>:root`. No JSON, XML, PDF, pagination, dump or OAI-PMH handling.
  `container.py:378-381` routes `web`, `rss`, `api` **and every other type** (`repository`, `drive`, `upload`)
  to that same fetcher, so a `repository` source registers fine and ingests one HTML landing page.
  Only `connector` has a real second fetcher (`sources.py:274-342`), and it needs a Track AP project.
- **Impact.** `max_documents` (up to 50,000, `admin.py:95`) means nothing for HTTP sources. Wikipedia,
  Wikidata, OpenAlex, arXiv, PubMed, PMC, Europe PMC, World Bank, NASA, NOAA, UN, OECD and WHO cannot be
  registered as the report describes; each needs its own adapter. A documentation site such as the FastAPI
  docs needs a crawler adapter, not one URL.
- **Fix direction.** Per-type fetchers behind the existing `SourceFetcher` port (the `CompositeFetcher`
  design already allows it), each yielding many documents with stable ids and a source version. Reject, at
  registration, a type that has no fetcher.

### KM-04 — No provenance or licence data (P1, CODE)

- **Evidence.** `domain/knowledge.py:427-458` (`KnowledgeSource`) has no licence, publisher, domain, language,
  attribution or commercial-use field. `admin.py:84-97` (`SourceCreateRequest`) accepts none, and the table's
  only free-form column, `metadata`, is not exposed. A chunk carries `source_type` and `title` only
  (`domain/knowledge.py:499-503`); its citation is `doc-id@vN (heading)` (`:172-188`), and `/chat` returns
  those strings (`api/v3.py:807`). `Document.language` exists but the refresh never sets it
  (`knowledge_refresh.py:267-276`).
- **Impact.** AURA cannot show a user where a fact came from, cannot honour CC BY / CC BY-SA attribution, and
  cannot exclude a source whose licence turns out to bar commercial use. This is the largest gap between the
  report's section 10B table and the code (17 fields requested; about 5 exist in some form — see
  `10-strategy-report-assessment.md`).
- **Fix direction.** Needs the licence policy decision first (the open question). Then: source metadata
  columns (Alembic migration with a working `downgrade()`), propagation to the document and chunk payload,
  a citation that names publisher and URL, and a registration check that refuses `license_status != approved`.

### KM-05 — Scale ceilings (P1 for the expansion, P3 today, CODE)

- 5,000 documents per run (`config/settings.py:1462`, `LEARNING_REFRESH_MAX_DOCS`).
- Same-embedder refresh rebuilds **into the live index**: `domain/knowledge.py:214-219` leaves the generation
  out of `physical_name`, and `_promote` (`knowledge_refresh.py:306-351`) builds the candidate with
  `identity_for_current_embedder()`. Every night re-embeds the whole corpus; "the live index is never
  modified" (`administrator-guide.md:62`) is false for this case (`D-03`, *still open*).
- BM25 is held in memory by every gateway and worker process and rebuilt from the document store at start
  (`knowledge/engine.py:1039-1057`). `docs/reports/LEXICAL_RETRIEVAL_BENCHMARK.md` measured to 50,000 chunks;
  200,000 needs about 9 GB for both indexes and was not measured (`D-08`, *still open*).
- Millions of Wikipedia/PubMed passages are out of reach on this design without a Postgres-FTS or external
  lexical index, a batched chunk-fetch port, and a build that skips unchanged chunks.

### KM-06 — `trust_tier` is mostly a label (P2, CODE)

- `domain/knowledge.py:68-73` and `docs/guides/administrator-guide.md:52` say it "drives safety strictness".
  `safety/pipeline.py:199-205` (`guard_context_items`) takes no tier, `knowledge/engine.py:315-317` passes none,
  and retrieval and context assembly never read it. It orders CAG pinning (`cag/engine.py:174`), caps connector
  sources (`sources.py:307`) and is stored on the payload (`index_manager.py:314`).
- **Impact.** The report's "authority level" would change nothing about ranking or citation today.

### KM-07 — `schedule_cron` is dead (P2, CODE)

- Only the domain, admin route, Postgres adapter and migration mention it; one beat entry runs everything at
  02:00 (`tests/unit/test_beat_schedule.py` pins `knowledge-refresh-nightly`). The report's daily, weekly and
  quarterly schedules cannot be expressed.

### KM-08 — No incremental sync (P2, CODE)

- `knowledge_refresh.py:293` always records `cursor=None`; `HttpSourceFetcher` sends no conditional headers.
  Each run downloads everything and relies on the content hash to skip unchanged text.

### KM-09 — Promotion is not recall-gated (P2, CODE, `D-02` still open)

- `container.py:382-392` builds `KnowledgeRefreshPipeline` with no `evaluator`; with `None` the recall gate is
  skipped (`knowledge_refresh.py:320-337`, `index_manager.py:109-117`). `administrator-guide.md:70` lists the
  recall gate as if it ran. With mass registration, a bad source could lower recall and still promote.

### KM-10 — No domain filtering; strategies unreachable (P2, CODE, `D-01` still open)

- Qdrant payload indexes exist only for `owner_id`, `document_id`, `kind` (`adapters/qdrant_store.py:172`),
  and chunk payloads carry no domain. `retrieve()` accepts `filters` but the kernel never passes one.
- The ten retrieval strategies, including graph and adaptive routing, are built (`container.py:424-439`) but
  the kernel receives `knowledge=self.knowledge` (`container.py:620`). The graph is written on ingest and read
  by no chat turn. Domain-routed specialist retrieval is therefore impossible without wiring work.

### KM-11 — The model sees stubs (P2, CODE, `D-05` still open)

- `retrieval_top_k=2` (`settings.py:947`), `prompt_chunk_max_chars=280` (`:970`), `DEFAULT_MAX_KNOWLEDGE_ITEMS=2`
  (`context/engine.py:60`); chunks are stored at about 320 tokens. Growing the corpus will not improve answers
  until this budget is sized to the deployed context window. No evaluation measures the clipped prompt.

### KM-12 — Arabic is real but narrow (P2, CODE + doc)

- PASS: Arabic folding (diacritics, tatweel, hamza and alef variants, digits) and light stemming in BM25
  (`knowledge/retrieval.py:27, 57-65`); multilingual embedder; "either" reranker gate. Offline recall@5: 0.95
  English, 0.90 Arabic-question-about-English-document, 0.65 Arabic corpus
  (`docs/reports/ARABIC_RETRIEVAL_EVAL_2026-11-17.md`; 23 + 23 + 60 questions, development CPU).
- GAP: no Egyptian-dialect handling, transliteration, or English-Arabic terminology mapping beyond the
  embedder; latency on the deploy target not measured; cannot take effect live until KM-01 is fixed.

### KM-13 — `last_success_at` can mislead (P3, CODE)

- `knowledge_refresh.py:293` records success after the fetch whatever happened to the documents, so a source
  whose every document failed ingestion still shows a fresh timestamp.

### KM-14 — Stale documents (P3, DOC)

- `docs/architecture/diagrams/03-turn-pipeline.md:173-178` still calls the summary-on-`SYSTEM` / unpersisted
  taint a known gap. It is fixed: the summary is fenced `DATA` (`context/engine.py:576-593`), taint is stored per
  message and on the summary (`memory/session.py:106-110, 164-194`), and a tainted history taints the next turn
  (`kernel/kernel.py:561-566, 1287-1289`). TEST: `tests/unit/test_history_summary_taint.py` passes.
  The strategy report's section 11 was written from this stale diagram.
- `administrator-guide.md:52, 62, 70` (trust tier, never-modified live index, recall gate) do not match the code
  (KM-05, KM-06, KM-09).

### KM-15 — One approver for a global source (P3, CODE)

- `POST /admin/sources` needs SuperAdmin and is audited (`admin.py:163-195`), which is sound. `ApprovalKind.SOURCE_REGISTRATION`
  exists but is unused (`docs/plans/adaptive-knowledge-update-system.md:650`). A global source reaches every
  user, and the context guard is the only defence against poisoned text, so a second approver and a licence
  review belong in the registration step once many sources exist.

### KM-16 — Thin evaluation (P3)

- `evaluation/suites/` holds `docs_corpus` (23 questions), `docs_corpus_ar` (the same 23 in Arabic) and
  `arabic_corpus` (60). They test AURA's own ADRs and authored Arabic documents. There is no held-out set per
  knowledge domain and no BEIR, MTEB or MIRACL loader. Citation correctness and faithfulness have an answer
  baseline for `docs_corpus` only.

## Checked and fine (PASS, with evidence)

| Area | Evidence |
|---|---|
| SSRF policy on source fetch | `sources.py:163-238`; `tests/security/test_ssrf.py` passed |
| Idempotent, versioned ingest and chunk retirement | `engine.py:273-367`; `test_ingestion_idempotency.py`, `test_knowledge.py` passed |
| Instruction-shaped chunks quarantined at ingest and at retrieval | `engine.py:311-350`; `context/engine.py:367-372` |
| Retrieved text is fenced DATA; summary is DATA; taint persists | KM-14 evidence |
| Owner/global isolation and tombstone-checked reads | `engine.py:808-862`; `test_knowledge_deletion_resilience.py` passed |
| Score floor returns nothing rather than noise | `engine.py:664-665`; `test_retrieval_threshold.py` passed |
| Connector sources refuse trust above tier 1 and non-project owners | `sources.py:307-312`; `test_adaptation_connector_sources.py` (not in this run) |

## Not verified

Authenticated live behaviour; real Qdrant and Postgres adapters under this module; any latency figure on the
deploy target; that side-effecting tools demand confirmation on a tainted turn (ADR-006 tests were not part of
this run); whether the Engineer agent queries the knowledge base for its own turns; the Stack Exchange terms.
