# ADR-027 — UI Capability Manifest and staged surfaces

**Status:** Proposed · **Date:** 2026-10-01 · **Track:** [AP](../roadmaps/AURA_ADAPTIVE_PLATFORM_ROADMAP.md)

## Context

Flutter routes (`aura_client/lib/core/routing/router.dart`) and the Life Hub panes are hardcoded,
and there are no generic entity widgets. The only backend-driven UI is the persona picker, which
parses `/api/v3/agents` tolerantly. Onboarding a project must not require client code changes.

## Decision

1. The blueprint's `ui.manifest` declares navigation and views from a closed vocabulary:
   `entity_list`, `entity_detail`, `metric_card`, `timeline`, `search`, `chat`, `approval_queue`
   (read-only in v1). Each view is bound to tool ids and their `output_schema` (ADR-025).
2. Clients render only known view types. Unknown types are skipped with a placeholder, following
   the persona-picker tolerant-parsing pattern; a manifest is never executable.
3. **Surfaces are staged:**
   - Console Project Factory (AP9).
   - Embeddable web widget plus a public AI API with scoped keys, rate limits and a CORS allowlist
     (AP12a).
   - Server-driven Flutter via `GET /api/v3/ui/manifest` and generic widgets in
     `core/design_system/` (AP12b).
4. The manifest is versioned with the blueprint and rolls back with it.

## Alternatives rejected

- **Generated Flutter code per project.** Rebuilds and app-store releases per change.
- **An arbitrary layout DSL.** Too much expressive power, and a new injection surface.

## Consequences

- Rich, domain-specific screens still need human-built view types. The vocabulary grows by
  reviewed change.
