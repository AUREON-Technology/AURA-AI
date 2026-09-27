# AURA AI — Unresolved Production Release Blockers

**Assessed:** 2026-09-27 (UTC)
**Repo revision audited:** `71dd01b` (`main`), shallow clone, 187 commits
**Production target audited:** `aureon-ai.tech` (A `152.239.118.239`, AAAA `2a02:4780:79:5dcd::1`)
**Verdict:** **NOT PRODUCTION CERTIFIED**

This file lists **only unresolved blockers**. Non-blocking findings, known limitations, and the
areas that could not be tested are in `AURA_REAL_PRODUCTION_TEST_REPORT.md`. Every item here was
observed against the live deployment, not inferred from source.

A note on scope, because it changes how this list should be read: the four P0 items below are not
four independent defects. They are four symptoms of one condition — **the production host is
serving a pre-v3 build of the application, and the deploy pipeline that would replace it has not
run successfully on any recent commit.** Fixing BLOCK-04 (the pipeline) is the precondition for
BLOCK-02 and BLOCK-03 resolving at all.

---

## BLOCK-01 — P0 — HTTPS on the product's own domain serves a certificate for an unrelated site

| Field | Value |
| --- | --- |
| Component | Ingress / TLS (`deploy/nginx/templates/aura.conf.template`) |
| Environment | Production, `https://aureon-ai.tech` |
| Severity | **P0** |
| Blocking | **Yes** |

**Reproduction**
```
openssl s_client -proxy <proxy> -connect aureon-ai.tech:443 -servername aureon-ai.tech
```

**Expected:** a certificate whose SAN contains `aureon-ai.tech`, terminating on AURA's nginx and
proxying to the gateway.

**Actual:** a valid Let's Encrypt certificate for a **different domain**:

```
subject=CN = velvet-group.shop
issuer=C = US, O = Let's Encrypt, CN = YE1
notBefore=Aug 29 11:50:23 2026 GMT   notAfter=Nov 27 11:50:22 2026 GMT
X509v3 Subject Alternative Name:
    DNS:velvet-group.shop, DNS:www.velvet-group.shop
```

Requests that ignore the name mismatch (`curl -k`) reach a **Django** application, not AURA —
`Bad Request (400)`, `Vary: Cookie, origin`, `Content-Language: ar`. AURA is FastAPI and does not
produce that page. Verified TLS (`curl` with the trust store) fails outright:
`SSL: no alternative certificate subject name matches target host name 'aureon-ai.tech'`,
reproduced on two consecutive attempts.

**Impact — this is the blocker that ends the user story.** The shipped Flutter client targets
`https://aureon-ai.tech` by default (`aura_client/lib/core/config/config.dart:17`) and its own
release policy *requires* HTTPS —
`'Release BACKEND_URL must use https (Android/iOS block cleartext).'`
(`aura_client/lib/core/config/backend_url_policy.dart:30`). Android and iOS validate the
certificate name, so every release build fails to connect before it sends a single byte of
application data. There is no in-app workaround and no user-side workaround.

**Root cause (as far as external evidence supports):** the host answers `:443` from a server block
belonging to another site. AURA's own HTTPS block — which the repo template defines with
`server_name ${AURA_DOMAIN}` and the matching Let's Encrypt paths — is either not rendered, not
loaded, or not selected on that host. External evidence cannot distinguish these; host access is
required. Note the host also serves a second, unrelated production site, so this is a shared-host
vhost problem, not a certificate-renewal problem (the certificate present is current, just for the
wrong name).

**Recommended fix:** on the host, confirm which nginx instance owns `:443` and that AURA's rendered
vhost is present and selected for `aureon-ai.tech`; issue/renew a certificate whose SAN covers
`aureon-ai.tech`; re-verify with a **name-verifying** client, not `-k`. If AURA's nginx is behind a
host-level nginx, apply `deploy/nginx/host-proxy.conf.example`'s override pattern, which exists for
exactly this topology.

**Regression test:** a deploy-time smoke check that fails the deploy when
`openssl s_client -servername $AURA_DOMAIN` returns a certificate whose SAN omits `$AURA_DOMAIN`.
A check that uses `-k`, or that only asserts HTTP 200, would have passed throughout this outage.

---

## BLOCK-02 — P0 — Production exposes no `/api/v3/*` surface; the shipped client speaks only v3

| Field | Value |
| --- | --- |
| Component | Gateway (`aura_backend/aura_service.py`, `aura_core/api/v3.py`) |
| Environment | Production, `http://aureon-ai.tech` |
| Severity | **P0** |
| Blocking | **Yes** |

**Reproduction**
```
curl -sS http://aureon-ai.tech/openapi.json | python3 -c "import json,sys; print(json.load(sys.stdin)['info'], len(json.load(open('/dev/stdin'))['paths']))"
curl -sS -X POST -d '{}' -H 'Content-Type: application/json' http://aureon-ai.tech/api/v3/chat/stream
```

**Expected:** `info.title == "AURA AI Gateway"`, `version == "3.0.0"`
(`aura_core/api/v3.py:1236-1238`), with the `/api/v3` router mounted
(`aura_core/api/v3.py:102`).

