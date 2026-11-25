# Observability on Railway: Grafana Cloud (Phase R)

How to watch the Railway Control Plane (`api.aureon-ai.tech`) with a free Grafana Cloud stack:
metrics, alerts, traces, dashboards and uptime checks. Plus where backups, the real-model eval and
the load test fit in. The VPS stack has its own Prometheus, Alertmanager and Grafana
(`deploy/monitoring/`). Railway has none of that, which is why this file exists.

Owner decision 2026-11-17 (`docs/plans/runtime_feature_enablement_plan.md`, decision 6):
Grafana Cloud free tier, no self-hosted monitoring on Railway.

Status: **built, not proven.** Nothing here is evidence until the checklist at the end has been
run against production and its results recorded.

## 1. Metrics

The gateway serves Prometheus metrics at `/metrics`. In production it answers **404 unless the
scrape sends `Authorization: Bearer $AURA_METRICS_TOKEN`** (fail closed;
`aura_core/api/security_headers.py`).

1. Railway → the gateway service → Variables: set `AURA_METRICS_TOKEN` to a long random string.
2. Grafana Cloud → Connections → **Metrics Endpoint** → new scrape job:
   - **Scrape job name: `aura-gateway`, exactly.** The alert rules in §2 select
     `job="aura-gateway"` (`AuraGatewayDown`).
   - URL: `https://api.aureon-ai.tech/metrics`
   - Authentication: Bearer, with the token from step 1.
   - Interval: 60 s.
3. Check it from a shell first:
   ```bash
   curl -sS -H "Authorization: Bearer $AURA_METRICS_TOKEN" https://api.aureon-ai.tech/metrics | grep -c '^aura_'
   ```
4. **If Grafana's *Test connection* fails** with "did not return a valid response", check the
   gateway log for `metrics scrape refused: ...` (one line per reason per minute, never the
   token). Observed live on 2026-11-22: reason `no Authorization header` with user-agent
   `grafanacloud`, for Bearer and Basic alike, which suggests the test sends no credentials until it is
   challenged and the deliberate 404 ends it (inferred, not yet shown to fix it). Try
   **`AURA_METRICS_CHALLENGE=true`**: a request with
   no `Authorization` header then gets `401` with `WWW-Authenticate: Bearer` (a wrong or malformed
   credential still gets 404, and without a configured token nothing changes). The cost is that
   anyone can tell the path exists; the metrics stay unreadable without the token. Leave it off
   anywhere the test is not needed.

**One scrape covers every worker.** Railway runs the image's `CMD` with `${AURA_WORKERS:-4}`
uvicorn workers. Since Phase R the `CMD` gives them one shared `PROMETHEUS_MULTIPROC_DIR`
(default `/tmp/aura-prometheus`), so a scrape adds up all workers. Before that, each scrape was
answered by one random worker from its own counters, and every `rate()` was meaningless.
`tests/unit/test_backend_image_user.py` pins it.

## 2. Alerts

The rule file is the VPS one, unchanged: `deploy/monitoring/alerts/aura.yml`. One source of truth.
Grafana Cloud's Prometheus (Mimir) accepts Prometheus rule groups. Load them with `mimirtool`.
The address, instance ID and a token with `rules:write` are on the stack's Prometheus details
page.

```bash
mimirtool rules load --address=https://prometheus-prod-<NN>-<region>.grafana.net \
  --id=<prometheus instance id> --key=<token> deploy/monitoring/alerts/aura.yml
```

Run it again whenever that file changes.

Rules that read metrics nothing on Railway exports never fire: there is no series, so there is
no alert. That covers the backup-sidecar rules (`AuraBackup*`, which watch the VPS sidecar;
Railway backups have their own heartbeat, §6) and `AuraDiskFilling` (node exporter). They stay
in the file for the VPS.

Alerting → Contact points: add your email or phone. Notification policy: route
`severity=critical` there immediately and `warning` in a digest.

**A sleeping vision Space is expected, not an incident.** `hfspace` uses
`AURA_PROVIDER_HFSPACE_HEALTH_PROBE=passive` (Phase I). Its `aura_dependency_up` series follows
its circuit breaker, which opens only after repeated real failures and half-opens 30 s later.
`AuraDependencyDown{dependency="provider:hfspace"}` therefore fires only if image turns keep
failing for 5 minutes. That is worth knowing.

## 3. Traces

Spans exist since Phase R. Before that, enabling tracing configured an exporter that nothing ever
sent to. A request produces:

| Span | Where | Attributes |
|---|---|---|
| `POST /api/v3/chat` (method + route template) | `api/http_metrics.py`, outermost middleware | `http.request.method`, `http.route`, `http.response.status_code`; error status on 5xx |
| `llm.generate` (one per provider attempt) | `ProviderRouter._observe_timing` | provider, model, pool, outcome, streamed, queue wait, TTFT, token counts |
| `aura.turn` (one per turn) | `AIKernel._record` | route, agent, strategy, outcome, error kind, provider, model, fallbacks, tokens, TTFT, degraded, tainted, retrieval strategy/count, per-stage durations |

**What spans never carry**: message or answer text, user/session/conversation ids, email, URL
or query string, client address, user agent. Spans leave for a third party.
`tests/unit/test_tracing_spans.py` sends a message containing a secret, a user id, a forwarded
client address and a user agent through `/chat`, then checks that none of them appears in any
span. `/health/*` and `/metrics` are not traced.

Railway variables:

