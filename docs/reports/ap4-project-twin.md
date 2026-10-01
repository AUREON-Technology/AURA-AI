# AP4: Normalization, Project Twin and Diff (evidence)

**Phase:** AP4 ([Track AP roadmap](../roadmaps/AURA_ADAPTIVE_PLATFORM_ROADMAP.md)) · **Gap:**
GAP-052 · **ADRs:** 020, 022, 026 · **Date:** 2026-10-01 · **Base:** `62aa74a` (main)

**Maturity reached:** IMPLEMENTED.

- Both pilot twins and three diff goldens are produced with no database and no model; these are
  the CPU-only, degraded-mode twins the exit criteria name.
- One HR twin was persisted on a real PostgreSQL 16, the live schema was altered with DDL,
  rediscovered and diffed.

**Still open:** Backend CI green on `main` with these tests. That needs the CI baseline fixes
noted in the AP1 report.

## What was built

### `adaptation/normalize.py`

A pure mapping from discovery reports to AUSM:

| Discovered | Becomes |
| --- | --- |
| table, view, schema object | ENTITY |
| column or property | ATTRIBUTE, with profile aggregates folded in and a coarse `semantic_type` |
| FK or `$ref` | RELATION, with cardinality and `via` |
| PK, unique, check | CONSTRAINT |
| enum labels, CHECK `IN`-lists, OpenAPI enums | ordered STATEs |
| grants | PERMISSIONs (`access`) |
| operations | ACTIONs: `GET` is read; every other verb is a write marked `deferred` |
| security schemes | CONSTRAINT on the SOURCE |

It also proposes candidates, each with confidence below 1:

- timestamp and date columns become EVENT candidates;
- numeric measures become METRIC candidates.

Labels are humanised identifiers. The raw identifier is kept as a property.

### `adaptation/matching.py`

Cross-source `same_as` RELATION proposals. The score is name similarity plus attribute-shape
similarity, discounted where shared attributes disagree on semantic type. Only mutual best
matches are proposed, the score is capped at 0.95, and every proposal has `status=proposed`.

### `adaptation/process_mining.py`

- **Workflow candidates** are proposed for each status attribute. A transition's confidence
  comes from:
  - the declared order of the values;
  - terminal-state vocabulary;
  - a timestamp named after a state (`shipped_at`), on the entity or a directly related one;
  - an action verb that inflects to a state (`approve` becomes `approved`, `cancelOrder` becomes
    `cancelled`), also counted through a `same_as` proposal at half weight;
  - a detected history entity.
- **`mine_event_log`** extracts directly-follows transitions from ordered `(case, state, at)`
  rows.

### `adaptation/labeling.py`

`LabelingPass` with the `LabelingModel` port (`ports/adaptation.py`), plus
`RouterLabelingModel` over the existing self-hosted router.

- It may change only the label and an absent description.
- Model output is re-sanitised and capped. Proposals for ids that were not asked about are
  dropped.
- Unchanged elements reuse their previous model label.
- A timeout or a provider failure falls back to the degraded label and is recorded as a twin
  issue.

### `adaptation/twin.py`

- **`build_twin`** runs the deterministic pipeline.
- **`diff_twins`** reports added, removed and changed elements (by field), plus rename candidates.
  Entities are paired by attribute overlap, attributes by profile similarity, and the rest by
  following a rename already found.
- **`TwinService`** discovers, builds, labels, diffs and appends.

### Domain types, storage and API

- **Domain** (`domain/twin.py`): `TwinVersion`, `TwinIssue`, `LabelingSummary`, `TwinDiff`,
  `ElementChange`, `RenameCandidate`, `LabelRequest` and `LabelProposal`.
- **Storage:** `InMemoryTwinStore` and `PostgresTwinStore`.
  - Migration `0010` creates `aura_adaptation_twin_versions` and `aura_adaptation_twin_elements`
    (JSONB, `project_id`), and issues no runtime DDL.
  - Storage is append-only, and duplicate versions are refused.
  - On read, every element passes through the strict codec and the digest is re-checked, so a
    tampered row raises `DataIntegrityError`.
- **HTTP**, Super Admin only, returning 503 when Track AP is off:
  - `POST /api/v3/admin/twin/snapshot`
  - `GET /api/v3/admin/twin/versions`
  - `GET /api/v3/admin/twin/versions/{v}` (optional `kind` filter)
  - `GET /api/v3/admin/twin/diff?from=&to=`
- **Settings:** `AURA_ADAPTATION_LABELER_ENABLED` (default off), `…_BATCH_SIZE` and
  `…_TIMEOUT_S`. They are wired in both compose files, which stay in parity.
