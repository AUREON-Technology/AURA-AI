# Deployment Guide

Deploying AURA AI to a single VPS. Roughly 30 minutes for a first run, most of it waiting for
model weights to download.

Target: **4 vCPU, 16 GB RAM, 100 GB SSD**, Debian 12 or Ubuntu 24.04. It runs on 2 vCPU / 8 GB
with a smaller quantised model; see [Sizing](#sizing).

---

## 1. Prerequisites

```bash
# Docker Engine + Compose plugin
curl -fsSL https://get.docker.com | sh
sudo usermod -aG docker "$USER" && newgrp docker
docker compose version   # expect v2.20 or later
```

A DNS `A` record for your domain pointing at the VPS, and ports 80 and 443 reachable. Nothing
else should be exposed — the compose stack binds no other host port, deliberately (SEC-12).

```bash
sudo ufw default deny incoming
sudo ufw allow 22/tcp && sudo ufw allow 80/tcp && sudo ufw allow 443/tcp
sudo ufw enable
```

## 2. Clone and configure

```bash
git clone https://github.com/anasibrahimgnedy-collab/aura-ai.git /opt/aura
cd /opt/aura/deploy

cp .env.example .env
$EDITOR .env          # set AURA_DOMAIN at minimum

./scripts/bootstrap-secrets.sh
```

`bootstrap-secrets.sh` writes five files into `deploy/secrets/` with mode 600. **Nothing in
this stack has a secret default** — a deployment that forgets a value fails to start rather
than running with a publicly-known password, which is the defect behind SEC-08.

> Back up `deploy/secrets/` somewhere the database backups are *not* stored. Losing
> `jwt_secret` invalidates every session; losing `postgres_password` locks you out of your own
> data.

## 3. TLS certificate

```bash
mkdir -p nginx/www
docker run --rm -p 80:80 \
  -v aura_nginx_certs:/etc/letsencrypt \
  -v "$PWD/nginx/www:/var/www/certbot" \
  certbot/certbot certonly --standalone \
  -d "$AURA_DOMAIN" --agree-tos -m you@example.com --no-eff-email
```

Renewal (Let's Encrypt certificates last 90 days):

```bash
sudo tee /etc/cron.d/aura-certbot <<'CRON'
0 3 * * 1 root docker run --rm -v aura_nginx_certs:/etc/letsencrypt -v /opt/aura/deploy/nginx/www:/var/www/certbot certbot/certbot renew --quiet && docker compose -f /opt/aura/deploy/docker-compose.prod.yml exec nginx nginx -s reload
CRON
```

## 4. Start

```bash
docker compose -f docker-compose.prod.yml --env-file .env up -d
docker compose -f docker-compose.prod.yml ps
```

Pull the local model (a few GB — this is the slow step):

```bash
docker compose -f docker-compose.prod.yml exec ollama \
  ollama pull qwen2.5:7b-instruct-q4_K_M
```

Verify:

```bash
curl -s https://$AURA_DOMAIN/health/ready | jq
./deploy/scripts/verify-client-hostname.sh
```

`healthy: true` means at least one provider answered its health probe and the index alias
resolves. A `503` here is correct behaviour while the model is still downloading — readiness
gates traffic, liveness does not.

## 5. Verify the security posture

Three checks worth running on every deployment.

```bash
# 1. Nothing but 80/443 is exposed.
ss -tlnp | grep -E ':(5432|6379|6333|11434)' && echo "FAIL: data tier is exposed" || echo "ok"

# 2. No security bypass is active. This must be empty.
curl -s http://localhost:8000/metrics | grep 'aura_insecure_mode' | grep -v ' 0$'

# 3. The removed proxy routes are gone (SEC-01).
curl -s -o /dev/null -w '%{http_code}\n' https://$AURA_DOMAIN/api/pull   # expect 404
```

The second check is also a Prometheus alert (`AuraInsecureModeActive`), because SEC-05 was
invisible partly because nothing reported it.

---

## Configuration

Everything is `AURA_`-prefixed environment variables, validated at startup. An invalid or
insecure combination **refuses to boot** rather than serving degraded traffic.

### Choosing providers

Providers are declared as data (ADR-003), so adding an inference node is a config change:

```bash
AURA_LLM_PROVIDERS=local,cloud
AURA_LLM_ROUTING_POLICY=local_first

AURA_PROVIDER_LOCAL_KIND=openai_compatible
AURA_PROVIDER_LOCAL_BASE_URL=http://ollama:11434/v1
AURA_PROVIDER_LOCAL_MODEL=qwen2.5:7b-instruct-q4_K_M
AURA_PROVIDER_LOCAL_IS_LOCAL=true
AURA_PROVIDER_LOCAL_QUALITY_TIER=1
AURA_PROVIDER_LOCAL_MAX_CONCURRENCY=2

AURA_PROVIDER_CLOUD_KIND=gemini
AURA_PROVIDER_CLOUD_MODEL=gemini-2.5-flash
AURA_PROVIDER_CLOUD_API_KEY_ENV=GEMINI_API_KEY
AURA_PROVIDER_CLOUD_QUALITY_TIER=3
AURA_PROVIDER_CLOUD_COST_INPUT=0.0003
AURA_PROVIDER_CLOUD_COST_OUTPUT=0.0025
```

The API key is referenced by *variable name*, never stored in settings — so it cannot appear in
`/api/v3/admin/status`, a config dump, or a Sentry breadcrumb.

| Policy | Behaviour |
| --- | --- |
| `local_first` | Prefer on-premise. The privacy-preserving default. |
| `cheapest` | Lowest projected cost meeting the capability floor. |
| `fastest` | Lowest EWMA time-to-first-token. |
| `quality_first` | Highest declared tier, cost secondary. |
| `balanced` | Normalised blend of latency, cost, and tier. |

A per-request `routing_policy` overrides the default; `require_local: true` forces on-premise
regardless of policy.

### Embeddings

```bash
AURA_EMBEDDING_PROVIDER=sentence-transformers
AURA_EMBEDDING_MODEL=BAAI/bge-small-en-v1.5
AURA_EMBEDDING_DIMENSION=384
```

**Changing either value changes the index identity and requires a rebuild.** That is by design
(ADR-007): vectors from one model are meaningless when searched with another, so the new
identity provisions a *new* index rather than corrupting the existing one. See
[Administrator Guide → Reindexing](administrator-guide.md#reindexing).

### Model pulls

```bash
AURA_MODEL_ALLOWLIST=qwen2.5:7b-instruct-q4_K_M,llama3.1:8b-instruct-q4_K_M
```

Empty means pulls are **disabled**. This fails closed on purpose: model weights are executable
artefacts fetched from a third-party registry (SEC-01).

---

## Sizing

| Component | 2 vCPU / 8 GB | 4 vCPU / 16 GB | Notes |
| --- | --- | --- | --- |
| Local model | 3B Q4 (~2 GB) | 7-8B Q4 (~5 GB) | The dominant memory consumer |
| Gateway workers | 2 | 4 | ~250 MB each at steady state |
| Postgres | 512 MB | 2 GB | `shared_buffers` is ~25 % of the limit |
| Qdrant | 1 GB | 2 GB | Roughly 4 KB per 384-dim chunk plus index |
| Redis | 256 MB | 768 MB | `allkeys-lru`, bounded |
| Observability | ~1 GB | ~2 GB | Prometheus 30 d + Loki |

Disk: model weights (5-15 GB) dominate, then Prometheus (~5 GB at 30 days), Postgres, and
Qdrant segments. 100 GB is comfortable; 50 GB works with one model and 14-day retention.

**GPU.** Uncomment the `deploy.resources.reservations.devices` block on the `ollama` service and
install the NVIDIA container toolkit. Expect roughly 10-20× the tokens/second of CPU inference
on a 7B Q4 model.

---

## Operations

```bash
# Logs — structured JSON; jq makes them readable
docker compose -f docker-compose.prod.yml logs -f gateway | jq -R 'fromjson? // .'

# Follow one request end to end
docker compose -f docker-compose.prod.yml logs gateway \
  | jq -R 'fromjson? // empty | select(.correlation_id == "<id>")'

# Restart just the gateway (workers keep draining)
docker compose -f docker-compose.prod.yml restart gateway

# Update
git pull && docker compose -f docker-compose.prod.yml up -d --build
```

### Backups

The `backup` service runs continuously, dumping Postgres daily and snapshotting Qdrant, with a
14-day retention. It **verifies each dump is restorable and non-empty before counting it** —
the failure that matters is discovering at recovery time that months of dumps were empty.

```bash
ls -lh backups/
cat backups/.last_success | xargs -I{} date -d @{}     # last verified backup
```

Restore is deliberately interactive:

```bash
docker compose -f docker-compose.prod.yml stop gateway worker-realtime worker-ingestion beat
docker compose -f docker-compose.prod.yml run --rm backup /scripts/restore.sh /backups/postgres-<stamp>.dump
docker compose -f docker-compose.prod.yml start gateway worker-realtime worker-ingestion beat
curl -X POST -H "Authorization: Bearer $ADMIN_TOKEN" https://$AURA_DOMAIN/api/v3/admin/index/rebuild
```

The final step matters: documents are the source of truth and the vector index is derived
(ADR-007), so a restored database needs its index rebuilt.

> Practise a restore on a staging copy before you need it. A restore procedure that has never
> been run is not a recovery plan.

### Monitoring

Grafana is at `https://$AURA_DOMAIN/grafana/`, restricted to private address ranges by Nginx.
Tunnel in rather than opening it:

```bash
ssh -L 3000:localhost:3000 user@vps
```

Three provisioned dashboards: **Overview** (throughput, latency, cost), **AI Quality**
(retrieval, groundedness, confidence, freshness), **Security** (guards, taint, rate limits,
insecure mode).

---

## Scaling beyond one host

Covered in [`docs/analysis/07-scalability-report.md`](../analysis/07-scalability-report.md).
The short version, in the order the constraints actually bind:

1. **Move inference to its own host.** Generation concurrency is the binding constraint at
   every stage. Point `AURA_PROVIDER_LOCAL_BASE_URL` at it — no code change.
2. **Scale gateway replicas.** They are stateless; all shared state is already in Redis and
   Postgres.
3. **Split the data tier.** Managed Postgres with read replicas, then a Qdrant cluster sharded
   by `user_id`.

---

## Troubleshooting

See the [Troubleshooting Guide](troubleshooting-guide.md). The three most common first-run
problems:

| Symptom | Cause | Fix |
| --- | --- | --- |
| `/health/ready` returns 503 with `healthy: false` | Model still downloading | Wait; watch `docker compose logs ollama` |
| Gateway exits immediately | Settings validation refused an insecure production config | Read the log — it names every problem |
| Chat returns 503 `all_providers_failed` | Every provider circuit is open | `docker compose logs ollama`, then `POST /api/v3/admin/providers/local/reset-circuit` |
