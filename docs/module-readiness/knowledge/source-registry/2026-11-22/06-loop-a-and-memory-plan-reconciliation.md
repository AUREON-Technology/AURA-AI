# Reconciliation: "fix the two knowledge defects" (Plan B) with the registry plan (Plan A)

Date 2026-11-22. **Plan A** = `05-implementation-plan.md` (WP0-WP10, in progress on
`anas/knowledge-wp1-index-mismatch`). **Plan B** = the pasted plan: Fix 1, memory subject rule;
Fix 2, Loop A gate-before-write. This is a planning document; no code was changed for it.

## State of our work (verified now)

| Item | State |
|---|---|
| WP0 | Owner step, not done. |
| WP1 | Code done on the branch, **not committed**: `IndexManager.status()` and `_reads_through`, foreign-baseline handling in `evaluate_promotion`, `Container.health()` adds `knowledge_index`, gauge `knowledge_index_mismatch`. Tests: **75 passed** (`test_knowledge.py` 40, `test_readiness_knowledge_index.py` 4, `test_learning_pipeline.py` 31). |
| WP1 still to do | Alert rule `AuraKnowledgeIndexMismatch` in `deploy/monitoring/alerts/aura.yml`; `up_to_date` field in `status()` (needed by Plan B, below); wider suites; commit. |
| Base | `main` is `6ac85900` (contains `c346d529` and #285). Plan B's "restart from `c346d52`" is out of date; branch from current `main`. |

## Plan B's claims, checked against the code

| Claim | Verdict |
|---|---|
| Memory: B4 replace-if-at-least-as-trusted exists (`_same_statement`, `_source_trust`, `_forget(replacing)`, `metadata.replaces`) | **True**: `memory/engine.py:63-85, 173-265` |
| Replace ignores the subject, deletes the old record, no audit trail | **True** (`:258-265`). A precedent for supersede-by-subject already exists in consolidation: `older.superseded(newest.id)` (`:453-463`) |
| Constructor `duplicate_threshold` is stored and unused | **True** (`:106, 119`; `_find_duplicate` gets `policy.duplicate_threshold` at `:171`). **But** `container.py:404` passes it, so "remove if no caller passes it" does not apply as written |
| Loop A: `_process_source` ingests before any gate; `dry_run` still writes live data; whole corpus rebuilt into the same index each night; count-delta compares an index with itself | **True** (`knowledge_refresh.py:150-220, 224-302, 306-351`; `index_manager.py` gate; same as audit `D-03`) |
| ADR-008's quarantine-rate gate does not exist | **True** (no such check in `PromotionGate`) |
| Tests `…_the_index_promoted` and `…_the_report_is_serialisable` assert `promoted` | **True** (`test_learning_pipeline.py:46-62, 183-192`) |
| Docs claim "the live index is never modified" | **True**: `administrator-guide.md:62`, `mlops-guide.md:49`, module docstrings |

Plan B's diagnosis is sound. Its design needs five corrections to sit beside ours (next section).

## Where the plans touch

| Area | Plan A | Plan B | Resolution |
|---|---|---|---|
| `knowledge/index_manager.py` | WP1 edits `evaluate_promotion`, adds `status()` | Puts `RefreshContentGate` beside `PromotionGate` | Same file, different regions. Put the new gate in a **new module** `knowledge/refresh_gate.py` (exported from `knowledge/__init__.py`) so the two branches do not collide |
| When a rebuild runs | WP1: a rebuild over an unreadable (other-embedder) index may promote | Rebuild only when the embedder changed | **Compatible and complementary.** Plan B's only rebuild case is exactly WP1's case. See correction 1 |
| Count-delta gate | Kept for readable baselines (WP1 test pins this) | Moved to a per-source shrink gate | After both: a refresh rebuild is gated on error rate, `min_chunks` (when not simply empty) and recall; content safety is per-source. The manual `/admin/index/rebuild` keeps count-delta against a readable baseline |
| `last_success_at` (KM-13) | WP5 | Applies `record_success` only to applied sources | **Plan B covers KM-13.** Remove it from WP5 |
| Nightly full re-embed (KM-05, `D-03`) | WP6: generation-named builds | Skips the rebuild unless the embedder changed | **Plan B covers it.** Drop "generation-named builds" from WP6 (an embedder change is already a separate index) |
| Recall gate (KM-09) | WP6 wires `RetrievalEvaluator` | n/a | Stays, but narrows to the embedder-change rebuild path plus the per-domain evaluation sets |
| `knowledge/engine.py` | WP3 propagates source metadata into the document and chunk payload | `_prepare` / `assess` refactor of `_ingest_serialised` | Land Plan B first; WP3 then adds metadata in `_prepare`'s inputs, one place |
| `learning/knowledge_refresh.py` | WP3 licence skip, WP4 multi-document fetch with versions and cursors, WP5 per-source dispatch | Restructures `_process_source` into stage, gate, apply | **Land Plan B Fix 2 before WP3, WP4, WP5.** It is the seam they need (see "Seams") |
| Docs | WP9 corrects admin-guide lines 52, 62, 70 | ADR-008 amendment, admin-guide gates | Split: Plan B owns lines 62 and 70 and the ADR; WP3 owns line 52 (trust tier). No double edits |
| Memory (Fix 1) | none | `memory/engine.py` | **No overlap.** Parallel branch |

## Corrections to Plan B (so it does not conflict)

1. **The "embedder changed" test must not compare `IndexIdentity` objects.** `active_identity()` is parsed
   back from the alias name and is lossy (`domain/knowledge.py:221-233`, the docstring on `matches_embedding`),
   so `active_identity() != identity_for_current_embedder()` can be true when nothing changed and would rebuild
   every night. Use the manager's status instead: **rebuild when `published != required`** (physical-name
   strings, both slugged). This also covers a `schema_version` bump, which `matches_embedding` alone would
   miss. Add `up_to_date: published == required` to `IndexManager.status()` as the single source of truth
   (small addition to WP1 before it merges).
