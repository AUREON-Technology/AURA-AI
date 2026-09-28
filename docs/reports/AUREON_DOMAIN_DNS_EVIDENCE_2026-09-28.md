# Evidence Report — `aureon-ai.tech` → Railway Control Plane

**Generated:** 2026-09-28T20:23:10Z
**Scope:** DNS/nameserver remediation for `aureon-ai.tech` and end-to-end verification of the
Railway Control Plane it fronts.
**Author:** Claude Opus 5 (Claude Code session), on behalf of anas.ibrahim.gnedy@gmail.com

> Supersedes the DNS facts recorded in `AURA_PRODUCTION_BLOCKERS.md`,
> `AURA_REAL_PRODUCTION_CERTIFICATION.md` and `AURA_FULL_SYSTEM_REVIEW.md`, all of which describe
> `aureon-ai.tech` in its pre-2026-09-28 state (`A 152.239.118.239`, `AAAA 2a02:4780:79:5dcd::1`).

## Verdict

DNS and nameservers are **fixed and verified**. The domain still does not serve traffic, for a
reason unrelated to DNS:

> **The Railway custom domain `aureon-ai.tech` is bound to `targetPort: 8000`, but the
> application listens on `PORT=8080`.** Every request to the apex therefore reaches Railway's
> edge, is routed to the correct service, finds nothing listening on 8000, and returns
> `502 Application failed to respond`.

The same application, reached over Railway's own generated hostname (which targets 8080),
returns `HTTP 200` and `"healthy": true`. This is a one-field fix in the Railway dashboard, not
a code, deploy, or DNS change.

---

## 1. Hostinger

### 1.1 Current authoritative nameservers

Registry delegation (`NS` query via 8.8.8.8) and the Hostinger portfolio API agree:

```
aureon-ai.tech.  NS  hyperion.dns-parking.com
aureon-ai.tech.  NS  atlas.dns-parking.com
```

`domains_portfolio_get` response (2026-09-28T20:23Z):

```json
{"domain":"aureon-ai.tech","status":"Active","is_locked":true,"is_privacy_protected":true,
 "name_servers":{"ns1":"atlas.dns-parking.com","ns2":"hyperion.dns-parking.com"},
 "registered_at":"2026-08-02T22:10:29Z","expires_at":"2027-08-02T22:10:29Z",
 "60_days_lock_expires_at":"2026-10-01T22:10:30Z"}
```

### 1.2 Current DNS records

`dns_records_list` response (verbatim, 2026-09-28T20:23Z):

| Name | Type | Content | TTL |
| --- | --- | --- | --- |
| `@` | ALIAS | `v5wlz9j2.up.railway.app.` | 300 |
| `@` | MX | `10 aureon-ai.tech.` | 14400 |
| `www` | CNAME | `aureon-ai.tech.` | 300 |
| `_railway-verify` | TXT | `"railway-verify=5c6b4a10…45ec19"` | 14400 |

### 1.3 Exact changes made

| # | Change | Before | After |
| --- | --- | --- | --- |
| 1 | Registrar nameservers | `hyperion.dns-parking.com` + **`v5wlz9j2.up.railway.app`** | `hyperion.dns-parking.com` + `atlas.dns-parking.com` |
| 2 | `@` A | `152.239.118.239` (Hostinger parking) | *deleted* |
| 3 | `@` AAAA | `2a02:4780:79:5dcd::1` (Hostinger parking) | *deleted* |
| 4 | `@` ALIAS | *(absent)* | `v5wlz9j2.up.railway.app.`, TTL 300 |

Untouched: the `www` CNAME, the `@` MX, and the `_railway-verify` TXT.

**Root cause of the original fault.** `v5wlz9j2.up.railway.app` is Railway's *CNAME target*, not a
nameserver — Railway operates no nameservers. It had been entered into the registrar's NS field,
which is why hPanel demanded a second nameserver. The registry never accepted it (delegation
remained `hyperion` + `atlas`), which is the only reason the domain continued to resolve at all.

**Why the AAAA deletion mattered.** Had the parking AAAA survived alongside the new ALIAS,
IPv6-capable clients would have silently reached the Hostinger parking page while IPv4 clients
reached Railway — an intermittent, cache-dependent split that is disproportionately hard to
diagnose.

