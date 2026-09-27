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
