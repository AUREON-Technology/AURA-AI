# AP6: Factories, Blueprint Compiler and Tool Output Schema (evidence)

**Phase:** AP6 ([Track AP roadmap](../roadmaps/AURA_ADAPTIVE_PLATFORM_ROADMAP.md)) · **Gaps:**
GAP-054, GAP-055 · **ADRs:** 022, 024, 025 · **Date:** 2026-10-04 · **Base:** `5244aa0` (main)

**Maturity reached:** IMPLEMENTED.

- **Both pilots compile schema-valid blueprints.** The blueprint JSON validates against
  `docs/schemas/blueprint.v1.json`, survives a strict decode with its digest unchanged, and passes
  `check_against_twin`.
- **The fitness and fuzz tests pass.**
- **Every compiled SQL template ran on a real PostgreSQL 16.** Each one returned rows that match
  the output schema its tool declares.

**Still open:** Backend CI green on `main` with these tests. That needs the CI baseline fixes
noted in the AP1 report.

## What was built

### Tool output schema (ADR-025, GAP-055)

- **`domain/output_schema.py`** accepts a closed JSON Schema subset:
  - `type`, including type lists;
  - `properties`, `required` and boolean `additionalProperties`;
  - `items` and `maxItems`;
  - `enum`, `format` and `description`.

  `$ref`, `oneOf`, `pattern` and anything else are refused when declared, never ignored.
  `output_errors` reports paths only, never the offending values.
- **`ToolSpec.output_schema`** is optional and defaults to `None`, so no existing tool changes.
- **`ToolRegistry.execute`** parses a schema'd tool's result as JSON and checks it. A
  non-conforming result is a failed, audited call with `error_kind="invalid_output"`.
- **MCP:**
  - `McpToolDescriptor.output_schema` carries the server's `outputSchema` when it fits the
    subset; otherwise the schema is dropped, with a log line.
  - `McpClient.call_tool` returns `structuredContent` as JSON, so the check applies.

### Read templates (ADR-022)

`domain/templates.py` is an AST, not query text.

- **`ReadTemplate`** has either an SQL body (`SqlSelect`) or an HTTP body (`HttpGet`).
- **`SqlSelect`** supports:
  - one table;
  - equality, containment and range filters. Optional filters render as `(… OR $n IS NULL)`.
    Range filters carry a closed `ValueCast`.
  - an OR-group for search;
  - aggregates with `GROUP BY`, and `ORDER BY`;
  - a `LIMIT LEAST(COALESCE($n::int, max), max)` that no argument can exceed.
- **`render_sql`** quotes every identifier and binds every value, so the statement text depends
  on the template alone.
- **`HttpGet`** is GET only. A placeholder must be a whole path segment, and paths with `..`, `?`
  or `#` are refused. `render_http` percent-encodes each value into its own segment.
- **`Blueprint.templates`** is the new section. The blueprint checks that each tool's parameters
  are exactly its template's, and that each template's kind suits its connector.
  `check_against_twin` proves that every table, column and path a template names is an element of
  the twin. That is the allowlist.

### Factories

Under `adaptation/factories/`:

- **tools:**
  - SQL tools for `entity.lookup` (single-column keys), `entity.search`, `entity.list_filtered`,
    `relation.traverse` (single-column foreign keys), `metric.aggregate`, `state.timeline` and
    `workflow.status`;
  - HTTP tools for `operation.read`.

  Each tool's sensitivity is at least its capability's risk floor and never below PERSONAL.
- **agents:** an explicit tool grant per proposal, and a risk ceiling equal to the tool maximum.
- **workflows:** read-status workflows, compiling only transitions with confidence ≥ 0.4.
- **ui:** one view per tool, navigation per entity, and a chat view.
- **evals:** `tool_contract`, `permission_denied`, `out_of_scope` and `injection_refusal`
  suites, with gates of 0.95, 1.0, 0.9 and 1.0.

### Compiler, storage and API

- **`adaptation/compiler.py`:**
  - `compile_blueprint` is pure and deterministic.
  - `CompilerService` plans a stored twin, compiles it, and appends a `proposed` version.
- **Storage:** `BlueprintRecord` holds the blueprint, its suites, the dropped items and its
  provenance.
  - `InMemoryBlueprintStore` / `PostgresBlueprintStore`, with migration `0011` creating
    `aura_adaptation_blueprints`. There is no runtime DDL.
  - On read, the record passes through the strict codec, which re-checks the digest.
- **HTTP:** Super Admin only, returning 503 when Track AP is off.
  - `POST /api/v3/admin/twin/versions/{v}/compile`
  - `GET /api/v3/admin/blueprints`
  - `GET /api/v3/admin/blueprints/{v}`
- **Metric:** `aura_blueprint_artifacts{kind}`.
- **Goldens:** `tests/fixtures/pilots/{hr,ecommerce}/golden/blueprint.json`.

## Results on the pilots