**Why ALIAS rather than CNAME.** A CNAME cannot legally coexist with the apex SOA/NS records.
Hostinger's DNS supports `ALIAS` (confirmed by the API's record-type enum and by
`dns_records_validate` returning success), so the apex flattens to Railway server-side. No
Cloudflare or other third-party proxy is required.

### 1.4 Timestamps and API responses

All Hostinger writes returned `{"message":"Request accepted"}` (the API applies asynchronously).
Server-side confirmation comes from the zone snapshot ledger, `dns_snapshots_list`:

| Snapshot ID | Reason | Created (UTC) | Corresponds to |
| --- | --- | --- | --- |
| `184119975` | Zone records update request | 2026-09-28T20:10:18Z | ALIAS insert (change 4) |
| `184119895` | Zone records delete request | 2026-09-28T20:10:00Z | A + AAAA deletion (changes 2–3) |
| `183365022` | Zone records update request | 2026-09-25T15:01:24Z | pre-existing — rollback point |

Rollback: `dns_snapshots_restore` with `snapshotId: 183365022` returns the zone to its
2026-09-25 state.

The nameserver change was applied via `domains_portfolio_update-nameservers` and confirmed by a
follow-up `domains_portfolio_get` showing `atlas` + `hyperion` with the Railway hostname gone.

---

## 2. DNS

### 2.1 Authoritative results

Queried directly against both authoritative servers. Both agree.

| Query | `hyperion.dns-parking.com` | `atlas.dns-parking.com` |
| --- | --- | --- |
| `aureon-ai.tech A` | `69.46.46.64` | `69.46.46.64` |
| `aureon-ai.tech AAAA` | *(none)* | *(none)* |
| `aureon-ai.tech NS` | hyperion + atlas | atlas + hyperion |
| `aureon-ai.tech MX` | `aureon-ai.tech` | `aureon-ai.tech` |
| `aureon-ai.tech TXT` | *(none — SOA returned)* | *(none — SOA returned)* |
| `www CNAME` | `aureon-ai.tech` | `aureon-ai.tech` |

The ALIAS is correctly flattened to an A record at the authoritative edge — `69.46.46.64` is
Railway's ingress. No AAAA is served, as intended.

### 2.2 Public recursive resolvers

Five resolvers, `aureon-ai.tech A`, at 2026-09-28T20:23:10Z:

| Resolver | Operator | Result | Status |
| --- | --- | --- | --- |
| `1.1.1.1` | Cloudflare | `69.46.46.64` | current |
| `9.9.9.9` | Quad9 | `69.46.46.64` | current |
| `9.9.9.11` | Quad9 ECS | `69.46.46.64` | current |
| `208.67.222.222` | OpenDNS | `69.46.46.64` | current |
| `8.8.8.8` | Google | `152.239.118.239` | **stale** |

### 2.3 `www` resolution

`www.aureon-ai.tech` → CNAME `aureon-ai.tech` → `69.46.46.64`, consistently across every
resolver tested *including* Google — Google's stale entry is for the apex A only, not the
`www` chain. DNS for `www` is correct and complete.

`www` nevertheless fails at the application layer; see §3.4.

### 2.4 Propagation status

**4 of 5 public resolvers current; Google Public DNS still serving the pre-change address.**

This is expected and self-resolving. The deleted A record carried a TTL of **14400s (4 hours)**,
so resolvers that cached it before 20:10Z may serve `152.239.118.239` until approximately
**2026-09-29T00:10Z**. The replacement ALIAS carries a TTL of 300s, so all subsequent changes
will propagate within five minutes.

No action is required or possible to accelerate this beyond what has been done.

---

## 3. Railway

Evidence via authenticated Railway CLI v5.23.0 (`railway whoami` →
`anas.ibrahim.gnedy@gmail.com`). Source: `railway status --json`.

### 3.1 Project

| Field | Value |
| --- | --- |
| Workspace | `anasibrahimgnedy-collab's Projects` |
| Project name | `perfect-prosperity` |
| Project ID | `762515ab-8c5b-408e-b7dc-b0f07d3ea1ba` |
| Environment | `production` (`9d2d88bc-717a-4451-9825-2e0d46d6f3fb`) |
| Plan | `hobby` |
| Region | `sfo`, 1 replica |

The account holds a second project, `generous-consideration`, which is not involved here.

### 3.2 Services

