# ADR-044 — Thirteen partial open tools completed: review depth, behaviour tests, derived diagrams, changed-line mutation, memory profiling, evidenced gates, SQL/OpenAPI/release/telemetry/data intelligence, stored drift history, multi-step research

**Status:** Proposed (built, not proven) · **Date:** 2026-11-23 · **Related:** ADR-006 (trust
channels, confirmation), ADR-036 (open analysis tools; groups A, B, C), ADR-039 (tool policy,
autonomy profiles), ADR-040 (web search, document readers) · **Inventory rows:** 5, 6, 8, 21, 23, 24,
92, 93, 94, 95, 102, 103, 106

## Context

Thirteen tools in the inventory were registered and working but marked PARTIAL, each for a stated gap
(`still_missing`): a rule list called a review, tests that proved shape but not behaviour, diagrams that
only rendered a spec the model wrote, mutation that only flipped `True`/`False`, a profiler with no memory
figure, a gate of six booleans, SQL linting with no notion of a schema, route diffs that could not see
schema changes, a release plan with nothing to check afterwards, log greps, three data checks, a drift
number with no history, and a "research" tool that structured notes without gathering any.

## Decision

Extend each tool **additively** (new optional arguments and new result fields; no argument renamed, no
default result shape removed) and put the substance in new modules, so the existing classes and their
tests stay as they were. Two new tools are justified because they need something a pure tool cannot have
(a store, a registry), and one because it reaches the network.

| Row | Tool | What it can now do | New module |
|---|---|---|---|
| 5 | `review_code_diff` | `deep` (default on): AST bug patterns (mutable default, `== None`, swallowed exception, unreachable code, duplicate dict key, tuple `assert`, discarded comparison, `open` without `with`, `return` in `finally`, un-awaited `asyncio.sleep`), complexity hits and layer-import violations, each with a line number; a diff gets new-side line numbers, a whole new file is parsed | `open/review_ext.py` |
| 6 | `generate_and_run_tests` | `include_behavior_tests` (default on): each function's docstring examples run through `doctest`; pure, simply-annotated functions get return-type, determinism, no-mutation and declared-exceptions-only contracts over a value grid | `open/testgen_ext.py` |
| 8 | `generate_diagram` | `er_from_sql` (DDL or Alembic), `flowchart_from_source` (branches, loops, `continue`/`break`, `try`), `sequence_from_source`, `architecture_from_modules`; `validate` now checks quotes, brackets and entity/participant/node references | `open/diagram_ext.py` |
| 21 | `analyze_test_quality` | `diff_text` scopes coverage and mutants to the added lines; mutants are comparison, arithmetic, `and`/`or` and boolean, located by column, spread across kinds, survivors listed with lines | `open/quality_ext.py` |
| 23 | `profile_python` | tracemalloc peak/current memory and top allocations, wall time and call count; `baseline` + `max_regression_pct` / `max_peak_kb` give a regression verdict | `open/profile_ext.py` |
| 24 | `evaluate_quality_gate` | `results`: any subset of checks as a boolean **or the other tool's result object** (review, tests, coverage, security, complexity, lint, architecture, mutation, custom), with the reason kept as evidence; booleans and objects that disagree fail; readiness requires the gate to be APPROVED | `open/gate_ext.py` |
| 92 | `inspect_sql` | `schema` (SQL DDL or Alembic text -> tables, relationships, indexes; missing foreign-key indexes, no primary key, duplicate/redundant indexes), `smells` (index-defeating constructs, unknown tables/columns and unindexed filters against a schema), `generate_select` (parameterised, schema identifiers only); `EXPLAIN ANALYZE` is no longer "safe" | `open/sql_schema_ext.py` |
| 93 | `compare_api_specs` | YAML as well as JSON; `validate_spec` (structure, `$ref`, path parameters, `operationId`); `breaking_changes` (request vs response direction, types, enums, constraints, required properties, removed responses, semver bump); `generate_tests` (pytest text); `diff` lists every breaking change in one field | `open/api_ext.py` |
| 94 | `plan_rollout` + **`verify_release_health`** | `changelog` and `migrations` give a go/no-go checklist (entry for the version, semver vs content, destructive or irreversible migrations, rollback safety, strategy, risk); `verify_release_health` GETs health URLs on operator-allowed hosts and judges the *body* (healthy flag, version, required fields), with bounded retries | `open/release_ext.py`, `tools/release.py` |
| 95 | `analyze_logs` | `metrics` (Prometheus text: families, histogram quantiles, anomalies, diff against an earlier snapshot), `structured` (JSON/logfmt/plain: levels, signatures, bursts, cross-component traces), `incident` (ranked root-cause hypotheses with evidence lines and supporting metrics) | `open/telemetry_ext.py` |
| 102 | `validate_data_records` | `formats`, `corrupted` (mojibake, U+FFFD, control characters, truncated JSON, impossible dates, ragged rows), `outliers` (z-score, IQR, MAD), `types` (contract or inferred), `profile` (all, with a score) | `open/data_ext.py` |
| 103 | **`record_model_metric` / `compare_model_metric`** | A per-owner metric history in the `Cache` port; comparison against a pinned or earliest baseline (accuracy drop, mean shift, PSI), with the trend | `tools/drift.py` |
| 106 | **`research_topic`** | Local `search_knowledge`, `web_search` and `fetch_url` composed *through the registry as the caller*; URL-normalised de-duplication; claims with provenance; corroboration and numeric/negation contradictions; a cited report with a coverage-and-gaps section | `open/research_ext.py`, `tools/research.py` |

### Why three tools are new rather than operations

