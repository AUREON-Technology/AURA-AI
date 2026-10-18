# AURA Adaptive AI Platform — Track AP Roadmap

**Status:** CANONICAL (parallel track) · **Baseline:** `3c7d8ed` (2026-10-01) · **Runs beside**
[AURA_MASTER_UPGRADE_ROADMAP.md](AURA_MASTER_UPGRADE_ROADMAP.md) (M0–M15), which stays the
canonical plan for production certification. This document is the canonical plan for **Track AP
(AP0–AP13)**: turning AURA from a personal AI platform into a general-purpose adaptive AI platform
that attaches intelligence to existing software systems. Decisions: ADR-018 … ADR-028
(`docs/architecture/018-*.md` … `028-*.md`). Gaps: GAP-049 … GAP-062 in
[`gap_register.yaml`](../production/gap_register.yaml). Backlog: CB-32 … CB-41 in
[AURA_CRITICAL_BACKLOG.md](AURA_CRITICAL_BACKLOG.md).

**Standing rule (binding, inherited):** a phase or item is complete only with the evidence its exit
criteria name, linked from `docs/reports/`. Source code existing is IMPLEMENTED; nothing more. Use
the master roadmap's maturity ladder and never collapse its states. **No A-phase can be reported
beyond BUILT until M0 restores CI evidence** — Track AP runs in parallel, it does not get a cheaper
definition of "proven".

---

## 0. Executive Objective

> **AURA Adaptive AI Platform:** AURA receives an existing system's structure and data, understands
> it deterministically, determines which AI capabilities are useful *from evidence in that system*,
> compiles a versioned **Project Blueprint**, materializes the agents, tools, knowledge, workflows,
> UI manifest and evaluation suite it describes, and — only after the Super Admin approves a
> specific blueprint version — runs it on the unchanged AURA core, re-analysing the system as it
> drifts.

One platform, one core, one blueprint per install. AURA itself is not re-implemented per project;
the **blueprint** changes, the **kernel** does not. The mental model is a compiler:

```
SOURCE SYSTEM (DB, OpenAPI, files, repo)
   │  connectors (read-only, credential-vaulted)
   ▼
DISCOVERY ─► raw artifacts (deterministic, provenance-tagged, untrusted text wrapped)
   ▼
NORMALIZE ─► AUSM (Universal Semantic Model) ─► PROJECT TWIN vN  (versioned, diffable)
   ▼
CAPABILITY PLANNER ─► evidence-linked capability selection (no industry templates)
   ▼
FACTORIES ─► tools · agents · workflows · UI manifest · eval suite   (declarative data only)
   ▼
BLUEPRINT vN (digest) ─► EVALUATE ─► SUPER ADMIN APPROVES (digest-bound) ─► DEPLOY (alias) ─► MONITOR
   ▲                                                                                       │
   └──────────────── DRIFT ◄── re-discovery ◄── TwinDiff ◄── impact analysis ◄─────────────┘
```

## 1. Current Verified State (what Track AP starts from)

| Fact | Evidence |
| --- | --- |
| Agents are already declarative-shaped (`AgentSpec`: tools, capabilities, risk ceiling, memory scope, budgets) but **hardcoded in Python** | `aura_core/agents/registry.py:32`, `build_default_agent_registry()` `:132` |
| The kernel reads `self._deps.agents.resolve()` per turn through a mutable `KernelDependencies` | `aura_core/kernel/kernel.py:173`, `:884-886` |
| Tools can be registered at runtime; MCP already converts foreign JSON Schema → `ToolSpec` with conservative defaults | `aura_core/tools/registry.py:93`; `build_mcp_bridge_tools()` `aura_core/tools/coding.py:1294` |
| `ToolSpec` has **no output schema**; tools return `str` | `aura_core/domain/tools.py:112`; `aura_core/ports/runtime.py` |
| Closed-world knowledge-graph schema with provenance exists, but one fixed schema per install | `aura_core/domain/graph.py:166-196`, migration `0007` |
| Alias-based promote/rollback with a gate exists for indexes; human approvals exist for models/sources | `aura_core/knowledge/index_manager.py:62,122,315,356`; `aura_core/learning/approval.py:40` |
| Evaluation harness, deterministic agent-run evaluator, regression gate | `aura_core/evaluation/harness.py`, `agent_runs.py`, `gate.py` |
| **No** connector framework, OpenAPI parsing, or database introspection anywhere | repo-wide search, 2026-10-01 |
| `CompositeFetcher` extension point exists but is never instantiated; only `HttpSourceFetcher` is wired | `aura_core/learning/sources.py:237`; `aura_core/container.py:282-284` |
| **No** workflow engine; one `TriggerSpec`; Celery beat for schedules | `aura_core/triggers/`; `celery_app.py` |
| **Nothing above `user_id`**: no tenant/project scope; one process-wide tool and agent registry | `aura_core/domain/knowledge.py` (`owner_id`); [AURA_FULL_SYSTEM_REVIEW.md](../reports/AURA_FULL_SYSTEM_REVIEW.md) ("single-tenant-per-user") |
| Roles are coarse (User…SuperAdmin) with three capability strings | `services/rbac.py`; `aura_service.py:86-104` |
| Console: Next.js, ~1.7k LOC, 9 hardcoded areas, deployed at `/console` | `aura_admin/src/components/shell.tsx` |
| Flutter: routes and Life Hub panes hardcoded; only the persona picker is backend-driven | `aura_client/lib/core/routing/router.dart`; `features/chat/data/agents_repository.dart` |
| Production LLM is CPU `qwen2.5:3b` at ~5–10 tok/s — unfit for compile-time semantic analysis | CLAUDE.md; GAP-011 |
| Foundations unproven: CI dead since 2026-09-13; SEC-01..03 and CB-11 open | master roadmap §1, §5 |

## 2. Principles (Track-AP-specific, in addition to the master roadmap §2)

1. **Immutable core, declarative adaptation** (ADR-018). The adaptation layer emits *data* —
   blueprints, specs, templates, manifests — that existing core mechanisms execute. It never
   imports kernel internals, never mutates `ToolPolicy`, never loads code.
2. **One project per install** (ADR-019). New tables carry a `project_id` key (default `p0`) with
   no tenancy semantics; no API accepts it as input.
3. **Deterministic first** (ADR-026). Structure comes from catalogs, constraints and specs; a model
   only labels and proposes, never decides structure, permissions or grants. The full pipeline
   runs without a GPU (degraded labels, complete structure).
4. **Discovered content is untrusted DATA** (ADR-022). Table/column names, comments, enum labels,
   OpenAPI descriptions, documents and row values are attacker-controllable input.
5. **No free-form SQL, ever** (ADR-022). Reads go through compiled, parameterized
   `QueryTemplate`s over twin-allowlisted identifiers.
6. **Read-only first** (ADR-021). v1 connectors cannot write by construction; writes arrive in
   AP12c behind argument-bound confirmation (depends on SEC-01).
7. **Super Admin is the authority boundary** (ADR-024). In v1 *nothing* auto-applies: every
   blueprint version is proposed → evaluated → approved (bound to its digest) → deployed.
8. **Evidence-linked generation** (ADR-023). Every capability, tool, agent and UI view links to the
   AUSM elements that justify it; drift propagates through those links.
9. **Feature = spec + policy + tests + telemetry + rollback.** A factory that emits a tool also
   emits its eval cases; nothing ships without its generated suite passing.
10. **Self-hosting unchanged.** `tests/architecture/test_no_external_inference.py` stays binding
    for compile time as well as runtime.

### Customization levels

