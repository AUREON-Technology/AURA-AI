# ADR-023 — Capability Registry and evidence-based planning

**Status:** Proposed · **Date:** 2026-10-01 · **Track:** [AP](../roadmaps/AURA_ADAPTIVE_PLATFORM_ROADMAP.md)

## Context

A universal platform cannot ship `HRAgent`, `SalesAgent` and `FactoryAgent`, nor branch on
`if project_type == "HR"`. It needs reusable primitives and a way to decide, from the connected
system itself, which ones apply.

## Decision

1. **Capabilities are data:** `aura_core/adaptation/capabilities/*.yaml` declares primitives
   (`entity.lookup`, `entity.search`, `entity.list_filtered`, `relation.traverse`,
   `metric.aggregate`, `state.timeline`, `workflow.status`, `document.qa`, …). Each has
   `requires` (predicates over AUSM), `produces` (tool, agent, UI and eval templates), `risk` and
   `cost`.
2. **Planning is predicate matching plus scoring** (relevance, risk, cost) over the twin. Every
   selected capability carries `evidence` — the AUSM ids that satisfied it. Rejected candidates are
   reported with reasons.
3. Agents are **composed**: capabilities are clustered by entity neighbourhood and role into agent
   proposals.
4. Capability files are domain-agnostic, enforced by `test_capabilities_are_domain_agnostic`
   (a domain-noun denylist).
5. The registry format is the future basis for a capability marketplace (not in Track AP scope).

## Alternatives rejected

- **Industry templates.** Brittle and unevidenced; they fail on systems that don't fit the
  template.
- **The LLM picks capabilities.** Not reproducible, and it cannot cite evidence.

## Consequences

- The planner can only propose what primitives exist; adding a primitive is a human-written,
  reviewed change.
- Evidence links make drift impact computable (AP11).