**Actual:** `{'title': 'AURA AI OS Backend', 'version': '1.0'}` — **24 paths, none under
`/api/v3`.** The live surface is:

```
api/v2/auth (3)  api/v2/chat  api/v2/sync  api/v2/insights (3)
api/chat/agent   api/chat/stream
api/memory/{save,list,extract,delete}
api/rag/{ingest,query}
api/learning/{log,evaluate,export,build_dataset}
api/automation/{analyze-email,analyze-issue}
api/telemetry/log
health
```

Every one of the 10 client-critical v3 endpoints tested returns `404 {"detail":"Not Found"}`:
`/api/v3/chat/stream`, `/api/v3/auth/nonce`, `/api/v3/memory`, `/api/v3/knowledge/search`,
`/api/v3/agents`, `/api/v3/complete`, `/api/v3/sync`, `/api/v3/voice/speak`,
`/api/v3/engineering/workspaces`, `/api/v3/insights`. The voice WebSocket handshake to
`/api/v3/voice/live` also returns `404`.

**The repository itself classifies this exact surface as what must be gone.**
`aura_backend/tests/unit/test_v1_locked_routes.py` lists 14 paths under `UNMOUNTED` and asserts
each returns **404**. Production serves all 14 of them as live, documented routes. The test passes
locally against `aura_service.py` and says nothing about the artifact actually deployed — which is
the precise gap this audit exists to find.

**Impact:** the shipped client makes **53 call sites across 25 distinct `/api/v3/*` endpoints**
(`grep -rho "api/v3/[a-z0-9/_-]*" aura_client/lib/`). Against this deployment every one of them
404s. Chat, memory, knowledge/RAG, voice, sync, insights, agents, and the Engineer workspace
surface are all unreachable. Combined with BLOCK-01, the client fails on two independent grounds.

**Root cause:** the deployed artifact predates the v3 cutover. See BLOCK-03 for the dating evidence
and BLOCK-04 for why it was never replaced.

**Recommended fix:** deploy the current `main` (`aura_service.py`) once BLOCK-04 is resolved, then
assert the v3 surface from outside before declaring the deploy good.

**Regression test:** a post-deploy smoke check asserting `info.version` starts with `3.` **and**
that a known `/api/v3` route does not 404. `/health` returning 200 is not sufficient — it returned
200 throughout.

---

## BLOCK-03 — P0 — The deployed artifact is an unidentified build that predates current `main`

| Field | Value |
| --- | --- |
| Component | Release engineering / deployment provenance |
| Environment | Production |
| Severity | **P0** |
| Blocking | **Yes** |

**Reproduction:** no endpoint on the deployed application reports a build, revision, or version
beyond `info.version: "1.0"`. `/health` exposes dependency status only.

**Expected:** the running revision is identifiable from outside, and matches a known commit.

**Actual:** the deployed revision **cannot be determined** from the live system. It can only be
*bounded*, from two independent observations:

1. **It predates the Gemini removal.** `/health` reports a `gemini` dependency:
   ```json
   "gemini": {"configured": true, "probed": false}
   ```
   Current `main` has no such field, and `aura_backend/config.py:57-59` documents its removal in
   terms that describe this exact artifact:
   > `gemini_api_key` used to live here. Removed with the provider: a settings field for a banned
   > API is an invitation to wire it back up, and the /health endpoint was reporting
   > `gemini: {configured: ...}` off the back of it long after nothing read the value.

   So production is running a build from before that commit. Stated precisely: this proves the
   build is **stale**, and that it still *advertises* a hosted-vendor key. It does **not** prove
   Gemini serves inference there — the comment says nothing read the value. Whether that build
   routes inference to a hosted vendor is **UNKNOWN** from outside and needs host access to settle.
   Until it is settled, the self-hosting invariant is unverified **in production**, however
   thoroughly `tests/architecture/test_no_external_inference.py` enforces it in the repo (it scans
   every `.py` under `aura_backend/` with only two self-referential exemptions, so the repo-side
   guarantee is sound — it simply does not reach the deployed artifact).

2. **It predates the v3 cutover entirely** (BLOCK-02).

The audited clone is shallow (187 commits, earliest 2026-09-13), so the age of the deployed build
cannot be narrowed further from this environment.

**Impact:** nothing about production can be reasoned about from the repository. Every code-level
assurance in `docs/` and every passing test describes an artifact that is not running. Incident
response, rollback, and security patching are all unanchored: there is no way to know what is
deployed, therefore no way to know what a rollback would restore or which CVEs apply.

**Recommended fix:** surface build provenance at runtime — commit SHA and build timestamp on a
`/health` or `/version` field, injected at image build. Then reconcile: identify what is running,
and deploy a known revision deliberately.

**Regression test:** post-deploy assertion that the reported SHA equals the SHA just deployed.

---

## BLOCK-04 — P0 — The deploy pipeline has not run successfully on recent commits; CI fails at startup

| Field | Value |
| --- | --- |
| Component | CI/CD (`.github/workflows/*`) |
| Environment | GitHub Actions |
| Severity | **P0** |
| Blocking | **Yes** |