| Service | Service ID | Source | Deployment status |
| --- | --- | --- | --- |
| `AURA-AI` | `1a28066c-7b3a-46c2-bf50-9f2d1e938e25` | repo `anasibrahimgnedy-collab/AURA-AI`, root `aura_backend` | SUCCESS / RUNNING |
| `Postgres` | `af8c5188-9ce0-4e91-b362-2563976b2895` | `ghcr.io/railwayapp-templates/postgres-ssl:18` | SUCCESS / RUNNING |
| `Redis` | `c12ad0ac-2205-411c-8c24-ecec189eafb1` | `redis:8.2` | SUCCESS / RUNNING |

Volumes: `postgres-volume` 165.8 MB / 5000 MB, `redis-volume` 90.8 MB / 5000 MB, both `READY`.

### 3.3 Deployment and commit

| Field | Value |
| --- | --- |
| Deployment ID | `bf66f358-b193-49b8-acbe-dc68d69fdc52` |
| Created | 2026-09-28T20:00:47.770Z |
| Status | `SUCCESS`, instance `618576b2-06f4-4f62-8476-692807ce7448` = `RUNNING` |
| Branch | `main` |
| Commit | `0f0ae15df861bd5ce3219aca9ae0abb3f8916a29` |
| Commit message | `Merge pull request #142 … UX Phase 11: classify failures instead of printing them in red` |
| Image digest | `sha256:b1f70df0612f16f7695e7821cd69d8322da45d1a577c4cbc542d6861ee37fc10` |
| Builder | `DOCKERFILE`, `/aura_backend/Dockerfile` |
| Restart policy | `ON_FAILURE`, max 10 retries |
| `deploymentStopped` | `false` |

Local `git rev-parse HEAD` is `0f0ae15df861bd5ce3219aca9ae0abb3f8916a29` — **identical**. The
running deployment is exactly the local working tree's commit, and `/health/ready` independently
self-reports the same `git_sha`.

### 3.4 Custom domains and generated hostname — the active fault

```
customDomains:  [{ domain: "aureon-ai.tech",
                   id: "c4bda498-4b17-4988-9227-8474ad1aec8a",
                   targetPort: 8000 }]
serviceDomains: [{ domain: "aura-ai-production-03a4.up.railway.app",
                   id: "77249172-08f2-4b27-9d9a-93c31e40da64",
                   targetPort: 8080 }]
```

The service's environment sets **`PORT = 8080`**, and `aura_backend/Dockerfile:134` starts
uvicorn with `--port ${PORT:-8000}`. The application therefore binds **8080**.

- The generated hostname targets 8080 → reaches the app → **HTTP 200**.
- The custom domain targets 8000 → nothing listening → **HTTP 502**.

`www.aureon-ai.tech` is **absent from `customDomains` entirely**, which is why it returns
`404 Application not found` no matter how correct its DNS is.

Note also that `v5wlz9j2.up.railway.app` — the ALIAS target — returns 404 when fetched directly.
That is correct behaviour: it is a Host-header-routed shared ingress, not a service endpoint, and
its direct response says nothing about service health.

### 3.5 Health status

`healthcheckPath` and `healthcheckTimeout` are `null` in the deployed service manifest. Railway
is performing **no application-level health check**; "SUCCESS/RUNNING" reflects only that the
container started and stayed up. The port misbinding was therefore invisible to Railway's own
monitoring — a healthcheck on `/health/ready` would have surfaced it.

---

## 4. Application

Endpoints exercised against both hostnames.

### 4.1 Via the generated hostname (`…up.railway.app`, port 8080)

| Endpoint | Status | Body |
| --- | --- | --- |
| `GET /` | `404` | `{"detail":"Not Found"}` |
| `GET /health` | `404` | `{"detail":"Not Found"}` |
| `GET /health/ready` (cold) | `503` | `"healthy":false` — `hfspace: {"healthy":false,"detail":"ReadTimeout","latency_ms":5146.7}` |
| `GET /health/ready` (warm) | **`200`** | `"healthy":true` — `hfspace: {"healthy":true,"detail":"ok","latency_ms":187.6}` |

The `404`s on `/` and `/health` are **correct, not faults**: per ADR-002 the gateway is v3-only
and exposes no root or bare `/health` route. `/health/ready` is the real readiness surface.

