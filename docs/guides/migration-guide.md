# Migration Guide

Moving from the v1/v2 application to `aura_core` and `/api/v3`, and the checklist for eventually
removing the legacy tree.

The approach is a **strangler fig** ([ADR-002](../architecture/002-strangler-migration.md)): the
new core is built alongside the old application, traffic moves route by route, and the old code
is deleted only when nothing calls it. There is no cut-over date and no big-bang rewrite branch.

---

## Why not a rewrite branch

A rewrite branch has to reach feature parity before it can ship, which means it is unreviewable
by the time it is mergeable, and every day it exists it drifts from a codebase that is still
being changed. The shipped Flutter client also holds tokens issued by the v2 auth path — a
cut-over would log every user out on deploy.

The strangler approach costs something real: two code paths coexist, and the legacy tree keeps
its known defects until its routes are removed. That is the trade accepted, and this guide is
what makes it finite rather than permanent.

---

## Current state

```
aura_service:app  (aura_backend/aura_service.py)
   │
   ├── build_app()            aura_core, the v3-only kernel routes + health/metrics
   └── _mount_platform_routers()
         api.v2.auth, api.v2.sync, api.v2.insights, automation, learning, admin_platform
```

`aura_service.py` is what production runs, and is the *only* thing production runs.
`aura_backend/main.py` — the legacy v1/v2 standalone app — has been deleted (Phase B, this guide's
own removal checklist below covered it first). `aura_service.create_app()` builds the container,
resolves an authenticator, mounts the v3 kernel routes and the surviving `api/v2/*` platform
routers directly, and adds split health endpoints. It never imported or mounted `main.py`, even
before the file was removed — `tests/architecture/test_dockerfile_and_observability.py` already
asserted `main:app` never appears in the Dockerfile's `CMD`.

**The dependency is one-directional and enforced.** `aura_core` never imports from `services/` or
`api/`; `tests/architecture/test_dependency_rules.py` inspects imports with the AST and fails the
build if it does. The one place the two meet is `aura_service._build_authenticator`, which
imports `services.jwt_auth.verify_access_token` **lazily and by name** so v3 accepts the tokens
the shipped client already holds.

That function is the entire coupling surface. Replacing the identity provider later touches it
and nothing else.

---

## What has already moved

| Capability | Legacy | v3 | Notes |
| --- | --- | --- | --- |
| Chat | `POST /api/chat`, `/api/chat/agent` | `POST /api/v3/chat` | Typed SSE frames replace raw text with inline markers (TD-12) |
| Retrieval | `services/rag.py` | `KnowledgeEngine` | Hybrid + reranked + score floor; honest empty results |
| Memory | `services/memory.py` | `MemoryEngine` | Six kinds, decay, provenance, consolidation |
| Tools | `agent.py:281` keyword dispatch | `ToolRegistry` | Capability gates, schema validation, timeouts, audit |
| Model management | `main.py:470–553` **(unauthenticated)** | `POST /api/v3/admin/models/*` | **Removed outright**, not deprecated — see below |
| Health | `GET /health` | `/health/live`, `/health/ready` | Liveness and readiness are different questions |

### The one breaking change

Four routes were **deleted**, not deprecated:

```
GET  /api/tags       POST /api/chat      (the Ollama proxy, not the assistant route)
POST /api/pull       POST /api/delete
```

None declared authentication (SEC-01). `POST /api/pull` let an unauthenticated caller make the
server download arbitrary multi-gigabyte model weights until the disk filled — model weights are
executable artefacts from a third-party registry, so this was a supply-chain vector, and the only
mitigation was a 100 req/min limit, which is 100 model pulls a minute.

A deprecation window would have meant leaving that reachable for a release cycle. The equivalent
functionality is `POST /api/v3/admin/models/*`: admin role, allowlisted model identifiers,
audited.

```bash
# Verify on any deployment
curl -s -o /dev/null -w '%{http_code}\n' https://$AURA_DOMAIN/api/pull   # expect 404
```

---

## Migrating a client

### 1. Point chat at v3

The token you already hold works — v3 verifies it through the same code path.

```diff
- POST /api/chat          { "message": "…" }
+ POST /api/v3/chat       { "message": "…", "session_id": "…" }
```

The response is a superset of the old one plus `citations`, `confidence`, `needs_confirmation`,
`degraded`, and `usage`. Existing fields keep their meaning.

### 2. Switch the stream to typed frames

The v1 stream is `text/plain` with markers embedded in the token text, so a client has to
string-split to find them. v3 emits typed JSON frames:

```diff
- if (chunk.startsWith('[[CITATIONS]]')) { … }
+ switch (frame['type']) {
+   case 'citations': renderSources(frame['citations']); break;
+   case 'token':     append(frame['text']); break;
+   case 'revision':  replace(frame['text']); break;   // redacted after streaming
+   case 'done':      finish(); break;
+   case 'error':     showError(frame['message'], frame['retryable']); break;
+ }
```

Handle `revision` even if it seems rare. It means an output guard caught something *after* the
text was displayed, and ignoring it leaves unredacted content on screen.

