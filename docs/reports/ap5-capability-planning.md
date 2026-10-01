# AP5: Capability Registry and Planner (evidence)

**Phase:** AP5 ([Track AP roadmap](../roadmaps/AURA_ADAPTIVE_PLATFORM_ROADMAP.md)) · **Gap:**
GAP-053 · **ADR:** 023 · **Date:** 2026-10-01 · **Base:** `4a0ab4f` (main)

**Maturity reached:** IMPLEMENTED. Both pilots are planned from the **same** eleven primitives,
with registry digest `sha256:77d92ccc0881…` identical on both plans (asserted by test). Every selection
cites AUSM evidence that exists in its twin.

**Still open:** Backend CI green on `main` with these tests. That needs the CI baseline fixes
noted in the AP1 report.

## What was built

- **`aura_core/domain/capabilities.py`** is stdlib only. It defines:
  - `CapabilitySpec` (`target`, `target_filter`, `requires`, `risk`, `cost`, `relevance`,
    `min_confidence`, `produces`, and `available`/`unavailable_reason`);
  - `Requirement`, with relation `child`, `referenced_by` or `ref` to the target;
  - `ElementFilter`, `PlannedCapability`, `RejectedCapability`, `AgentProposal` and
    `CapabilityPlan`.
- **`aura_core/adaptation/capabilities/*.yaml`** holds the eleven primitives, one file each,
  named after the id:
  - entity primitives: `entity.lookup`, `entity.search`, `entity.list_filtered`;
  - structure and data: `relation.traverse`, `metric.aggregate`, `state.timeline`,
    `workflow.status`;
  - content and output: `document.qa`, `notification.draft` (a draft only; a person sends it);
  - API operations: `operation.read`, and `operation.write`, which is declared *unavailable*
    with reason `writes_deferred` (ADR-021) so the plan can say why each write action was not
    recommended.

  They are loaded through the strict codec, so an unknown key such as `execute:` is refused.
- **`aura_core/adaptation/planner.py`**:
  - **`CapabilityRegistry`** holds the primitives. Its digest pins the primitive set a plan was
    made from.
  - **`plan_capabilities`** is pure and deterministic:
    1. Collect candidates by target kind and filter.
    2. Check the requirements. Matches below `min_confidence` do not count, so a proposal or a
       weak inference cannot carry a capability alone.
    3. Score each candidate: relevance × the weakest evidence confidence, minus small cost and
       risk penalties.
    4. Select candidates at or above a 0.3 threshold.

    Every rejection carries reason codes:
    - `no_candidates`;
    - `unmet:<requirement>`;
    - `low_confidence`;
    - `below_threshold`;
    - `unavailable:<reason>`.
  - **Agent composition**, from selections only:
    - one `agent.project` agent;
    - one `agent.role.<role>` agent per source-system ROLE with a read PERMISSION. It covers the
      entities that role can read, extended through `same_as` proposals to the same entity in
      another source. Its evidence names those PERMISSIONs and proposals.

    An agent's risk ceiling is its riskiest capability.
  - **`PlanningService`** plans a stored twin version. Plans are recomputed, not stored, because
    the same twin and registry always give the same plan.
- **Security:** a capability's `risk` is the selection's sensitivity floor. AP6's tool factory
  takes it as the generated tool's minimum `ToolSensitivity`.
- **HTTP:** `GET /api/v3/admin/twin/versions/{version}/plan`, Super Admin only, returning 503
  when Track AP is off.
- **Metrics:** `aura_capability_plan_selected{capability}` (bounded by the registry) and
  `aura_capability_plan_evidence` (evidence per selection).
- **Goldens:** `tests/fixtures/pilots/{hr,ecommerce}/golden/plan.json`, rendered by
  `scripts/pilot_twin_goldens.py`.

## Cross-domain report

The same registry was used on both pilots. Each cell gives the number selected, then the
rejections by reason.