Warm readiness payload:

```json
{"providers":{"hfspace":{"healthy":true,"detail":"ok","latency_ms":187.6}},
 "index":"aura-knowledge__v1__hash-embed-v1__384","tools":37,"prompts":17,"agents":8,
 "voice":{"tts":"piper-en+piper-ar","stt":"faster-whisper-tiny.en","live":false},
 "environment":"production","healthy":true,
 "checks":{"schema":{"healthy":true,"revision":"0008"}},
 "release":{"app_version":"3.0.0","api_version":"v3","revision":"0f0ae15df861",
   "git_sha":"0f0ae15df861bd5ce3219aca9ae0abb3f8916a29","git_ref":"main",
   "deployment_id":"bf66f358-b193-49b8-acbe-dc68d69fdc52",
   "inference_contract":"openai-compatible/v1","identified":true}}
```

Alembic schema revision `0008`, healthy. 37 tools, 17 prompts, 8 agents registered.

### 4.2 Via the custom domain (`aureon-ai.tech`, port 8000)

All three endpoints return an identical Railway-edge error — the request never reaches the app:

```json
{"status":"error","code":502,"message":"Application failed to respond",
 "request_id":"CV-46yT2SDCEl_T-0_TJvA"}
```

| Endpoint | Status | Latency |
| --- | --- | --- |
| `GET /` | `502` | 0.84s |
| `GET /health` | `502` | 0.38s |
| `GET /health/ready` | `502` | 0.37s |

TLS note: an earlier probe failed certificate validation (`SEC_E_WRONG_PRINCIPAL`) because
Railway had not yet issued a certificate for the apex — issuance begins only once DNS points at
Railway, which happened at 20:10Z. `http://` correctly returns `301` to `https://`, confirming
the edge recognises the hostname.

### 4.3 Startup logs

From `railway logs --service AURA-AI`, deployment `bf66f358`:

```
2026-09-28T20:01:54.232Z [INFO] container built … environment="production"
                                providers=["hfspace"] embedder="hash-embed-v1"
                                tools=37 prompts="[redacted]" agents=8
2026-09-28T20:01:54.705Z [WARN] No Firebase Service Account credentials found.
                                Firebase Auth verification is disabled.   (×4)
INFO:     Started server process [4] / [5] / [6] / [7]
INFO:     Waiting for application startup.
2026-09-28T20:01:55.652Z [INFO] container startup complete
2026-09-28T20:01:55.652Z [INFO] lexical index rebuilt … chunks="[redacted]"
INFO:     Application startup complete.   (×4)
```

Clean boot: build at 20:00:47Z, container built 20:01:54Z, all four uvicorn workers up by
20:01:55Z — **68 seconds** from deploy to serving.

### 4.4 Crash / restart evidence

**None.** Specifically:

- `deploymentStopped: false`; the single instance has been `RUNNING` continuously since
  2026-09-28T20:00:47Z.
- No traceback, no `SIGTERM`/`SIGKILL`, no exit code, and no repeated "Started server process"
  cycle appears anywhere in the log window.
- The four `Started server process` lines are the four configured uvicorn workers starting once,
  not one worker restarting four times.
- `restartPolicyType: ON_FAILURE` with `maxRetries: 10` has not been triggered.

Observed request log, consistent with the above and with the 502 diagnosis (these are the
author's own probes arriving via the generated hostname; nothing arrives via the custom domain,
because the edge rejects it before it reaches the app):

```
GET /api/v3/insights  → 401 Unauthorized
GET /                 → 404 Not Found
GET /health           → 404 Not Found
GET /health/ready     → 503 Service Unavailable   (cold inference; later 200 when warm)
```

**The application is not, and has not been, failing.** The 502 is purely a port-binding
misconfiguration on the custom domain.

---

## 5. Security

### 5.1 No secrets exposed

