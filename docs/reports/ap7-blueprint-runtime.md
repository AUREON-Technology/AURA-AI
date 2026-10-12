# AP7: Blueprint Runtime — Approve, Deploy, Roll Back (evidence)

**Phase:** AP7 ([Track AP roadmap](../roadmaps/AURA_ADAPTIVE_PLATFORM_ROADMAP.md)) · **Gap:**
GAP-056 · **ADRs:** 018, 024 · **Date:** 2026-10-04 · **Base:** `8eea19d` (main)

**Maturity reached:** IMPLEMENTED. Each of these was exercised through the real HTTP API in
process, and the pointer swap was checked live on PostgreSQL 16:

- a pilot blueprint deployed, redeployed and rolled back;
- the audit trail for every step;
- the core-only fallback after a corrupt load.

**Not reached:** the exit criterion names the **dev stack**: deploy and roll back through the
API there, with the audit trail. No dev stack is reachable from this environment, so that run
has not happened. Backend CI on `main` is also still pending (see the AP1 report).

## What was built

### Runtime and lifecycle

**`adaptation/runtime.py`:**

- **`ActiveBlueprint`** implements the `AgentRegistry` read interface over an immutable
  snapshot: core personas plus the live blueprint's agents.
  - Swapping is a single reference assignment, and the kernel resolves an agent once per turn, so
    in-flight turns keep their spec. The kernel is unchanged.
  - A generated agent can never be unrestricted (`allowed_tools=None`), can never replace a core
    persona, and always carries a `bp_` id.
- **Generated agents:**
  - They all use one fixed prompt, `system.blueprint_agent`. Nothing from the connected system is
    rendered into any system prompt.
  - They require the new `project_data` capability (`AURA_PROJECT_DATA_ROLES`, empty by
    default). A caller without it falls back to the orchestrator.
- **`GeneratedReadTool`** runs a compiled template through the new `ReadConnector` port.
  - It is read-only, gated by `project_data`, and tagged `untrusted_content`, so its rows taint
    the turn.
  - Its output schema is checked by the registry.
  - The model-facing description is a fixed sentence per capability plus the template's validated
    identifier. Blueprint labels, comments and API summaries never reach a tool *description*.
    *(Correction, 2026-10-05: they could still reach a declaration through the **parameter**
    descriptions, which quoted twin labels. AP8's `model_surface` gate check found this, and
    parameter descriptions are now composed too. See `ap8-generated-eval-gate.md`.)*
- **`BlueprintToolMaterializer`** registers `bp<version>_*` tools all-or-nothing, and retires the
  old version's tools after `AURA_ADAPTATION_TOOL_GRACE_S`.

**`adaptation/deployment.py`, `BlueprintDeployment`:**

- **The lifecycle:**
  1. evaluate (`proposed → evaluated`);
  2. request approval: a `BLUEPRINT_DEPLOYMENT` approval bound to the digest;
  3. approve (`evaluated → approved`, refused for a different digest or approval kind);
  4. deploy: tools, then the atomic pointer move, then the agent swap, then old tools retired.
     A failed pointer move unregisters the new tools and changes nothing else.
  5. rollback, to the previous or a named superseded version, under that version's own approval.
     Rollback tolerates an expired approval window, but status and digest must still match.
- **Startup:** load the active version. On any failure, run core-only and report
  `{"state": "degraded"}` through `register_readiness_check("blueprint")`.
- **`sync()`:** reconciles each gateway worker with the store's pointer. The container runs it
  every `AURA_ADAPTATION_SYNC_S`, because a deploy swaps only the worker that ran it.
- **A per-process lock** keeps agent swaps in pointer order.

### Connectors and storage

- **Evaluation gate:** none exists until AP8. `evaluate` is refused unless
  `AURA_ADAPTATION_ALLOW_UNGATED_DEPLOY=true`, which is off in both compose files. A version that
  used the bypass records `gate="ungated"` permanently. *(AP8 replaced the bypass with the gate.)*
- **Connector reads:**
  - `PostgresConnector.read` runs in a read-only transaction. It sets the timeout with literal
    `set_config` SQL plus a bound value, renders the statement with the domain renderer only,
    converts rows to JSON types, and applies `row_cap`.
  - `OpenApiConnector.read` sends GETs to the approved `base_url` under the same SSRF policy as
    the spec fetch.
  - Failures surface as `ToolExecutionError` without vendor text.
- **Storage:**
  - Migration `0012` adds `aura_adaptation_blueprint_active`, the pointer row.
  - `BlueprintStore.transition` is a compare-and-set on state.
  - `activate` moves the target to `active`, the predecessor to `superseded`, and the pointer, in
    one transaction under an advisory lock.
  - `BlueprintRecord` gained `gate`, `approval_id`, `activated_at` and `activated_by`, with
    invariants per state.

### API, settings and metrics

- **HTTP:** Super Admin only, returning 503 when Track AP is off.
  - `POST /api/v3/admin/blueprints/{v}/evaluate`
  - `POST /api/v3/admin/blueprints/{v}/request-approval`
  - `POST /api/v3/admin/blueprints/{v}/approve`
  - `POST /api/v3/admin/blueprints/{v}/deploy`
  - `POST /api/v3/admin/blueprints/rollback`
  - `GET /api/v3/admin/blueprints/active`

  The decision itself goes through the existing approvals routes, by a named person.