2. **`AuraKnowledgeStale` would start firing falsely.** `_observe` (`knowledge_refresh.py:355-363`) sets
   `refresh_last_success_timestamp` and `knowledge_age_seconds` **only when `promoted`**; the alert fires after
   48 hours without it (`deploy/monitoring/alerts/aura.yml:141-149`). Once nightly runs stop promoting, it
   fires every two days. Fix inside Plan B: define a successful run as *no source errored and the index phase
   was `not_needed` or `promoted`*, set both gauges on success, add a gauge `refresh_sources_held`, and reword
   the alert and the three guides that describe it (`maintenance-guide.md:43-45, 444`,
   `troubleshooting-guide.md:140-158`, `mlops-guide.md:46-64`).
3. **`dry_run` and the empty-registry early return.** `run()` returns before the index phase when no source is
   enabled (`knowledge_refresh.py:159-167`). Keep that for Plan B (so the nightly job stays a no-op on an empty
   registry); repairing a stale alias remains **WP0**, run manually. Do not move the index phase ahead of the
   early return in this change.
4. **A legitimately shrunk source would be held forever.** Plan B has no way to accept it. Add a per-source
   override (`metadata.gate_max_shrink`, `gate_max_quarantine_rate`), writable by SuperAdmin and audited; the
   metadata column arrives with WP3, so until then the thresholds are global settings. State this limit in the
   PR. (New decision Q7.)
5. **Dead parameter.** Do not remove `duplicate_threshold` as a drive-by. `container.py:404` and the
   `AURA_MEMORY_DUPLICATE_THRESHOLD` setting feed it, so removing it is a settings change. Record it as a
   finding, keep it, and leave the code comment accurate (the per-kind policy wins).

Smaller notes: put the new thresholds next to the existing `gate_*` settings in `LearningSettings`
(`LEARNING_GATE_MAX_QUARANTINE_RATE`, `LEARNING_GATE_MAX_SHRINK`) rather than only as dataclass defaults, so
operators can change them the way they change the other gates. Fetchers that will return many documents
(WP4) can drop documents upstream; Plan B's shrink gate sees only documents that came back. WP4 must add
"documents missing from this run" to the same gate before retiring anything.

## Revised Plan B

### B1 — Memory: subject rule on top of B4 (independent; start any time)

Branch from current `main`. Changes only in `aura_core/memory/engine.py` and memory tests; no migration,
no adapter change.

In `remember()`, inside the `duplicate is not None` branch, after `_same_statement` is false:

1. `effective_subject = subject or duplicate.subject`. It is the same fact only if `duplicate.subject` is set
   and equals `effective_subject`.
2. Different fact: store the new record normally (no `replaces`); the old one stays active.
3. Same fact, lower-trust write: unchanged B4 behaviour, return the stored record.
4. Same fact, at least as trusted: store the new record with `subject=effective_subject` and
   `metadata.replaces`; **then** `await self._store.update(duplicate.superseded(record.id))` instead of
   `_forget`. The old vector point stays; `recall()` and `_find_duplicate` already skip inactive records, and
   erasure removes superseded ones (`include_superseded=True`). Log both ids.
5. Rewrite the comments and docstring for the four outcomes.