| Check | Result |
| --- | --- |
| `aura_backend/credentials.json` tracked in git? | **No** — `git ls-files --error-unmatch` fails; ignored via `aura_backend/.gitignore:1` |
| Any tracked `*.env` / `*.pem` / `*.key` / `*serviceaccount*` / credential files? | **None.** All `git ls-files` matches are source modules and tests (`secret_manager.py`, `secrets_crypto.py`, `test_no_committed_client_secrets.py`, …), not secret material |
| Secrets in application logs? | **No.** Sensitive fields are redacted at the logger: `prompts="[redacted]"`, `chunks="[redacted]"` |
| Secrets in HTTP responses? | **No.** `/health/ready` returns version, provider health and counts only — no credentials, connection strings or keys |
| Secrets in this report | Values were never read. Variable **names** were enumerated; `AURA_PROVIDER_HFSPACE_API_KEY` was verified by **length only (64 chars)** |
| `DEBUG_MODE` | `false` |
| `ALLOWED_ORIGINS` | `https://aureon-ai.tech` — correctly scoped, not a wildcard |
| `AURA_TRUSTED_PROXY_COUNT` | `1` — correct for a single Railway edge; prevents client IP spoofing via forged `X-Forwarded-For` |

A repository fitness test, `tests/architecture/test_no_committed_client_secrets.py`, enforces the
no-committed-secrets property in CI.

**Open finding (not introduced by this work):** `No Firebase Service Account credentials found.
Firebase Auth verification is disabled.` is logged four times at startup in **production**. Any
authentication path depending on Firebase token verification is inactive. `GET /api/v3/insights`
correctly returned `401`, so the primary JWT path is enforcing auth — but the disabled Firebase
verifier should be confirmed as intentional for this deployment.

### 5.2 Inference endpoint is not publicly usable

Base URL: `https://anas-ibrahim-mahmoud-aura-ai-backend.hf.space/v1`. Probed unauthenticated from
the public internet:

| Probe | Result |
| --- | --- |
| `POST /v1/chat/completions`, no auth header | **`401`** `{"error":"Unauthorized"}` |
| `GET /v1/models`, `Authorization: Bearer not-a-real-key` | **`401`** `{"error":"Unauthorized"}` |
| `GET /` (Space root) | `404` — no UI or console surface exposed |

