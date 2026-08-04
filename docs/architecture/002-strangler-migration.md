# ADR-002 — Strangler-fig migration, not a rewrite

**Status:** Accepted · **Date:** 2026-08-02

## Context

`aura_backend` has 22 v1 routes and 7 v2 routes in production use by a shipped Flutter client
(`aura_client` v3.0.1). The v2 cursor-sync protocol and the Postgres schema are well designed and
should be kept. The AI layer must be replaced wholesale.

Rewriting everything at once would break a shipped client and provide no intermediate value.

## Decision

1. `aura_core` is a **new package** inside `aura_backend`. Nothing in `aura_core` imports anything
   from `aura_backend/services/` or `aura_backend/api/`. The dependency is one-directional and
   enforced by test.
2. A new **`/api/v3`** gateway exposes the kernel. v1 and v2 continue to work unchanged.
3. `main.py` mounts the v3 router and the health/metrics routers; the legacy app is otherwise
   untouched **except** for the one security fix that cannot wait (SEC-01, the unauthenticated Ollama
   proxy — see ADR note below).
4. Migration order for the client: chat → memory → knowledge → admin. Each is independently
   switchable via a client feature flag.
5. When a v1 route's v3 replacement has been live for one release, the v1 route is deleted. The
   removal checklist lives in `docs/guides/migration-guide.md`.

### The one in-place change

`/api/pull`, `/api/delete`, `/api/chat`, `/api/tags` (`main.py:470-553`) are removed rather than
deprecated. They accept unauthenticated requests that download and delete model weights on the host.
A deprecation window for an open door is not a trade-off worth making. The Flutter client's local
model management moves to the authenticated `/api/v3/admin/models` endpoints.

## Alternatives rejected

| Alternative | Why rejected |
| --- | --- |
| Big-bang rewrite of `main.py` | Breaks the shipped client; no incremental value; all risk lands in one deploy. |
| Refactor `services/` in place | Every module has import-time side effects and cross-module private access; incremental refactoring would need to keep all six singletons alive during the transition — more work than building clean and switching. |
| New service in a separate repository | Splits deployment, configuration, and auth for a system that runs on one VPS. Premature. |

## Consequences

**Positive** — no downtime, no client breakage, each phase independently valuable, easy rollback
(stop routing to v3).

**Negative** — two AI paths coexist for a period; `services/rag.py` and `aura_core/knowledge/` both
write to Qdrant. Mitigated by giving v3 its own versioned indexes (ADR-007), so the two never share
a collection and the old data can be reindexed on a schedule rather than migrated in a big step.

**Impact:** maintainability temporarily worse (duplication), risk substantially lower, cost of the
transition spread over releases.
