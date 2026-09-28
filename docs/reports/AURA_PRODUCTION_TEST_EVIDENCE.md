# AURA AI — Production Test Evidence

Concise command/output records backing `AURA_REAL_PRODUCTION_TEST_REPORT.md`. No credentials,
tokens, keys, or user data are reproduced here. Where a probe touched the live system it was
read-only unless noted; no synthetic records were written, because authentication could not be
obtained (see EV-09).

**Window:** 2026-09-27, ~17:13–17:30 UTC · **Vantage:** Claude Code cloud container, egress via
agent HTTPS proxy (`127.0.0.1:46325`) · **Repo:** `71dd01b`

> **Vantage caveat that limits several results.** Outbound TCP from this container succeeds on
> ports 80/443 and times out elsewhere (control: `140.82.121.4:22` → timeout, while
> `152.239.118.239:80` and `:443` connect). HTTPS is also terminated and re-signed by the agent
> proxy. Consequently: (a) any port-exposure result for non-web ports is **inconclusive from here**
> and is reported as NOT TESTABLE rather than PASS; (b) origin certificate chain validation must be
> read from the CONNECT-tunnel capture (EV-02), not from a normal `curl` verification.

---

## EV-01 — DNS and topology discovery

```
$ python3 -c "socket.getaddrinfo(...)"
A     aureon-ai.tech       -> 152.239.118.239
AAAA  aureon-ai.tech       -> 2a02:4780:79:5dcd::1
      www.aureon-ai.tech   -> 152.239.118.239, 2a02:4780:79:5dcd::1
      dev.aureon-ai.tech   -> gaierror (NXDOMAIN)
```

Documented production VPS (comment in `.github/workflows/deploy-backend.yml`): `169.58.188.43`.

```
$ curl -m 15 http://169.58.188.43/health    -> (28) timed out, 0 bytes
$ curl -m 15 https://169.58.188.43/health   -> (35) Recv failure: Connection reset by peer
```

**The live domain does not resolve to the documented VPS address.** Whether AURA was migrated to a
different host or DNS was repointed cannot be determined from outside. → Report §2.

## EV-02 — `:443` serves a certificate for an unrelated domain (BLOCK-01)

```
$ openssl s_client -proxy 127.0.0.1:46325 -connect aureon-ai.tech:443 -servername aureon-ai.tech
subject=CN = velvet-group.shop
issuer=C = US, O = Let's Encrypt, CN = YE1
notBefore=Aug 29 11:50:23 2026 GMT
notAfter=Nov 27 11:50:22 2026 GMT
X509v3 Subject Alternative Name:
    DNS:velvet-group.shop, DNS:www.velvet-group.shop
```

Name-verifying client, two consecutive attempts:

```
$ curl https://aureon-ai.tech/health/ready
curl: (60) SSL: no alternative certificate subject name matches target host name 'aureon-ai.tech'
```

Content behind it (`-k`) is a Django application, not AURA:

```
HTTP/1.1 400 Bad Request
Server: nginx/1.24.0 (Ubuntu)
Content-Language: ar
Vary: Cookie, origin
Strict-Transport-Security: max-age=31536000; includeSubDomains; preload
<title>Bad Request (400)</title>
```

Note the `Strict-Transport-Security` header belongs to **that** application; AURA's own surface
emits no security headers (EV-07). → BLOCK-01.

## EV-03 — `:80` serves a pre-v3 AURA build (BLOCK-02, BLOCK-03)

```
$ curl http://aureon-ai.tech/openapi.json
{"openapi":"3.1.0","info":{"title":"AURA AI OS Backend","version":"1.0"}, ...}
total paths: 24
```

Repo expectation (`aura_core/api/v3.py:1236-1238`): `title="AURA AI Gateway"`, `version="3.0.0"`.

Live path inventory (24, grouped) — **no `/api/v3` prefix present**:

```
api/v2/auth (3) · api/v2/chat · api/v2/sync · api/v2/insights (3)
api/chat/agent · api/chat/stream
api/memory/{save,list,extract,delete} · api/rag/{ingest,query}
api/learning/{log,evaluate,export,build_dataset}
api/automation/{analyze-email,analyze-issue} · api/telemetry/log · health
```

Direct probes, all `404 {"detail":"Not Found"}`:

```
POST /api/v3/chat/stream · /api/v3/auth/nonce · /api/v3/memory · /api/v3/knowledge/search
     /api/v3/agents · /api/v3/complete · /api/v3/sync · /api/v3/voice/speak
     /api/v3/engineering/workspaces · /api/v3/insights          -> 404 (10/10)
GET  /api/v3/voice/live (WebSocket upgrade headers)              -> 404
```

Root banner (`:80`), which is **not** in the repo's nginx template (its `:80` block 301-redirects
and serves only `/healthz` + ACME):

```
$ curl http://aureon-ai.tech/
<h1>AURA AI backend is up. Client web build not available yet.</h1>
$ grep -rn "backend is up" deploy/ aura_backend/    -> (no match)
```

**Cross-reference:** `aura_backend/tests/unit/test_v1_locked_routes.py` lists 14 of these live paths
under `UNMOUNTED` and asserts each returns 404. → BLOCK-02.

## EV-04 — Stale `gemini` key dates the build (BLOCK-03)

```
$ curl http://aureon-ai.tech/health
"gemini": {"configured": true, "probed": false}
```

`aura_backend/config.py:57-59` (current `main`):

> `gemini_api_key` used to live here. Removed with the provider: … the /health endpoint was
> reporting `gemini: {configured: ...}` off the back of it long after nothing read the value.

Repo-side enforcement is genuine and broad — `tests/architecture/test_no_external_inference.py`
scans **every** `.py` under `aura_backend/` (`BACKEND.rglob("*.py")`) with exactly two
self-referential exemptions. It therefore proves the *repository* is clean; it cannot speak to the
deployed artifact. Correct reading: the build is **stale and still advertises the key**; whether it
*routes* inference to a hosted vendor is **UNKNOWN** from outside. → BLOCK-03.

## EV-05 — Health contract: degraded under a 200, and inconsistent (BLOCK-05)

12 sequential samples over ~24 s — **12/12 `degraded`**, HTTP **200** every time:

```
{"status":"degraded","degraded":["qdrant"],
 "dependencies":{"qdrant":{"configured":true,"reachable":false,
   "error":"'NoneType' object has no attribute 'get_collections'"},
  "postgres":{"configured":true,"reachable":true},
  "redis":{"configured":true,"reachable":true},
  "gemini":{"configured":true,"probed":false}}}
```

Latency (12 samples): min 0.151 s, max 0.290 s, ≈p50 0.24 s.

**One earlier sample in the same session returned `status: healthy` with `qdrant.reachable: true`**
(1 of 14 total observations). Recorded as observed; two disagreeing instances vs. a first-touch
transient cannot be distinguished from outside.

Absent endpoints: `/health/ready`, `/health/live`, `/healthz`, `/api/v3/health` → all `404`. The
`/healthz` in the repo's nginx template is not being served. → BLOCK-05.

## EV-06 — Authentication and attestation are enforced (PASS)

Method: deliberately invalid body (`{"__aura_synthetic_probe__":true}`) on every documented
endpoint, so that a `401/403` proves an auth-first gate while a `422` would reveal validation
running before authentication. **No data was written.**

```
22/24  AUTH-ENFORCED
        403 {"detail":"Device attestation validation failed."}   (15 endpoints)
        401 {"detail":"Authorization header is required."}       ( 7 endpoints)
 2/24  by design unauthenticated: /api/v2/auth/{refresh,revoke}  (422, token is the credential)
 1/24  intentionally public: /health                             (200)
```

Follow-up with a bogus token:

```
POST /api/v2/auth/refresh {"refresh_token":"aura-synthetic-invalid-token-000"}
  -> 401 {"detail":"Refresh token is invalid, expired, or already used."}     PASS
POST /api/v2/auth/revoke  (same invalid token)
  -> 200 {"status":"revoked"}                                                P4 (see report §7)
```

