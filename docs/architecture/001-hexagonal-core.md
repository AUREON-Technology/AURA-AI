# ADR-001 — Hexagonal core with a stdlib-only domain

**Status:** Accepted · **Date:** 2026-08-02

## Context

The existing backend resolves every dependency by importing a module-level singleton that connects to
the network at import time (`services/rag.py:287`, and five more — see TD-01). Four modules import
`google.genai` directly. Three modules reach into `RAGService`'s private methods. The result: the
process cannot start without Qdrant reachable, unit tests must monkey-patch globals, and changing the
embedding backend is a four-module edit.

## Decision

`aura_core` is a hexagonal (ports-and-adapters) package.

- `aura_core/domain/` — entities, value objects, domain errors, domain events. **Imports only the
  Python standard library.**
- `aura_core/ports/` — `typing.Protocol` interfaces for everything outside the process: LLM
  providers, embeddings, vector store, memory store, document store, graph store, cache, event bus,
  tools, clock, audit sink. **Imports only the standard library and `domain`.**
- Engines depend on `domain` + `ports` only.
- `aura_core/adapters/` and `aura_core/llm/providers/` hold every vendor SDK import, each behind a
  lazy import inside the constructor so an unused adapter never has to be installed.
- `aura_core/container.py` wires concrete adapters to ports at startup, from typed settings.

The rule is enforced by `tests/architecture/test_dependency_rules.py`, which walks the AST of every
module in `domain/` and `ports/` and fails on any non-stdlib import, and fails if any engine imports
an adapter.

## Alternatives rejected

| Alternative | Why rejected |
| --- | --- |
| Keep service singletons, add interfaces later | Interfaces added after the fact get shaped by the existing implementation and change nothing. The singleton import-time connection is the actual defect. |
| Full DDD with aggregates, repositories, and a unit of work for everything | Over-engineering for a system whose transactional surface is small. DDD is applied where there are real invariants (memory consolidation, plan state, index versioning) and not elsewhere. |
| Framework-provided DI (`fastapi.Depends` everywhere) | Couples the AI core to FastAPI, so the same kernel cannot be used from a Celery worker, a CLI, or a test without a web request. Celery workers need the kernel — this is not hypothetical. |

## Consequences

**Positive**
- The entire core is unit-testable on bare CPython with no Docker, no network, no Qdrant. This is why
  the test suite in this repository actually runs.
- Swapping Gemini → vLLM → Anthropic is a settings change.
- Two configurations can coexist in one process (e.g. a cheap tier and a quality tier).
- Celery workers, the HTTP gateway, and the CLI share one kernel instance construction path.

**Negative**
- More files and one extra level of indirection. A developer chasing a call goes port → adapter.
  Mitigated by keeping ports small and naming adapters after their technology (`QdrantVectorStore`).
- Protocols are structural, so a typo in a method name is caught by mypy, not at import. Mitigated by
  `mypy --strict` on `aura_core` in CI.

**Impact**

| Dimension | Effect |
| --- | --- |
| Scalability | Enables it — adapters can be swapped for clustered/managed equivalents without touching policy. |
| Maintainability | Large improvement; the blast radius of an infrastructure change is one adapter. |
| Security | Improves — guards and policy live in the core and cannot be bypassed by an adapter. |
| Cost | Slightly more code to maintain; substantially less cost to change providers when pricing shifts. |
