# AURA AI — Real Production Test & User Readiness Certification

**Audit window:** 2026-09-27, ~17:13–17:30 UTC
**Repo revision audited:** `71dd01b` (`main`) — shallow clone, 187 commits, earliest 2026-09-13
**Production target:** `aureon-ai.tech` → A `152.239.118.239` · AAAA `2a02:4780:79:5dcd::1`
**Deployed revision:** **UNIDENTIFIABLE** (see §3, §31)

---

# 1. Executive Summary

## Certification result

> # NOT PRODUCTION CERTIFIED

**The question asked was:** *can a real end user use AURA safely, reliably, correctly, and
consistently for its currently advertised functionality?*

**The answer is no, and the reason is not subtle.** A user installing the shipped Flutter client
cannot establish a connection to the backend at all. Two independent, individually fatal faults:

1. **`https://aureon-ai.tech` serves a valid Let's Encrypt certificate for `velvet-group.shop`** — a
   different site, with a Django application behind it. The client's own release policy requires
   HTTPS (`'Release BACKEND_URL must use https (Android/iOS block cleartext).'`), and mobile
   platforms validate certificate names. Every release build fails during the TLS handshake.
2. **Even ignoring TLS, the deployed backend has no `/api/v3/*` surface.** The client makes 53 call
   sites across 25 distinct v3 endpoints. All of them 404. The live application self-identifies as
   `AURA AI OS Backend v1.0` and serves 24 legacy paths — including all 14 that the repository's own
   `tests/unit/test_v1_locked_routes.py` asserts must return 404.

So the certification result is not a close call on quality. **No end-user journey starts.**

## What is actually wrong — one condition, four symptoms

It would be misleading to present this as a list of independent defects. The evidence points to a
single condition:

> **Production is running an old, pre-v3 build of AURA, and the pipeline that would replace it has
> not run successfully on any recent commit.**

- `Backend CI` on `main` @ `71dd01b` **failed in ~5 seconds** — all four jobs failing in 1–3 s,
  meaning no test body executed. This is a workflow/runner-level failure, not failing assertions.
- `Deploy VPS` is gated on `Backend CI` succeeding, so it concluded **`skipped`**.
- Per `CLAUDE.md`, "merging to `main` is the entire deploy action." With the gate shut, **merging to
  `main` deploys nothing.**
- Both workflows for the *other* deploy target (Railway control plane, HF inference Space) also fail
  at startup, and that target is confirmed undeployed (`Application not found`).

The stale artifact still advertises a `gemini` dependency on `/health` — a field current `main`
deleted, whose removal comment in `aura_backend/config.py:57-59` describes this exact symptom. That
dates the build to before the Gemini removal *and* before the v3 cutover.

**This is the finding that matters most for how the rest of this report should be read: every
code-level assurance in `docs/`, and all 2,776 passing tests, describe an artifact that is not
running.** The deployed thing was never audited by anything.

## What is genuinely working

The audit did not only find failures, and the passes are meaningful:

- **Authentication and device attestation are correctly enforced** — 22 of 24 production endpoints
  reject unauthenticated requests before validation (`403 Device attestation validation failed.` /
  `401 Authorization header is required.`). The two exceptions are the refresh/revoke endpoints where
  the token *is* the credential, and an invalid refresh token is correctly rejected with 401.
- **No configuration or secret exposure.** `.env`, `.git/config`, compose files, a traversal attempt,
  and admin/ops console paths all return a harmless placeholder. **Secrets: NOT FOUND.**
- **Postgres and Redis are reachable** from the application.
- **The repository's engineering discipline is real.** 2,776 collected tests with one failure (a
  docs-freshness check), a clean 100-test legacy suite, AST-enforced layering, and a self-hosting
  invariant enforced across every `.py` in the backend. The `docker-socket-proxy` allowlists are at
  parity across both compose files, exactly as `CLAUDE.md` requires.

The security *posture* of the deployed surface, where it could be measured, held up. The problem is
deployment and delivery, not the codebase's care.

## Coverage honesty

| Test maturity reached | Areas |
| --- | --- |
| STATICALLY VERIFIED | layering, self-hosting invariant, compose isolation parity, sandbox exec surface |
| LOCALLY TESTED | full `pytest` (2,776) + legacy `unittest` (100) |
| INTEGRATION TESTED | — (no environment available) |
| PRODUCTION TESTED | topology, TLS, API surface, health, authn, headers, CORS, exposure, CI/CD |
| FAILURE TESTED | invalid credentials, invalid bodies, hostile CORS origin, missing routes |
| LOAD TESTED | **not performed** (deliberately — §24) |
| RECOVERY TESTED | **not performed** (§22) |
| USER JOURNEY TESTED | attempted; **all journeys fail at step 1** (§29) |
| RELEASE CERTIFIED | **no** |

**16 capabilities are BLOCKED and 11 NOT TESTED.** The dominant blocker is structural: the surface
those tests would target does not exist in production. A secondary blocker is that attestation is
enforced and this audit holds no credentials — correctly so; that is a pass, not an obstacle to work
around.

**4 of 57 capabilities are production-ready, and all four are negative controls** (the system
correctly refusing access and not leaking config). Nothing user-facing is verified in production.

---

# 2. Production Environment

## Discovered topology

