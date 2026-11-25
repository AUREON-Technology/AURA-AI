# Knowledge-Base System: business interview and decisions (2026-11-22)

Mode: DISCOVER. Source of every `USER-CONFIRMED` row: the owner's answers in the 2026-11-22
session. Nothing here changes code.

## Decisions confirmed by the owner

| ID | Question | Confirmed answer | Affects |
|---|---|---|---|
| D1 | Which parts must be production-ready? | **All three:** user library, global corpus (Loop A, licence gate, console), and Loop C. | scope of every requirement below; KB-007 |
| D2 | Must Loop C evidence meet D-001? | **Yes.** Only pages from a source with an approved licence count as evidence. SearxNG results are leads only and are never cited or quoted unless their domain is licence-approved. A proposal with no approved-licence evidence ends `NO_SOURCES`. | ADR-035 §3, plan §5 and C2, BR-004, SEC-003 |
| D3 | What happens to a source's documents on removal or takedown? | **Retire them, with confirmation.** Deleting a source asks "also remove its N documents?"; if confirmed they are retired through the normal delete path. A separate Super Admin **takedown** does the same while keeping the source registered as `restricted`. Every step audited. | KB-003, DATA-002, FR-010 to FR-012 |
| D4 | Which upload types for this release? | **PDF, TXT/MD/HTML and .docx.** | KB-009, FR-001, SEC-005 |

Previously recorded owner decisions that still bind this work: **D-001** (global corpus accepts
CC0, CC BY and permissive licences only; commit `723f005c`); **ADR-035 §1.1** (every Loop C change is
queued, only platform admins approve, the asker gets the normal answer, approved knowledge goes to the
global corpus); **owner decision 17** (Arabic "either" relevance gate).

## Recommendations R1 to R9 (approved by the owner on 2026-11-22: "Approve R1-R9")

Approved for planning. They are reversible technical choices with one sensible answer. Implementation
still needs its own go-ahead on the plan in `05-implementation-plan.md`. R1 is an Admin action that
has not been performed.

| ID | Recommendation | Why |
|---|---|---|
| R1 | An Admin runs `POST /admin/index/rebuild` (or `scripts/rebuild_index.py` over `railway ssh`) now, then commits a report. Also let the nightly refresh promote an up-to-date index even when no source is enabled. | KB-001. Today the refresh returns early with no sources (`knowledge_refresh.py:164-172`), so it can never heal this. |
| R2 | Make an unavailable index a first-class degraded state: a `knowledge_unavailable` degraded reason on the turn, `aura_retrieval_total{result="unavailable"}`, a Prometheus alert, a `knowledge.degraded` block on `/health/ready` (keep `healthy` true so chat stays in rotation), and a client notice. | KB-002 |
| R3 | While the index is not ready, ingest stays accepted but the response says `searchable: false` and the client says so. | Uploads are acknowledged today and invisible to search. |
| R4 | Structured citations: `{document_id, version, title, location, source_name, publisher, license, attribution, url}`, resolved from the document store at response time; keep the old `citation` string for compatibility; client chips link to the source. | KB-005, D-001 |
| R5 | Console: licence and provenance fields on the register form, an "inert, licence missing" badge driven by `ingestible`, a provenance edit action, the takedown action. | KB-006, D3 |
| R6 | Client fixes: delete confirmation, keep typed text on a failed save, a search error is not "no results", role-aware controls, invalidate the library on workspace switch, retry on load error, pin failure stays local, show unreadable PDF pages. | KB-008 |
| R7 | Amend ADR-035 and the plan: renumber the Loop C migration to `0033`; add the D2 evidence gate; SearxNG hosted as a Railway service (the VPS target is blocked); asker threshold configurable, 3 in production and 2 on dev. | KB-007 |
| R8 | Investigate then fix: refresh ingests into the live index when the embedder is unchanged, so `_promote` compares an index with itself. | KB-017 |
| R9 | Ship parts 1 and 2 first and Loop C behind `AURA_KNOWLEDGE_ADAPTIVE_ENABLED=false` until its C3 evidence exists. | Sequencing, reversible |

## Open or deferred

| ID | Item | Status | Blocks |
|---|---|---|---|
| O1 | Where SearxNG runs (Railway service vs VPS workers) | `DECIDED` via R7: a Railway service (cost to be confirmed when C2 starts) | Nothing |
| O2 | Distinct-asker threshold (3 or 2) | `DECIDED` via R7: configurable, 3 in production, 2 on dev | Nothing |
| O3 | Who reviews proposals, and a realistic queue-age alert | `OPEN` | Loop C phase C3 |
| O4 | Arabic handling in SearxNG engine selection | `DEFERRED` to the Arabic plan | Nothing before C2 |
| O5 | Notify an explicit requester when approved | `DEFERRED` (owner chose "normal answer") | Nothing |
| O6 | The live inventory: which global sources and documents exist, with what licence | `UNKNOWN` (needs an authenticated admin call) | KB-004 disposition |
| O7 | Whether any live source has type `rss`, `api`, `repository` or `drive` (the WIP stops fetching those) | `UNKNOWN` | WP4 merge |