Tests: read the B4 tests in `tests/unit/test_agent_audit_remaining.py` first and update, not delete, any that
expect a subjectless rewording to replace. Add the six cases from Plan B with a constant-vector embedder.
Keep `memory/engine.py:421-463` consolidation behaviour unchanged. Doc: a short note in
`004-memory-taxonomy.md`.

Not in scope: the `duplicate_threshold` parameter (correction 5).

### B2 — Loop A: gate before write (after WP1 merges; before WP3-WP5)

1. **`KnowledgeEngine.assess`**: extract the read-only part of `_ingest_serialised` into
   `_prepare(...) -> _Prepared`; `assess` returns what ingest would do and writes nothing. Ingest behaviour and
   the fully-quarantined event stay as they are. Metadata passes through `_prepare`, which is where WP3 will add
   provenance.
2. **`RefreshContentGate`** in new `knowledge/refresh_gate.py` (quarantine rate ≤ 10%, shrink ≤ 40%), thresholds
   from `LearningSettings`.
3. **Pipeline**, as small units so WP5 can reuse them: `_stage_source` (fetch, validate, dedupe, `assess`),
   `_gate_source` (returns reasons), `_apply_source` (today's exact `ingest` call, then `record_success`).
   `run()` composes them; `dry_run` stops after the gate and writes nothing.
4. **Index phase** as its own method, `_ensure_index_current()`: rebuild and gate only when
   `not status["up_to_date"]` (correction 1). Otherwise `index_rebuild="not_needed"`.
5. **Report and observability**: `held_sources`, `index_rebuild`, `SourceOutcome.held`, `as_dict`, `jobs.py`,
   event gate-failure prefix `source:<id>:`; success gauges and `refresh_sources_held` (correction 2).
6. **Tests**: update the two `promoted` tests and the module docstring; add the six held/dry-run/embedder-change/
   `last_success_at`/`assess` tests from Plan B, plus: *no rebuild when `published == required`*, *rebuild on a
   schema-version change*, *a successful `not_needed` run sets the success gauge*.
7. **Docs**: ADR-008 amendment; `administrator-guide.md:57-75`, `mlops-guide.md:46-64`,
   `maintenance-guide.md:43-45, 444`, `troubleshooting-guide.md:140-158`; mark both defects fixed in
   `adaptive-knowledge-update-system.md` §11.

Out of scope: licence checks, multi-document fetchers, per-source schedules (WP3-WP5 build on the seams).

## Updated Plan A (what changes in our plan)

| WP | Change |
|---|---|
| WP1 | Add `up_to_date` to `status()` and the alert rule `AuraKnowledgeIndexMismatch`; then commit |
| WP3 | The licence check becomes a **staging gate** in B2's `_gate_source` (reason `license:<status>`); legacy rows with unknown licence are held, not silently skipped |
| WP4 | Fetchers return documents plus source version; add "documents missing this run" to the shrink gate before any retirement |
| WP5 | Per-source dispatch reuses `_stage_source`/`_gate_source`/`_apply_source`; **KM-13 removed** (done in B2) |
| WP6 | **Generation-named builds dropped** (B2 removes the same-embedder rebuild); keeps the evaluator wiring for embedder-change rebuilds and the per-domain evaluation sets |
| WP9 | Admin-guide lines 62/70 and the ADR move to B2; line 52 stays with WP3 |

Register status after B2: KM-05 (nightly re-embed) and KM-13 resolved; KM-09 narrowed.

## Order

```
WP0 (owner, any time)
WP1 (finish, commit) ──► B2 ──► WP3 ──► WP4 ──► WP5 ──► WP6 ──► WP7
B1 (independent, parallel from main)
WP2 (independent of both)
```

Why B2 after WP1: both change how a rebuild is gated, and B2's "rebuild only on an embedder change" relies on
WP1's `status()`. Why B2 before WP3-WP5: all three modify `knowledge_refresh.py`; doing the restructure once
avoids three rebases.

## Decisions

| # | Decision | Recommendation |
|---|---|---|
| Q7 | How an admin accepts a source the gate holds | Per-source threshold override in source metadata (WP3), audited; global settings until then |
| Q8 | Success definition for the staleness alert | No source errored and index phase `not_needed` or `promoted`; held sources do not fail it but raise a separate gauge |
| Q9 | `duplicate_threshold` parameter | Keep; track as a finding |

Plan B asks to commit, push, open a draft PR and subscribe. None of that is done here; each is an outward
action to be confirmed when the work is ready. CI is blocked by GitHub billing (known), so the local suite
counts are the evidence.

## Not verified

`test_agent_audit_remaining.py` contents (only its existence); the exact names of the existing `gate_*`
settings (read them before adding the new two); whether any client reads `promoted` from the refresh report
(the admin console does not: no match in `aura_admin`).