* `verify_release_health` makes outbound requests; every operation of `plan_rollout` is pure. A pure
  tool that sometimes touches the network is the thing ADR-036's grouping exists to prevent. It is
  registered only when `AURA_TOOLS_HTTP_ALLOWED_HOSTS` names a host, requires https on the default port,
  and goes through `_ops_common.open_guarded`, which has no opt-out: private/internal hosts, the
  platform's own hosts and the gateway's `/api/v3` and `/health` paths are refused, redirects are not
  followed, and every redirect hop would be re-checked. A consequence worth stating: a service whose
  health path is literally `/health` cannot be probed by this tool (use `/healthz`, `/ready`,
  `/status`), and neither can the platform's own deployment. `tests/unit/test_open_tools_no_gateway.py`
  gains one entry for the one new module that imports `urllib.request`.
* `record_model_metric` writes and `compare_model_metric` reads, so the write is `side_effecting` and
  the read is not; one tool would have made every drift check a write.
* `research_topic` needs the registry. It is `nested_dispatch` (so the registry does not hold a slot
  while the nested calls need theirs) and `untrusted_content` (it returns other people's text).

### Invariants

* **Composition never bypasses policy.** `research_topic` calls `ToolRegistry.execute` with the caller's
  own `ToolContext` and capabilities. A tool the persona may not use, or the deployment does not
  register, is reported in `coverage.tools_unavailable` and `gaps`; it is not reached another way.
* **No new state outside the platform's ports.** Metric history lives in `Cache` under
  `aura:drift:v1:<owner>:<model>:<metric>` (owner = knowledge owner, else user), 50 observations per
  series, 200 values per observation, 50 series per owner, 90-day expiry. There is no module-level mutable
  state (the lock that serialises a read-modify-write lives in the builder's closure) and no table, so no
  migration.
* **Everything is bounded**: inputs by the existing `_MAX_TEXT` / `_MAX_ITEMS`, results by `_dump`,
  generated diagrams and findings by caps that say when they cut (`truncated`).
* **The planner prompt budget** (`tests/unit/test_analysis_tools.py`) rose from 7,000 to 8,500 characters
  for the group: twelve tools gained operations, which costs about 1,500 characters (roughly 400 tokens)
  against twelve new tool names costing more. It is still pinned, so further growth is a decision.
* **Grants.** The Engineer holds all of the new names; `research_agent` also holds `research_topic`. The
  coding agent holds none of the new tools that read external text or write state
  (`verify_release_health`, `research_topic`, `record_model_metric`, `compare_model_metric`), and the
  `untrusted_content` rule (the coding agent holds no such tool) is unchanged.

## What this does not do (stated so the rows are not over-read)

* **Row 5** is still a rule engine. It is not an independent or model-based reviewer and says so in
  `deep_review.method`; a partial diff hunk is checked by line patterns, not the AST.
* **Row 6**: behaviour tests check contracts and docstring examples. A function with neither a doctest nor
  simple type hints gets nothing (listed under `behaviour.skipped`). Execution is still a child process of
  the gateway behind the existing operator flag and per-call confirmation (GAP-066).
* **Row 8** draws what the code and DDL contain; it runs no Mermaid renderer, so `valid` means "passes the
  checks listed", not "renders".
* **Row 21**: surviving mutants include equivalent ones; a survivor is a question about the tests.
* **Row 23** still runs only on the user's paired desktop (it executes user code). Measuring under
  cProfile and tracemalloc is slower than a plain run, so figures compare like with like.
* **Row 24** trusts the result objects it is given; it cannot know they were not edited by the caller.
* **Row 92** has no database connection, so there is no `EXPLAIN` plan, row estimate or timing
  (`plan_available` is always false). Live schema discovery stays a Super-Admin connector (Track AP).
* **Row 93** compares schemas to depth 8 after resolving local `$ref`; `allOf`/`oneOf`/`anyOf` are not
  merged and remote `$ref` is never fetched. Generated tests are text that nothing runs.
* **Row 94** verifies; it does not deploy, restart or roll back anything. The pipeline stays the
  operator's CI/CD (`deploy-backend.yml`, `vps-update.sh`, Railway).
* **Row 95** reads text the caller passes; it has no connection to `observability/` or the Prometheus
  server. Hypotheses are pattern rules and their confidence is how much of the pattern appeared, not a
  probability.
* **Row 102**: nothing calls these checks automatically before ingestion or training; the hook is a
  separate piece of work.
* **Row 103**: the tool does **not** trigger an evaluation or retraining run when drift is found
  (`evaluation_triggered: false`); starting one needs a job runner that agent tools do not have. The
  store is best-effort (an unavailable cache reads as empty history) and has no compare-and-set, so two
  processes recording the same series at the same instant can lose one observation.
* **Row 106**: contradiction detection is lexical (numeric values and negation over overlapping
  sentences); paraphrased disagreement is not found, and the report says so. Web research exists only
  where the operator configured a search backend and fetch hosts.

## Consequences

* Existing callers see no change: every new argument is optional, and the only defaults that alter an
  existing result are `deep` (adds `deep_review` and can flip `approved` to false for code the old rules
  passed) and `include_behavior_tests` (adds tests that can fail where structural ones passed). Both can be
  switched off per call.
* `analyze_test_quality` now mutates in the tool's own throwaway directory instead of a copy of it; the
  baseline run requirement and kill semantics are unchanged.
* **Verification.** The new tests (`tests/unit/test_open_partial_tools_completed.py`) drive every
  addition through `Tool.execute` or a real `ToolRegistry`. Not proven by them: release verification
  against a real deployed service over the public internet (the success path is tested against a
  loopback server with the guard replaced, the refusals with the real guard), `profile_python` on a real
  paired desktop (a local subprocess stands in), and any quality claim about hypotheses or contradictions
  on real incident data. The status stays "built, not proven" until those have run.