**Confirmed: the inference plane rejects both unauthenticated requests and requests bearing an
invalid key.** No model access, no `/v1/models` enumeration, and no completion capability is
available without the correct key. This matches the documented posture in
`docs/deployment/inference-plane.md` §1 (the Space "refuses to boot without an API key, rejects
unauthenticated requests").

### 5.3 Railway → inference authentication

| Field | Value |
| --- | --- |
| `AURA_PROVIDER_HFSPACE_KIND` | `openai_compatible` |
| `AURA_PROVIDER_HFSPACE_BASE_URL` | `https://anas-ibrahim-mahmoud-aura-ai-backend.hf.space/v1` |
| `AURA_PROVIDER_HFSPACE_MODEL` | `Qwen/Qwen2.5-7B-Instruct-AWQ` |
| `AURA_PROVIDER_HFSPACE_API_KEY_ENV` | `AURA_PROVIDER_HFSPACE_API_KEY` |
| `AURA_PROVIDER_HFSPACE_API_KEY` | present, 64 characters (value not read) |
| `AURA_PROVIDER_HFSPACE_TIMEOUT_S` | `45` |
| `AURA_ALLOW_REMOTE_INFERENCE` | `true` |
| `AURA_LLM_PROVIDERS` | `hfspace` |

**Authentication is configured and functionally proven.** The decisive evidence is behavioural,
not merely declarative: the same endpoint that returns `401` to this report's unauthenticated
probes returns `{"healthy":true,"detail":"ok","latency_ms":187.6}` to the Control Plane. Only a
correctly authenticated caller obtains that result. Transport is HTTPS throughout.

**Architectural risk worth recording.** `AURA_LLM_PROVIDERS = hfspace` is the primary-only
topology; there is no `local`/Ollama fallback. `docs/deployment/inference-plane.md` §2 states the
consequence plainly: a GPU-plane outage is a **full generation outage**, not a degradation. The
cold-start `ReadTimeout` observed in §4.1 is exactly this failure mode in miniature — the Space
sleeps, the first caller pays a cold start exceeding the 45s timeout, and `/health/ready` reports
the whole system unhealthy. An always-on GPU flavour (as that document recommends "even more
strongly" for primary-only) or a keep-warm ping would eliminate it.

---

## 6. Mail

### 6.1 Current state

| Record | Value | Assessment |
| --- | --- | --- |
| MX | `10 aureon-ai.tech.` → ALIAS → `69.46.46.64` (Railway) | **Broken.** Mail is directed at an HTTP ingress that runs no SMTP listener |
| SPF (`TXT @`) | **absent** | No sender policy published |
| DKIM | **absent** — probed `default`, `google`, `k1`, `s1`, `selector1`, `railway`; all NXDOMAIN | No signing keys published |
| DMARC (`TXT _dmarc`) | **absent** | No policy, no reporting |

The only apex TXT record in the zone is `_railway-verify`, which is a domain-ownership token, not
a mail record.

### 6.2 Intended mail provider

**Not determined — no evidence exists in the zone, the Railway configuration, or the repository
to indicate one.** The MX record points the domain at itself, which is Hostinger's default
placeholder rather than a deliberate choice. This question requires an owner decision and cannot
be answered from evidence.

### 6.3 Assessment

This is **not a regression** — the MX pointed at the Hostinger parking page before this work and
at Railway after it; inbound mail failed in both cases. It was left untouched because removing
mail records was outside the requested scope.

Two distinct issues, in priority order:

1. **Spoofing exposure (act regardless of mail plans).** With no SPF and no DMARC, any party can
   forge `From: …@aureon-ai.tech`. For a domain fronting an AI product this is a live phishing
   risk against your own users. If you will never send mail from this domain, publish a null
   policy — `TXT @ "v=spf1 -all"` and `TXT _dmarc "v=DMARC1; p=reject; rua=mailto:…"` — which
   costs nothing and closes the hole.
2. **Inbound mail is dead.** If mail is wanted, choose a provider, point MX at it, and publish
   that provider's SPF include and DKIM selector. Until then, delete the self-referential MX so
   senders fail fast with a clear NXDOMAIN rather than timing out against an HTTP endpoint.

---

## 7. Final state

| Surface | State | Evidence |
| --- | --- | --- |
| **`aureon-ai.tech`** | DNS ✅ · HTTP ❌ **502** | Resolves to `69.46.46.64` on 4/5 public resolvers and both authoritative servers. Blocked solely by custom-domain `targetPort: 8000` vs app `PORT=8080` |
| **`www.aureon-ai.tech`** | DNS ✅ · HTTP ❌ **404** | CNAME chain resolves correctly everywhere, including Google. Not registered in Railway `customDomains`; DNS cannot fix this |
| **Railway service URL** | ✅ **Fully operational** | `https://aura-ai-production-03a4.up.railway.app/health/ready` → `200`, `"healthy":true` |
| **Control Plane** | ✅ **Healthy** | Deployment `bf66f358` SUCCESS/RUNNING since 20:00:47Z, commit `0f0ae15` = local HEAD, schema rev `0008`, 37 tools / 17 prompts / 8 agents, no crashes or restarts |
| **Inference Plane** | ⚠️ **Healthy but sleeps** | Warm: `healthy:true`, 187.6 ms. Cold: `ReadTimeout` at 5.1 s → whole-system `503`. Auth correctly enforced (401 unauthenticated). No fallback provider configured |

### Required actions, in order

1. **Set the custom domain's target port to `8080`** (Railway → `AURA-AI` → Settings → Networking
   → `aureon-ai.tech`). This is the single change that makes the domain serve. No redeploy needed.
2. **Add `www.aureon-ai.tech`** as a custom domain on the same service, also target port `8080`,
   then point the existing `www` CNAME at whatever target Railway returns.
3. **Set `healthcheckPath` to `/health/ready`** on the service. Its absence is why Railway
   reported a healthy deployment while the public domain was returning 502 to every request.
4. **Publish SPF and DMARC** (§6.3), even as null policies. Independent of any mail decision.
5. **Keep the HF Space warm**, or accept that the first request after idle fails (§5.3).
6. **Confirm the disabled Firebase auth verifier is intentional** for production (§5.1).

Items 1–2 are the only ones that affect whether `aureon-ai.tech` serves traffic. Items 3–6 are
correctness and hardening.

### Verification command after fixing item 1

```bash
curl -sS -w '\n[%{http_code}]\n' https://aureon-ai.tech/health/ready
```

Expect `200` and `"healthy":true`. Should it still 502 while
`https://aura-ai-production-03a4.up.railway.app/health/ready` returns 200, the port change did
not take effect. Google Public DNS may serve the old address until ~2026-09-29T00:10Z; test
against `1.1.1.1` in the interim.