**Reproduction:** workflow runs for `main` at `71dd01b` (2026-09-27T06:05Z).

**Actual:**

| Workflow | Run | Conclusion | Wall clock |
| --- | --- | --- | --- |
| Backend CI | #281 | **failure** | ~5 s |
| Deploy VPS | #182 | **skipped** | — |
| Agent Evaluation | #85 | **failure** | ~3 s |
| control-plane-deploy | #71 | **failure** | 0 s |
| inference-plane-deploy | #71 | **failure** | 0 s |

Backend CI's four jobs individually: `pip-audit` failure (1 s), `test` failure (3 s),
`backup-scripts` failure (2 s), `docker-build` skipped. **Jobs that fail in 1–3 seconds did not run
their test bodies** — this is a workflow/runner-level failure (a setup step, action resolution, or
an account-level condition), not a suite of failing assertions. Job logs return HTTP 404 and have
expired, so the precise cause could not be read and is recorded here as **UNKNOWN**; the *shape* of
the failure is nonetheless unambiguous from the timings.

**`Deploy VPS` is gated on `Backend CI` succeeding on `main`**
(`workflow_run` + `conclusion == 'success'`, `deploy-backend.yml`). Because Backend CI fails, the
deploy is **skipped**. Per `CLAUDE.md`, "merging to `main` is the entire deploy action, there is no
separate manual step" — so with the gate shut, **merging to `main` deploys nothing.** This is the
root cause of BLOCK-02 and BLOCK-03: current code cannot reach production by the documented path.

**Impact:** no route to production for any fix, including the fixes for the other blockers. Also
means CI provides no gating signal at all right now — including `pip-audit`, so dependency
vulnerabilities are ungated.

**Recommended fix:** re-run one failed job to surface a live log before it expires, fix the
startup-level cause, confirm Backend CI green on `main`, then confirm `Deploy VPS` actually runs
and deploys rather than skipping.

**Regression test:** an alert on `Deploy VPS` concluding `skipped` on a `main` push — the current
failure mode is silent precisely because "skipped" is not "failed."

---

## BLOCK-05 — P1 — Vector retrieval is down in production; `/health` reports HTTP 200 while degraded

| Field | Value |
| --- | --- |
| Component | Qdrant adapter + health contract |
| Environment | Production |
| Severity | **P1** |
| Blocking | **Yes** for any retrieval-dependent capability |

**Reproduction:** `curl -sS http://aureon-ai.tech/health`, sampled 12× over ~24 s.

**Actual:** 12/12 samples:
```json
{"status":"degraded","degraded":["qdrant"],
 "dependencies":{"qdrant":{"configured":true,"reachable":false,
   "error":"'NoneType' object has no attribute 'get_collections'"},
  "postgres":{"configured":true,"reachable":true},
  "redis":{"configured":true,"reachable":true},
  "gemini":{"configured":true,"probed":false}}}
```

The error is not a network timeout — the client object is `None`, i.e. it was never constructed.
Postgres and Redis are reachable, so this is specific to Qdrant.

**Two secondary defects visible in the same evidence:**

- **The endpoint returns HTTP `200` while `status` is `degraded`.** Any load balancer, uptime
  monitor, or container probe keyed on status code sees a healthy service. A degraded stack is
  indistinguishable from a healthy one to every automated consumer.
- **Inconsistent responses across samples.** One earlier request in the same session returned
  `status: healthy` with `qdrant.reachable: true`, against 13 subsequent `degraded` responses. Two
  backends in disagreement, or a transient at first touch — not distinguishable from outside, and
  worth settling, because a fleet where one instance has retrieval and another does not produces
  user-visible nondeterminism.

**Impact:** RAG and any vector-backed memory recall cannot work on the affected instance(s). Users
would see answers silently missing retrieved context rather than an error.

**Recommended fix:** fix Qdrant client construction on the host; make `/health` return `503` when
`degraded` is non-empty (or add a separate `/health/ready` that does, and point probes at it —
note `/health/ready` and `/health/live` currently 404); reconcile the instance disagreement.

**Regression test:** assert non-200 from the readiness endpoint when any dependency is
unreachable — a test the current contract would fail.

---

## Mandatory gates failed

Per the certification criteria, these gate categories are unmet and independently block release:

- **Broken core user workflow** — BLOCK-01, BLOCK-02: no user journey completes.
- **Critical inference path unverified in production** — BLOCK-02, BLOCK-03: no v3 kernel surface
  deployed; provider configuration of the running build unknown.
- **Severe production instability** — BLOCK-05: retrieval down, degradation invisible to monitoring.
- **Deployment integrity** — BLOCK-03, BLOCK-04: unidentified artifact, no working path to replace it.

Additionally, the following mandatory gates are **unmet because they are untested**, which the
criteria treat as blocking rather than passing: authorization/RBAC beyond unauthenticated rejection,
prompt-injection and safety enforcement, backup/restore and disaster recovery, and unsafe-tool /
agent-execution containment. All four were blocked by the absence of the surface they would be
tested against, not merely by missing credentials. See the test report's coverage table.

---

*Generated by [Claude Code](https://claude.ai/code)*