Full transcript: `scratchpad/evidence/auth_probe.txt`. → Report §7.

## EV-07 — Security headers absent on AURA's surface (P2)

```
$ curl -D - http://aureon-ai.tech/health
HTTP/1.1 200 OK
server: nginx/1.24.0 (Ubuntu)
date: ...
content-type: application/json
content-length: 307
```

No `Strict-Transport-Security`, `X-Content-Type-Options`, `Referrer-Policy`, `X-Frame-Options`,
`Content-Security-Policy`, or `Cross-Origin-*`. Contrast EV-02, where the co-hosted unrelated
application does send them. → Report §9.

## EV-08 — CORS allows any origin (P2)

```
$ curl -X OPTIONS -H 'Origin: https://attacker.example' \
       -H 'Access-Control-Request-Method: POST' http://aureon-ai.tech/api/v2/sync
HTTP/1.1 200 OK
access-control-allow-origin: *
access-control-allow-methods: DELETE, GET, HEAD, OPTIONS, PATCH, POST, PUT
access-control-max-age: 600
```

Identical response for `Origin: https://aureon-ai.tech` — the allowlist in `config.py`
(`allowed_origins`) is not in effect. **No `access-control-allow-credentials` was returned**, which
bounds real exploitability (browsers will not attach credentials to a wildcard origin), so this is
rated P2, not P1. → Report §9.

## EV-09 — No configuration or secret exposure (PASS)

```
/.env  /.git/config  /docker-compose.yml  /deploy/.env  /static/../.env
/admin  /console  /console/login  /flower  /grafana  /prometheus
  -> all 200 with the placeholder banner; no file contents, no secrets, no admin surface
```

**Secrets search result: NOT FOUND.** No passwords, API keys, tokens, service credentials, or
private URLs were observed in any production response or header during this audit.

Consequence for coverage: no credential material exists in this environment and attestation is
enforced (EV-06), so authorization/RBAC/IDOR, memory isolation, RAG, planning, agents, tools, and
the adversarial safety suite are **BLOCKED**, not passed. See report §4 for the isolated procedure
that would unblock them.

## EV-10 — Rate limiting not observed (PARTIAL)

```
$ 30 sequential GET /health   -> 30 × 200, no 429
```

Deliberately low volume; no load was applied. `/health` is plausibly exempt, and authenticated
endpoints could not be reached, so this is **inconclusive** for the limiter as configured — not
evidence of absence on protected routes. → Report §24.

## EV-11 — Non-web port exposure: NOT TESTABLE from here

TCP connect attempts to `5432, 6379, 6333, 6334, 11434, 8000, 9090, 3000, 9093, 3100, 2375, 2376,
22, 8080, 5555` on `152.239.118.239` all timed out. **Control invalidates the inference:**

```
152.239.118.239:80  -> OPEN      152.239.118.239:443 -> OPEN
140.82.121.4:22     -> TimeoutError   (public SSH, should connect)
```

The container blocks non-80/443 egress, so the uniform timeouts are explained by the vantage point.
Reported as **NOT TESTABLE**, not PASS. Static corroboration only: `deploy/docker-compose.prod.yml`
publishes host ports on nginx alone (`"80:80"`, `"443:443"`), consistent with SEC-12.
Correct procedure: scan from an external host, or enumerate listeners on the VPS itself.

## EV-12 — CI/CD is not delivering to production (BLOCK-04)

`main` @ `71dd01b`, 2026-09-27T06:05Z:

| Workflow | Run | Conclusion | Wall clock |
| --- | --- | --- | --- |
| Backend CI | #281 | failure | ~5 s |
| **Deploy VPS** | #182 | **skipped** | — |
| Agent Evaluation | #85 | failure | ~3 s |
| control-plane-deploy | #71 | failure | 0 s |
| inference-plane-deploy | #71 | failure | 0 s |

Backend CI jobs: `pip-audit` failure (1 s), `test` failure (3 s), `backup-scripts` failure (2 s),
`docker-build` skipped. Sub-3-second failures mean **no test body executed**. Job logs return
HTTP 404 (expired), so this workflow's precise cause is **UNKNOWN**; the shape is not.

`Deploy VPS` triggers on `workflow_run` of `Backend CI` with
`conclusion == 'success'` → permanently **skipped** while CI fails. → BLOCK-04.

**Plane-deploy startup failure, cause identified.** The two 0-second failures are a different fault
from Backend CI's. Re-checked on PR #128's head `62ce219`:

```
control-plane-deploy.yml   run #72  failure  0 s   list_workflow_jobs -> total_count: 0
inference-plane-deploy.yml run #72  failure  0 s   list_workflow_jobs -> total_count: 0
```

Zero jobs created = startup failure. Cause, confirmed by inspection:

```yaml
# .github/workflows/control-plane-deploy.yml  (same in inference-plane-deploy.yml)
jobs:
  deploy:
    if: |
      (... ) && secrets.RAILWAY_TOKEN != ''      # <-- `secrets` is not available in a job-level if:
```

Scan across all workflows — 4 occurrences, and nothing else does it:

```
control-plane-deploy.yml    job 'deploy'   references secrets in job-level if:
control-plane-deploy.yml    job 'skipped'  references secrets in job-level if:
inference-plane-deploy.yml  job 'deploy'   references secrets in job-level if:
inference-plane-deploy.yml  job 'skipped'  references secrets in job-level if:
```