| Level | What changes | Who produces it | Path to production |
| --- | --- | --- | --- |
| 1 — Configuration | prompts, policies, models, RAG settings, agent parameters, UI config | factories or Super Admin | blueprint version → eval → approval (auto-apply only after AP13 evidence) |
| 2 — Composition | new agents, tools, workflows, entity types, dashboards from existing primitives | factories | blueprint version → eval → approval |
| 3 — Extension | new connectors, processors, algorithms | humans; Engineer agent may **draft** | reviewed PR + CI in the extension repo; never hot-loaded (ADR-018) |

## 3. Current Architecture (relevant slice)

```
Super Admin ─► /console (9 fixed areas) ─► /api/v3/admin/*
User ─► Flutter (fixed routes, fixed Life Hub) ─► /api/v3/chat ─► AIKernel
   AIKernel ─► AgentRegistry (8 hardcoded personas) ─► Planning/Execution ─► ToolRegistry (process-wide)
           ─► KnowledgeEngine (one index alias, owner_id scoped) ─► Memory (user × persona scope)
Domains: tasks · calendar · finance · health (PersonalOsRecord + client-encrypted legacy tables)
```

## 4. Target Architecture

```
                                  AURA PLATFORM (one project per install)
                                              │
                ┌─────────────────────────────┴──────────────────────────────┐
           CONTROL PLANE                                                INFERENCE PLANE
                │                                                       vLLM / Ollama (LLMs)
   ┌────────────┴─────────────────────────┐                             labeler model (batch, AP4)
   │ INTELLIGENCE CORE (unchanged)         │
   │ AIKernel · Planning · Execution       │◄── ActiveBlueprint holder (agents snapshot, atomic swap)
   │ ToolRegistry/ToolPolicy · Safety      │◄── bp<ver>_* generated tools (namespaced)
   │ Knowledge/RAG · Memory · Eval harness │◄── project knowledge sources (CompositeFetcher)
   └────────────┬─────────────────────────┘
                │ data only (blueprint, specs, templates, manifests)
   ┌────────────┴───────────────────────────────────────────────────────────────┐
   │ ADAPTIVE INTELLIGENCE LAYER  aura_core/adaptation/                          │
   │ Connectors (ports) · Discovery · Normalize→AUSM · Project Twin + Diff       │
   │ Capability Registry + Planner · Factories (tool/agent/workflow/UI/eval)     │
   │ Blueprint Compiler · Deployment (propose→eval→approve→alias→rollback)       │
   │ Drift monitor · Impact analysis                                             │
   └────────────┬───────────────────────────────────────────────────────────────┘
                │ read-only, vaulted credentials, QueryTemplates only
        TARGET SYSTEM: SQL DB · OpenAPI/REST · files & documents · Git repository

Surfaces (staged): Console Project Factory (AP9) → embeddable widget + public AI API (AP12a)
                   → server-driven Flutter from the UI Capability Manifest (AP12b)
```

### Code placement (respects ADR-001 and `tests/architecture/`)

| Layer | Path | Contents |
| --- | --- | --- |
| domain (stdlib only) | `aura_core/domain/ausm.py`, `blueprint.py`, `connectors.py` | AUSM kinds + `Provenance`, `ProjectTwin`, `TwinDiff`, `Blueprint`, `BlueprintVersion`, `CapabilitySpec`, `ConnectorDescriptor`, `QueryTemplate`, `UntrustedText` |
| ports (stdlib, async) | `aura_core/ports/connectors.py`, `ports/adaptation.py` | `Connector`, `DiscoveryConnector`, `ReadConnector`, `CredentialVault`, `TwinStore`, `BlueprintStore`, `LabelingModel` |
| engine | `aura_core/adaptation/` — **added to `ENGINE_PACKAGES`** in `tests/architecture/test_dependency_rules.py` | `discovery`, `normalize`, `twin`, `capabilities/`, `planner`, `factories/`, `compiler`, `runtime`, `deployment`, `impact` |
| adapters | `aura_core/adapters/connectors/{postgres,mysql,mssql,openapi,files,git}.py`, `adapters/postgres_adaptation.py` | vendor drivers, imported lazily inside functions |
| api | `aura_core/api/admin_adaptation.py` | Super-Admin-only routes (`admin.super`) |
| wiring | `aura_core/container.py` `_build_adaptation()` | remains the single wiring point |

Connector port sketch (async per `test_all_ports_async`):

```python
class Connector(Protocol):
    descriptor: ConnectorDescriptor            # kind, identity_mode: DELEGATED | SERVICE_ACCOUNT
    async def health(self) -> ConnectorHealth: ...

class DiscoveryConnector(Connector, Protocol):
    def discover(self, *, budget: DiscoveryBudget) -> AsyncIterator[RawArtifact]: ...

class ReadConnector(Connector, Protocol):
    async def read(self, template: QueryTemplate, params: Mapping[str, Any],
                   identity: CallerIdentity) -> ReadResult: ...    # rows, row_count, truncated
# No write(). WriteConnector is a separate port introduced in AP12c (ADR-021).
```

### AUSM — the Universal Semantic Model (ADR-020)

Fourteen concepts: thirteen element kinds — **Entity, Attribute, Relation, Event, Action, Workflow,
Role, Permission, Document, Metric, State, Constraint, Source** — plus the **Provenance** record every
element must carry (provenance is attached to elements, not an element itself). Every element has a stable id
(`source_id + locator`), a `Provenance` (`source_id, locator, extractor, confidence,
evidence_hash`) and — for any human-readable text it carries — an `UntrustedText` wrapper. The
same concept discovered in different shapes (`employee` table, `/employees` path, `employees.csv`)
becomes one candidate Entity with several sources; identifier variants (`employee_id`, `emp_id`,
`employeeCode`) are candidate-merged by profile similarity, not by name guessing alone, and the
merge is a confidence-scored proposal the Super Admin can reject.

### Project Blueprint (ADR-024)

```yaml
blueprint:
  schema: aura.blueprint/v1
  project: {id: p0, name: ..., version: 7, twin_version: 12, digest: sha256:...}
  data_model: {entities: [...], relations: [...], events: [...], documents: [...]}  # AUSM refs
  security:   {roles: [...], permission_map: [...], sensitivity_classes: [...], identity_modes: {...}}
  capabilities: [{id: entity.search, target: Employee, evidence: [ausm:pg:public.employee]}, ...]
  tools:      [{name: employee_search, template: qt:..., output_schema: {...}, sensitivity: PERSONAL, registered_as: bp7_employee_search}]   # no side_effecting field: read-only by construction
  agents:     [{id: hr_assistant, tools: [employee_search, ...], risk_ceiling: PERSONAL, memory_scope: hr}]
  workflows:  [{id: leave_status, kind: read_status, states: [...], triggers: [...]}]
  knowledge:  {sources: [...], retrieval_strategy: hybrid}
  ui:         {manifest: {navigation: [...], views: [...]}}
  integrations: {connectors: [conn:pg-main, conn:openapi-hr]}
  policies:   {default_risk_ceiling: PUBLIC, budgets: {...}}
  evaluation: {suites: [...], gate: {...}}
```

`blueprint_digest()` is SHA-256 over canonical JSON; approvals bind to it.

## 5. Critical Blockers and External Gates

| ID | Blocker / gate | Blocks | Phase |
| --- | --- | --- | --- |
| AB-1 | CI not executing (M0, GAP-043) | any Track AP claim beyond BUILT | all |
| AB-2 | SEC-01..03 (M1) — confirmation binding, desktop exemption, committed secret | AP2 admin surface live; AP12c writes | AP2, AP7, AP12c |
| AB-3 | CB-11 — Qdrant treats missing `owner_id` as global | proving project-knowledge isolation | AP10 |
| AB-4 | M4 — GPU inference plane with a large self-hosted model | labeling *quality* (not function) | AP4 |
| AB-5 | M7 — real-model evaluation gate | AP8 proven | AP8 |
| AB-6 | M14 — client reach | AP12b server-driven Flutter | AP12 |
| AB-7 | Generated tools lack output schemas (`ToolSpec`) | typed UI binding, golden evals | AP6 |