- **Settings:**
  - `AURA_PROJECT_DATA_ROLES`
  - `AURA_ADAPTATION_ALLOW_UNGATED_DEPLOY`
  - `AURA_ADAPTATION_TOOL_GRACE_S`
  - `AURA_ADAPTATION_SYNC_S`

  The security-relevant two are in both compose files, in parity.
- **Metrics:**
  - `aura_blueprint_active_version`
  - `aura_blueprint_swap_seconds`
  - `aura_generated_tool_calls_total{outcome}`

## Verification

```
cd aura_backend
pytest                                   # 3211 passed, 56 skipped (main baseline: 3179)
TESTING=True python -m unittest discover -p "test_*.py"   # Ran 100, OK (skipped=4)
AURA_TEST_DATABASE_URL=postgresql://aura:***@localhost:55440/postgres \
  pytest tests/load/test_adaptation_runtime_postgres.py tests/load/test_adaptation_compiler_postgres.py \
         tests/load/test_adaptation_twin_postgres.py tests/load/test_adaptation_discovery_postgres.py \
         tests/load/test_adaptation_postgres.py tests/load/test_knowledge_graph_postgres.py -m load   # 36 passed
```

### `tests/unit/test_adaptation_runtime.py` (22 tests)

- **Snapshot swap:** the swap and reset; capability fallback; generated agents can't be
  unrestricted or replace a core persona.
- **Concurrent turns:** 8 concurrent turns each resolved an agent 200 times while a swapper
  flipped between versions 200 times. Every resolution saw exactly one version's tool namespace.
- **Tool declarations:** model-facing descriptions never carry blueprint text, even a hostile one.
- **Materialised tools:** gated, tagged and read-only; registration is all-or-nothing.
- **A generated call through the real registry:**
  - it succeeds and taints the turn;
  - it is denied without `project_data`;
  - a down connector is a tool error, not a crash;
  - rows that break the schema produce `invalid_output`.
- **Lifecycle:**
  - deploy v1 → deploy v2 → roll back to v1, with tool namespaces and agent grants following,
    and the audit trail checked;
  - no ungated evaluation unless allowed;
  - an approval for one digest refused for another;
  - an approval of another kind refused;
  - state enforcement;
  - a failed pointer move leaves everything as it was.
- **Startup and sync:** a fresh process loads the active version; a corrupt pointer falls back to
  core-only, with readiness reporting `degraded`; a second worker converges through `sync()`.
- **Migration parity.**
- **The whole lifecycle through the HTTP API:**
  - compile twice;
  - evaluate, request approval, decide through the approvals route, approve, and deploy each
    version;
  - roll back;
  - `/api/v3/agents` lists `bp_project`.

### `tests/architecture/test_core_immutable_from_adaptation.py` (4 tests)

- The adaptation package imports only domain, ports, observability, itself, `agents.registry` and
  `learning.approval`.
- It never writes to policy, kernel-dependency or registry internals.
- The checker itself flags a kernel or tool-policy import.
- An unrestricted generated agent is refused.

### Live tests (`tests/load/test_adaptation_runtime_postgres.py`, 4 tests)

- The pointer and both record states move together, including a rollback.
- **Four concurrent activations** from separate connections leave exactly one active version, and
  the pointer names it.
- `transition` is a real compare-and-set: of two racers, one is refused.
- **Every generated SQL tool of the HR pilot ran**, materialised exactly as a deploy does,
  through `ToolRegistry` and `ToolPolicy` against the loaded pilot database. Each passed its
  output-schema check and tainted the turn.

### Bugs found by the new tests

- **`ToolExecutionError` was raised without its required `tool=` keyword**, in the runtime and
  both connector `read()` methods. A down connector would have surfaced as an unexpected
  `TypeError` instead of a tool error.
- **A route-ordering trap:** `GET /admin/blueprints/{version}` would have swallowed
  `/admin/blueprints/active`. Fixed by registration order; an `int` converter would have hidden
  the route from the route-auth suite.
- **`test_no_freeform_sql` caught a new f-string `SET LOCAL` in `read()`**; it is now literal SQL
  with a bound value.

## Residual risks, stated

- **Not proven on the dev stack.** That run, with its audit trail, is the remaining exit
  evidence.
- ~~**No evaluation gate yet.**~~ Superseded by AP8 (`docs/reports/ap8-generated-eval-gate.md`):
  deploys now pass the generated evaluation gate, and the bypass is gone.
- **Workers converge by polling** (`sync_s`, default 15 s). Between a deploy and the next sync,
  other workers keep serving the previous version, which stays a complete version, never a mix.
- **Delegated API connectors** forward no user token yet (AP10). They read only what the API
  serves anonymously.
- **Connections are not pooled.** Each Postgres read opens and closes its own connection, which
  is simple and bounded but not fast.
- **Generated agents are not delegation targets.** The delegation tool lists targets once, at
  build time.
