# ADR-024 — Project Blueprint lifecycle: propose, evaluate, approve, deploy, roll back

**Status:** Proposed · **Date:** 2026-10-01 · **Track:** [AP](../roadmaps/AURA_ADAPTIVE_PLATFORM_ROADMAP.md)

## Context

The Project Blueprint is the compiled representation of a connected system: data model, security,
capabilities, tools, agents, workflows, knowledge, UI manifest, integrations, policies and
evaluation. It changes what AURA can do on a live system, so its activation must be as controlled
as a model or index promotion (ADR-007, ADR-008).

## Decision

1. Blueprint versions move through `proposed → evaluated → approved → active → superseded`
   (or `rejected`). `blueprint_digest()` is SHA-256 over canonical JSON.
2. **Approval binds to the digest** (`ApprovalKind.BLUEPRINT_DEPLOYMENT` in `ApprovalService`);
   deployment is refused if the digest changed after approval.
3. **Nothing auto-applies in v1.** Every version, including configuration-only changes, needs a
   Super Admin approval. Level-1 auto-apply may be introduced only by amending this ADR, after a
   report showing N clean manual cycles.
4. **Deployment mirrors `IndexManager`:** one active-version pointer updated atomically; a gate
   fed by the generated evaluation suite; `rollback(version)` repoints.
5. **Runtime swap without kernel changes:** an `ActiveBlueprint` holder implements the
   `AgentRegistry` read interface over an immutable snapshot passed as `KernelDependencies.agents`.
   Generated tools are registered as `bp<version>_<name>` (`generated_tool_name()`; tool names are
   lower-case snake_case and providers cap them at 64 characters) before the swap and removed after a grace
   period. In-flight turns keep what they resolved.
6. Generated `AgentSpec`s must have an explicit `allowed_tools`; the default risk ceiling is PUBLIC,
   and anything above PERSONAL is listed separately in the approval request.
7. If the active blueprint fails to load or validate at startup, AURA runs core-only and reports
   degraded readiness.

## Alternatives rejected

- **Mutating registries in place.** Turns could see a mixed snapshot, and there is no clean
  rollback.
- **Auto-deploying anything that passes evaluation.** Generated tests are not yet proven
  trustworthy enough to replace the authority boundary.

## Consequences

- Every behaviour change on a live project has an approver, a digest and a revert path.
- Throughput of change is bounded by Super Admin review; that is intentional in v1.

## Implementation notes (AP7, 2026-10-04)

- **Lifecycle:** evaluate → request approval (`BLUEPRINT_DEPLOYMENT`, bound to the digest) →
  approve → deploy → rollback.
  - Every transition is a compare-and-set on the stored state.
  - A deploy registers `bp<version>_*` tools, then moves the pointer, the target's state and the
    predecessor's state in one transaction (migration `0012`), then swaps the agent snapshot.
    Old tools are retired after a grace period.
- **Rollback** returns to a superseded version under its own approval. The approval's window may
  have closed, but its status and digest must still match.
- **Until AP8's gate exists**, `evaluate` refuses unless `AURA_ADAPTATION_ALLOW_UNGATED_DEPLOY` is
  set, and the version records `gate="ungated"`. Nothing auto-applies.
- **Startup** loads the active version or falls back to core-only with degraded readiness. Each
  gateway worker converges on the pointer through a periodic `sync()`.
