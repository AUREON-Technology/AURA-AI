# Implementation plan — Knowledge source registry v2 (PLAN, 2026-11-22)

Nothing here is approved for implementation yet. File lists are **estimated** until work starts. Finding IDs
are in `04-gap-and-risk-register.md`, requirement IDs in `03-requirements-and-acceptance-criteria.md`.

> **Reconciled with a second plan (memory subject rule and Loop A gate-before-write) on 2026-11-22:** see
> `06-loop-a-and-memory-plan-reconciliation.md`. It changes WP1 (add `up_to_date`, alert), WP3, WP4, WP5, WP6
> and WP9, and inserts a Loop A change (B2) before WP3.

## Order and dependencies

```
WP0 (owner, no code) ──► WP1 ─┐
                       └► WP2 ─┴─► WP3 ──► WP4 ──► WP5
                                      │         └──► WP6 ──► WP7 ──► WP8
                                      └──► WP9 (docs, any time)         WP10 (only when measured)
```

Release classification: **blockers** WP0-WP4; **before general availability** WP5-WP7; **later** WP8, WP10.

## Release blockers

### WP0 — Restore retrieval and take inventory (owner, no code) · KM-01, KM-02

1. Read the registry: `GET /api/v3/admin/sources` and `GET /api/v3/admin/refresh/latest` (admin token), or the
   console Knowledge page. Record what is registered, by whom, with what trust tier.
2. On Railway (`railway ssh`): `python scripts/rebuild_index.py --dry-run`, read the target and active names,
   then run it without the flag. If it exits 1 (gate failed), read `gate_failures` first. Use `--force` only
   if the reason is the expected one (an empty corpus or a chunk-count change caused by the embedder switch).
   Exit 2 means the build failed; stop and report. Do not touch Qdrant by hand.
3. **Done when:** `/health/ready` shows an index name containing the e5 slug, and an authenticated search for
   a known document returns `strategy="hybrid"` with a hit. Record both outputs in `docs/status/BACKEND_STATUS.md`.
   *Note:* the HTTP endpoint has no `--force`; the script does. Prefer the script.

### WP1 — Make an index mismatch visible and recoverable · KM-01 · FR-005, FR-006

**Status 2026-11-22: implemented on branch `anas/knowledge-wp1-index-mismatch`, uncommitted, not merged or
deployed.** Local result: 307 tests passed across the touched and neighbouring suites (knowledge, lifecycle,
refresh, readiness, API surface, monitoring). Added: `IndexManager.status()`, `knowledge_index` in
`/health/ready`, gauge `aura_knowledge_index_mismatch`, alert `AuraKnowledgeIndexMismatch` (both copies of the
rules file), and a gate that ignores a foreign-embedder baseline. Still to do: the layering run (in progress),
then review and merge.

- Scope: `/health/ready` reports published vs required index and `matches_embedder`; a metric and alert on
  mismatch; first-publish works on an empty corpus; the count-delta gate is skipped when the baseline was built
  by a different embedder.
- Likely files (estimated): `aura_core/knowledge/index_manager.py`, `aura_service.py` (readiness),
  `aura_core/observability/` (metric), tests in `tests/unit/test_knowledge_lifecycle.py`.
- Risk: loosening a gate. Keep it narrow: the skip applies only when the embedder differs, never to a
  same-embedder refresh.
- Tests: mismatch reported; empty-corpus rebuild promotes; same-embedder 60% shrink still blocked.
- Depends on: nothing. Blocks production: yes (otherwise this recurs on the next embedder change).

### WP2 — Size what the model sees · KM-11 · NFR-002

- Scope: make `retrieval_top_k` and `prompt_chunk_max_chars` per-deployment settings with documented values for
  the Railway providers (`hosted1`, `claude1`), and add an answer suite over the **rendered** prompt.
- Likely files: `aura_core/config/settings.py`, `docs/deployment/control-plane.md`, `deploy/docker-compose.*.yml`
  (both, same commit, parity rule), `evaluation/suites/`.
