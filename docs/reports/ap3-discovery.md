# AP3: Deterministic Discovery for Postgres and OpenAPI (evidence)

**Phase:** AP3 ([Track AP roadmap](../roadmaps/AURA_ADAPTIVE_PLATFORM_ROADMAP.md)) · **Gap:**
GAP-051 · **ADRs:** 020, 022, 026 · **Date:** 2026-10-01 · **Base:** `7a1cd85` (main)

**Maturity reached:** IMPLEMENTED. Goldens pass without infrastructure, and a live capture of both
pilots equals the recorded catalog on a real PostgreSQL 16.

**Still open:** Backend CI green on `main` with these tests (needs the CI baseline fixes noted in
the AP1 report).

## What was built

- **`aura_core/domain/discovery.py`** defines the discovery model:
  - `RawArtifact`: kind, stable locator, `UntrustedText` name and description, and bounded
    scalar attributes.
  - `DiscoveryBudget`: maximum objects, sampled rows, profiled columns, seconds, and the opt-in
    `sample_values`.
  - `DiscoveryIssue` and `DiscoveryReport` (canonical order, digest).
  - `quote_identifier`, the SQL-style rule that makes locators unambiguous (for example
    `table:shop."SupplierContacts"`).
- **`aura_core/ports/connectors.py`** adds `DiscoveryConnector.discover(budget)`.
- **`aura_core/adapters/connectors/postgres_catalog.py`** has two halves:
  - *Capture* runs in one read-only, repeatable-read transaction with `statement_timeout`. It
    reads relations, columns, constraints (PK, FK, unique, check), enum labels and table grants
    from `pg_catalog`. Owner grants are excluded because they depend on the environment.
  - *Profiles* are computed in SQL over `LIMIT sample_rows`: counts, distinct counts, and pattern
    matches for uuid, email, url, ISO date and numeric text. Each table runs under its own
    savepoint, so a refused table becomes an issue and the run continues. Unreadable tables are
    marked `readable: false` and skipped.
  - *Mapping* is pure: `artifacts_from_catalog`.
- **`aura_core/adapters/connectors/openapi.py`**:
  - *Fetch* resolves the host first and refuses internal addresses. `allow_private_network`
    permits private ranges only; loopback, link-local, carrier-grade NAT, multicast and reserved
    ranges stay refused. It follows no redirects, caps the spec at 5 MB, and sends a credential
    only over HTTPS.
  - *Parse* accepts OpenAPI 3.x only, as JSON or YAML via `safe_load`.
  - *Mapping* is pure. It covers the API, security schemes, schemas and properties, and
    operations (parameters, including `$ref`'d ones, request and response schemas, security).
    External `$ref`s become `external_ref_ignored` issues and are never fetched.
- **`aura_core/adaptation/discovery.py`** adds `DiscoveryService`. It works on active connectors
  only, applies an outer time bound, and reports connector failures by exception type only. Runs
  are audited by digest and counts, never by content.
- **HTTP:** `POST /api/v3/admin/connectors/{id}/discover`, Super Admin only, with an optional budget in the body.
- **Metrics:** `aura_discovery_runs_total{kind,outcome}`, `aura_discovery_seconds{kind}` and
  `aura_discovery_artifacts{connector}`.
- **Pilots** (`aura_backend/tests/fixtures/pilots/{hr,ecommerce}`), each with `schema.sql`,
  deterministic `seed.sql`, an OpenAPI spec (YAML and JSON respectively), a policy document, and
  `golden/`. They exercise:
  - enum types, a self-referencing FK, a composite PK and a composite FK;
  - a non-`public` schema and a quoted mixed-case table;
  - CHECK lists, a view, and role grants;
  - an external `$ref`;
  - a hostile column comment and a hostile OpenAPI summary.
- **`aura_backend/scripts/pilot_discovery_goldens.py`** records catalogs from a live Postgres
  (`--record-catalog DSN`) and renders the expected reports.
- **`pyyaml` is now a direct requirement.** It was already pinned transitively; the lockfile
  change is one provenance comment.

## Results on the pilots

| Pilot | Postgres artifacts | OpenAPI artifacts | Issues |
| --- | --- | --- | --- |
| hr | 100 | 42 | 1 `external_ref_ignored` (by design) |
| ecommerce | 114 | 34 | none |

Seeded row values do not appear in either golden; a test asserts this. Status columns are still
classified `categorical` from aggregates alone.

## Verification

```
cd aura_backend
pytest                                   # 3055 passed, 56 skipped (main baseline: 3012)
TESTING=True python -m unittest discover -p "test_*.py"   # Ran 100, OK (skipped=4)
AURA_TEST_DATABASE_URL=postgresql://aura:***@localhost:55437/postgres \
  pytest tests/load/test_adaptation_discovery_postgres.py tests/load/test_adaptation_postgres.py \
         tests/load/test_knowledge_graph_postgres.py -m load        # 27 passed
```

The 43 extra default-suite tests are the 42 in `tests/unit/test_adaptation_discovery.py`, plus
one route-auth case enumerated from the new route.

The live tests cover:

- a fresh capture equals the recorded catalog for both pilots;
- a real read-only role with `SELECT` revoked on `employees` still sees that table's structure,
  gets a `not_readable` issue for it, and the other tables are still profiled;
- budget truncation, profiling disabled, and the profiled-column cap;
- opt-in values;
- the `schemas` option.

The OpenAPI connector tests stub only the network (httpx `MockTransport` plus a DNS stub). They
show that a refused destination sends **no request at all**, including when the endpoint is an IP
literal.

## Residual risks, stated

- **DNS can change between the check and the connection.** Resolve-then-connect narrows the
  window but does not pin the address, the same trade-off `fetch_url` documents. The target is
  set by a Super Admin and approved, which bounds the exposure.
- **Profiling samples the first `sample_rows` rows, not a random sample.** That keeps it
  deterministic, but it may under-represent recent data.
- **The recorded catalog depends on PostgreSQL's own formatting** (`format_type`,
  `pg_get_constraintdef`). A different major version may need re-recording; the live test will
  say so.