| Pilot | SQL / HTTP templates | Tools | Agents (tools) | Workflows (states / transitions) | Views | Eval cases | Dropped |
| --- | --- | --- | --- | --- | --- | --- | --- |
| hr | 23 / 4 | 27 | project 27, hr_manager 19, hr_viewer 27 | leave_requests.status (4 / 1) | 28 | 34 | 15 |
| ecommerce | 38 / 4 | 42 | project 42, shop_ops 18, shop_support 23 | orders.status (6 / 5) | 43 | 50 | 20 |

Every dropped item names its reason:

- `api_entity_uses_operation_read`: API entities are reached through their own GET operations,
  compiled once.
- `no_search_operation`, `no_aggregate_operation` and `no_timeline_operation`: the API has no
  such operation.
- `composite_key`: e.g. `returns_item_fk`.
- `not_a_foreign_key`: an embedded API relation.
- `no_factory:notification.draft`: there is no draft path in v1.

Nothing is stubbed.

## Verification

```
cd aura_backend
pytest                                   # 3179 passed, 56 skipped (main baseline: 3138)
TESTING=True python -m unittest discover -p "test_*.py"   # Ran 100, OK (skipped=4)
AURA_TEST_DATABASE_URL=postgresql://aura:***@localhost:55439/postgres \
  pytest tests/load/test_adaptation_compiler_postgres.py tests/load/test_adaptation_twin_postgres.py \
         tests/load/test_adaptation_discovery_postgres.py tests/load/test_adaptation_postgres.py \
         tests/load/test_knowledge_graph_postgres.py -m load        # 32 passed
```

### Default-suite tests

- **`tests/unit/test_adaptation_compiler.py` (35):**
  - **Output-schema subset:** closed; registry `invalid_output`; free-text tools unchanged;
    MCP mapping.
  - **SQL rendering:** exact SQL text. A fuzz test runs 500 random hostile argument sets through
    `render_sql`; the statement never changes and the values are bound in order.
  - **HTTP rendering:** a fuzz test runs 500 hostile values through `render_http`; the path's
    segment structure never changes.
  - **Compilation:**
    - golden blueprints;
    - JSON-Schema validity plus a strict round-trip with the digest unchanged;
    - determinism and inert output (no callables, no code or SQL strings);
    - explicit agent grants within their ceilings;
    - dropped reasons;
    - only strong workflow transitions compiled;
    - view bindings and suites.
  - **Twin allowlist:** forged columns, tables and paths are refused, and so is an extra tool
    parameter.
  - **Service and storage:** a plan from another twin is refused; the service, record and
    migration parity are tested; the HTTP routes are tested.
- **`tests/architecture/test_no_freeform_sql.py` (3):**
  - The adaptation engine makes no driver call at all.
  - Connector adapters pass only literals, UPPER_CASE constants or `render_sql(...).text` to a
    driver, except two reviewed catalog builders, which are named with the reason.
  - The checker itself catches an interpolated statement.
- **`tests/unit/test_adaptation_domain.py`:** the AP1 fixture now carries a template for its
  tool.

### Live tests (`tests/load/test_adaptation_compiler_postgres.py`, 4 tests)

1. Both pilots are loaded. Every SQL template runs with real argument values. The rows, converted
   to JSON types, match each tool's output schema, and limits hold.
2. Injection strings bound into search and filters match nothing and change nothing, and `%` is
   not a wildcard.
3. A compiled blueprint survives a Postgres JSONB round-trip with its digest intact. A duplicate
   version is refused. A row whose tool sensitivity is lowered by hand is refused on read with
   `DataIntegrityError`.

### What the live run caught

The first live run found a real bug the unit tests could not. PostgreSQL cannot infer a type for
`$n IS NULL` when it appears before the comparison, and a date column refuses a string parameter.
Optional filters now render comparison-first, and range filters carry a closed cast
(`$n::text::timestamptz`).

### A digest bug found along the way

The round-trip check found a digest bug. A `float` field holding an int encodes as `1` but
decodes as `1.0`, so the canonical JSON, and with it the digest, changed after storage. An
approval bound to that digest would never have matched again. The factory now passes floats, and
the test asserts digest stability, not just equality, because `1 == 1.0` hid the bug.

## Residual risks, stated

- **Nothing executes the templates yet.** `GeneratedReadTool` and the read adapter (row → JSON
  conversion, connector pool, row cap) are AP7. The live test shows the rendered SQL is correct;
  it does not show the runtime path.
- **Model-facing descriptions** contain the connected system's labels, and for API operations its
  summary text, as `UntrustedText`. AP7 renders them into tool declarations and must fence them
  or tag the tools `untrusted_content`. The blueprint carries no tags field yet.
- **Agent prompt templates are not in the blueprint.** The roadmap asks for a fixed core template
  with blueprint fields in DATA fences; that rendering belongs where agents are materialised
  (AP7).
- **Generated eval cases are declared, not run.** `tool_contract` arguments are placeholders that
  AP8 replaces with replica values, and no gate has executed yet.
- **MCP error results** from a tool with an `outputSchema` come back as text, so they report
  `invalid_output` instead of the server's error text. The call still fails safely.