- **Needs a decision/measurement, not a guess:** the values depend on each provider's context window and the
  latency budget. Measure recall and answer quality at, say, 2×280, 4×600, 5×1000 on the committed suites
  before choosing.
- Depends on: WP0 (retrieval must work to measure). Blocks production: yes.

### WP3 — Registry metadata, licence gate, provenance in citations · KM-04, KM-06, KM-15 · BR-001/002, DATA-001/002, SEC-002

- Migration `0032` adding the DATA-002 columns (with `downgrade()`); `KnowledgeSource`, the Postgres adapter,
  `SourceCreateRequest`, `_source_payload`, and the console form.
- Licence allow-list in settings (CC0-1.0, CC-BY-4.0 and the permissive SPDX ids; D-001). Registration and
  refresh both refuse anything else. Existing rows default to `license_status=unknown` and are **skipped by the
  refresh** until reviewed (fail closed; the registry may be empty, see WP0).
- Propagate source metadata into `Document.metadata` and the chunk payload; change `Chunk.citation()` output
  so `/chat` citations carry publisher, title, URL, licence.
- Wire `ApprovalKind.SOURCE_REGISTRATION` so a global source needs a second admin.
- Decide `trust_tier`: recommend keeping it as a stored label, correcting the docs (WP9), and using it only for
  citation badges; do not invent ranking behaviour.
- Likely files: `alembic/versions/0032_*.py`, `aura_core/domain/knowledge.py`, `adapters/postgres_sources.py`,
  `api/admin.py`, `knowledge/engine.py`, `knowledge/index_manager.py`, `aura_admin/app/(console)/knowledge/page.tsx`,
  `aura_client` citation chip, tests (unit, architecture, load migration round trip on a real Postgres).
- Compatibility: the `/chat` citations field is a list of strings today; older clients must keep working.
  Add a new field alongside it rather than changing the string format.
- Depends on: WP0 inventory (to know what legacy rows exist). Blocks production: yes.

### WP4 — Fetcher framework and the first adapter · KM-03 · FR-001, FR-002, SEC-001

- Registration refuses a type without a fetcher. `SourceFetcher` may return many documents with stable ids,
  a source version and a cursor; conditional GET with ETag/Last-Modified.
- First adapter: a **documentation-site fetcher** (same host only, sitemap or link-limited crawl, page ceiling,
  per-page licence recorded from the source row). It serves the permissive software docs, the highest-value
  set for the Engineer agent, and is small enough to prove the framework.
- Reuse the shared SSRF checks; do not copy them. Every new adapter ships its own SSRF and
  oversized-response tests.
- Likely files: `aura_core/learning/sources.py` (or a new `learning/fetchers/`), `container.py`,
  `tests/security/test_ssrf.py`, `tests/unit/test_session_and_sources.py`.
- Depends on: WP3. Blocks production: yes for any source beyond a single page.

## Before general availability

### WP5 — Per-source schedules and honest success · KM-07, KM-08, KM-13 · FR-003

- A beat dispatcher runs every few minutes, selects sources whose `schedule_cron` is due and not already
  running (lock in Redis or Postgres), and enqueues one task per source. Record `last_run_at`, `last_result`,
  `next_run_at`; record `last_success_at` only when at least one document ingested or was confirmed unchanged.
- Likely files: `celery_app.py`, `tasks.py`, `learning/knowledge_refresh.py`, migration `0033` (run state),
  `tests/unit/test_beat_schedule.py`. Console shows the three fields (UX-001).
- Depends on: WP3, WP4.

### WP6 — Recall-gated, generation-named builds · KM-05 (part), KM-09, KM-16 · NFR-001

- Name refresh builds by generation so the live index is never written (`D-03`); wire a `RetrievalEvaluator`
  with the committed `docs_corpus` suite into `KnowledgeRefreshPipeline`; add one small held-out question set per
  newly registered domain (template plus a rule that evaluation text is never ingested).