Corroborating signals: the API reports both runs by **file path** rather than their `name:`
(`Deploy Control Plane (Railway)`), which GitHub does when it cannot evaluate the file — yet the YAML
parses cleanly and carries that `name`; and both runs are attributed to a `push` event although
neither workflow declares a `push` trigger. Reproduces identically on `main` (`71dd01b`, runs #71)
and on the prior branch (`600402a`, runs #70), so it is **pre-existing and not PR #128's**.

## EV-13 — Second deploy target is not deployed

```
$ curl https://aura-production-a31f.up.railway.app/health
404 {"status":"error","code":404,"message":"Application not found","request_id":"..."}
$ curl https://aura-production-a31f.up.railway.app/v1/models        -> same
```

Hugging Face inference Space: repo contains only placeholder hostnames
(`https://<your-username>-aura-inference-vllm.hf.space/v1`, `deploy/.env.example:131`); no concrete
Space is referenced. Both plane deploy workflows fail at startup (EV-12). → Report §2, §11.

## EV-14 — Local suites (repo-side verification, `71dd01b`)

```
$ pytest                     # aura_core suite, in-memory substrate
2776 tests collected (47 deselected)
FAILED tests/architecture/test_api_reachability.py::test_the_committed_matrix_is_current
  AssertionError: docs/guides/api-reachability.md is stale --
  run `python scripts/api_reachability.py` from aura_backend/
  (diff is line-number drift, e.g. host_daemon_page.dart:147 -> :146)
→ 1 failed; all other collected tests passed or skipped

$ TESTING=True python -m unittest discover -p "test_*.py"      # legacy tree
Ran 100 tests in 9.835s
OK (skipped=4)
```

On PR #128's docs-only head, **Backend CI did not run at all** (its path filters exclude
`docs/**`), so the suites below are this audit's own local execution, not a CI result.

Both suites are gated in CI and both are required to pass. The single pytest failure is a
documentation-freshness check (P3), fixed by regenerating the matrix. It is a genuine red on `main`
and distinct from the CI startup failure in EV-12 — both are real, and neither explains the other.

## EV-15 — Static isolation checks (repo-side, PASS)

```
docker-socket-proxy allowlist parity:
  deploy/docker-compose.prod.yml : CONTAINERS=1 IMAGES=0 POST=1 EXEC=0 NETWORKS=0 VOLUMES=0 INFO=0
  deploy/docker-compose.dev.yml  : CONTAINERS=1 IMAGES=0 POST=1 EXEC=0 NETWORKS=0 VOLUMES=0 INFO=0
→ parity holds on both security-relevant keys (EXEC, IMAGES) per CLAUDE.md's standing rule

published host ports (prod): nginx only — "80:80", "443:443"   (SEC-12 satisfied, statically)
published host ports (dev):  none
internal: true — prod 5, dev 3 (dev defines fewer networks; not a parity defect on its face)
tests/unit/test_sandbox_exec_surface.py present (pins both halves of the no-exec rule)
```

Static only — no runtime verification of the sandbox was possible (no Docker API access, and per
`CLAUDE.md` the default suite's runner never touches it).

## EV-16 — Repository's own recorded position (corroborating)

```
docs/production/gap_register.yaml : open 31 · built_not_proven 13 · in_progress 1 · closed_stale 3
docs/reports/AURA_AI_PRODUCTION_READINESS_SCORE.md :
  "No dimension can score above 8, because this system has never served a real user."
```

Consistent with this audit. Recorded because it establishes that the gap between built and proven
was already known internally; what this audit adds is that production is additionally running a
**different, older** artifact than the one those documents describe.

---

## EV-17 — GitHub Actions has executed nothing since 2026-09-13 (BLOCK-06)

**Date:** 2026-09-27 ~17:55 UTC. **Classification:** MEASURED.

The decisive evidence of this remediation. Recorded with a deliberate re-run and a control.

Last successful run of *any* workflow in the repository (340 successes total, newest first):

```
list_workflow_runs?status=success  ->
  Deploy VPS  #151  success  2026-09-13T14:40:45Z  head 78bdc83
  Backend CI  #220  success  2026-09-13T14:34:08Z  head 78bdc83
  Backend CI  #219  success  2026-09-13T14:23:52Z
```

`78bdc83` is dated `2026-09-13 17:34:05 +0300` in the local checkout — the newest commit that has
ever reached production.

`Backend CI` #281 on `main` @ `71dd01b`, then re-run deliberately (`rerun_failed_jobs`) to obtain
a fresh log:

```
attempt 1  pip-audit       failure  06:05:46 -> 06:05:47   (1s)
attempt 1  test            failure  06:05:46 -> 06:05:49   (3s)
attempt 1  backup-scripts  failure  06:05:46 -> 06:05:48   (2s)
attempt 1  docker-build    skipped                          (needs: test)

attempt 2  backup-scripts  failure  17:55:10 -> 17:55:13   (3s)
attempt 2  pip-audit       failure  17:55:10 -> 17:55:13   (3s)
attempt 2  test            failure  17:55:11 -> 17:55:14   (3s)
```

Reproduced identically. Billing for the whole run:

```
get_workflow_run_usage 36299091078 ->
  billable.UBUNTU.total_ms: 0
  jobs: 8   every job_runs[].duration_ms: 0
  run_duration_ms: 8000
```

Log retrieval, with freshly minted signed URLs:

```
get_job_logs 108674437732 (created 3 minutes earlier) -> HTTP 404
get_job_logs run=36299091078 failed_only return_content ->
  pip-audit       HTTP 404
  test            HTTP 404
  backup-scripts  HTTP 404
```

`get_check_run 108674437732` -> `output.title ""`, `output.summary ""`, `output.text ""`.

**Control — is this specific to `Backend CI`?** No:

```
Client CI        #129  failure  3s   (2026-09-26)
Agent Evaluation #85   failure  3s   (2026-09-27)
Backend CI       #281  failure  4s   (2026-09-27)
control-plane-deploy  #74  failure  0s, 0 jobs
inference-plane-deploy #74  failure  0s, 0 jobs
Deploy VPS       #183  skipped
```

Repository visibility: `curl https://github.com/anasibrahimgnedy-collab/AURA-AI` unauthenticated
-> **HTTP 404**, i.e. private, so its Actions minutes are metered.

**Reading.** Zero billable milliseconds plus no log blob means the jobs were rejected at
assignment — before `actions/checkout`, before any step. It affects every workflow regardless of
content, so no workflow's content causes it. Consistent with exhausted Actions minutes or a
reached spending limit on a private repository, with a clean 2026-09-13 boundary.

**Residual unknown, stated:** the billing page was not readable from this vantage, so *which*
entitlement failed — exhausted minutes, a `$0` spending limit, or a payment-method failure — is
**UNKNOWN**. It is not inferred.

---

## EV-18 — Production topology changed mid-audit, and regressed (BLOCK-01)

**Date:** 2026-09-27 ~18:05 UTC. **Classification:** PRODUCTION TESTED.

```
$ getent hosts aureon-ai.tech
2a02:4780:79:5dcd::1   aureon-ai.tech        # was IPv4 152.239.118.239 (EV-01); Hostinger range

$ curl -o /dev/null -w "http=%{http_code} ssl_verify=%{ssl_verify_result}\n" https://aureon-ai.tech/
http=000 ssl_verify=1                        # hostname verification still fails

$ curl -k https://aureon-ai.tech/openapi.json
<!doctype html><html lang="en"><head><title>Bad Request (400)</title></head>
<body><h1>Bad Request (400)</h1><p></p></body></html>

$ curl -k -o /dev/null -w "%{http_code}\n" https://aureon-ai.tech/health/version
400
```

The `Bad Request (400)` body is Django's `ALLOWED_HOSTS` rejection page — the site behind the
address does not recognise `aureon-ai.tech` as a permitted host.

Both addresses that previously served AURA:

```
$ curl -k -H "Host: aureon-ai.tech" https://152.239.118.239/openapi.json
curl: (35) Recv failure: Connection reset by peer      # previously served AURA v1 (EV-03)

$ curl -k -H "Host: aureon-ai.tech" https://169.58.188.43/openapi.json
curl: (35) Recv failure: Connection reset by peer      # documented VPS (vars.VPS_HOST)
```

**Reading.** The stale AURA v1 backend recorded in EV-03 is **no longer reachable at any address
this audit knows of**. BLOCK-01 regressed: production moved from *stale* to *absent*. The 400 at
`/health/version` is Django answering, not AURA — it is not evidence about the new endpoint.

---

## EV-19 — EV-05, EV-07 and EV-08 describe the stale image, not current `main` (CORRECTION)

**Classification:** STATICALLY VERIFIED. Recorded because reading these as code defects would
send a remediation after bugs that do not exist.

`aura_core/api/v3.py` in `build_app`:

```python
@app.get("/health/live")
async def liveness() -> JSONResponse:
    """Process is running. Deliberately checks nothing else -- a liveness probe that fails
    on a dependency outage causes the orchestrator to restart a healthy process, turning a
    degradation into an outage."""
    return JSONResponse(content={"status": "alive"})

@app.get("/health/ready")
async def readiness() -> JSONResponse:
    health = await container.health()
    return JSONResponse(status_code=200 if health["healthy"] else 503, content=health)
```

`aura_core/container.py` provides `register_readiness_check(name, check)` and aggregates it in
`health()`; a falsey `healthy` from any probe makes the whole payload unhealthy. Already used by
`_register_schema_readiness` to gate on the Alembic head.

`aura_service.py::_apply_cors` drops `allow_credentials` when the origin list contains `*`, and
warns. `deploy/nginx/templates/aura.conf.template` sets HSTS, `X-Content-Type-Options`,
`X-Frame-Options`, `Referrer-Policy`, `Permissions-Policy` and CSP across its server blocks.

**Reading.** EV-05's 404s on `/health/ready` and `/health/live`, EV-07's absent headers and
EV-08's `ACAO: *` are all properties of the 2026-09-13 image (EV-17), not of current `main`. They
stay open as *production* findings — unverifiable until a current build deploys — and are **not**
recorded as passes.

---

## EV-20 — Suites after remediation

**Date:** 2026-09-27 ~18:20 UTC. **Classification:** LOCALLY TESTED · FAILURE TESTED.

```
$ pytest tests
2764 passed, 56 skipped, 47 deselected, 82 warnings in 56.12s

$ TESTING=True python -m unittest discover -p "test_*.py"
Ran 100 tests in 5.815s
OK (skipped=4)
```

2820 collected = the 2776 baseline (EV-14) + 44 new tests. **Zero failures**; the baseline's one
failure (DEF-12) is fixed.

New suites:

```
$ pytest tests/unit/test_release_identity.py -q
..................                                                       [100%]   # 18

$ pytest tests/unit/test_workflow_job_contexts.py -q
..........................                                               [100%]   # 26
```

**Negative control** — reintroduced `secrets.RAILWAY_TOKEN != ''` into
`control-plane-deploy.yml`'s job-level `if:`:

```
FAILED test_no_job_level_if_references_secrets[control-plane-deploy.yml]
FAILED test_job_level_if_uses_only_available_contexts[control-plane-deploy.yml]
FAILED TestDeployPreflight::test_the_preflight_publishes_an_output_the_deploy_gates_on[control-plane-deploy.yml]
```

Restored -> 26 pass. The tests detect the defect they were written for.

**Production gate, exercised directly:**

```
AuraSettings(environment=LOCAL).validate()       -> ['at least one LLM provider must be configured and enabled']
AuraSettings(environment=PRODUCTION).validate()  -> ['release.git_sha must be injected in staging/production -- build with
                                                      --build-arg GIT_SHA=$(git rev-parse HEAD) (see Dockerfile, BLOCK-03)',
                                                     ... 5 pre-existing production requirements ...]
```

**Three intermediate failures**, recorded rather than quietly fixed — each a real consequence of a
real change, caught by the suite:

```
FAILED tests/architecture/test_api_reachability.py::test_the_committed_matrix_is_current
FAILED tests/security/test_route_auth.py::test_protected_route_rejects_every_bad_credential[GET-/health/version]
FAILED tests/unit/test_settings_validation.py::TestProductionGate::test_a_correctly_configured_production_deployment_starts
```

Resolved at the cause: matrix regenerated; `/health/version` declared in `test_route_auth.py`'s
`PUBLIC` allowlist with its justification; the production-gate test now sets `AURA_GIT_SHA`
because release identity is now part of a correctly configured production deployment. **No check
was weakened.**

The second is the repository's own architecture working as designed: a new unauthenticated route
cannot be added without a security test forcing the author to declare it.

**Static checks:**

```
$ for f in .github/workflows/*.yml; do yaml.safe_load(f); done
all 10 parse
$ grep -rn "if:" .github/workflows/*.yml | grep "secrets\."
(no matches)
$ yaml.safe_load(docker-compose.prod.yml / docker-compose.dev.yml)
gateway, worker-realtime, worker-ingestion, worker-training, beat -> all carry
GIT_SHA/GIT_REF/BUILD_TIME/DEPLOYMENT_ID (worker-training also AURA_TRAINING_DEPS)
$ bash -n deploy/scripts/vps-update.sh
(clean)
```

---

## EV-21 — The workflow fix is confirmed in CI, not just locally (BLOCK-04 Cause A CLOSED)

**Date:** 2026-09-27 18:24 UTC. **Classification:** PRODUCTION TESTED (of the CI system itself).

The fix was pushed to `claude/aura-production-readiness-c3h5jv` @ `b1b0657`, which touches
`deploy/spaces/inference-vllm/**`'s workflow and so triggers it on `pull_request`.

**Before the fix** — `inference-plane-deploy.yml` #73 @ `a98cee1`:

```
list_workflow_jobs 36337846647 -> total_count: 0
run "name": ".github/workflows/inference-plane-deploy.yml"     <- reported by PATH
conclusion: failure, 0s
```

**After the fix** — #75 @ `b1b0657`:

```
list_workflow_jobs 36340513484 -> total_count: 4
run "name": "Deploy Inference Plane (Hugging Face)"            <- reported by its own name:

validate        failure  18:24:17 -> 18:24:19   (2s)   <- scheduled, then hit BLOCK-06
preflight       skipped                                 <- correct: pull_request, not push to main
not-configured  skipped                                 <- correct: preflight did not run
deploy          skipped                                 <- correct: gated on preflight's output
```

**Reading.** Two independent signals both flip:

1. **Jobs are created.** `total_count` went 0 → 4. The run no longer ends before scheduling, which
   is the definition of the startup failure being gone.
2. **GitHub can read the file's `name:` again.** It reported the run by file path while the file
   was unevaluable; it now reports "Deploy Inference Plane (Hugging Face)". This was one of the
   three signals used to diagnose the fault, and it has reversed.

The three `skipped` jobs are the preflight gating working exactly as designed on a pull request.

`validate` then failed in 2 s with the BLOCK-06 signature — which is the honest limit of what this
evidence proves: **the file is valid and its jobs schedule; whether they can execute is BLOCK-06,
and still cannot be shown from here.**

`control-plane-deploy.yml` has no `pull_request` trigger (only `workflow_run` + `workflow_dispatch`),
so it did not run on this head and its identical fix remains **LOCALLY TESTED** only.

**`Backend CI` #282** did run on this head (`pull_request`, and the PR now touches
`aura_backend/**`) and failed in the same way — further confirmation of EV-17 from a second event
type.

---

## EV-22 — A working v3 Control Plane exists on Railway (BLOCK-01 / BLOCK-02 resolved on that target)

**Date:** 2026-09-27 18:30–18:40 UTC. **Target:** `https://aura-ai-production-03a4.up.railway.app`
**Classification:** PRODUCTION TESTED. **Provenance:** URL supplied by the operator; this audit had
not discovered it, because nothing in the repository, DNS, or the Railway docs names it.

This is the single largest correction to the audit. A reachable, correctly-served AURA v3 gateway
exists. Every "BLOCKED — no reachable deployment" result in the earlier reports was true of
`aureon-ai.tech` and false of the product as a whole.

### It is the real v3 gateway, over valid TLS

```
$ curl -o /dev/null -w "http=%{http_code} ssl_verify=%{ssl_verify_result} time=%{time_total}\n" $U/
http=404 ssl_verify=0 time=0.497s          # 404 at / is correct: v3 mounts no root route

$ curl $U/openapi.json | head -c 160
{"openapi":"3.1.0","info":{"title":"AURA AI Gateway","description":"Enterprise AI platform
gateway. See docs/architecture/00-overview.md","version":"3.0.0"}
```

`AURA AI Gateway` / `3.0.0` is `build_app` in `aura_core/api/v3.py`. **`ssl_verify_result=0`** — the
certificate validates, unlike BLOCK-01's target.

Route surface: **83 paths, 80 of them `/api/v3`**, plus exactly the three extras `build_app`
mounts (`/health/live`, `/health/ready`, `/metrics`). **Zero legacy v1 paths** — the 14 that
`tests/unit/test_v1_locked_routes.py` requires to 404 are absent, so that invariant **holds in
production**. No `/health/version`, which correctly dates the build to before this branch's
release-identity commit.

### The health contract is correct in production (BLOCK-05 — PASS)

```
$ curl $U/health/live      -> 200  {"status":"alive"}
$ curl $U/health/ready     -> 503
{"providers":{"hfspace":{"healthy":false,"detail":"ReadTimeout","latency_ms":5162.9}},
 "index":"aura-knowledge__v1__hash-embed-v1__384","tools":37,"prompts":17,"agents":8,
 "voice":{"tts":"piper-en+piper-ar","stt":"faster-whisper-tiny.en","live":false},
 "environment":"production","healthy":false,
 "checks":{"schema":{"healthy":false,"expected":"0008","revision":"0006",
                     "detail":"database schema is behind the code in this image"}}}
```

Liveness 200 while readiness 503 on a genuinely unhealthy dependency is **exactly** the semantics
EV-19 predicted from the code, now observed. BLOCK-05 moves from "unprovable" to **PASS,
PRODUCTION TESTED** — and it earns its keep immediately, by surfacing the two defects below that
nothing else was reporting.

### Authentication is enforced (PASS)

All 82 GET/POST operations in the spec, probed unauthenticated with a deliberately invalid body so
a 401 proves an auth-first gate and a 422 would reveal a missing one:

```
401: 72    403: 1    200: 4    422: 4    503: 1
```

Every non-gated path is one the repository's own `tests/security/test_route_auth.py` already
declares public or identity-exchange:

```
200  GET  /health/live · GET /metrics · POST /api/v3/auth/nonce · POST /api/v3/auth/desktop/pair/start
422  POST /api/v3/auth/refresh · /auth/revoke · /auth/desktop/pair/poll · GET /integrations/github/callback
503  GET  /health/ready
```

No write was performed and no synthetic record was created, so nothing needed cleanup.

### No insecure bypasses are active (PASS)

From `/metrics`:

```
aura_insecure_mode{bypass="debug"}                 0.0
aura_insecure_mode{bypass="mock_auth"}             0.0
aura_insecure_mode{bypass="mock_attestation"}      0.0
aura_insecure_mode{bypass="safety_disabled"}       0.0
aura_insecure_mode{bypass="rate_limit_disabled"}   0.0
```

All five production gates `AuraSettings.validate()` enforces are confirmed **off in the running
process** — not merely required by code.

### CORS refuses an untrusted origin (PASS — confirms EV-19)

```
$ curl -X OPTIONS $U/api/v3/chat -H "Origin: https://evil.example.com" \
       -H "Access-Control-Request-Method: POST" -D -
HTTP/2 400
access-control-allow-credentials: true
access-control-allow-methods: DELETE, GET, HEAD, OPTIONS, PATCH, POST, PUT
access-control-max-age: 600
```

**No `access-control-allow-origin`** — the hostile origin is not granted. The CORS middleware is
present (so `_apply_cors` ran) and `allow_credentials: true` proves the configured list does *not*
contain `*`, since `_apply_cors` disables credentials in that case. The audit's `ACAO: *` finding
was the stale v1 image, exactly as EV-19 argued.

### Defects found — and these are real, on a live system

| ID | Sev | Finding |
|---|---|---|
| **DEF-19** | **P0** | **Inference is entirely down.** The only provider, `hfspace`, fails its probe with `ReadTimeout` after 5.16 s; `aura_dependency_up{dependency="provider:hfspace"} 0.0`. No LLM is reachable, so chat, planning, agents and grounded answers cannot function. The Inference Plane is the missing half of the deployment. |
| **DEF-20** | **P0** | **Database schema is two migrations behind**: `expected 0008, revision 0006`. `railway.json` declares `preDeployCommand: alembic upgrade head` with `|| exit 1`, so either the running service predates that config or the pre-deploy step did not gate the release. Code expects tables the database does not have. |
| **DEF-21** | **P2** | **No security headers at all.** No HSTS, `X-Content-Type-Options`, `X-Frame-Options`, `Referrer-Policy` or `Permissions-Policy`. Not a stale-image artifact: Railway has **no nginx in the path**, and the policy lived only in the nginx template. Response headers were `content-type`, `date`, `server: railway-hikari`, `x-railway-*` and nothing else. **Fixed on this branch** — see `aura_core/api/security_headers.py`. |
| **DEF-22** | **P2** | **`/metrics` is world-readable**, publishing `aura_insecure_mode`, `aura_dependency_up`, `aura_knowledge_generation`, plan-step and groundedness histograms — 80 series, 13 KB. The repository had already decided this must not be public: nginx answers `deny all; return 404;` for `/metrics` in **both** server blocks. Railway defeats that decision by having no edge. **Fixed on this branch** (opt-in `AURA_METRICS_TOKEN`). No PII or user labels present. |
| **DEF-23** | **P2** | **A dashboard cannot see DEF-19.** `aura_provider_healthy{provider="hfspace"} 1.0` while `aura_dependency_up{dependency="provider:hfspace"} 0.0`. Not a bug: `provider_healthy` reflects the **circuit-breaker state**, which only opens on *routed* traffic (`router.py:215,252`), while `dependency_up` is an **active probe** (`router.py:762`). With no traffic flowing, the breaker stays closed and the gauge reads healthy. An alert built on the obvious metric name stays green through a total inference outage. |
| **DEF-24** | **P3** | Active index is `aura-knowledge__v1__hash-embed-v1__384` — the **hash embedder**, not a semantic model, with `aura_knowledge_generation 0.0` (nothing indexed). Retrieval would be lexical noise. Consistent with an unconfigured embedding provider rather than a defect, but it means RAG quality cannot be meaningfully measured on this deployment. |

### Not established, and not guessed

- **Rate limiting:** 12 rapid unauthenticated requests to `/api/v3/chat` all returned 401 and 10 to
  `/api/v3/auth/nonce` all returned 200 — no 429. `aura_insecure_mode{rate_limit_disabled} 0.0`
  says it *is* enabled, and `_apply_http_limits` sets 100/min, so 10–12 requests would not trip
  it. **Deliberately not probed to the threshold**: that is load generation against production,
  which the brief forbids. Status: configured (metric-confirmed), threshold **not measured**.
- **Deployed revision:** still unidentifiable. A route-set diff against repo HEAD was attempted and
  **abandoned as invalid** — the local router omits sandbox/GitHub/engineering routes when those
  features are unconfigured, so the diff measures settings, not revision. This is precisely
  BLOCK-03, and precisely what `/health/version` fixes.
- **`/api/v2/*` returns 404** (`auth/login`, `sync`, `insights` all probed). `create_app` does call
  `_mount_platform_routers`, and the CORS middleware proves `create_app` ran — so the deployed
  build predates those mounts. **No client impact**: no `/api/v2` call site exists in
  `aura_client/lib/` or `aura_admin/app/`.
- **Authorization/isolation, safety enforcement, agent containment, backup/restore** remain
  **without evidence**. All four need authenticated synthetic identities, and no credential or
  registration path is available to this audit. They stay blocking.

---

## EV-23 — Outage, cause, fix, and the first verified release identity (BLOCK-03 CLOSED)

**Date:** 2026-09-27 19:52 → 21:28 UTC. **Classification:** PRODUCTION TESTED · FAILURE TESTED.

The most useful hour of this engagement, because a change of mine broke production and the
recovery proved several things that had only ever been claimed.

### The outage

| Time (UTC) | State |
|---|---|
| 18:30 | Railway serving a healthy v3 gateway (EV-22) |
| 18:35 | PR #128 merged — carried the **fail-closed** release-identity gate |
| 18:51 | PR #129 merged |
| 19:52 | **502 on every path** — `/health/live`, `/health/ready`, `/openapi.json`, repeatedly |
| 21:26 | PR #130 merged (the fix) |
| **21:27:36** | **200 `{"status":"alive"}`** — recovered ~90 s after the merge |

### Cause — a verified chain, not an inference

Initially recorded as a strong inference. The gap was closed by checking the one link never
verified: whether `validate()` runs at boot at all. It does, at **import**:

```
aura_service.py:192   app = create_app()        <- module level
create_app()       -> load_settings()
load_settings()    -> settings.validate()        <- validate=True by default
```

So `uvicorn` could not import the app. Not a request-time error, not a failed probe — which is
why *every* path returned 502 rather than some working.

| # | Step | Verified by |
|---|---|---|
| 1 | `Dockerfile`: `ARG GIT_SHA=""` → `ENV AURA_GIT_SHA=` | reading the file |
| 2 | Railway builds that Dockerfile and cannot pass our build args | `railway.json` `builder: DOCKERFILE` |
| 3 | The service runs as production | `/health/ready` reported `"environment":"production"` pre-outage |
| 4 | `create_app()` runs at import and calls `validate()` | `aura_service.py:192` |
| 5 | `validate()` recorded `release.git_sha must be injected` for production | executed directly; it was the **first** problem |
| 6 | a non-empty problem list raises `ConfigurationError` | reading the file |

**The lesson, recorded because it is the transferable part:** every other production check in that
block prevents an outcome *worse than not serving* — mock auth, plaintext secrets, an in-memory
system of record. An unidentified build is a **diagnosability** problem. A feature whose entire
purpose is making incidents easier to diagnose must never be able to cause one. The gate was
downgraded to a warning, and a parametrised test now asserts no environment refuses to boot over
release identity.

### BLOCK-03 — CLOSED, and verified in production

```
$ curl https://aura-ai-production-03a4.up.railway.app/health/version
{"app_version":"3.0.0","api_version":"v3",
 "revision":"a517cb689458",
 "git_sha":"a517cb689458488669a3f54f15c3d0840c74e72d",
 "git_ref":"main","build_time":"",
 "deployment_id":"798fce91-04b8-45c8-ad2b-852fb2beae57",
 "inference_contract":"openai-compatible/v1","identified":true}

$ git log -1 --format="%H %s" a517cb6894584886...
a517cb689458488669a3f54f15c3d0840c74e72d Merge pull request #130 from ...
```

**The live `git_sha` is exactly the merge commit of PR #130.** For the first time, "which build is
production running?" is answerable in one request and provably current. The original audit needed
hours of indirect inference to *bound* it (a stale `gemini` key in `/health`).

Note what populated it: `git_sha`, `git_ref: main` and a Railway UUID `deployment_id` all came
from the **platform fallback** (`RAILWAY_GIT_COMMIT_SHA`, `RAILWAY_GIT_BRANCH`,
`RAILWAY_DEPLOYMENT_ID`), with **no build arg wired**. `build_time` is empty because Railway
exposes none — correct, and reported honestly rather than filled with the process clock. The
original build-arg-only design could never have identified this target.

### DEF-21 — FIXED, verified in production

```
$ curl -D - https://aura-ai-production-03a4.up.railway.app/health/live
strict-transport-security: max-age=63072000; includeSubDomains
x-content-type-options: nosniff
x-frame-options: DENY
referrer-policy: strict-origin-when-cross-origin
permissions-policy: geolocation=(), microphone=(), camera=()
```

All five present where an hour earlier there were none, on a target with **no nginx in the path**.
No CSP, as designed. The app-level floor works.

### The readiness contract, earning its keep

`/health/ready` returns **503** with the release block now carried on the payload, so an operator
reading the 503 sees which build produced it without a second request. Both dependency faults are
still visible and still true:

```
"providers":{"hfspace":{"healthy":false,"detail":"ReadTimeout","latency_ms":5157.7}}
"checks":{"schema":{"healthy":false,"expected":"0008","revision":"0006"}}
"healthy":false                                                      HTTP 503
```

### Still open

| ID | Status |
|---|---|
| **DEF-19** (P0) | Inference still down — `hfspace` `ReadTimeout` 5.16 s. Unchanged. |
| **DEF-20** (P0) | Schema still `0006` vs expected `0008`. **New evidence below.** |
| **DEF-22** (P2) | `/metrics` still `200` unauthenticated. Working as designed — the gate is opt-in and needs `AURA_METRICS_TOKEN` set on this target. |

### New finding: the migration gate is not in effect (DEF-20)

A deploy just succeeded — a new revision is serving — while the schema stayed at `0006`. Yet
`railway.json` declares:

```json
"preDeployCommand": "... alembic upgrade head ... || (echo '=== MIGRATION FAILED ==='; exit 1)"
```

Three-way deduction:

- if it ran and succeeded → the schema would be `0008`. It is not.
- if it ran and failed → `exit 1` should have failed the deploy → the new revision would not be
  serving. It is.
- therefore **it did not run**, or it migrated a different database than the app reads.

**Most likely explanation, offered as a hypothesis:** `railway.json` lives at
`aura_backend/railway.json`, and Railway reads it relative to the **service's configured root
directory**. If that root is the repository root, the file is never found — no `preDeployCommand`,
and no `healthcheckPath` either.

That is checkable in one place (Railway → service → Settings → Root Directory) and it matters
beyond DEF-20: **a schema-breaking change could deploy silently.** It also means the
`healthcheckPath` change in PR #130 may have had no effect, and the recovery is attributable to
the gate fix alone — so no credit is claimed for it here.

---

## EV-24 — Inference is intermittent, not down and not recovered (DEF-19, CORRECTION)

**Date:** 2026-09-27 21:49 → 22:41 UTC. **Classification:** MEASURED.

**This entry exists to correct an overstatement.** At 21:49 a single `/health/ready` sample showed
`hfspace: healthy=True, "ok"`, and that was reported as "inference is UP, DEF-19 resolved". One
sample is not a state. Sampled properly:

```
22:38:24Z  False ReadTimeout 5159.4
22:38:46Z  False ReadTimeout 5149.7
22:39:08Z  False ReadTimeout 5150.5
22:39:30Z  False ReadTimeout 5159.7
22:39:52Z  False ReadTimeout 5127.9
22:40:13Z  False ReadTimeout 5144.2
22:40:35Z  False ReadTimeout 5154.9
22:40:52Z  True  ok           225.7
                 --> healthy in 1/8 samples
```

**DEF-19 is INTERMITTENT, approximately 1-in-8. Not resolved.**

The latency split is the diagnostic part, and it rules out the obvious explanation:

| Outcome | Latency |
|---|---|
| healthy | **225.7 ms** |
| unhealthy | **5127-5160 ms** -- a fixed ~5 s ceiling, seven times |

When the provider answers, it answers in a quarter of a second. So this is **not a slow model**; it
is an availability problem with a client timeout being hit exactly. A variable network fault would
scatter those numbers.

**Hypothesis, stated as one:** the Hugging Face Space is asleep and cold-starting. Free-tier Spaces
sleep after inactivity and take tens of seconds to wake, and a probe arriving every ~20 s would
occasionally catch one awake -- which is the shape of 1-in-8 with a fast success. Not confirmed from
here; the Space's own runtime page would confirm it.

**What follows from it:**

- The right fix is keeping the Space warm (a paid tier, or a keep-alive), **not** lengthening the
  readiness probe's timeout. A 5 s readiness probe is a reasonable contract; a dependency that
  cannot meet it seven times out of eight is the thing that is wrong.
- A user's turn succeeds or fails largely at random, which is worse for trust than a clean outage:
  a consistent failure gets reported, an intermittent one gets blamed on the user.
- Any certification run touching inference will be flaky against this target. The journey and
  safety suites skip rather than pass when a turn does not complete, so a flaky provider produces
  *untested*, never a false pass -- but a green run needs the Space warm first.
- The single healthy sample at 21:49 is now explained rather than treated as recovery.

---

## EV-25 — Readiness is green: DEF-19 and DEF-20 both resolved

**Date:** 2026-09-27 23:19-23:22 UTC. **Classification:** PRODUCTION TESTED · MEASURED.
**Live revision:** `b4ab9ad3b66c` = merge commit of PR #134.

The first `HTTP 200` from `/health/ready` in this entire engagement.

```
$ curl -w "\nHTTP=%{http_code}\n" https://aura-ai-production-03a4.up.railway.app/health/ready
{"providers":{"hfspace":{"healthy":true,"detail":"ok","latency_ms":200.4}},
 "index":"aura-knowledge__v1__hash-embed-v1__384","tools":37,"prompts":17,"agents":8,
 "environment":"production","healthy":true,
 "checks":{"schema":{"healthy":true,"revision":"0008"}},
 "release":{"revision":"b4ab9ad3b66c","git_ref":"main","identified":true}}
HTTP=200
```

### DEF-20 — RESOLVED

`schema: healthy=true, revision=0008`, against a code expectation of `0008`. The two migrations
that had been outstanding since before this audit began are applied.

This also confirms the mechanism recorded in EV-23: the migration gate was inert because
`aura_backend/railway.json` was not being read, and it began working once that was corrected. The
prediction and the outcome match, which is worth noting because the alternative explanation (the
`preDeployCommand` running and failing) would have left the schema at `0006`.

### DEF-19 — RESOLVED, and sampled this time

EV-24 recorded an overstatement: a single healthy sample was reported as recovery when the true
rate was 1-in-8. The correction set a bar of five-plus consecutive samples before any claim.
Meeting it:

```
23:19:51Z  True  200.4        (the first reading, from the full payload above)
23:20:33Z  True  200.2  overall=True
23:20:48Z  True  229.9  overall=True
23:21:03Z  True  231.4  overall=True
23:21:18Z  True  196.6  overall=True
23:21:32Z  True  199.9  overall=True
23:21:47Z  True  196.6  overall=True
           --> 7/7 consecutive, 196.6-231.4 ms
```

Seven consecutive successes in a 196-231 ms band, where the failing state was a fixed ~5150 ms
timeout. That is a different regime, not a lucky streak: under the 1-in-8 rate of EV-24, seven
consecutive successes would be a roughly 1-in-2-million coincidence.

Consistent with the cold-start hypothesis in EV-24 -- a warm Space answers in ~200 ms, which is
exactly what the one anomalous sample at 21:49 showed.

### What this unlocks, and what it does not

**Unlocked:** inference-dependent certification is now viable. The E2E journey and adversarial
safety suites skip when a turn cannot complete, so they were producing *untested* rather than
results. With a completing turn they produce evidence -- given credentials.

**Not unlocked:** the verdict. Four mandatory gates still have **no evidence at all**
(authorization/isolation, adversarial safety, agent containment, backup/restore), and
`test_certification_gate.py` refuses certification while that holds. Two gates moving from FAIL to
PASS does not offset four with nothing.

**Still open:** DEF-22 -- `/metrics` returns 200 unauthenticated, because `AURA_METRICS_TOKEN` is
unset. Unchanged by this deploy.

### Release identity, fifth consecutive deploy

`a517cb68` → `0832665f` → `5c527fdd` → `d79c26bb` → `b4ab9ad3`, each matching its merge commit,
all populated by the platform fallback with no build arg wired. The mechanism introduced for
BLOCK-03 has now tracked five deploys unaided.

---

## EV-26 — The DEF-19 recovery reproduces on a different revision (independent window)

**When:** 2026-09-27 23:45–23:46 UTC
**Target:** `https://aura-ai-production-03a4.up.railway.app`
**Live revision:** `8366aa3367ca` — the merge commit of #136, a *different* deploy from the
`b4ab9ad3b66c` that EV-25 measured.

EV-24 is the reason this entry exists. There, a single healthy sample was reported as recovery when
the true rate was 1-in-8, and the correction set the standing rule: five or more consecutive
samples before any claim about inference. EV-25 met that rule inside one window. One window on one
revision is still one observation of one process, so this is the same measurement repeated after a
deploy replaced the container.

```
23:45:19Z  healthy=True  208.2 ms  ok
23:45:29Z  healthy=True  213.2 ms  ok
23:45:40Z  healthy=True  229.3 ms  ok
23:45:51Z  healthy=True  218.2 ms  ok
23:46:01Z  healthy=True  213.0 ms  ok
23:46:12Z  healthy=True  228.6 ms  ok
23:46:22Z  healthy=True  212.8 ms  ok
                     --> 7/7 consecutive, 208.2–229.3 ms
```

Combined with EV-25: **14/14 consecutive healthy across two windows and two revisions**, every
latency inside 196.6–229.3 ms. The failing state EV-24 recorded was a fixed ~5150 ms timeout, so
this is not the same process performing better — it is a different regime, and it now survives a
container replacement.

### What this does and does not add

**Adds:** durability. EV-25 could have been a warm Space in a lucky window; a fresh container
answering identically rules out the reading that the previous result depended on that particular
process's state.

**Does not add:** anything about generation. `/health/ready`'s provider probe establishes that the
inference endpoint is reachable and answering within its contract. It is not a completion, does not
exercise `AIKernel`, and must not be cited as evidence that a user turn produces a useful answer —
that needs the E2E journey suite, which needs credentials. The Maturity column stays honest about
the difference.

### Two unchanged findings, re-checked rather than assumed

| Check | Result | Note |
| --- | --- | --- |
| `GET /metrics` unauthenticated | **`200`** | DEF-22 open. `AURA_METRICS_TOKEN` still unset on the Railway service; the gate is opt-in by design, so an unset token means no gate. |
| `checks.schema.revision` | `0008`, healthy | DEF-20 stays resolved across the new deploy — the corrected Root Directory is not a one-time effect. |

### Release identity, sixth consecutive deploy

`a517cb68` → `0832665f` → `5c527fdd` → `d79c26bb` → `b4ab9ad3` → `8366aa33`, each matching its
merge commit on `main`, all from the platform fallback with no build arg wired. The BLOCK-03
mechanism has now tracked six deploys unaided, including one that changed only client-side Dart.

**Verdict unchanged: NOT PRODUCTION CERTIFIED.** Nothing here touches the four mandatory gates that
have no evidence. A stronger result on a gate that already passed cannot move a verdict that is
refused for absence elsewhere, and recording it as though it could is the failure mode this ledger
exists to prevent.

---

## EV-27 — The Engineer journey completed and fabricated (DEF-26, mandatory gate downgrade)

**When:** 2026-09-28, from two screenshots of the live desktop client
**Target:** AURA Desktop paired to `https://aura-ai-production-03a4.up.railway.app` (status bar:
`Host Connected`, `main`)
**Workspace:** `E:\VELVET` — a real folder open in the editor

This is the first observation in this engagement of a **core user journey running to completion**,
and it is the reason the "Core user journeys usable" gate moves from UNKNOWN to **FAIL** rather
than to PASS.

### What happened

The user asked the Engineer persona to summarise the `README.md` in the open folder. The file was
visible in the editor pane throughout: a Django/Flutter e-commerce platform, with sections on
gateway-independent payments, a tech-stack table, and a pointer to `docs/PAYMENTS.md`.

The answer described a different document entirely — "project description, installation
instructions, usage examples, contributing guidelines, license information" — the contents of a
generic README. The model stated its own method in the middle of the answer:

> The command has been executed, but since the output is not shown here, I will assume the file
> exists and proceed with summarizing based on typical content found in a README.md file.

Two further details from the same screenshots matter:

- the plan panel showed **"Read the contents of README.md" with a completion tick**; and
- the model twice emitted `run_command("cat README.md")` as **prose**, which executes nothing
  (ADR-006: a model's own tool-call frames are informational; planning executes tools).

The turn did carry an `Ungrounded — no sources attached` badge. It is the only honest signal in
the exchange, and it did not prevent the answer.

### Why it happened

Four mechanisms, each individually defensible, reproduced locally before anything was changed:

```
s1 kind=generate status=skipped  result='No handler is configured for generate steps.'
s2 kind=respond  status=blocked  result=None
failed_steps        : ()
blocked_steps       : ()
summary_for_context : []
```

1. The plan's read step named a tool that is not registered. `_parse_steps` **demoted** it to
   `GENERATE`, on the stated reasoning that a step which "can never succeed" should still "let the
   plan make progress".
2. **No `GENERATE` handler exists** — no caller anywhere passes `handlers` to
   `ExecutionEngine.execute`, so every non-TOOL step kind is unhandled in every deployment. The
   step was SKIPPED and the step depending on it BLOCKED.
3. The agentic loop asked whether anything was worth replanning. `failed_steps` was empty (nothing
   *failed*) and `blocked_steps()` was empty too — it reports only steps still PENDING, and the
   executor had already moved the dependent to BLOCKED. Verdict: `nothing_to_repair`, no retry.
4. `summary_for_context()` reports SUCCEEDED steps only, so the turn generated with a context in
   which the read was never mentioned at all — not as failed, not as missing.

Link 1's comment is the exact inverse of what the code did: the demotion did not let the plan make
progress, it guaranteed the plan could make none, while removing every signal that would have said
so.

### The finding under it

Links 1–3 are ordinary bugs. Link 4 is the one worth recording as a property of the architecture:
**an absence cannot be reasoned about.** A model handed no tool output cannot distinguish "the
tool returned nothing" from "no tool ran" — and only in the second case does inventing an answer
look locally reasonable. Every guard in the turn was working: the trust channels held, the
ungrounded badge fired, no tool was executed without a grant. None of them is a guard against
*nothing having happened*.

### One route not ruled out

`read_file` does exist — a server-side sandboxed reader (`tools/builtin.py`), registered only when
a workspace is configured, confined to a server directory it describes as "the user's workspace
directory". It cannot see a Windows path on the user's machine. So either it was unregistered (and
links 1–3 ran), or it was registered, dispatched, failed to find the file, and **FAILED** — and
`summary_for_context()` excludes failed steps too, so generation was equally uninformed. Which one
occurred cannot be determined without that deployment's registered tool set, and is not asserted
here. The fix covers both.

### Fix and status

PR #137 addresses each link: the planner keeps an unknown tool name so dispatch refuses it
usefully; `Plan.unexecuted_steps()` makes "planned, never ran" visible; the agentic loop replans on
a BLOCKED step; and the kernel states which steps produced nothing, on the SYSTEM channel, with an
explicit instruction not to substitute typical content. 16 new tests, 11 of which fail against the
previous code.

**The gate does not move to PASS on a merged fix.** It requires the same journey re-run against the
deployed revision, which needs (a) #137 merged and deployed and (b) the paired desktop client. Until
then:

| Gate | Was | Now |
| --- | --- | --- |
| Core user journeys usable | UNKNOWN (no journey reached step three) | **FAIL** (a journey completed and fabricated) |

A downgrade on a mandatory gate does not change the overall verdict, which was already refused. It
does change what the refusal is *about*: until now the four unevidenced gates were the whole case.
This is the first mandatory gate failing on observed behaviour rather than on missing evidence.

---

## EV-28 — Inference sleeps, and the probe cannot see it wake (DEF-27, CORRECTION to EV-25/EV-26)

**When:** 2026-09-28 10:44–10:52 UTC, after ~9.5 hours with no traffic from this session
**Target:** `https://aura-ai-production-03a4.up.railway.app`
**Live revision:** `e8ed25a86006` — the merge commit of #137

### The measurement

```
10:44:17Z  healthy=False  5143.8 ms  ReadTimeout
10:44:31Z  healthy=False  5173.2 ms  ReadTimeout
   ...       20 consecutive failures, every one at a flat ~5150 ms
10:49:48Z  healthy=False  5156.8 ms  ReadTimeout
10:50:03Z  healthy=True    220.3 ms  ok
10:50:14Z  healthy=True    215.2 ms  ok
   ...       11 consecutive successes, 209–225 ms
10:51:49Z  healthy=True    212.6 ms  ok
```

Nothing changed at that boundary except that the endpoint — a Hugging Face Space that scales to
zero — finished waking up, under the load of the probes themselves. The flatness of the failures
is the tell: ~5150 ms *every single time* is not a server answering slowly, it is a client giving
up at a fixed deadline.

### What this corrects

EV-25 and EV-26 recorded 14/14 consecutive healthy samples across two windows and two revisions,
and I moved the "Inference works" gate to **PASS** on that basis. Both measurements were real.
Both were of a **warm** Space, because I had been sampling it continuously for the preceding
hour.

The cold-start hypothesis, raised in EV-24 and never tested, now explains all four observations
without contradiction:

| Evidence | Result | State of the Space |
| --- | --- | --- |
| EV-24 | 1/8 healthy, 5.15 s when failing | waking, or sleeping between samples |
| EV-25 | 7/7, 196–231 ms | warm |
| EV-26 | 7/7, 208–229 ms | warm (deploy replaced the container, not the Space) |
| **EV-28** | **0/20 then 11/11** | **asleep, then woken by the probing** |

EV-26 was written to rule out "a warm Space in a lucky window" by re-measuring after a deploy.
It did not, and could not: the deploy replaced the *gateway* container, while the Space's
warmth lives on Hugging Face's side. The control I thought I had was not a control.

**Gate correction: Inference works moves PASS → PARTIAL.** Working when warm is not working.
A user opening the app after an idle period is exactly the case none of the passing evidence
covers.

### The defect under it (DEF-27)

`openai_compatible.health()` and `ollama.health()` both hardcoded `timeout=5.0`. For an endpoint
that is always up, that is correct — a liveness check has to return fast enough to be polled.
For one that scales to zero it is a design error with a sharp consequence: **the probe cannot
observe the recovery at all.** It times out identically whether the provider is thirty seconds
from ready or permanently dead, so `/health/ready` reports the same `healthy: false` for both.
An operator cannot tell "asleep, warming" from "broken", and an orchestrator acting on that
signal pulls a service that was about to be fine.

Fixed by making the probe timeout configurable per provider
(`AURA_PROVIDER_<NAME>_HEALTH_TIMEOUT_S`), defaulting to 5.0 so no existing deployment changes
behaviour, with 7 tests pinning the path from the env var to the request — including that
`build_provider` actually threads it, since a setting that reads as configured and is silently
inert is worse than none.

### What remains untested, and it is the important one

**Whether a real user's first turn after an idle period succeeds.** A turn goes through
`timeout_s` — 180 s on this deployment — not the 5 s probe, so the numbers above say nothing
about it. Three outcomes are possible and I have evidence for none:

1. the turn waits, the Space wakes, the answer arrives slowly but correctly;
2. the turn exhausts 180 s mid-wake and surfaces `AllProvidersFailedError`;
3. the turn fails, but the attempt warms the Space, so a retry works.

The wake took roughly 5.5 minutes under continuous probing here, which is longer than the 180 s
budget — that makes (2) or (3) more likely than (1), but a probe every ~15 s is not the same
traffic shape as one long-lived request, so this is a reason to test rather than a conclusion.
Testing it needs one authenticated turn against a cold deployment, which needs the synthetic
tokens.

Raising a probe timeout makes the *report* honest. It does not make a cold start fast, and
nothing here should be read as claiming the user-facing problem is solved.

### Unchanged, re-checked rather than assumed

| Check | Result |
| --- | --- |
| `GET /metrics` unauthenticated | **`200`** — DEF-22 open, `AURA_METRICS_TOKEN` still unset |
| `checks.schema.revision` | `0008`, healthy — DEF-20 stays resolved |
| Release identity | `e8ed25a8` = merge commit of #137, seventh consecutive deploy tracked |
| Tools / agents registered | 37 / 8 |

### #137 is deployed

The fabrication fix (EV-27) is live on `e8ed25a8`. That unblocks the re-run of the Engineer
journey, which is the only thing that can move "Core user journeys usable" off FAIL. It needs
the paired desktop client, so it is an owner action; the gate stays FAIL until that re-run
produces evidence.

---

## Evidence artifacts retained

| File | Contents |
| --- | --- |
| `prod_openapi.json` | Full production OpenAPI document (20,952 bytes), 24 paths |
| `auth_probe.txt` | Per-endpoint unauthenticated probe transcript (24 endpoints) |
| `summary.txt` | Topology one-pager |

Stored in this session's scratchpad (ephemeral). Nothing containing credentials was written to any
file. Re-running EV-01…EV-13 reproduces all live results; they are cheap and read-only.

---

*Generated by [Claude Code](https://claude.ai/code)*
