# The "Knowledge Resource Expansion Strategy" report, checked against the code

Report dated 2026-11-22. Code at `6ac85900`; live probe of `api.aureon-ai.tech` the same day. Finding IDs
(`KM-xx`) refer to `04-gap-and-risk-register.md`.

## Bottom line

The report's **direction is sound** (expand the registry, keep one platform, separate domains, treat every
external document as untrusted DATA, keep an evaluation catalogue apart from production knowledge). Three of
its premises do not hold, and its phase order skips the work that has to come first:

1. **"Your current architecture already supports this."** True for the *pipeline* (ingest, guard, embed,
   hybrid retrieve, fence). False for the *registry*: one HTTPS page per source, no licence or domain data,
   no scale path past a few tens of thousands of chunks (KM-03, KM-04, KM-05).
2. **"Scheduled knowledge refresh" with per-source schedules.** One nightly job; `schedule_cron` is never read
   (KM-07).
3. **Section 11, "critical improvement before mass registration".** Already built (KAN-101). The report relied
   on a stale diagram (KM-14). Keep its five rules as acceptance criteria; none needs new work except rule 5,
   whose tests I did not run.

What the report does not mention at all: **retrieval is switched off in production today** (KM-01), and the
model would receive only two 280-character stubs even once it is back (KM-11). More sources do nothing for
users until both are fixed.

## Section 10B — "mandatory source metadata" against what exists

| Field | Exists? | Where / note |
|---|---|---|
| `source_id` | yes | `KnowledgeSource.id` |
| `source_name` | no | |
| `source_url` | yes | `KnowledgeSource.uri` |
| `publisher` | no | |
| `knowledge_domains` | no | no domain concept anywhere; Qdrant filters only on owner, document, kind (KM-10) |
| `language` | no | `Document.language` exists; refresh never sets it |
| `license_spdx`, `license_status`, `commercial_usage`, `attribution` | no | KM-04 |
| `authority_level` | partly | `trust_tier` 0-3; changes nothing about ranking or guards (KM-06) |
| `acquisition_method` | partly | `type`; all but `connector` share one fetcher (KM-03) |
| `refresh_policy` | no | `schedule_cron` stored, never used (KM-07) |
| `last_synced_at` | yes | `last_success_at`; can mislead (KM-13) |
| `source_version` | no | `last_cursor`, always `None` (KM-08) |
| `document_hash` | yes | `Document.content_hash` (sha-256), drives idempotency |
| `index_status` | partly | per document: active, superseded, quarantined, tombstoned; per source: `enabled` only |

About 5 of 17 exist in a usable form. Adding the rest needs an Alembic migration with a working
`downgrade()` (project rule), API and console changes, and propagation to the chunk payload.

## Section 10C — refresh schedules

| Report schedule | Possible today? |
|---|---|
| Daily or weekly per source | No: one 02:00 job for all sources (KM-07) |
| "Daily change checks, version-pinned" for docs | No: no conditional fetch, no version field (KM-08) |
| Retries, failures, versioning, reindexing "handled by workers" | Partly: per-source failure is isolated and reported; documents are versioned; reindex is a full rebuild (KM-05) |

## Which of the report's sources the registry can take