- Cost to state before building: a generation-named build doubles vector storage during the build, and the full
  re-embed remains until chunk-level skip is added.
- Likely files: `domain/knowledge.py`, `knowledge/index_manager.py`, `learning/knowledge_refresh.py`,
  `container.py`, `evaluation/`.
- Depends on: WP1.

### WP7 — Domain filtering in the request path · KM-10 · FR-004

- Add `domain` to the chunk payload and the Qdrant keyword-index list; let the kernel pass a filter chosen by
  agent or intent. **Decision needed first:** either route through the existing `StrategyFacade`
  (`container.retrieval`, built and unused) or add a plain `filters` argument to the kernel's call. Recommend the
  plain filter; the facade brings ten strategies that have never served a turn.
- Likely files: `knowledge/engine.py`, `adapters/qdrant_store.py`, `kernel/kernel.py`, `agents/`, tests.
- Depends on: WP3 (domain data exists).

## Later

### WP8 — Adapters for allowed sources · KM-03 · BR-001

One adapter per source, each with a licence filter and a bounded first slice: Wikidata (a topic subset),
OpenAlex and arXiv metadata, PMC open-access limited to CC0/CC BY articles, World Bank CC BY datasets.
**Still to find:** an allowed source for Arabic prose, since Wikipedia is excluded. Each adapter is its own
reviewable change with its own acceptance test.

### WP9 — Correct the documents (any time, no code risk) · KM-14

Update `docs/architecture/diagrams/03-turn-pipeline.md:173-178` (fixed by KAN-101), `administrator-guide.md`
(trust tier, "never modified", recall gate), `docs/status/BACKEND_STATUS.md` (WP0 result). Do not edit the
strategy report; send its author the section 11 correction.

### WP10 — Scale (only when measured) · KM-05 · NFR-003

Postgres-FTS lexical adapter behind the existing port, a batched chunk-fetch method, chunk-level skip on
rebuild. Trigger: a measured corpus above 50,000 chunks. Run the 200,000-chunk benchmark first.

## Verification plan

| Check | Where | When |
|---|---|---|
| Backend `pytest` and legacy `unittest` | local, venv, temp on `E:` | every WP |
| `tests/architecture` file by file | local | every WP touching layering |
| Real-Postgres migration round trip | `tests/load` with a throwaway Postgres | WP3, WP5 |
| Real-Qdrant filter and index test | throwaway Qdrant | WP7 (CB-40 is still open) |
| Admin console `lint`, `build`, `test:e2e` | `aura_admin` | WP3, WP5 |
| Client `analyze` and `test` | `aura_client` | WP3 (citation chip) |
| Live checks | `/health/ready`, authenticated search, refresh report | WP0, then after each deploy |

Rules that apply throughout: Railway auto-deploys every `main` push, CI is unavailable (CB-44), and `main` is
unprotected, so each WP goes through a PR and local suites with the counts quoted. Both compose files change
in the same commit.

## Open decisions

| # | Decision | Needed by | Recommendation |
|---|---|---|---|
| Q1 | How legacy sources with unknown licence are treated | WP3 | Fail closed (skip until reviewed) |
| Q2 | Domain routing: plain filter or `StrategyFacade` | WP7 | Plain filter |
| Q3 | May share-alike content be used in private or per-workspace corpora | WP8 | Defer; not needed for the first slices |
| Q4 | Who reviews a licence | WP3 | A second admin through the existing approval kind |
| Q5 | Prompt budget values | WP2 | Measure, then choose |
| Q6 | Source for Arabic prose | WP8 | Research candidates under D-001 |

## Risks and exclusions

A migration touches a live database whose schema head is `0031` (verified live); the first deploy after `0032`
must have its `/health/ready` `schema` block checked. No work package was measured for effort; I have given no
estimates. Anything involving real traffic, Qdrant Cloud sizing or provider latency is unmeasured.