### Top risks → mitigations

| Risk | Mitigation | Where |
| --- | --- | --- |
| Prompt injection via schema names, comments, enum labels, OpenAPI text, documents, row values | `UntrustedText` value object; DATA channel with per-turn nonce; generated tools tagged `untrusted_content` (taint path); labeler output limited to names/descriptions, length-capped, control-chars stripped; labeler cannot alter structure, permissions or grants; injection eval suite with malicious column comments | ADR-022; AP3, AP4, AP8 |
| Credential exposure | `CredentialVault`, `secret_ref` only; never in blueprint/twin/prompts/logs; redaction tests; delegated tokens never persisted | ADR-021; AP2 |
| SQL abuse or exfiltration | AST builder; twin-allowlisted identifiers; bound parameters; SELECT-only; forced LIMIT; read-only transaction; `statement_timeout`; health check refuses DB roles with write grants; fitness + fuzz tests | ADR-022; AP6 |
| Over-broad generated agents | `allowed_tools=None` forbidden for generated specs (test); default ceiling PUBLIC; ceilings above PERSONAL need explicit approval rows | ADR-024; AP6 |
| Stale tools after the target system changes | drift → impact → affected tools marked `stale` → refused by policy until a new version is approved | AP11 |
| CPU model cost/latency | labeling is batch-only, never on the turn path; cached by evidence hash; only diffs relabeled; degraded mode complete | ADR-026; AP4 |
| Breaking the live personal app | Project 0 parity test + shadow evaluation + fallback registry; each step reversible by blueprint rollback | ADR-028; AP13 |
| Scope creep against M0–M15 | explicit AB gates; nothing reported beyond BUILT without M0 | this §5 |

---

## 6. Phases

Every phase lists: **Objective · Why · Inputs · Dependencies · Implementation areas · Security ·
Testing · Failure tests · Metrics · Exit criteria · Rollback · Evidence required.** Every phase
starts at **DOCUMENTED** as of the baseline.

---

### AP0 — Charter, ADRs, Gap and Backlog Entries *(Governance)*

- **Objective:** make Track AP canonical and reviewable before any code lands.
- **Why:** the master roadmap does not mention tenancy, connectors, blueprints, an extension SDK or
  server-driven UI; work without a charter would repeat the "multiple overlapping roadmaps" problem
  the reconciliation fixed.
- **Inputs:** the Adaptive Platform proposal; master roadmap; full system review.
- **Dependencies:** none.
- **Implementation areas:** this document; ADR-018…028 (Proposed); GAP-049…062; CB-32…41;
  cross-links in the master roadmap, reconciliation and `CLAUDE.md`; shareable architecture page.
- **Security:** n/a (docs).
- **Testing:** `tests/architecture/test_gap_register_schema.py` passes; `GAP_REGISTER.md`
  re-rendered.
- **Failure tests:** n/a.
- **Metrics:** n/a.
- **Exit criteria:** (1) docs merged to `main`; (2) gap schema test green in CI (needs M0);
  (3) ADRs reviewed and moved to Accepted or amended.
- **Rollback:** revert the docs PR.
- **Evidence required:** the merged PR link in `docs/reports/ap0-charter.md`.

### AP1 — AUSM and Blueprint Domain Model *(Core model)*

- **Objective:** stdlib-only domain types for AUSM, Project Twin, Blueprint and their canonical
  serialization.
- **Why:** every later phase exchanges these objects; getting identity, provenance and digest
  semantics right first keeps diffing, approval and rollback sound.
- **Inputs:** GAP-049; ADR-020, ADR-024.
- **Dependencies:** AP0.
- **Implementation areas:**
  1. `aura_core/domain/ausm.py` — 13 element kinds + Provenance, stable ids, `Provenance` (confidence semantics copied
     from `FactProvenance`, `domain/graph.py`), `UntrustedText`.
  2. `aura_core/domain/blueprint.py` — sections as in §4; `BlueprintVersion` state enum
     (`proposed → evaluated → approved → active → superseded | rejected`); `blueprint_digest()`.
  3. `docs/schemas/blueprint.v1.json` and `project_twin.v1.json` exported from the dataclasses.
- **Security:** `UntrustedText` has no implicit `__str__` into prompt text; rendering requires the
  DATA-channel fence helper.
- **Testing:** round-trip; digest stable under key reordering; property tests on id stability;
  `tests/architecture/test_adaptation_schemas.py` (exported schema ↔ dataclasses drift check); existing
  stdlib-only domain fitness applies automatically.
- **Failure tests:** malformed blueprint JSON rejected with typed `AuraError`s.
- **Metrics:** n/a.
- **Exit criteria:** schema published; tests green in CI on `main`.
- **Rollback:** additive modules; delete.
- **Evidence required:** `docs/reports/ap1-domain-model.md` (CI run link).

### AP2 — Connector Framework and Credential Vault *(Integration)*

- **Objective:** async connector ports, a `ConnectorRegistry`, a credential vault and a Super-Admin
  approval flow for registering a connector.
- **Why:** every discovery and read path goes through a connector; credentials are the highest-value
  secret Track AP introduces.
- **Inputs:** GAP-050; ADR-021.
- **Dependencies:** AP1; **AB-2** (SEC-01..03) before the admin surface is exposed in production.
- **Implementation areas:**
  1. `aura_core/ports/connectors.py` (§4 sketch); `aura_core/adaptation/connectors.py` registry.
  2. `CredentialVault` port + adapter encrypting at rest with the existing data-encryption key path
     (separate key per stack — GAP-047 pattern); connectors receive `secret_ref` only.
  3. `ApprovalKind.CONNECTOR_REGISTRATION` in `aura_core/learning/approval.py`.
  4. `aura_core/api/admin_adaptation.py`: connector CRUD, health, test-connection (`admin.super`).
  5. Identity modes: `DELEGATED` (per-call `CallerIdentity` carrying the end user's OIDC/OAuth
     token; target system enforces its own permissions; AURA RBAC is an extra filter) and
     `SERVICE_ACCOUNT` (vaulted credential + discovered-role → AURA-policy mapping, AP10).
- **Security:** read-only DB role required — health check inspects grants and refuses a role with
  INSERT/UPDATE/DELETE/DDL; connection strings never logged; delegated tokens never persisted;
  route-auth and IDOR suites (`tests/security/`) extended to the new routes.
- **Testing:** `tests/architecture/test_connector_secrets.py` (no domain/twin/blueprint field named
  like `password|token|secret|key` other than `secret_ref`); log-capture redaction test; lazy
  vendor-import fitness extended to `adapters/connectors/`.
- **Failure tests:** vault unavailable → connector unhealthy, no plaintext fallback; approval TTL
  expiry; digest change after approval refused.
- **Metrics:** `aura_connector_health{kind}`, connector call latency/error counters (no content).
- **Exit criteria:** a Postgres fixture connector registered through the approval flow on the dev
  stack; secrets and redaction tests green in CI.
