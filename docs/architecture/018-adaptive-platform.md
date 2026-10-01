# ADR-018 — Adaptive platform: immutable core, declarative adaptation layer

**Status:** Proposed · **Date:** 2026-10-01 · **Track:** [AP](../roadmaps/AURA_ADAPTIVE_PLATFORM_ROADMAP.md)

## Context

AURA is moving from a personal AI platform with hardcoded domains (tasks, calendar, finance,
health) and hardcoded personas (`build_default_agent_registry()`, `aura_core/agents/registry.py:132`)
to a platform that attaches intelligence to *existing* software systems. The temptation is to let
generated, project-specific logic reach into the kernel. That would erase the properties ADR-001
(hexagonal core) and ADR-006 (one execution path; retrieved content is data) exist to protect.

## Decision

1. A new engine package, `aura_core/adaptation/` (the **Adaptive Intelligence Layer**), owns
   discovery, the semantic model, the project twin, capability planning, factories, the blueprint
   compiler, deployment and drift. It is listed in `ENGINE_PACKAGES` and obeys the same layering
   fitness tests as every other engine.
2. The layer **emits only declarative data** — blueprints, `AgentSpec`s, `ToolSpec`s with
   `QueryTemplate`s, workflow specs, UI manifests, evaluation suites. Existing core mechanisms
   (`AgentRegistry` read interface, `ToolRegistry.execute`, `ToolPolicy`, the kernel) execute it.
3. The layer never imports kernel internals beyond public types, never mutates `ToolPolicy`, and
   never loads code (`importlib`, `exec`, dynamic module import) — enforced by
   `test_core_immutable_from_adaptation` and `test_no_dynamic_code_load`.
4. Customization has three levels: **Configuration** and **Composition** are blueprint content;
   **Extension** (new connectors, processors, algorithms) is human-owned code. The Engineer agent
   may *draft* an extension, which lands only through a reviewed PR and CI in the extension's own
   repository — never hot-loaded at runtime.

## Alternatives rejected

- **Per-project forks or project-specific code generation into `aura_core`.** N codebases, no
  shared security guarantees.
- **Runtime-generated, sandbox-executed extension code.** Largest attack surface; the Docker
  sandbox protects the host, not the correctness or authority of what runs.
- **A plugin system with dynamic imports.** Bypasses review and the layering fitness tests.

## Consequences

- Every adaptive feature is inspectable data with a digest, diffable between versions and
  revertible by pointer.
- New *kinds* of capability still need human-written primitives (AP5) — the layer composes, it does
  not invent.
