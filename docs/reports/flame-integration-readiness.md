# Flame in Freefall integration readiness (evidence)

**Gap:** GAP-063 · **ADRs:** 021, 022, 029 · **Date:** 2026-10-05 · **Base:** `25cc5b8` (main)
**Counterpart:** `FCDS-College/Nasa` → `docs/AURA_INTEGRATION_REQUIREMENTS.md` (requirements
AR-1…AR-8) and `tests/integration/aura_blueprint_dryrun.py`.

**Maturity reached:** BUILT and TESTED. **Not reached:** PROVEN. Nothing here has run against a
live Flame API, the dev stack or Backend CI. The runs that would prove it are in
[`flame-integration-runbook.md`](../guides/flame-integration-runbook.md) §2.6 and §3.4.

## What each requirement got

| AR | Built | Tests |
| --- | --- | --- |
| AR-1 typed output schemas | merged earlier (#154) | dry run below |
| AR-2 optional query parameters | discovery descriptors → `query_param_N` → optional `ParameterSpec`; token-only enums on the model surface; `render_http` booleans/arrays; per-key twin allowlist | `test_adaptation_compiler.py::TestOptionalQueryParameters` (6); `test_adaptation_gate.py` surface tests; regenerated pilot goldens |
| AR-3 credentialed read-only connectors | `read_only_attested` option, digest-bound; health `credential_scope_attested`; audited at activation | `test_adaptation_connectors.py::TestAttestedReadOnlyOpenApi` (6); `test_adaptation_discovery.py` |
| AR-4 service keys | `aurask_` tokens, digest-only store, route allowlist in `principal`, WebSocket refusal, own rate limit, forced no memory/tools/session, audit, immediate revocation, admin routes, Alembic 0013 | `tests/unit/test_service_keys.py` (23); `tests/load/test_service_keys_postgres.py` (3, real PostgreSQL) |
| AR-5 caller context | `ChatRequest.context` → guarded, fenced DATA, always taints; citations by id; non-stream `/chat` citations now user-facing | `tests/security/test_caller_context.py` (16) |
| AR-6 structured output | `ChatRequest.response_schema`; validate after guards; one budget-bounded repair over error paths; `422 output_invalid`; `data` on success; capability-aware JSON mode | `tests/unit/test_structured_output.py` (18) |
| AR-7 live proof | runbook only | **not run** |
| AR-8 drift | runbook §4 | manual by design until AP11 |

## Flame's offline dry run

`AURA_BACKEND=…/aura_backend python tests/integration/aura_blueprint_dryrun.py`, run against
Flame `db18d7f`.

**Before (AURA `25cc5b8`):** exit 0, and 8/8 ACCEPTED (AR-1 was already merged). But **14
optional query parameters were unreachable**:

- 8 on `GET /api/analyses`: `status`, `experiment_id`, `pipeline_version`, `created_from`,
  `created_to`, `sort`, `page`, `page_size`;
- 6 on `GET /api/search`: `type`, `source_id`, `experiment_id`, `domain`, `page`, `page_size`.

**After (this branch):**

```
discovery: 406 artifacts, issues=0
compiled tools: 8
  api_list_analyses   GET /api/analyses   params=['status', 'experiment_id', 'pipeline_version', 'created_from', 'created_to', 'sort', 'page', 'page_size']  ACCEPTED
  api_get_analysis    GET /api/analyses/{analysis_id}      params=['analysis_id']    ACCEPTED
  api_get_dashboard_counts GET /api/dashboard/counts       params=[]                 ACCEPTED
  api_get_document    GET /api/documents/{document_id}     params=['document_id']    ACCEPTED
  api_get_evidence    GET /api/evidence/{evidence_id}      params=['evidence_id']    ACCEPTED
  api_get_experiment  GET /api/experiments/{experiment_id} params=['experiment_id']  ACCEPTED
  api_get_health      GET /api/health                      params=[]                 ACCEPTED
  api_search          GET /api/search   params=['q', 'type', 'source_id', 'experiment_id', 'domain', 'page', 'page_size']  ACCEPTED
exit=0
```

No `not reachable by AURA` lines. `source_id` and `status` are arrays (`form`, exploded) and
render as repeated keys. `domain` keeps its `combustion`/`all` enum on the model surface, so an
AURA user can opt out of Flame's server-side DSI exclusion (FR-4) explicitly.

## Suites

| Suite | Base `25cc5b8` | This branch |
| --- | --- | --- |
| `pytest` (default) | 3254 passed, 56 skipped, **1 failed** | **3328 passed**, 56 skipped, 0 failed |
| `TESTING=True python -m unittest discover` | 100 run, OK (4 skipped) | 100 run, OK (4 skipped) |
| `pytest tests/architecture` | — | 204 passed |
| `pytest tests/load/test_service_keys_postgres.py -m load` (PostgreSQL 16.x, local throwaway) | — | 3 passed |

**The one failure on base was pre-existing:**
`test_adaptation_compiler.py::test_committed_blueprint_golden_matches[hr]`. The HR blueprint
golden was stale on `main` after #154 (AR-1) and the AP8 merge crossed. AR-2 regenerates every
pilot golden, which fixes it.

**Golden diff, reviewed by hand:**

- HR gains `departmentId` (integer) and `status` (enum `active`/`on_leave`/`terminated`) on
  `GET /employees`, and `employeeId` on `GET /leave-requests`. That operation's summary carries an
  injection string; it is not carried, because descriptors hold no descriptions.
- E-commerce gains `category` and `inStock` (boolean) on `GET /products`.
- Every other change is a digest that follows from those.

**Migration 0013** was applied with `alembic upgrade head`, chained 0001 → 0013, on a fresh
PostgreSQL 16 database. The table, its CHECK constraints and the adapter round-trip were all
checked through asyncpg: `TEXT[]` maps to a `frozenset`, and `TIMESTAMPTZ` maps to an aware UTC
datetime. A revocation written by one store instance refuses the token on another instance's
next call.

## Judgement calls worth reviewing

- **AR-3 is an attestation, not a probe.** AURA still cannot see a bearer token's scope. The
  control is that a named Super Admin's claim is bound into the approved digest and audited, and
  that every generated template is a `GET`.
- **AR-5 taints on presence.** Any caller context taints the turn, whatever the guards found. A
  service turn has no tools anyway. A person's turn with `context` will see side effects go to
  `needs_confirmation`, which is the intended cost.
- **AR-6's JSON mode is a hint.** On a `CompletionRequest`, `response_schema` is a hard routing
  filter (`no_json_mode`), so it is set only when some configured provider supports JSON mode.
  Correctness never depends on it, because AURA validates the answer itself.
- **The `/chat` citation fix changes behaviour for every caller.** Non-stream `/chat` returned
  `memory:`/`tool:`/`goal:` provenances in `citations`; it now matches the stream path and returns
  documents and context ids only.
- **The new admin routes have no console page** (`api-reachability.md`: "to wire"). Issue and
  revoke keys with curl, as in the runbook.

## Not proven

- **AR-7:** a deployed `bp<N>_api_search` answering a question with a Flame `evidence_id`. It
  needs Flame's read API (Flame FR-1/FR-2, not yet implemented) and the dev stack.
- **Link 1 end to end:** Flame's `AuraInterpreter` (FR-6) does not exist yet.
- **CPU-latency behaviour of a repaired structured turn on the production model.** It is
  estimated at 40–160 s for 400 tokens, from the deployed 5–10 tok/s, and has not been measured.
- **Backend CI** on this branch.