- **Rollback:** feature flag `AURA_ADAPTATION_ENABLED=false` removes routes and wiring.
- **Evidence required:** `docs/reports/ap2-connector-framework.md`.
- **As built (2026-10-01):** IMPLEMENTED and tested against a real Postgres 16 locally. Where the
  build differs from the plan above:
  - The vault uses the third-party-credentials key (`AURA_SECRETS_ENCRYPTION_KEY`, now a
    rotatable list), not the data key. Losing it costs "re-enter connector passwords", not user
    memories.
  - With the flag off the routes stay mounted and answer 503, so the route-auth suite keeps
    covering them.
  - The IDOR suite does not apply: connectors are install-wide Super Admin resources with no
    per-user owner.
  - The only metric so far is `aura_connector_health{connector,kind}`. Call latency and error
    counters arrive with reads in AP6.
  - Schema comes only from migration `0009`; the adapter issues no runtime DDL.
  - Registration is deliberately not idempotency-keyed, because that store hashes the body,
    which carries the credential.
  - Postgres only has health and privilege inspection; discovery is AP3.

### AP3 — Deterministic Discovery: Postgres and OpenAPI *(Discovery)*

- **Objective:** discover schemas, constraints, roles and APIs deterministically, with provenance;
  build the two reference pilot systems.
- **Why:** system understanding — not prompting — is the hard problem; a deterministic base makes
  the result reproducible, diffable and testable on CPU.
- **Inputs:** GAP-051; ADR-020, ADR-022, ADR-026.
- **Dependencies:** AP2.
- **Implementation areas:**
  1. Postgres adapter: `information_schema` / `pg_catalog` — tables, views, columns, types, PK/FK,
     unique and check constraints, enums, comments, roles and grants; sampled profiling with a row
     cap (`TABLESAMPLE`): cardinality, null rate, pattern classifiers (email, money, date,
     status-like low cardinality, identifier-like). Read-only transaction, `statement_timeout`.
  2. OpenAPI 3.x adapter: paths, operations, schemas, `securitySchemes`, tags.
  3. **Reference pilots** (owned fixtures): `tests/fixtures/pilots/hr/` and
     `tests/fixtures/pilots/ecommerce/` — DDL, seed data, `openapi.yaml`, a small document set;
     compose profile `pilot` for live runs. Default `pytest` uses recorded catalog snapshots (no
     Docker), live runs live under `tests/load`-style opt-in markers.
  4. All names, comments and descriptions wrapped in `UntrustedText` at the adapter boundary.
- **Security:** discovery budget (tables, rows sampled, time) enforced; sample values never leave
  the profiler — only aggregate statistics enter the twin.
- **Testing:** golden discovered-artifact sets for both pilots.
- **Failure tests:** permission-denied on a schema → partial discovery recorded, not a crash;
  oversized catalog → budget stop with a recorded truncation.
- **Metrics:** discovery duration, elements discovered, truncations.
- **Exit criteria:** goldens green in CI; live run against real Postgres for both pilots recorded.
- **Rollback:** additive.
- **Evidence required:** `docs/reports/ap3-discovery.md`.
- **As built (2026-10-01):** IMPLEMENTED. Goldens pass with no database, and a live capture of both
  pilots equals the recorded catalog on PostgreSQL 16. Differences from the plan:
  - **Two halves per connector.** Each connector separates a live **capture** from a **pure
    mapping**. Goldens replay the recorded catalog snapshots (`golden/catalog.json`) and specs
    through the mapping; `scripts/pilot_discovery_goldens.py` records and renders them.
  - **Profiles are computed in SQL.** Counts, distinct counts and pattern matches are calculated
    by the database, so row values never reach the process.
  - **Values are opt-in.** Distinct values of low-cardinality text columns come back only with
    `sample_values`, per run. Schema-declared values (enum labels, CHECK lists) are always
    captured.
  - **External `$ref`s** in an OpenAPI spec are recorded as an issue and never fetched.
  - **Spec fetches follow the SSRF policy.** Hosts are resolved first, redirects are refused,
    and internal addresses are refused unless `allow_private_network` is set. Even then,
    loopback, link-local and carrier-grade NAT stay refused.
  - **Discovery runs only on active connectors**, through
    `POST /api/v3/admin/connectors/{id}/discover`.
  - **Not persisted yet.** Reports are returned and audited by digest; storage arrives with the
    Project Twin in AP4.
  - **No compose `pilot` profile.** The pilots load into any Postgres through the golden script
    and the load tests. A standing pilot service is deferred to AP9, where the Console runs
    against it.
  - **OpenAPI with a credential** reports `write_capable=None`, so activation is refused until
    token-scope checks exist. Public and delegated specs work today.

### AP4 — Normalization, Project Twin and Diff *(Core model)*

- **Objective:** map raw artifacts to AUSM deterministically, version it as the Project Twin, and
  diff versions.
- **Why:** a versioned twin means AURA never re-derives understanding from scratch and can explain
  exactly what changed.
- **Inputs:** GAP-052; ADR-020, ADR-026.
- **Dependencies:** AP3; AB-4 for label quality only.
- **Implementation areas:**
  1. `adaptation/normalize.py`: table→Entity, column→Attribute, FK→Relation, enum or
     low-cardinality status column→State, check constraint→Constraint, DB role/grant→Role/Permission,
     OpenAPI GET→read Action, other verbs→write Action marked `deferred`, timestamp columns→Event
     candidates; cross-source entity merge proposals by profile similarity.
  2. **Process mining v0:** state-transition candidates inferred from status + history/audit tables
     (ordered by timestamp), confidence-scored, presented as Workflow candidates.
  3. Optional `LabelingModel` pass (batch, offline): proposes display names, descriptions and Metric
     candidates; output stored with `extractor="llm"`, confidence < 1; cannot change structure.
     Without a GPU it falls back to identifier humanization — the twin is still complete.
  4. `adaptation/twin.py`: `ProjectTwin(version, digest)`, `TwinDiff` (added/removed/changed,
     rename candidates by profile similarity); Alembic tables `adaptation_twin_versions`,
     `adaptation_twin_elements` (JSONB, `project_id`).
- **Security:** labeler prompts carry discovered text only inside DATA fences; labeler output
  re-sanitized (length cap, control characters) before storage.
- **Testing:** same input ⇒ same digest; diff goldens (add, drop, rename); `test_degraded_without_labeler`.
- **Failure tests:** labeler timeout or provider failure → degraded labels, twin still versioned.
- **Metrics:** labeling coverage, labeler latency per element, merge-proposal acceptance rate.
- **Exit criteria:** HR and e-commerce twins produced on CPU-only CI; diff goldens green; one twin
  persisted and diffed on a real Postgres.
- **Rollback:** twin versions are append-only; a bad version is superseded.
- **Evidence required:** `docs/reports/ap4-project-twin.md`.
- **As built (2026-10-01):** IMPLEMENTED. Both pilot twins and the three diff goldens pass with no
  database and no model. One twin is persisted, altered with real DDL, re-discovered and diffed on
  PostgreSQL 16. Differences from the plan:
  - **Proposals and candidates live in the twin as elements**, so they are versioned, diffed and
    evidence-linked like everything else, and every one is below certainty:
    - cross-source merges are `same_as` RELATIONs with `status=proposed`
      (`adaptation/matching.py`);
    - workflows and their transitions are WORKFLOW elements with `role=process` or
      `role=transition`, citing their evidence by locator (`adaptation/process_mining.py`).
  - **Process mining v0 is structural.** It works from declared order, terminal-state vocabulary,
    state-named timestamps on related entities, and action verbs (also through a `same_as` match,
    at half weight). It also detects history entities. Mining a history table's *rows*
    (`mine_event_log`) is built and tested, but it needs a templated read to feed it (AP6). The
    pilots have no history table.
  - **Labeling** goes through the existing router (`RouterLabelingModel`), is opt-in
    (`AURA_ADAPTATION_LABELER_ENABLED`), and is limited to ENTITY, ATTRIBUTE and ACTION names and
    descriptions. Labels carry over for elements whose deterministic evidence is unchanged. It
    does not propose Metrics: deterministic METRIC candidates (numeric measures) come from
    normalisation instead.
  - **The merge-proposal acceptance-rate metric waits for AP9.** Nothing can accept a proposal
    yet. Labeling coverage and per-element latency are exported.
  - **Tables are named `aura_adaptation_twin_versions` and `aura_adaptation_twin_elements`**
    (migration `0010`), matching the `aura_` prefix of every other table.
  - **An unchanged snapshot does not create a new version**; it returns the latest one.
  - **Routes:** `POST /api/v3/admin/twin/snapshot`, `GET /api/v3/admin/twin/versions[/{v}]`,
    `GET /api/v3/admin/twin/diff`.