| Group | Sources | Verdict |
|---|---|---|
| **Needs a new adapter, then fits** | Wikipedia, Wikidata (dumps); OpenAlex, Europe PMC, World Bank, NASA, NOAA, UN, OECD, WHO (APIs); arXiv (OAI-PMH); PubMed, PMC (E-utilities); official docs sites (crawl) | KM-03. Also bounded by KM-05: a full Wikipedia is millions of chunks |
| **Fits only as a curated slice** | Wikipedia/Wikidata subsets, arXiv categories, selected PMC open-access titles | Feasible within the 5,000-per-run cap if scoped by topic |
| **Not knowledge text** | Mozilla Common Voice (audio): belongs to speech evaluation or training, not the registry | Use `scripts/measure_stt_wer.py` and Loop B instead |
| **Discovery catalogues, not sources** | Masader, RAG Repo, Google Dataset Search, Hugging Face Hub | Registering them would index catalogue pages. Use them to find candidates, then register the dataset itself |
| **Evaluation only** | BEIR, MTEB/MMTEB, MIRACL | No loader exists (KM-16). Keep out of production knowledge, as the report says |
| **Parser needed per dataset** | Hugging Face datasets (parquet/JSONL), Project Gutenberg, DOAB | Dataset card and licence check first |
| **Licence-gated** | GitHub repositories (per-repo licence), Stack Exchange, OpenStax, Egyptian legislation and CAPMAS, Common Crawl | Policy decision needed (the open question). OpenStax: the page the report cites says its textbooks are CC BY-NC-SA, so the report's claim matches that page. Stack Exchange: terms could not be fetched, so unverified |
| **`repository` type** | GitHub | The type exists but routes to the HTML fetcher (KM-03); it does not clone anything. Engineer-agent workspaces (ADR-013/014) are a separate mechanism |

## Section 10A — the 17 knowledge domains

A reasonable taxonomy. The code has no domain field, so none of the report's "domain-filtered retrieval and
specialist-agent routing" works. Needed: a `domain` value on the source, copied to the document and chunk
payload; a Qdrant keyword index on it (add the field to the fixed list in `qdrant_store.py:172`; the store then
creates it on existing collections too, because it re-ensures that list on every start); and a kernel path that passes a filter (KM-10). One collection with
filters, as the report suggests, is the right shape.

## Section 4 — Arabic requirements

| Report item | State |
|---|---|
| Diacritic, MSA normalisation | **Done** in lexical search (`knowledge/retrieval.py:57-65`) |
| Arabic/English cross-language matching | **Done offline** via the multilingual embedder and "either" reranker (0.90 recall@5, 23 questions) |
| Egyptian colloquial queries | Not handled specifically; only the 23-question Arabic-ask-English set approximates it |
| Terminology equivalents, transliteration | Not built |
| Language-aware reranking | The multilingual cross-encoder covers it; not Arabic-specific |
| Dataset catalogue use (Masader) | Discovery only (see table above) |

All of it is moot in production until KM-01 is fixed.

## Section 9 — evaluation

The report's metric list (recall@5/10/20, MRR, nDCG, citation correctness, faithfulness) is mostly present in
`evaluation/` (recall, MRR, nDCG, an answer suite for `docs_corpus`). Missing: per-domain held-out sets,
recall@10/20 reporting, contradictory and outdated-document cases, and wiring the gate into refresh promotion
(KM-09). The report's rule "never train on the evaluation answers" is respected by construction today because
no evaluation data is ingested into production.

## Section 12 — suggested phase order

| Report | Recommended |
|---|---|
| Phase 1: register Wikipedia, Wikidata, OpenAlex, arXiv, PubMed, PMC, HF datasets | **Phase 0, before any registration:** KM-01 (re-publish the index), KM-11 (size the prompt budget), KM-02 (inventory what is registered), the licence policy |
| Phase 2: engineering documentation | **Phase 1:** registry v2: metadata and migration (KM-04), multi-document fetchers (KM-03), real `schedule_cron` and cursors (KM-07, KM-08), recall-gated promotion (KM-09) |
| Phase 3: Arabic and public data | **Phase 2:** first curated slice, engineering docs (small, versioned, high value for the Engineer agent), with domain filtering (KM-10) and a held-out evaluation set per domain |
| Phase 4: specialist knowledge | **Phase 3:** Arabic and public-data APIs, one adapter each |
| Phase 5: continuous improvement | **Phase 4:** scale work (KM-05) only when a measured corpus needs it; then specialist domains |

## Claims I could not check

Wikipedia, Wikidata, OpenAlex, arXiv, PMC, Common Voice, World Bank, GitHub and Stack Exchange licence terms;
dataset sizes and counts (3.3 GB, 1,162 datasets, 381 sources, 300 billion pages); the report's 2026 dates for
the MCP specification. Only the OpenStax licence statement was compared with its cited page. Treat every other
external fact as the report author's, to be re-verified before it goes into a licence field.