- **Metrics:**
  - `aura_twin_labeling_coverage`
  - `aura_twin_labeling_elements_total{outcome}`
  - `aura_twin_labeler_seconds_per_element`

## Results on the pilots (degraded mode, no model)

| Pilot | Elements | Entities | Attributes | States | Relations (of which `same_as`) | Workflows / transitions | Events | Metrics |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| hr | 178 | 11 | 59 | 26 | 13 (3) | 4 / 14 | 12 | 2 |
| ecommerce | 167 | 13 | 58 | 15 | 13 (4) | 2 / 14 | 7 | 11 |

The merge proposals are the expected pairs:

- `Department`, `Employee` and `LeaveRequest` with their tables;
- `Order`, `OrderItem`, `Product` and `Return` with theirs.

Process mining picks up the following signals:

- **`shop.orders.status`:** `placed→paid`, `paid→shipped` and `shipped→delivered` at 0.55 each,
  from declared order plus `payments.paid_at`, `shipments.shipped_at` and
  `shipments.delivered_at`.
- **The API `Order` workflow:** `placed→cancelled` at 0.45, from the `cancelOrder` action.
- **HR leave requests:** `pending→approved` at 0.55 in the API, from the `approve` action, and at
  0.40 in the database, through the `same_as` proposal.

Diff goldens (hr):

| Change | Added | Removed | Changed | Rename candidates |
| --- | --- | --- | --- | --- |
| add column + table | 9 | 0 | 2 | 0 |
| drop column + table | 0 | 11 | 2 | 0 (no false renames) |
| rename column + table | 14 | 14 | 1 | 14 (table: `attribute_overlap`, column: `profile_similarity`, 12 implied) |

The "changed" entries are the `same_as` proposal on `employees`, whose shape score moved, and
the DB source's artifact count.

## Verification

```
cd aura_backend
pytest                                   # 3109 passed, 56 skipped (main baseline: 3055)
TESTING=True python -m unittest discover -p "test_*.py"   # Ran 100, OK (skipped=4)
AURA_TEST_DATABASE_URL=postgresql://aura:***@localhost:55438/postgres \
  pytest tests/load/test_adaptation_twin_postgres.py tests/load/test_adaptation_discovery_postgres.py \
         tests/load/test_adaptation_postgres.py tests/load/test_knowledge_graph_postgres.py -m load   # 28 passed
```

`tests/unit/test_adaptation_twin.py` holds 50 tests. They include:

- golden equality for both twins and all three diffs;
- same input gives the same digest, regardless of report order, artifact order or version;
- `test_degraded_without_labeler`;
- labeler timeout and provider failure, each giving degraded labels with the twin still
  versioned;
- hostile model output (control characters, bidi overrides, oversize text, unasked-for ids);
- model labels never changing structure;
- reuse of unchanged labels with no model call;
- identifiers going into the prompt only inside DATA fences;
- the store being append-only;
- the schema in `0010` matching the adapter's;
- route behaviour: Super Admin only, and 503 when disabled.

The live test (`tests/load/test_adaptation_twin_postgres.py`) did the following against a real
PostgreSQL 16:

1. Discovered the HR pilot from a live database and stored version 1. JSONB round-trip kept the
   digest.
2. Re-snapshotted. No version was minted.
3. Ran
   `ALTER TABLE employees RENAME COLUMN full_name TO display_name`,
   `ALTER TABLE assets RENAME TO equipment` and
   `ADD COLUMN badge_number`.
4. Rediscovered and stored version 2.
5. Diffed both versions as read back from Postgres. It found both renames and the added column.
6. Re-appending version 1 raised `ConflictError`.
7. Editing a stored element's label in SQL made the read raise `DataIntegrityError`.

## Residual risks, stated

- **The workflow chain is a weak prior.** Declared order proposes `approved→rejected` at 0.25 for
  leave requests, which is not a real transition. Confidence is low by design, and evidence
  (timestamps, actions, a history log) is what lifts a transition. Such candidates are for a
  human to accept in AP9, never to act on.
- **Model label quality is unmeasured.** No GPU plane (AB-4 / M4) exists here, so a labeling run
  against a real model has not been done. Only the mechanism, the fallback and the safety
  properties are tested.
- **History-table mining cannot run on live data yet.** Reading the rows needs AP6 templated
  reads, and neither pilot has a history table.
- **Merge-proposal acceptance rate has no acceptance path yet** (AP9), so the metric is not
  exported.