### AP5 — Capability Registry and Planner *(Planning)*

- **Objective:** domain-agnostic, composable capability primitives as data, and a planner that
  selects them from twin evidence.
- **Why:** "HR assistant" must be *composed* from primitives that apply to any domain, not coded as
  an `HRAgent`; this is what makes the platform universal.
- **Inputs:** GAP-053; ADR-023.
- **Dependencies:** AP4.
- **Implementation areas:**
  1. `adaptation/capabilities/*.yaml` loaded into a registry: e.g. `entity.lookup`,
     `entity.search`, `entity.list_filtered`, `relation.traverse`, `metric.aggregate`,
     `state.timeline`, `workflow.status`, `document.qa`, `notification.draft` (no send in v1).
     Each declares `requires` (predicates over AUSM), `produces` (tool/agent/UI/eval templates),
     `risk`, `cost`.
  2. `adaptation/planner.py`: predicate matching → candidates → relevance/risk/cost scoring →
     selection; every selection carries `evidence` (AUSM ids). Recommended **and not recommended**
     capabilities are both reported with reasons.
  3. Agent grouping: capabilities clustered by entity neighbourhood and role into agent proposals.
- **Security:** capability `risk` sets the generated tool's sensitivity floor.
- **Testing:** `test_capabilities_are_domain_agnostic` (domain-noun denylist over capability files);
  100% of selections evidenced on both pilots.
- **Failure tests:** an empty twin yields an empty plan with a reason, not a guess.
- **Metrics:** capabilities selected per pilot; evidence count per selection.
- **Exit criteria:** both pilots planned from the **same** primitive set; cross-domain report.
- **Rollback:** additive.
- **Evidence required:** `docs/reports/ap5-capability-planning.md`.
- **As built (2026-10-01):** IMPLEMENTED. Both pilots are planned from the same eleven primitives
  (same registry digest). Every selection is evidenced, and a cross-domain table is in the report.
  Differences from the plan:
  - **Primitives** are one YAML file each, decoded by the strict codec, so an unknown key is
    refused. Two were added to the list above, `operation.read` and `operation.write`.
    `operation.write` is declared *unavailable* (`writes_deferred`), so every write action
    appears in the plan with that reason instead of silently vanishing.
  - **Requirements** are three relations to the target (`child`, `referenced_by`, `ref`) with
    property filters. Evidence below a primitive's `min_confidence` does not count.
  - **Agents** are composed from selections:
    - one `agent.project` agent;
    - one `agent.role.<role>` agent per source ROLE with read grants, extended through
      `same_as` proposals.
  - **Plans are recomputed, not stored**, through
    `GET /api/v3/admin/twin/versions/{v}/plan`: the same twin and registry digests always give
    the same plan.
  - **Metrics:** selections per primitive and evidence per selection.

### AP6 — Factories, Blueprint Compiler and Tool Output Schema *(Generation)*

- **Objective:** turn a capability plan into a complete, schema-valid blueprint; add output schemas
  to tools.
- **Why:** this is the "compile" step; its outputs must be inert data the core already knows how to
  enforce.
- **Inputs:** GAP-054, GAP-055; ADR-022, ADR-025.
- **Dependencies:** AP5.
- **Implementation areas:**
  1. **`ToolSpec.output_schema`** (`aura_core/domain/tools.py`): optional JSON-Schema subset; no
     effect on existing tools; `ToolRegistry.execute` validates generated tools' JSON results after
     execution (failure = audited tool error); MCP `outputSchema` mapped into it.
  2. **Tool factory:** `GeneratedToolSpec = ToolSpec + QueryTemplate + output_schema`.
     `QueryTemplate` is an AST from an internal builder: identifiers only from the twin allowlist
     (driver-quoted), values only as bound parameters, SELECT only, mandatory LIMIT, optional
     row-filter column from the security map. OpenAPI tools: `operationId` + parameter mapping,
     GET only in v1. Registered as `bp<version>_<name>` (tool names are `[a-z0-9_]`, ≤64 chars for
     providers; `generated_tool_name()`); tagged `untrusted_content`.
  3. **Agent factory:** `AgentSpec` data; `allowed_tools` always explicit; `risk_ceiling` and budgets
     from `policies`; prompt template rendered from a fixed core template with blueprint fields in
     DATA fences.
  4. **Workflow factory:** declarative read-only status workflows and trigger specs (stored; run in
     AP11).
  5. **UI factory:** UI Capability Manifest — views `entity_list`, `entity_detail`, `metric_card`,
     `timeline`, `search`, `chat`, `approval_queue` (read-only in v1) bound to tool ids and output
     schemas.
  6. **Eval factory:** JSONL suites in `aura_core/evaluation/suites/` format — golden rows from
     fixtures, `AgentRunScenario`s (`evaluation/agent_runs.py:150`), permission-denied,
     out-of-scope and injection refusal cases.
  7. `adaptation/compiler.py` assembles and stores the blueprint as `proposed`.
- **Security:** `tests/architecture/test_no_freeform_sql.py` (AST check: in `adaptation/` and
  `adapters/connectors/`, nothing executes a non-`QueryTemplate`); builder rejects non-allowlisted
  identifiers; factories' output contains no callables or code strings (test).
- **Testing:** fuzzed parameter values never alter the query AST; both pilots compile valid blueprints.
- **Failure tests:** a capability with unmet predicates is dropped with a reason, never stubbed.
- **Metrics:** tools/agents/views/eval cases per blueprint.
- **Exit criteria:** both pilots compile schema-valid blueprints; fitness and fuzz tests green in CI.
- **Rollback:** additive; `output_schema` defaults to `None`.
- **Evidence required:** `docs/reports/ap6-blueprint-compiler.md`.
- **As built (2026-10-04):** IMPLEMENTED.
  - Both pilots compile blueprints that are valid against the JSON Schema and keep a stable digest
    through a strict round-trip.
  - The fitness test and both fuzz tests pass (SQL text and HTTP path).
  - Every compiled SQL template ran on PostgreSQL 16 and returned rows matching its output schema.

  Differences from the plan:
  - **Templates are a blueprint section** (`Blueprint.templates`, `domain/templates.py`).
    `check_against_twin` enforces the identifier allowlist, and a tool's parameters must be
    exactly its template's. Optional filters render as `(… OR $n IS NULL)`, so arguments never
    change the statement text. Range filters carry a closed `ValueCast`; the first live run
    showed both were needed.
  - **Output schemas use a closed subset**, `domain/output_schema.py`. Anything outside it is
    refused, not ignored. A non-conforming result is an audited `invalid_output` failure.
  - **The rule in `test_no_freeform_sql`:**
    - the adaptation engine makes no driver call at all;
    - connector adapters pass only literals, UPPER_CASE constants or `render_sql(...).text` to a
      driver;
    - two reviewed catalog builders are named in an explicit allowlist.
  - **API entities are reached only through `operation.read`** (GET operations). Search, list,
    aggregate and timeline over an API are dropped with a reason. Nothing is stubbed.
  - **Not built in AP6:**
    - agent prompt templates and `untrusted_content` tags on tools, which happen at
      materialisation (AP7);
    - workflow trigger specs (AP11);
    - the `notification.draft` factory, for which there is no draft path in v1.
  - **Blueprints are stored** as `proposed` versions (migration `0011`) and exposed through:
    - `POST /api/v3/admin/twin/versions/{v}/compile`
    - `GET /api/v3/admin/blueprints[/{v}]`