### 3. Handle `needs_confirmation`

New behaviour with no v1 equivalent. When present, a side-effecting tool was selected in a turn
whose context contained quarantined content, and it **did not run**. Prompt the user, then resend
with the tool named in `confirmed_tools`.

Auto-confirming defeats the control that breaks the prompt-injection chain
([ADR-006](../architecture/006-safety-pipeline.md)). If a deployment genuinely wants tools to run
unconfirmed, that is a policy change to make explicitly in `tools/policy.py`, not a client
shortcut.

### 4. Render an empty retrieval honestly

`searched_but_empty: true` with no results means the corpus was searched and nothing cleared the
relevance floor. The legacy pipeline had no floor and always returned `limit` results, so a
client written against it has never seen this case — and rendering it as an error trains users to
distrust a correct answer.

### 5. Move health checks to `/health/ready`

`/health/live` reports only that the process is alive. Gating a load balancer on it means traffic
keeps arriving during a dependency outage; gating a *liveness* probe on dependencies means the
orchestrator restarts a healthy process during one.

---

## Running v3 standalone

`aura_service.create_app()` takes no arguments — there is no legacy app left to opt out of, so
this is simply what running the gateway *is* now:

```python
from aura_service import create_app
app = create_app()
```

It still mounts the surviving `api/v2/{auth,sync,insights}` platform routers (see "What has not
moved, and why" below) and still authenticates through `services.jwt_auth` by default — that
coupling did not go away with `main.py`. A deployment that wants an authenticator not backed by
`services.jwt_auth` configures one in `_build_authenticator`; the container raises
`ConfigurationError` naming the problem rather than starting without authentication.

---

## Removal checklist

The legacy tree can be deleted when every item is true. Each is verifiable rather than a
judgement call.

- [ ] **No v1/v2 route has received a request in 30 days.** Check the access log or
      `aura_http_requests_total` by path. This is the only item that requires waiting.
- [ ] **The shipped mobile client's minimum supported version targets v3.** Older installs exist
      in the field; deleting v1 strands them.
- [ ] **Authentication no longer routes through `services/jwt_auth`.** Replace
      `_build_authenticator` with a direct implementation. Until then, deleting `services/` breaks
      v3.
- [x] **`api/v2/sync.py` *is* the v3 sync endpoint.** Despite its directory, its router is mounted
      at `/api/v3/sync` (`router = APIRouter(prefix="/api/v3", ...)`), and `SYNCABLE_MODELS`
      already covers tasks, calendar events, finance, health, messages, and memories. What
      remains is the client side: not every local Drift table goes through it yet — see Phase B2.
      Renaming the module out of `api/v2/` to stop the directory name overstating what is
      actually still legacy is a good small follow-up, not gating.
- [ ] **Celery tasks import from `aura_core`, not `services/`.** Check `learning_pipeline` and any
      scheduled job.
- [ ] **Database models are owned by one layer.** v1/v2 SQLAlchemy models and the v3 storage
      adapters must not both write the same tables.
- [ ] **The Phase 1 technical-debt items scoped to `services/` are closed or moot.** Deleting the
      code closes most of them; confirm none describe behaviour v3 still relies on.

`aura_backend/main.py` itself is already gone (Phase B) — it was never on this checklist's
critical path, since `aura_service.py` never imported it. Its removal took `api/v2/chat.py` with
it: that router had exactly one caller, `main.py`, and is now dead by the same argument. Neither
touched `services/` or the rest of `api/` — `api/v2/{auth,sync,insights}` and `services/rbac.py`,
`services/jwt_auth.py` are live production code reached from `aura_service.py`, not legacy
survivors, and stay until the checklist above is actually all true:

```bash
git rm -r aura_backend/services aura_backend/api/v2
# api/automation.py, api/admin_platform.py, api/learning.py are v3-native, not legacy -- keep them.
pytest   # the architecture tests already forbid any dependency on the removed tree
```

The fitness tests are what make this safe: if anything in `aura_core` had grown a dependency on
`services/`, the suite would have been failing since the day it appeared.

---

## What has not moved, and why

**`api/v2/sync.py`'s directory name.** The endpoint itself already moved — it serves `/api/v3/sync`
and is correct and efficient as written (`WHERE user_id = ? AND updated_at > ?` over composite
indexes; rewriting it would be churn, not improvement). What has not moved is the module out of
`api/v2/`, and every Drift table onto it client-side (Phase B2). Renaming the module is cosmetic
and not gating; the client migration is the real remaining work.

**Firebase auth.** Works, and the shipped client depends on it. Replacing an identity provider is
its own project with its own migration; bundling it into this one would have made both harder.

**The Flutter client.** Out of scope for this backend work. The v3 API is additive, so the client
can migrate route by route at its own pace.

---

Related: [ADR-002 Strangler migration](../architecture/002-strangler-migration.md) ·
[API Reference](api-reference.md) · [Deployment Guide](deployment-guide.md) ·
[Security Audit](../reports/security-audit.md)
