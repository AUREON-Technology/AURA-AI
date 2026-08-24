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

## Update — 2026-08-24 (Phase B1/B3)

`aura_backend/main.py` — the legacy v1/v2 standalone app decision 3 describes above — is deleted.
It was never what production ran: `aura_service.py` is the sole entry point (`Dockerfile`'s `CMD`,
enforced by `tests/architecture/test_dockerfile_and_observability.py`), and never imported
`main.py`. Its removal took `api/v2/chat.py` with it — that router had exactly one caller.

**B3's question — fold `api/v2/{auth,sync,insights}` into `/api/v3`, or document the boundary as
permanent — turns out to have a narrower answer than it looked.** By URL, they already are
`/api/v3`: every one of `api/v2/auth.py`, `api/v2/sync.py`, `api/v2/insights.py` declares
`APIRouter(prefix="/api/v3/...")`. There is no live `/api/v2/*` surface left to fold in; the
`v2` in the directory path is a stale name from before the routers were repointed, not a live
API version. **Decision:** the architectural boundary these three modules sit behind — direct
SQLAlchemy against `infrastructure/db/models.py`, outside `aura_core`'s hexagonal ports, per this
ADR's rule 1 — is **permanent**, for the same reason decision 1 gives: `api/v2/sync.py`'s cursor
queries are correct and efficient as written, and rewriting them into a port/adapter would be
churn, not improvement. They are not "legacy code awaiting removal"; they are the platform-adapter
layer ADR-002 always intended `aura_service.py` to mount alongside the kernel. Phase C's new
endpoints go through `aura_core`'s tool/kernel path, not this layer.

Renaming `api/v2/` to something that does not claim legacy status (`api/platform/`, say) is a
correct, low-risk cleanup — five files import `api.v2.*` — but cosmetic and not gating; left as
optional follow-up rather than done here, so this update stays a decision record, not a refactor.