### AP7 — Blueprint Runtime: Approve, Deploy, Roll Back *(Kernel integration)*

- **Objective:** run an approved blueprint on the unchanged kernel, swap versions atomically, and
  roll back.
- **Why:** this is where generated data becomes live behaviour — it must reuse every existing
  enforcement point and add none of its own bypasses.
- **Inputs:** GAP-056; ADR-018, ADR-024.
- **Dependencies:** AP6; AB-2 before production exposure.
- **Implementation areas:**
  1. `adaptation/runtime.py` `ActiveBlueprint`: implements the `AgentRegistry` read interface over
     an immutable snapshot (core personas merged with blueprint agents), swapped atomically;
     `container.py` passes it as `KernelDependencies.agents` (`kernel.py:173`). In-flight turns keep
     the spec they resolved.
  2. `BlueprintToolMaterializer`: registers `bp<new>_*` before the swap, unregisters `bp<old>_*`
     after a grace period; `GeneratedReadTool` executes a `QueryTemplate` via `ReadConnector`.
     Every call still passes `ToolRegistry.execute` → `ToolPolicy` → timeout → taint → audit.
  3. `adaptation/deployment.py` `BlueprintDeployment`, modelled on `IndexManager`: one active-version
     pointer row updated atomically; gate thresholds from AP8; `rollback(version)`.
  4. `ApprovalKind.BLUEPRINT_DEPLOYMENT` bound to `blueprint_digest`; deploy refused if the digest
     changed after approval.
  5. Startup: load the active version; on load/validation failure fall back to core-only and report
     degraded readiness (`register_readiness_check`).
- **Security:** `test_core_immutable_from_adaptation` (adaptation never imports kernel internals,
  never mutates `ToolPolicy`); generated `AgentSpec` with `allowed_tools=None` rejected.
- **Testing:** concurrent-turn swap (no mixed snapshot); digest-mismatch refusal; rollback restores
  the previous tool namespace.
- **Failure tests:** connector down mid-turn → tool error, turn completes; corrupted active blueprint
  → core-only fallback.
- **Metrics:** `aura_blueprint_active_version`, swap duration, generated-tool error rate.
- **Exit criteria:** a pilot blueprint deployed and rolled back on the dev stack through the API,
  with audit trail.