| Primitive | hr | ecommerce |
| --- | --- | --- |
| entity.lookup | 8 · unmet:identifier ×2 | 9 · unmet:identifier ×4 |
| entity.search | 9 · unmet:searchable ×1 | 12 · unmet:searchable ×1 |
| entity.list_filtered | 8 · unmet:filterable ×2 | 10 · unmet:filterable ×3 |
| relation.traverse | 7 | 7 |
| metric.aggregate | 2 | 11 |
| state.timeline | 2 · unmet:events ×4, below_threshold ×4 | 6 · unmet:events ×7 |
| workflow.status | 2 · below_threshold ×2 | 2 |
| operation.read | 4 | 4 |
| notification.draft | 0 · unmet:contact ×7, unmet:events ×4, below_threshold ×2 | 1 (customers) · unmet:contact ×11, unmet:events ×7 |
| operation.write | 0 · unavailable:writes_deferred ×4 | 0 · unavailable:writes_deferred ×2 |
| document.qa | 0 · no_candidates | 0 · no_candidates |
| **Total** | **42 selected, 30 rejected** | **62 selected, 30 rejected** |

Evidence per selection is at least 2 on both pilots (the target plus what satisfied each
requirement); the mean is 3.29 (hr) and 2.68 (ecommerce).

How the differences between the two pilots fall out, without any domain knowledge:

- **E-commerce has eleven measures; HR has two.** Prices, quantities and totals are numeric
  measures. HR's salary is one; HR's other is a misfire, covered under residual risks.
- **`workflow.status`:** HR's `employees.status` workflow is rejected `below_threshold`. Nothing
  but declared order supports its transitions (process confidence 0.4). Leave requests are
  selected: the `approve` action is evidence.
- **`notification.draft`** is selected exactly once: on `shop.customers`, the one entity with
  both a contact address and timestamped events. HR's employees have an address, but only a
  date event, so it falls below the threshold.

Agents composed:

| Pilot | Agent | Capabilities | Risk ceiling |
| --- | --- | --- | --- |
| hr | agent.project | 42 | personal |
| hr | agent.role.hr_manager | 30 (employees, departments, leave requests and their API schemas) | personal |
| hr | agent.role.hr_viewer | 38 | personal |
| ecommerce | agent.project | 62 | confidential |
| ecommerce | agent.role.shop_ops | 29 | personal |
| ecommerce | agent.role.shop_support | 34 | confidential (can read customers) |

## Verification

```
cd aura_backend
pytest                                   # 3138 passed, 56 skipped (main baseline: 3109)
TESTING=True python -m unittest discover -p "test_*.py"   # Ran 100, OK (skipped=4)
```

The 29 new tests:

- **`tests/unit/test_adaptation_planner.py` (27):**
  - golden plans;
  - same registry for both pilots;
  - 100% of selections evidenced, with the evidence existing in the twin;
  - determinism;
  - requirement and reason codes;
  - the risk floor;
  - weakened inferences failing to carry a capability;
  - agent composition through grants and `same_as`;
  - empty-twin and nothing-readable failure cases;
  - registry strictness (unknown keys, file naming, duplicate ids);
  - the service, and the HTTP route.
- **`tests/architecture/test_capabilities_domain_agnostic.py`:** a domain-noun denylist over the
  capability files and `planner.py`. On its first run it caught "in time order" in a summary,
  because "order" is a commerce noun. The summary was reworded.
- One more route-auth case, enumerated from the new route.

No live-infrastructure check applies: planning is pure over a stored twin, and AP4's live test
already covers storing and reading twins on Postgres.

## Residual risks, stated

- **Inferred measures can be wrong.** The HR API's `approvedBy` is an integer with no `$ref` and
  no `…Id` suffix, so normalisation calls it a measure, and `metric.aggregate` selects it at
  confidence 0.5. The database side knows the same column is a foreign key. Using `same_as`
  proposals to correct attribute semantics across sources is a candidate for a later pass. Until
  then, AP9's review is the safeguard.
- **Request-body schemas** (`NewEmployee`, `NewReturn`) are ENTITY elements, so they receive
  entity capabilities. AP6's tool factory needs a matching read operation to make a tool, so no
  tool can result without one, but the plan lists them.
- **Scores are a fixed formula, not learned.** Relevance values in the capability files are
  human judgements, and two were raised after the first pilot run: `metric.aggregate` to 0.85,
  because measures are inferred at confidence 0.5, and `notification.draft` to 0.6. Both changes
  are in the reviewed YAML, not in code.