| Component | Location | Version / Revision | Health | Dependency |
| --- | --- | --- | --- | --- |
| DNS apex | `aureon-ai.tech` | A `152.239.118.239`, AAAA `2a02:4780:79:5dcd::1` | resolves | — |
| DNS dev | `dev.aureon-ai.tech` | — | **NXDOMAIN** | — |
| Ingress | same host, `nginx/1.24.0 (Ubuntu)` | — | `:80` up, `:443` **wrong vhost** | — |
| **`:443`** | same host | Let's Encrypt **`velvet-group.shop`** | serves a **Django** app | not AURA |
| **`:80`** | same host | `AURA AI OS Backend` **`v1.0`** | **degraded** | pg, redis, qdrant |
| API surface | `http://aureon-ai.tech` | 24 paths, **0 × `/api/v3`** | reachable | — |
| PostgreSQL | internal | unknown | **reachable** | — |
| Redis | internal | unknown | **reachable** | — |
| Qdrant | internal | unknown | **UNREACHABLE** (client `None`) | — |
| `gemini` (stale key) | — | — | `configured: true, probed: false` | dates the build |
| Documented VPS | `169.58.188.43` (Contabo) | — | `:80` timeout, `:443` RST | **not the live host** |
| Railway control plane | `aura-production-a31f.up.railway.app` | — | **`Application not found`** | not deployed |
| HF inference Space | placeholder only in repo | — | **not deployed** | not deployed |
| Celery / Beat / outbox | internal | unknown | **not observable** | — |
| Monitoring / logging | not exposed | — | **not observable** | — |
| Admin console | not exposed | — | **not observable** | — |
| Host daemon | client-side | — | **not observable** | — |

## Three facts about this topology that matter

1. **The live domain is not the documented VPS.** `deploy-backend.yml` names `169.58.188.43`; DNS
   points at `152.239.118.239` (and a Hostinger IPv6 range). The documented host does not answer.
   Whether AURA moved or DNS was repointed cannot be settled from outside — but the deploy workflow
   SSHes to `vars.VPS_HOST`, so **if that variable still holds the old address, successful deploys
   would land on a host no user reaches.** This should be checked before anything else is fixed.
2. **AURA shares its host with an unrelated production website.** `velvet-group.shop` owns `:443`.
   This is the shared-host topology `deploy/nginx/host-proxy.conf.example` exists for, and it is
   currently resolved in the other site's favour.
3. **Neither documented deploy target is serving current code.** Target 1 (VPS) serves a stale
   build; target 2 (Railway + HF) is absent. `CLAUDE.md` describes both as live side by side.

## Configuration hygiene checks (Phase 0)

| Check | Result |
| --- | --- |
| Debug mode enabled in production | **NOT FOUND** — no debug output, no tracebacks in responses |
| Mock auth / attestation bypass | **NOT FOUND** — attestation enforced on all 15 attested routes |
| Test endpoints / debug routes | **NOT FOUND** in the 24-path surface |
| Localhost/staging URLs leaking | **NOT FOUND** |
| Secrets in responses or headers | **NOT FOUND** |
| Interactive API docs exposed | `/docs` **returns 200** — Swagger UI is public (P3, §9) |
| Old API versions still mounted | **FOUND** — this is the whole of the live surface (BLOCK-02) |

---

# 3. Test Methodology

**Posture: non-destructive by default.** Read-only probes; no writes, no deletion, no configuration
change, no load generation, no destructive exploitation. Total live request volume was roughly 130
requests over ~17 minutes.

**The auth probe deserves explaining,** because it tested a write-capable surface without writing.
Every documented endpoint was called unauthenticated with a deliberately invalid body
(`{"__aura_synthetic_probe__":true}`). A framework that authenticates before validating answers
`401/403`; one that validates first answers `422`. So the response code alone distinguishes "auth
enforced" from "auth missing" **without a valid payload ever being processed**. No synthetic records
were created, and none needed cleanup.

**No synthetic identities were created** because attestation is enforced and this audit holds no
credentials. That is the correct outcome for a hardened system; it is recorded as a coverage blocker
rather than worked around.

**Vantage limitations, stated plainly** (both narrow what may be claimed):