- **Rollback:** `rollback()`; or `AURA_ADAPTATION_ENABLED=false` → core-only.
- **Evidence required:** `docs/reports/ap7-blueprint-runtime.md`.
- **As built (2026-10-04):** IMPLEMENTED. It was exercised through the real HTTP API in process,
  and the pointer and live reads were checked on PostgreSQL 16. **The exit criterion's dev-stack
  run is still pending.**
  - **`ActiveBlueprint`** swaps an immutable snapshot of core and generated agents. Generated
    agents use the fixed `system.blueprint_agent` prompt and need `project_data`
    (`AURA_PROJECT_DATA_ROLES`).
  - **`GeneratedReadTool`** runs templates through the new `ReadConnector` port
    (`PostgresConnector.read`, and `OpenApiConnector.read` against an approved `base_url`).
    Tools are tagged `untrusted_content`, and their descriptions carry only our own sentence plus
    a validated identifier. *(Correction, AP8: parameter descriptions still quoted twin labels
    until AP8's `model_surface` check found it; they are now composed the same way.)*
  - **`BlueprintDeployment`:** evaluate → digest-bound approval → approve → deploy (tools, then
    the atomic pointer, then agents) → rollback. Startup falls back to core-only with degraded
    readiness, and `sync()` converges every worker on the pointer.
  - **Until AP8, evaluation was refused** unless `AURA_ADAPTATION_ALLOW_UNGATED_DEPLOY` was set.
    AP8 removed that bypass: every evaluation now runs the gate. A version evaluated under the
    bypass keeps `gate="ungated"`.
  - **Pointer table:** migration `0012`. Activation is one transaction under an advisory lock.

### AP8 — Generated Evaluation Gate and Simulation *(Evaluation)*

- **Objective:** a blueprint version is deployable only if its generated suite passes.
- **Why:** features ship with their tests; "AI generated it" is not evidence.
- **Inputs:** GAP-057; `harness.py`, `RegressionGate`, `AgentRunEvaluator`.
- **Dependencies:** AP6, AP7; AB-5 (M7) for "proven".
- **Implementation areas:** run generated suites (tool correctness against pilot fixtures, agent
  scenarios, permission-denied, out-of-scope, injection) via existing evaluators; **simulation** =
  candidate blueprint executed against a pilot replica with scripted and real providers; results
  attached to the approval request; deploy blocked below gate.
- **Security:** injection suite includes malicious column comments, OpenAPI descriptions and
  document text attempting tool-grant escalation.
- **Testing:** gate unit tests; end-to-end gate on both pilots.
- **Failure tests:** seeded-bad blueprint (wrong join, over-broad grant) blocked.
- **Metrics:** per-blueprint pass rates by suite; deterministic metrics gate; LLM-judge advisory.
- **Exit criteria:** gate blocks a seeded-bad blueprint and passes a good one, recorded in CI.
- **Rollback:** gate advisory by config, recorded in the report.
- **Evidence required:** `docs/reports/ap8-generated-eval-gate.md`.
- **As built (2026-10-05):** IMPLEMENTED. The gate blocks seeded-bad blueprints and passes both
  pilots in the default test suite, so CI records it. The live PostgreSQL replays agree with the
  in-memory replica. **"Proven" waits on M7 (AB-5): a real-model run of the advisory suites.**
  - **`adaptation/gate.py` `BlueprintGate`** materialises the candidate into a throwaway
    `ToolRegistry` under the serving `ToolPolicy`. Every call uses the `ToolContext` the kernel
    builds for that agent, so the gate's verdict is what a live turn would get.
  - **Suites:**
    - `tool_contract`: each tool, called with identifiers the replica supplies, is compared with
      an **oracle read composed from the twin** (the relation's foreign key, the entity's primary
      key, the metric's measure), not from the tool's template.
    - `permission_denied`: refused for the grant's own reason, with a positive control.
    - `grant_scope` (gate-owned): a role agent may hold only its role's permission-map
      capabilities.
    - `injection_refusal`: reading rows taints the turn, and escalation afterwards is refused.
    - `model_surface` (gate-owned): no connected-system text reaches a tool declaration.
    - `out_of_scope`: advisory, `not_run` without a model.
  - **Case outcomes:** a case that cannot run is `not_run`, never a pass. A coverage floor
    (`AURA_ADAPTATION_GATE_MIN_COVERAGE`, 0.8) bounds how many may be skipped.
  - **Seeded-bad blueprints blocked:** a wrong join, a lookup on the wrong key, an aggregate over
    the wrong measure, an over-broad grant (including one the compiler's own suites would miss),
    a grant that refuses everything, and declarations quoting connected-system text.
  - **Lifecycle:** `evaluate` runs the gate. An enforced failure stays `proposed` with its report
    stored; `ap8:advisory` advances with the failure recorded. The approval request carries the
    verdict, and `AURA_ADAPTATION_ALLOW_UNGATED_DEPLOY` is gone.
  - **Simulation:** runs against an in-memory replica (`adapters/connectors/memory.py`) over the
    pilots' recorded rows. Every read the gate sends returns the same rows live on PostgreSQL 16.
  - **Found and fixed:**
    - generated parameter descriptions quoted twin labels, which a hostile labeller could steer;
    - the `tool_contract` threshold was 0.95, which let one wrong tool in 23 through, so it is now
      1.0.

### AP9 — Console Project Factory *(Admin)* — **MVP 1**

- **Objective:** the Super Admin can run the whole loop from the Console.
- **Why:** the Console is the "AI compiler" UI and the authority boundary.
- **Inputs:** GAP-058; GAP-007.
- **Dependencies:** AP7, AP8.
- **Implementation areas:** `aura_admin` area `/console/adaptation` (added to `LINKS` in
  `src/components/shell.tsx`): Connect system · Discovery runs · System understanding (counts) ·
  Twin browser + diff · Capability analysis (recommended / not recommended, with evidence) ·
  Generated agents/tools/workflows/views · Blueprint diff · Evaluation results · Simulate (test chat
  against the candidate) · Approve / Deploy / Roll back · Runtime monitoring.
- **Security:** all routes `admin.super`; typed-value confirm dialog (`confirm-dialog.tsx`) on
  approve/deploy/rollback.
- **Testing:** Playwright with `tests/e2e/mock-gateway.mjs` fixtures.
- **Failure tests:** approval of a stale digest shows refusal.
- **Metrics:** time from connect → approved blueprint per pilot.
- **Exit criteria:** Playwright green on PRs; live Console run for both pilots recorded.
- **Rollback:** hide area behind the adaptation flag.
- **Evidence required:** `docs/reports/ap9-console-mvp.md`.

### AP10 — Files, Documents and Git Connectors; Project Knowledge; Identity Modes *(Discovery & knowledge)*

- **Objective:** complete the v1 connector set and connect project knowledge to RAG.
- **Inputs:** GAP-059.
- **Dependencies:** AP9; **AB-3 (CB-11)**.
- **Implementation areas:**
  1. Files: CSV/Excel/JSON → tabular AUSM entities via the same normalizer; PDF/DOCX/MD/HTML →
     `KnowledgeEngine.ingest` (`knowledge/engine.py:189`) with explicit owner/project sentinel.
  2. Git: ORM models, migrations, route files, API docs → AUSM candidates (code is untrusted
     text); reuses the GitHub client and workspace plumbing (read-only checkout).
  3. Instantiate `CompositeFetcher` (`learning/sources.py:237`) in `container.py` with per-connector
     fetchers.
  4. MySQL and SQL Server discovery adapters behind the same port.
  5. Identity: service-account role → AURA policy mapping (`security.permission_map`); delegated
     OIDC passthrough; both enforced per connector.
- **Security:** isolation probe: a document from one identity is never retrieved for another;
  delegated token scope never widened.
- **Testing:** pilot retrieval eval; cross-identity isolation test; Git discovery goldens.
- **Failure tests:** expired delegated token → refusal with re-auth hint, never a service-account
  fallback.
- **Metrics:** recall@k on pilot docs; delegated-call refusal rate.
- **Exit criteria:** pilot retrieval eval passes; isolation probe clean on Qdrant after CB-11.
- **Rollback:** disable a connector kind by config.
- **Evidence required:** `docs/reports/ap10-connectors-knowledge.md`.

### AP11 — Drift, Impact Analysis, Regeneration; Workflow Runtime v0 *(Lifecycle)*

- **Objective:** continuous adaptation — detect target-system change, explain its impact, propose a
  new version.
- **Inputs:** GAP-060.
- **Dependencies:** AP10.
- **Implementation areas:** Celery beat re-discovery; drift `TriggerSpec`; `adaptation/impact.py`
  maps `TwinDiff` through evidence links to affected tools/agents/views/evals; affected generated
  tools marked `stale` and refused by policy; regenerated candidate lands as `proposed`; workflow
  runtime v0 runs read-only status workflows on the event bus / outbox.
- **Security:** stale tools fail closed.
- **Testing:** column-drop fixture ⇒ exact impact set; stale-refusal test; new-entity fixture ⇒ new
  capabilities proposed.
- **Failure tests:** discovery failure never marks the twin changed.
- **Metrics:** drift detection latency; stale-tool count; time-to-approved-regeneration.
- **Exit criteria:** drift → impact → approved regeneration recorded on a pilot.
- **Rollback:** re-discovery schedule disabled by config.
- **Evidence required:** `docs/reports/ap11-continuous-adaptation.md`.

### AP12 — Reach and Writes *(Surfaces)*

| ID | Scope | Exit criteria |
| --- | --- | --- |
| AP12a | Embeddable web widget + public AI API: scoped API keys per surface, rate limits, CORS allowlist, delegated identity handoff | widget embedded in a pilot app; key-scope and rate-limit tests; report |
| AP12b | Server-driven Flutter: generic `entity_list` / `entity_detail` / `metric_card` / `timeline` in `core/design_system/`, `GET /api/v3/ui/manifest`, dynamic `go_router` branch, tolerant parsing (persona-picker pattern) | `flutter analyze` + `flutter test` counts quoted; manifest renders both pilots; depends on M14 |
| AP12c | `WriteConnector` port; write tools behind argument-bound confirmation and preview; rollback strategy declared per action | depends on SEC-01; write eval suite incl. denied/invalid/valid cases; report |

- **Inputs:** GAP-061. **Evidence required:** `docs/reports/ap12{a,b,c}-*.md`.

### AP13 — Convergence: Project 0, Level-1 Auto-Apply Gate, Level-3 Drafts *(Convergence)*

| ID | Scope | Exit criteria |
| --- | --- | --- |
| AP13.1 | `blueprints/project0.yaml` re-declares today's personas exactly | parity test: blueprint registry == `build_default_agent_registry()` (ids, grants, ceilings, scopes, budgets) |
| AP13.2 | Project 0 active with hardcoded registry as fallback; shadow-compare eval suites for one release | shadow report with no regression |
| AP13.3 | "Native store" connector maps server-readable `PersonalOsRecord` kinds to AUSM; client-encrypted legacy tables stay out of discovery permanently, their tools declared as opaque `native` tools (ADR-028) | generic capabilities serve tasks/events/finance/health records with parity |
| AP13.4 | Life Hub reads the UI manifest for non-encrypted views behind a flag; hardcoded panes remain fallback | client analyze/test counts; flag-off parity |
| AP13.5 | Retire hardcoded coupling: `build_default_agent_registry()` reduced to orchestrator + Engineer | parity + shadow evidence linked |
| AP13.6 | Level-1 auto-apply (config-only, no security/permission/write path) — only after N clean manual cycles | ADR-024 amendment + report of N cycles |
| AP13.7 | Level-3: Engineer agent drafts extension PRs into a separate extension repo; CI required; `test_no_dynamic_code_load` forbids `importlib`/`exec` in `adaptation/` | one drafted connector merged via PR |

- **Inputs:** GAP-062. **Dependencies:** AP7 (AP13.1–13.2 may start then), AP12b (AP13.4).
- **Evidence required:** `docs/reports/ap13-*.md`.

---

## 7. Final Target State

| Dimension | Target (each measured, linked) |
| --- | --- |
| Universality | Two reference pilots in different domains compiled from the **same** capability primitives |
| Understanding | Deterministic twin for SQL, OpenAPI, files and Git sources with provenance on every element |
| Safety | Zero free-form SQL; read-only v1 by construction; discovered text never on SYSTEM channel; injection suite gating |
| Authority | Every live blueprint version has a digest-bound Super Admin approval |
| Lifecycle | Drift → impact → regeneration → approval demonstrated |
| Reach | Console MVP; embeddable widget + API; server-driven Flutter |
| Convergence | Personal AURA running as Project 0 with parity evidence |

## 8. Execution Sequence

```
AP0 ─► AP1 ─► AP2 ─► AP3 ─► AP4 ─► AP5 ─► AP6 ─► AP7 ─► AP8 ─► AP9 (MVP 1)
                    (SEC-01..03 gate)  │      (M7 gate)   │
                                       │                  ├─► AP10 (CB-11 gate) ─► AP11 ─► AP12a
                                       │                  │                          ├─► AP12b (M14 gate)
                                       │                  │                          └─► AP12c (SEC-01 gate)
                                       └─► AP13.1 ─► AP13.2 ─► AP13.3 ─► AP13.4 (after AP12b) ─► AP13.5
                                                                           AP13.6 / AP13.7 after AP11
M0 (CI evidence) gates every A-phase beyond BUILT; Backend CI runs again (green on main since #332,
2026-10-07), but M0 also needs branch protection and Client/Hostd/Admin runs, so it has not exited.   M4 (GPU) gates label quality in AP4, not function.
```

| ID | Name | Priority | Depends on | Complexity | Risk | Current status | Primary components | Exit (short) |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| AP0 | Charter, ADRs, gaps | Critical | — | Low | Low | DOCUMENTED | docs/ | Merged + schema test |
| AP1 | AUSM + Blueprint model | Critical | AP0 | Medium | Low | IMPLEMENTED; CI green on main (Backend CI #347); held at BUILT by M0 | domain/ | Schema + digest tests |
| AP2 | Connectors + vault | Critical | AP1, (SEC-01..03) | Medium | High (secrets) | IMPLEMENTED; CI green on main (#347) + real Postgres 2026-10-11; dev-stack run pending (VPS unreachable) | ports/, adaptation/, api/ | Fixture connector approved |
| AP3 | Discovery PG + OpenAPI | Critical | AP2 | Medium | Medium | IMPLEMENTED; CI green on main (#347) + live Postgres; held at BUILT by M0 | adapters/connectors/, fixtures | Pilot goldens + live run |
| AP4 | Twin + diff | Critical | AP3, (M4) | High | Medium | IMPLEMENTED; CI green on main (#347) + live Postgres; held at BUILT by M0 | adaptation/normalize, twin | Deterministic twins + diffs |
| AP5 | Capabilities + planner | High | AP4 | Medium | Medium | IMPLEMENTED; CI green on main (#347); held at BUILT by M0 | adaptation/capabilities | Same primitives, both pilots |
| AP6 | Factories + compiler | Critical | AP5 | High | High (SQL safety) | IMPLEMENTED; CI green on main (#347) + live Postgres; held at BUILT by M0 | adaptation/factories, domain/tools | Valid blueprints + fitness |
| AP7 | Blueprint runtime | Critical | AP6, (SEC-01..03) | High | High (kernel seam) | IMPLEMENTED; CI green on main (#347) + live Postgres; dev-stack run pending (VPS unreachable) | adaptation/runtime, container | Deploy + rollback |
| AP8 | Generated eval gate | High | AP6, AP7, (M7) | Medium | Medium | IMPLEMENTED; CI green on main (#347) + live Postgres; live Flame run at default coverage (Railway); M7 for proven | adaptation/gate, adapters/connectors/memory | Bad blueprint blocked |
| AP9 | Console MVP | High | AP7, AP8 | Medium | Low | DOCUMENTED | aura_admin | Both pilots via Console |
| AP10 | Files/Git + knowledge + identity | High | AP9, (CB-11) | High | High (isolation) | IN PROGRESS (CB-11 fixed; files connector: tables pass the gate, documents feed project knowledge; Git connector over a checkout with goldens; CompositeFetcher wired with connector-backed refresh sources; MySQL/SQL Server discovery with live-server goldens, reads not yet; identity modes enforced per connector (delegated passthrough, role bindings), no client sends a delegated token yet; Excel/DOCX open) | adapters/connectors, knowledge | Retrieval eval + isolation probe |
| AP11 | Drift + impact | Medium | AP10 | Medium | Medium | DOCUMENTED | adaptation/impact, triggers | Drift → approved regeneration |
| AP12 | Reach + writes | Medium | AP11, (M14), (SEC-01) | High | High (writes) | DOCUMENTED | api/, aura_client, connectors | Three sub-reports |
| AP13 | Convergence / Project 0 | Medium | AP7 (start), AP12b | High | High (live app) | DOCUMENTED | agents/, blueprints/, aura_client | Parity + shadow evidence |

**Critical path (MVP 1):** AP0 → AP1 → AP2 → AP3 → AP4 → AP5 → AP6 → AP7 → AP8 → AP9.
**High-risk path:** AP6 (query safety) → AP7 (kernel seam) → AP12c (writes).
**Parallel lanes:** AP13.1–13.2 after AP7 · AP12a beside AP11 · Console pages incrementally from AP2.
**Not in this track (deliberately):** multi-project or multi-org tenancy (would amend ADR-019);
capability marketplace distribution (built on the AP5 registry format, later); AI-generated code
hot-loaded at runtime (forbidden, ADR-018); hosted inference for compile-time analysis (forbidden,
ADR-026).

## 9. Definition of Done (Track AP)

- [ ] ADR-018…028 Accepted; gap and backlog entries tracked (AP0)
- [ ] AUSM and Blueprint schemas published with digest semantics tested (AP1)
- [ ] Connectors approved via Super Admin; secrets fitness + redaction tests green (AP2)
- [ ] Both pilots discovered deterministically, twinned and diffed on CPU-only CI (AP3, AP4)
- [ ] Both pilots planned from the same domain-agnostic primitives with 100% evidence (AP5)
- [ ] No free-form SQL (fitness) and AST fuzz tests green; tool output schemas in place (AP6)
- [ ] Digest-bound approve → deploy → rollback demonstrated without kernel changes (AP7)
- [ ] Generated eval gate blocks a bad blueprint (AP8); proven once M7 lands
- [ ] Console MVP run end to end for both pilots (AP9)
- [ ] Project knowledge isolated after CB-11; identity modes tested (AP10)
- [ ] Drift → impact → regeneration recorded (AP11)
- [ ] Widget/API, server-driven Flutter and confirmed writes each with a report (AP12)
- [ ] Project 0 parity + shadow evidence; hardcoded coupling retired (AP13)

## 10. Glossary (proposal term → module)

| Proposal term | Here |
| --- | --- |
| Adaptive Intelligence Layer (AIL) / Adaptation Engine | `aura_core/adaptation/` |
| AURA Universal Semantic Model (AUSM) | `aura_core/domain/ausm.py` (ADR-020) |
| Project Digital Twin | `ProjectTwin` + `TwinDiff`, `adaptation/twin.py` |
| Capability Selection Engine / Capability Registry | `adaptation/capabilities/`, `planner.py` (ADR-023) |
| Project Blueprint | `aura_core/domain/blueprint.py` (ADR-024) |
| Tool / Agent / Workflow / UI / Evaluation Factory | `adaptation/factories/` |
| Connector SDK | `aura_core/ports/connectors.py` + `adapters/connectors/` (ADR-021) |
| UI Capability Manifest | blueprint `ui.manifest` (ADR-027) |
| Deployment Manager | `adaptation/deployment.py` (ADR-024) |
| Business Process Miner | process mining v0 in AP4, workflow runtime v0 in AP11 |
| AURA Console / Project Factory | `aura_admin` `/console/adaptation` (AP9) |