```bash
AURA_TRACING_ENABLED=true
AURA_OTLP_ENDPOINT=https://otlp-gateway-prod-<region>.grafana.net/otlp/v1/traces   # full traces URL
OTEL_EXPORTER_OTLP_HEADERS=Authorization=Basic%20<base64 of "<instance id>:<token>">  # mark as secret
```

The OTLP endpoint, instance ID and a token with `traces:write` are on the stack's **OpenTelemetry**
page. The exporter reads `OTEL_EXPORTER_OTLP_HEADERS` itself, so the credential never enters
AURA's settings. Note the `%20`: header values in that variable are URL-encoded.

## 4. Dashboards

Import `deploy/monitoring/grafana/dashboards/{overview,quality,security}.json` (Dashboards → New →
Import). Each asks for its `DS_PROMETHEUS` datasource: pick the stack's Prometheus
(`grafanacloud-<stack>-prom`). The trace links point at a Loki datasource, which this setup does
not have; logs stay in Railway.

## 5. Uptime (Synthetic Monitoring)

Testing & synthetics → Synthetics → add two **HTTP** checks, from at least two probe locations:

| Check | URL | Frequency | Success when |
|---|---|---|---|
| Liveness | `https://api.aureon-ai.tech/health/live` | 1 min | status 200 |
| Readiness | `https://api.aureon-ai.tech/health/ready` | 5 min | status 200 and body matches `"healthy":\s*true` |

Alert on each with "fails from 2 locations for 3 runs".

Readiness is probed less often on purpose. Every call checks each active provider's health
endpoint, so it costs a request against Groq's and Cloudflare's daily limits. It no longer wakes
the vision Space (passive probe), but that only holds while
`AURA_PROVIDER_HFSPACE_HEALTH_PROBE=passive` stays set. Remove that variable and this check keeps
a GPU awake around the clock.

## 6. Backups

Railway Postgres, Redis and Qdrant Cloud are backed up by the Railway cron service described in
`docs/deployment/backups.md` (built 2026-11-17, PR #252). Prometheus cannot see a cron job, so it
reports through a dead man's switch (`BACKUP_HEARTBEAT_URL`, §3 of that file), not through §2
above. Then run the restore drill (§5 of that file) and commit its report. That is what GAP-028
asks for.

## 7. Real-model evaluation

Every quality number in the repo so far came from scripted providers (GAP-021). The real run
uses the agent suite through the real kernel against `hosted1`. The workflow
`.github/workflows/real-model-eval.yml` needs `secrets.AURA_HOSTED1_API_KEY` and working runners
(CB-44). Locally, from `aura_backend/`, with the production slot's settings:

```bash
export AURA_ENVIRONMENT=local AURA_JWT_SECRET=real-model-eval-only \
  AURA_LLM_PROVIDERS=hosted1 AURA_ALLOW_HOSTED_INFERENCE=true \
  AURA_PROVIDER_HOSTED1_KIND=openai_compatible \
  AURA_PROVIDER_HOSTED1_BASE_URL=https://api.groq.com/openai/v1 \
  AURA_PROVIDER_HOSTED1_MODEL=openai/gpt-oss-120b \
  AURA_PROVIDER_HOSTED1_API_KEY_ENV=AURA_PROVIDER_HOSTED1_API_KEY \
  AURA_PROVIDER_HOSTED1_API_KEY=<groq key> \
  AURA_PROVIDER_HOSTED1_MAX_DATA_CLASSIFICATION=sensitive \
  AURA_EMBEDDING_PROVIDER=sentence-transformers AURA_EMBEDDING_MODEL=BAAI/bge-small-en-v1.5 \
  AURA_EMBEDDING_DIMENSION=384
python -m evaluation.runner --suite agent_tasks --output real-model-eval-agent_tasks.json --max-turns 8 --timeout 120
```

Commit the JSON and a short Markdown summary under `docs/reports/`. The same harness then decides
plan items 0.5–0.6: rerun with `AURA_KNOWLEDGE_RETRIEVAL_STRATEGY=adaptive` and with
`AURA_KNOWLEDGE_RERANK_PROVIDER=cross_encoder`, and keep what wins. The vision run comes after
Phase I (`docs/deployment/inference-plane.md` §8).

## 8. Load test

`docs/guides/load-testing.md` is the procedure. On Railway:
- Point it at `https://api.aureon-ai.tech`. There is no nginx; uvicorn is the edge.
- Raise `AURA_RATE_LIMIT_USER_PER_MIN` / `_IP_PER_MIN` for the test window only, and record them
  with `--condition`.
- Run 1 → 10 → 25 users. Groq's free tier (30 requests/min, 8,000 tokens/min) with Cloudflare's
  allowance caps throughput long before 50 users. The report shows that ceiling; GAP-041 stays
  open.

## Checklist (record results in the PR / status file)

1. `curl` with the token returns `aura_` series; without the token it is a 404.
2. Grafana Cloud shows `up{job="aura-gateway"} == 1`, and `sum(aura_http_requests_total)` stays
   steady across scrapes instead of jumping between per-worker values.
3. `mimirtool rules list` shows the `aura-*` groups. Stopping the scrape for 5 min fires
   `AuraGatewayDown` to the contact point.
4. A chat turn produces one trace: `POST /api/v3/chat` with `llm.generate` and `aura.turn`
   beneath it, and nothing personal in any attribute.
5. Both synthetic checks are green. A deliberately wrong URL turns one red and alerts.
6. The backup heartbeat check is green after the first nightly run; the restore drill report is committed.
7. The real-model eval report is committed.