- Egress from the audit container succeeds only on ports 80/443. A control probe
  (`140.82.121.4:22` → timeout, while the target's `:80`/`:443` connect) proves the uniform timeouts
  on datastore ports are **my** restriction. Non-web exposure is therefore **NOT TESTABLE**, not
  PASS — an important distinction, since reporting it as PASS would have manufactured a clean result
  from an absence of evidence.
- HTTPS is terminated and re-signed by an agent proxy, so the origin certificate was read from a
  CONNECT-tunnel capture rather than ordinary verification.

**Evidence standard.** Every claim in this report traces to a recorded command and output in
`AURA_PRODUCTION_TEST_EVIDENCE.md` (EV-01…EV-16). Where evidence is missing, the result is BLOCKED,
NOT TESTED, NOT TESTABLE, or UNKNOWN. **No missing evidence was converted into a PASS.**

---

# 4. Test Coverage

| Phase | Area | Coverage | Result |
| --- | --- | --- | --- |
| 0 | Production discovery | Full | **FAIL** (unidentifiable revision) |
| 1 | Infrastructure health | Partial (via `/health`) | **FAIL** (Qdrant down) |
| 2 | Network & TLS | Full for web; datastores not testable | **FAIL** (BLOCK-01) |
| 3 | Authentication | Unauthenticated paths full | **PASS** |
| 4 | Authorization / RBAC | None | **BLOCKED** |
| 5 | Core API | Surface enumerated; behavior blocked | **FAIL** (BLOCK-02) |
| 6 | AI Kernel | None | **BLOCKED** |
| 7 | Real inference | None | **BLOCKED** |
| 8 | ProviderRouter | Static only | **BLOCKED** |
| 9 | Memory | None | **BLOCKED** |
| 10 | RAG / Knowledge | Dependency observed down | **FAIL** |
| 11 | Planning / reasoning | None | **BLOCKED** |
| 12 | Agents | None | **BLOCKED** |
| 13 | Tools | None | **BLOCKED** |
| 14 | Safety / prompt injection | None | **BLOCKED** |
| 15 | Personal OS | None | **BLOCKED** |
| 16 | Celery / automation | None | **NOT TESTED** |
| 17 | Database | Reachability only | **PARTIAL** |
| 18 | Backup / restore / DR | None | **BLOCKED** |
| 19 | Observability | Endpoint absence confirmed | **FAIL** |
| 20 | Performance | `/health` only | **PARTIAL** |
| 21 | Failure / chaos | Auth + routing failures only | **PARTIAL** |
| 22 | Desktop | None | **NOT TESTED** |
| 23 | Host daemon | None | **NOT TESTED** |
| 24 | Flutter client | Compatibility proven broken | **FAIL** |
| 25 | Admin console | Not exposed | **NOT TESTED** |
| 26 | Security deep test | Web surface only | **PARTIAL** |
| 27 | Data privacy | Secret scan of live surface | **PASS** (as far as reachable) |
| 28 | End-to-end journeys | Attempted | **FAIL** (none start) |
| 29 | Version / compatibility | Full | **FAIL** |
| 30 | Deployment / release | Full | **FAIL** (BLOCK-04) |
| 31 | Update / rollback | None | **BLOCKED** |

---

# 5. Infrastructure Results

`/health`, 12 sequential samples over ~24 s — **12/12 `degraded`, HTTP 200 every time**:

```json
{"status":"degraded","degraded":["qdrant"],
 "dependencies":{"qdrant":{"configured":true,"reachable":false,
   "error":"'NoneType' object has no attribute 'get_collections'"},
  "postgres":{"configured":true,"reachable":true},
  "redis":{"configured":true,"reachable":true},
  "gemini":{"configured":true,"probed":false}}}
```

| Component | Result | Evidence |
| --- | --- | --- |
| Gateway / API process | **PASS** — serving, 0.15–0.29 s | EV-05 |
| PostgreSQL | **PASS** (reachable only) | EV-05 |
| Redis | **PASS** (reachable only) | EV-05 |
| **Qdrant** | **FAIL** — client is `None`, never constructed | EV-05 |
| Readiness / liveness endpoints | **FAIL** — `/health/ready`, `/health/live`, `/healthz` all 404 | EV-05 |
| Celery / Beat / outbox / backup / monitoring | **NOT TESTED** — not observable | — |

**Three distinct defects here, not one.** (a) Qdrant is down. (b) The failure is invisible to
automation, because a degraded stack answers **200** — every uptime monitor and container probe sees
health. (c) The readiness endpoints that *should* carry that signal don't exist, including the
`/healthz` the repo's own nginx template defines.

**One inconsistency worth chasing:** a single earlier sample (1 of 14 total) returned
`status: healthy` with `qdrant.reachable: true`. Either two instances disagree — which would make
retrieval nondeterministic per request — or it was a first-touch transient. Not distinguishable from
outside; it needs settling on the host.

---

# 6. API Results

Live surface: **24 paths, zero under `/api/v3`.** Self-identifies as
`{"title": "AURA AI OS Backend", "version": "1.0"}`; current `main` builds
`title="AURA AI Gateway", version="3.0.0"` (`aura_core/api/v3.py:1236-1238`).

## Endpoint coverage matrix

| Endpoint group | Paths | Unauth result | Authed behavior | Verdict |
| --- | --- | --- | --- | --- |
| `GET /health` | 1 | 200 (public) | — | **PARTIAL** (200 while degraded) |
| `/api/v2/auth/{token,refresh,revoke}` | 3 | 403 / 422+401 / 422+200 | BLOCKED | **PARTIAL** |
| `/api/v2/{chat,sync,insights×3}` | 6 | 401 | BLOCKED | **BLOCKED** |
| `/api/chat/{stream,agent}` | 2 | 403 | BLOCKED | **BLOCKED** |
| `/api/memory/{save,list,extract,delete}` | 4 | 403 | BLOCKED | **BLOCKED** |
| `/api/rag/{ingest,query}` | 2 | 403 | BLOCKED | **BLOCKED** |
| `/api/learning/×4` | 4 | 403 | BLOCKED | **BLOCKED** |
| `/api/automation/×2` | 2 | 403 | BLOCKED | **BLOCKED** |
| `/api/telemetry/log` | 1 | 403 | BLOCKED | **BLOCKED** |
| **`/api/v3/*` (25 client endpoints)** | **0** | **404** | — | **FAIL** |

No production endpoint is left untested without explanation: all 24 were probed for authentication;
authenticated behavior is uniformly BLOCKED for the reason in §3.

**The `/api/v3` row is the headline.** `tests/unit/test_v1_locked_routes.py` asserts the 14 legacy
paths return 404 and the v3 platform routes stay mounted. Production is the exact inverse. The test
passes against `aura_service.py` and says nothing about the deployed artifact — a gap worth closing
with a post-deploy assertion, since a green suite gave false comfort here for an unknown period.

---

# 7. Authentication

**Result: PASS.** The strongest area measured.

| Test | Expected | Actual | Verdict |
| --- | --- | --- | --- |
| Unauth. on 15 attested routes | reject | `403 Device attestation validation failed.` | **PASS** |
| Unauth. on 7 v2 routes | reject | `401 Authorization header is required.` | **PASS** |
| Auth-before-validation ordering | auth first | 401/403, never 422 | **PASS** |
| Invalid refresh token | reject | `401 Refresh token is invalid, expired, or already used.` | **PASS** |
| Invalid token → revoke | reject or no-op | `200 {"status":"revoked"}` | **P4** |
| Token issuance / expiry / rotation / multi-device / recovery | — | **BLOCKED** | — |

`revoke` returning `200` for a token that never existed is defensible — it denies an attacker a
validity oracle and keeps revocation idempotent. It is recorded as **P4 informational**, not a
defect: the caveat is only that it also masks genuine failures from legitimate clients, so if that
was not the intent it is worth an explicit decision.

Everything behind a successful login — session invalidation, concurrent sessions, expiry, refresh
rotation — is **BLOCKED**.

---

# 8. Authorization / RBAC

**Result: BLOCKED — no evidence.** Not a pass.

Nothing was tested: horizontal or vertical privilege escalation, object-level authorization, IDOR,
cross-user memory/task/finance/file/workspace access, or admin endpoint reachability. Two
credentials in the same tenant are required, and attestation is enforced.

This is a **mandatory gate**, so its untested state blocks certification on its own — independently
of the P0s.

**Procedure to unblock (isolated, not production):** stand up the stack from
`deploy/docker-compose.dev.yml` with `seed-dev.sh`, provision two synthetic users with attestation
in the dev posture, then assert User B receives 403/404 — never 200 — on every one of User A's object
IDs across memory, sync, insights, and workspaces. Confirm at the database layer that the row filter
is by owner, not merely absent from the response.

---

# 9. Security

| Test | Result | Severity |
| --- | --- | --- |
| Configuration / secret file exposure | **PASS** — none | — |
| Admin & ops console exposure | **PASS** — not reachable | — |
| Path traversal (`/static/../.env`) | **PASS** — no traversal | — |
| Authentication enforcement | **PASS** | — |
| Debug mode / mock auth in production | **PASS** — not found | — |
| **TLS: certificate name mismatch** | **FAIL** | **P0** |
| **CORS: `Access-Control-Allow-Origin: *`** | **FAIL** | **P2** |
| **Security response headers: none** | **FAIL** | **P2** |
| Swagger UI (`/docs`) public | **FAIL** | P3 |
| Unknown paths return 200 not 404 | **FAIL** | P4 |
| Rate limiting | **PARTIAL** — none on `/health`/30 req | P3 |
| Dependency CVE gating | **FAIL** — `pip-audit` job failing | P1 |
| Non-web port exposure | **NOT TESTABLE** | — |
| Prompt injection, SSRF, command exec, container escape, IDOR | **BLOCKED** | — |

**CORS**, stated precisely: the wildcard is returned for `Origin: https://attacker.example` as
readily as for the real origin, so the `allowed_origins` allowlist in `config.py` is not in effect.
**No `Access-Control-Allow-Credentials` was returned**, and browsers will not attach credentials to
a wildcard origin — which is why this is P2 rather than P1. It is still a misconfiguration that
contradicts the intended policy, and it would become P1 the moment credentials were added.

**Security headers:** AURA's surface returns none — no HSTS, `X-Content-Type-Options`,
`Referrer-Policy`, frame options, or CSP. The unrelated co-hosted application on `:443` *does* send
them, which is a useful tell that AURA's intended nginx configuration is not the one in force.

**No penetration test, threat model, or automated scanner run** is part of this audit; the repo's own
readiness score already records their absence.

---

# 10. AI Kernel · 11. Inference · 12. Memory · 13. RAG · 14. Planning · 15. Reasoning · 16. Agents · 17. Tools · 18. Safety

**All BLOCKED. Grouped because they share one cause and one remedy.**

The kernel pipeline, `ProviderRouter`, memory, retrieval, planning, agents, tools, and the trust-
channel safety mechanism are reached **only** through `/api/v3/*`. That surface does not exist in
production (§6). No amount of credentials would help: there is nothing to call.

Consequently **none** of the following was tested: kernel authority over execution, plan→execute→
generate ordering, model-emitted `tool_calls` being informational only (ADR-006), routing policies
and circuit breakers, pre-first-token failover, six memory kinds and cross-user isolation, hybrid
retrieval / RRF / reranking, retrieval quality (Recall@k, MRR, nDCG, groundedness, citation
correctness), DAG planning and replanning, agent step and time limits, tool confirmation gating, or
the adversarial prompt-injection suite (direct, indirect, poisoned document, hostile tool response,
system-prompt extraction, confirmation bypass, exfiltration).

**Two things must not be inferred from the repo's green suite:**

- **Agent classification is UNKNOWN.** Whether the shipped personas behave as autonomous agentic
  loops or as prompt wrappers with tool collections cannot be determined without running them. The
  code describes a genuine kernel loop; this audit did not observe one execute, and code presence is
  not behavior.
- **The self-hosting invariant is unverified in production.** Repo-side enforcement is genuinely
  strong — `tests/architecture/test_no_external_inference.py` scans every `.py` under
  `aura_backend/` with two self-referential exemptions. But the deployed build **still advertises a
  `gemini` key on `/health`**, a field current `main` deleted. That proves the build is stale and
  predates the removal. Whether it *routes* inference to a hosted vendor is **UNKNOWN** and needs
  host access — the removal comment notes nothing read the value, so the honest reading is "stale
  artifact advertising a banned key", not "confirmed external inference". Either way, **AURA's
  headline architectural promise is currently unproven where it counts.**

**Real production metrics recorded: none.** No model ID, TTFT, token counts, throughput, GPU or VRAM
utilisation — no inference plane is deployed and no v3 surface exists to drive one. Any figure here
would be fabricated, so there is none.

---

# 19. Personal OS · 20. Automation

**Personal OS: BLOCKED.** Tasks, calendar, finance, health logs, projects, goals, automation, and
insights are all v3-surface features. CRUD, authorization, persistence, sync, and recovery untested.
Note for future audits: the repo's own gap register distinguishes *propose* from server-side CRUD —
that distinction must not be collapsed when this is eventually tested.

**Automation / Celery: NOT TESTED.** Worker, Beat, outbox, retries, idempotency, duplicate delivery,
and restart behavior are not externally observable, and no monitoring surface is exposed. Whether
workers are even running on the host is **UNKNOWN**.

---

# 21. Database

| Test | Result |
| --- | --- |
| Postgres reachable from app | **PASS** |
| Redis reachable from app | **PASS** |
| Qdrant reachable from app | **FAIL** (§5) |
| Migration state / schema version | **BLOCKED** |
| Constraints, FKs, indexes, transactions, pooling, concurrency, rollback | **NOT TESTED** |

Reachability is not health. Per `CLAUDE.md`, asyncpg type mapping (e.g. `Decimal` from
`avg()`/`percentile_cont`) has produced real bugs the in-memory adapter cannot surface — so the
concurrency-sensitive paths need a real Postgres, which this audit had no access to.

---

# 22. Backup / Restore / Disaster Recovery

**BLOCKED — and this is a mandatory gate.**

Nothing was verified: backup existence, timestamps, integrity, retention, offsite copies, or
restore. No restore was performed into an isolated environment. **RPO and RTO are UNKNOWN and
unmeasured.**

Per the standing rule in the audit criteria and in `CLAUDE.md`: **disaster recovery readiness cannot
be claimed without a successful restore.** `deploy/scripts/{backup,restore,restore-drill}.sh` exist,
and `docs/reports/phase-a2-restore-drill.md` records prior work — but no drill was executed here, so
this audit contributes no evidence either way. Note also that `Backend CI`'s `backup-scripts` job is
currently failing (§31), so even the static gate on these scripts is not running.

---

# 23. Observability

**FAIL.**

| Test | Result |
| --- | --- |
| `/metrics` exposed | **FAIL** — returns the HTML placeholder banner, not metrics |
| Prometheus / Grafana / Alertmanager / Loki | **NOT TESTED** — not reachable |
| Request/correlation ID propagation | **NOT TESTED** — no authenticated request completed |
| Degradation observable | **FAIL** — `degraded` served under HTTP 200 |
| Alerting | **NOT TESTED** |

The critical observability failure is §5(b): **the one health signal that is exposed reports success
while a core dependency is down.** A monitoring stack keyed on status codes would have shown green
throughout this outage — and given that the deploy pipeline has also been silently `skipped` rather
than failed, the system's two most important faults are both invisible to their own alarms. That
pattern, more than any single defect, is what makes the current state unsafe to operate.

---

# 24. Performance

**Measured (production, live):**

| Metric | Value |
| --- | --- |
| `GET /health` samples | 12 sequential |
| min / max | 0.151 s / 0.290 s |
| ≈p50 | 0.24 s |
| Error rate | 0 % (all HTTP 200) |

That is the **only** honest production performance figure available. It measures one uncached JSON
handler plus three dependency probes, over the public internet through a proxy — it is not an
application latency baseline.

**Not measured, and deliberately not:** API/auth/memory/RAG/planning/tool/inference latency, TTFT,
throughput, concurrency, p95/p99, queue depth. Load testing production was out of scope by the
audit's own safety rules, and the required isolated production-equivalent environment does not
exist.

**Prior benchmark numbers in `docs/reports/` must not be read as production figures** — the repo's
own readiness score is explicit that they were taken with in-memory adapters and an echo provider,
i.e. they measured the parts that are not the bottleneck. This audit reuses none of them.

---

# 25. Desktop · 26. Host Daemon

**Both NOT TESTED.** No Flutter or Dart toolchain in this environment (as `CLAUDE.md` anticipates),
and — decisively — the desktop Engineer flow depends on `/api/v3/engineering/*` and
`/api/v3/auth/desktop/*`, which 404 in production.

Untested: workspace lifecycle, explorer, editor, terminal/PTY, git operations, guarded push,
diagnostics, AI coding proposals and approval/rejection/rollback; and on the daemon side, pairing,
authentication, workspace isolation, process lifecycle, filesystem permissions, cleanup, reconnect,
and recovery. **No containment or escape testing was performed** — so the workspace boundary is
unproven here.

Per `CLAUDE.md`, Phase J (Engineer workspaces, ADR-013/014) is **built, not proven** until
`deploy/scripts/verify-engineer-workspaces.sh` runs on the VPS and writes its report. That has not
happened in this audit, and a green `pytest` cannot substitute — the default suite's sandbox runner
never touches the Docker API.

---

# 27. Flutter Client

**FAIL — the client cannot reach production.** Proven, not inferred:

| Fact | Evidence |
| --- | --- |
| Default target `https://aureon-ai.tech` | `config.dart:17` |
| Release builds must use HTTPS | `backend_url_policy.dart:30` |
| That host serves a foreign certificate | EV-02 |
| Client speaks v3: 53 call sites, 25 endpoints | `grep` over `aura_client/lib/` |
| Production has zero v3 routes | EV-03 |

Two independent fatal faults. TLS fails first; had it succeeded, every API call would 404.

**Credential hygiene (static): PASS.** Scanned `aura_client/lib/` for hosted-vendor inference
hosts, `sk-`-style keys, and long embedded literals: **no inference credentials, no private
inference endpoint, and no embedded server secret.** The `googleapis` imports that the scan surfaces
are Google Workspace integrations — Calendar, Tasks, Gmail, Drive, via user-consented OAuth scopes
(`lib/features/{calendar,tasks,automations}/`) — not inference providers, so they are not a
self-hosting violation. The client's `backend_url_policy` also actively refuses cleartext, localhost,
and raw-IP targets in release builds, which is sound hardening (and part of why BLOCK-01 is fatal
rather than something a user could work around).

Runtime behavior — launch, login, token refresh, streaming, offline mode, sync conflicts,
notifications, deep links, recovery — is **NOT TESTED**.

---

# 28. Admin Console

**NOT TESTED.** No admin surface is reachable on the production host (`/admin`, `/console`,
`/console/login` all return the placeholder). Whether it is deployed elsewhere is **UNKNOWN**.
Authentication, authorization, user management, approvals, audit, knowledge, models, providers, and
prompts are all untested. The negative control holds: admin paths are not exposed to anonymous
callers on this host.

---

# 29. End-to-End User Journeys

| Journey | Result | Failed at |
| --- | --- | --- |
| **A — New user**: install → launch → register/login → first AI request | **FAIL** | Step 2. TLS handshake fails (foreign certificate). Never reaches login. |
| **B — Intelligent assistant**: question → memory → RAG → planning → answer | **FAIL** | Step 1. `/api/v3/chat/stream` → 404. Qdrant also down. |
| **C — Tool workflow**: request → plan → confirmation → execute → audit | **FAIL** | Step 1. No v3 surface. |
| **D — Coding**: desktop → workspace → AI request → proposal → approval → tests → diff | **FAIL** | Step 1. `/api/v3/engineering/*` → 404. |
| **E — Failure recovery**: session → dependency fails → controlled error → recovery | **NOT TESTABLE** | No session can be established. |

**No journey reaches step 3.** This is the clearest statement of the certification result: the
product's advertised functionality is not merely degraded or partially available — it is
**unreachable**. A user who installs the app today sees a connection error and nothing else.

Ironically, Journey E's premise — a dependency failure surfacing as a controlled error — is exactly
the live condition (Qdrant down), and the system's own health endpoint reports success through it.

---

# 30. Compatibility

| Pair | Result | Detail |
| --- | --- | --- |
| **Flutter ↔ Control Plane** | **FAIL** | Client v3-only; server v1/v2-only. Zero overlap on 25 endpoints. |
| **Desktop ↔ Control Plane** | **FAIL** | `/api/v3/engineering/*`, `/api/v3/auth/desktop/*` absent. |
| Control Plane ↔ Inference Plane | **FAIL** | No inference plane deployed. |
| Control Plane ↔ Postgres | **PARTIAL** | Reachable; schema/migration state unknown. |
| Control Plane ↔ Redis | **PARTIAL** | Reachable. |
| **Control Plane ↔ Qdrant** | **FAIL** | Client `None`. |
| Workers ↔ Database | **NOT TESTED** | — |
| Admin ↔ API | **NOT TESTED** | — |
| Hostd ↔ Desktop | **NOT TESTED** | — |

The client/server mismatch is total, not partial. There is no version negotiation, deprecation
window, or graceful-degradation path bridging a v3 client to a v1/v2 server — and nothing in the
client would present this to a user as anything other than an opaque failure.

---

# 31. Deployment / Release Engineering

**FAIL — the root cause of most of this report.**

`main` @ `71dd01b`, 2026-09-27T06:05Z:

| Workflow | Run | Conclusion | Wall clock |
| --- | --- | --- | --- |
| Backend CI | #281 | **failure** | ~5 s |
| **Deploy VPS** | #182 | **skipped** | — |
| Agent Evaluation | #85 | **failure** | ~3 s |
| control-plane-deploy | #71 | **failure** | 0 s |
| inference-plane-deploy | #71 | **failure** | 0 s |

Backend CI jobs: `pip-audit` **failure (1 s)**, `test` **failure (3 s)**, `backup-scripts`
**failure (2 s)**, `docker-build` skipped.

**Jobs failing in 1–3 seconds did not execute their test bodies.** This is a workflow- or
runner-level failure — a setup step, action resolution, or an account-level condition — not failing
assertions. Job logs return HTTP 404 (expired), so the precise cause is **UNKNOWN** and must be
recovered by re-running one job. The *shape* is unambiguous.

`Deploy VPS` fires on `workflow_run` of `Backend CI` with `conclusion == 'success'`. CI fails →
deploy **skips**. Since `CLAUDE.md` states merging to `main` *is* the deploy action, **no merge to
`main` currently deploys anything.**

Two consequences worth separating:

- **The failure mode is silent.** `skipped` is not `failed`; nothing pages, no red mark appears on
  the deploy. Production drifting arbitrarily far behind `main` produces no signal at all — which is
  how a pre-v3 build survived a v3 cutover in the repository.
- **CI provides no gating whatsoever right now**, including `pip-audit`. Dependency
  vulnerabilities are ungated (P1), and so is the `backup-scripts` check (§22).

Also unverified because no deploy runs: clean build, image integrity, migration execution, startup,
readiness gating, smoke tests, and artifact provenance. And per §2, `vars.VPS_HOST` should be
checked against live DNS before the pipeline is repaired — otherwise the first successful deploy in
weeks may land on a host no user reaches.

---

# 32. Rollback

**BLOCKED.** No rollback was exercised. More fundamentally, **rollback is not currently meaningful**:
the deployed revision is unidentifiable (§3), so there is no known-good version to return to and no
way to predict what a rollback would restore. `docs/deployment/rollback.md` exists and is untested
here. Database/API/model compatibility across a rollback boundary is **UNKNOWN**.

Establishing runtime build provenance (BLOCK-03) is a prerequisite for rollback being an operation
rather than a guess.

---

# 33. Privacy

| Check | Result |
| --- | --- |
| Passwords, API keys, tokens, service credentials in responses/headers | **NOT FOUND** |
| Private URLs / internal hostnames leaked | **NOT FOUND** |
| Sensitive user data in error responses | **NOT FOUND** |
| Stack traces / debug output | **NOT FOUND** |
| Config files reachable (`.env`, `.git/config`, compose) | **NOT FOUND** (no exposure) |
| Client-embedded secrets | **NOT FOUND** (static review) |
| User isolation, memory privacy, exports, deletion, retention, admin visibility | **BLOCKED** |
| Log/telemetry content hygiene | **NOT TESTED** (logs not reachable) |

No secret values are reproduced anywhere in these reports. Scope note: this covers only the
externally reachable surface — backups, logs, and telemetry were not reachable, and data-subject
rights (export, deletion, retention) are untested.

---

# 34. Defects

| ID | Component | Severity | Blocking | Summary |
| --- | --- | --- | --- | --- |
| **BLOCK-01** | Ingress / TLS | **P0** | **Yes** | `:443` serves a Let's Encrypt cert for `velvet-group.shop`; verifying clients fail |
| **BLOCK-02** | Gateway | **P0** | **Yes** | No `/api/v3/*` in production; 14 locked legacy routes live |
| **BLOCK-03** | Release provenance | **P0** | **Yes** | Deployed revision unidentifiable; stale `gemini` key dates it pre-v3 |
| **BLOCK-04** | CI/CD | **P0** | **Yes** | Backend CI fails at startup; `Deploy VPS` silently skipped |
| **BLOCK-05** | Qdrant + health contract | **P1** | **Yes** | Retrieval down; `degraded` served under HTTP 200 |
| DEF-06 | CI (`pip-audit`) | **P1** | No | Dependency CVE gate not running |
| DEF-07 | CORS | **P2** | No | `Allow-Origin: *` for any origin; allowlist not in effect |
| DEF-08 | HTTP headers | **P2** | No | No HSTS/CSP/XCTO/Referrer-Policy/frame options |
| DEF-09 | Health endpoints | **P2** | No | `/health/ready`, `/health/live`, `/healthz` all 404 |
| DEF-10 | Observability | **P2** | No | `/metrics` not exposed; returns placeholder HTML |
| DEF-11 | Fleet consistency | **P2** | No | One sample reported `healthy` against 13 `degraded` |
| DEF-12 | Docs / API matrix | **P3** | No | `api-reachability.md` stale → red test on `main` |
| DEF-13 | API docs exposure | **P3** | No | Swagger UI public at `/docs` |
| DEF-14 | Rate limiting | **P3** | No | Not observed on `/health`; unverified on protected routes |
| DEF-15 | DNS / dev env | **P3** | No | `dev.aureon-ai.tech` NXDOMAIN; dev stack unreachable |
| DEF-16 | Nginx 404 handling | **P4** | No | Unknown paths return 200 + banner instead of 404 |
| DEF-17 | Auth semantics | **P4** | No | `revoke` returns 200 for a non-existent token |
| DEF-18 | Docs accuracy | **P4** | No | `CLAUDE.md` cites root `docker-compose.dev.yml`; file is under `deploy/` |

Full reproduction, expected/actual, root cause, impact, fix, and regression test for each P0/P1 are
in `AURA_PRODUCTION_BLOCKERS.md`.

**DEF-12 detail** (the one red local test): `tests/architecture/test_api_reachability.py::
test_the_committed_matrix_is_current` — the committed `docs/guides/api-reachability.md` no longer
matches what `scripts/api_reachability.py` renders; the diff is line-number drift
(`host_daemon_page.dart:147` → `:146`). Fix: `python scripts/api_reachability.py` from
`aura_backend/`. Left unfixed deliberately so the recorded suite evidence stays reproducible against
`71dd01b`.

---

# 35. Blocking Issues

**Five blockers: BLOCK-01 … BLOCK-05.** See `AURA_PRODUCTION_BLOCKERS.md`.

Mandatory gates failed **on evidence**: broken core user workflow; critical inference path
unverified; severe production instability; deployment integrity.

Mandatory gates failed **for want of evidence** (untested ≠ passed): authorization/RBAC and
cross-user data isolation; prompt-injection and safety enforcement; backup/restore and disaster
recovery; unsafe-tool and agent-execution containment.

**Suggested repair order** — it is not the severity order, because BLOCK-04 gates the others:

1. **BLOCK-04** — restore CI so a deploy can happen at all. Nothing else can ship first.
2. **§2 pre-check** — reconcile `vars.VPS_HOST` with live DNS, so the first deploy lands where users go.
3. **BLOCK-01** — fix the `:443` vhost and certificate; verify with a name-verifying client.
4. **BLOCK-02/03** — deploy current `main`; add runtime build provenance and a post-deploy v3 assertion.
5. **BLOCK-05** — fix Qdrant; make readiness return non-200 when degraded.
6. Then re-audit: the entire BLOCKED set above becomes testable only after step 4.

---

# 36. Known Limitations

Limitations of **this audit**, so its scope is not overstated:

- **No credentials.** Attestation is correctly enforced, so everything behind login is unmeasured.
- **Vantage restricted to ports 80/443.** Non-web exposure is NOT TESTABLE, not PASS (§3).
- **TLS terminated by an agent proxy.** Origin chain read via CONNECT capture; full chain validation
  from a normal client is unverified.
- **No load testing.** Out of scope against production by the audit's safety rules, and no isolated
  production-equivalent environment exists.
- **No host access.** Containers, workers, logs, backups, migrations, and the Docker API are all
  unobservable, so several causes are bounded rather than identified.
- **No Flutter/Dart toolchain.** Client and hostd runtime behavior untested.
- **Shallow clone** (187 commits from 2026-09-13) — the deployed build's age cannot be narrowed by
  git archaeology.
- **Job logs expired**, so BLOCK-04's precise cause is UNKNOWN.
- **Single audit window** (~17 minutes) — no observation of behavior over time, under real traffic,
  or across a deploy.

---

# 37. Evidence Index

`AURA_PRODUCTION_TEST_EVIDENCE.md`:

| ID | Subject |
| --- | --- |
| EV-01 | DNS and topology discovery; documented VPS unreachable |
| EV-02 | `:443` foreign certificate + Django application (BLOCK-01) |
| EV-03 | Pre-v3 build; 24 paths; 10/10 v3 endpoints 404 (BLOCK-02) |
| EV-04 | Stale `gemini` key dates the build (BLOCK-03) |
| EV-05 | Health: 12/12 degraded under HTTP 200; latency (BLOCK-05) |
| EV-06 | Authentication and attestation enforced (PASS) |
| EV-07 | Security headers absent |
| EV-08 | CORS wildcard for any origin |
| EV-09 | No configuration/secret exposure (PASS) |
| EV-10 | Rate limiting not observed (inconclusive) |
| EV-11 | Non-web port exposure NOT TESTABLE, with invalidating control |
| EV-12 | CI/CD failing; deploy skipped (BLOCK-04) |
| EV-13 | Railway/HF target not deployed |
| EV-14 | Local suites: 2,776 collected / 1 failed; legacy 100 OK |
| EV-15 | Static isolation checks (compose parity, SEC-12) |
| EV-16 | Repository's own recorded position (corroborating) |

---

# 38. Final Certification

## NOT PRODUCTION CERTIFIED

**Date:** 2026-09-27 · **Repo revision:** `71dd01b` · **Deployed revision:** unidentifiable

This result is issued on evidence, not caution. It is not a marginal judgment about polish or
coverage:

- **No end-user journey completes**, or even reaches its third step (§29).
- **The shipped client cannot connect to production** — TLS name mismatch, and no v3 surface behind
  it (§27).
- **The deployed artifact is unidentified** and predates the v3 cutover (§3).
- **The deploy pipeline has not delivered** and fails silently as `skipped` (§31).
- **Retrieval is down**, and the health endpoint reports 200 through it (§5).
- **Four mandatory gates are untested**, which the criteria treat as blocking, not passing (§35).

**Neither** `CONDITIONAL PRODUCTION CERTIFICATION` **nor** `PRODUCTION CERTIFIED` **is available.**
Conditional certification requires all mandatory safety, security, and core-user gates to pass with
remaining issues classified as non-blocking limitations. Here the core-user gate fails outright, and
four mandatory gates have no evidence at all.

## What this audit is *not* saying

The codebase is not the problem, and this result should not be read as a verdict on it. 2,776 tests
collected with one documentation-freshness failure; a clean 100-test legacy suite; AST-enforced
hexagonal layering; a self-hosting invariant enforced across every backend source file; socket-proxy
allowlists at parity across both compose files; no secrets in the client or on the wire; and
authentication and device attestation demonstrably enforced in production. That is careful work, and
the areas where production *could* be measured for hardening largely held.

**The gap is between a well-built repository and a broken delivery path.** The repo already said so
in its own words — `gap_register.yaml` (31 open, 13 built-not-proven) and
`AURA_AI_PRODUCTION_READINESS_SCORE.md`: *"No dimension can score above 8, because this system has
never served a real user."* This audit confirms that and sharpens it: **production is not running the
code this repository describes, and the mechanism intended to fix that is itself broken.** Until
build provenance is visible at runtime and the deploy gate opens, no claim about production —
including a future PASS — can be substantiated.

## Path to certification

1. Restore CI so deploys can run (**BLOCK-04**); recover a live job log before it expires.
2. Reconcile `vars.VPS_HOST` with live DNS (§2).
3. Fix the `:443` vhost and certificate (**BLOCK-01**); verify with a name-verifying client, not `-k`.
4. Deploy current `main`; add runtime build provenance and a post-deploy assertion that
   `info.version` starts with `3.` and a known v3 route does not 404 (**BLOCK-02/03**).
5. Fix Qdrant; make readiness non-200 when degraded (**BLOCK-05**).
6. Then, in an isolated environment, close the four untested mandatory gates: authorization and
   cross-user isolation; the adversarial safety suite; a real restore drill with measured RPO/RTO;
   and agent/tool containment including the `verify-engineer-workspaces.sh` report `CLAUDE.md`
   requires for Phase J.
7. Re-audit. Most of §10–§18 only becomes testable after step 4.

**A failed certification with accurate evidence is a successful audit.** The system is not ready; the
reasons are specific, mostly deployment-level, and each has a named fix.

---

*Generated by [Claude Code](https://claude.ai/code)*
