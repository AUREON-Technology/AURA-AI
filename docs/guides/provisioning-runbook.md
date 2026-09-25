# Provisioning Runbook — full stack, from a bare host

The procedure for standing up `deploy/docker-compose.prod.yml` on a host that has never run it.
The [Deployment Guide](deployment-guide.md) covers steady-state configuration and operations; this
covers the first time, including the parts that only bite once.

**Read [§0](#0-before-you-start-check-the-hardware) before anything else.** The most common way this
goes wrong is starting on a machine that cannot hold the stack, and discovering it forty minutes in
when the OOM killer takes the gateway.

> **Honest status.** This stack has never been deployed anywhere. Every module it references now
> exists and a fitness test builds the real container from this compose file's backend selection, so
> it is verified to *construct* — that is a different claim from verified to *run*. Expect to hit
> real problems and budget time for them. What you get in exchange is a known set of problems on a
> designed stack.

---

## 0. Before you start: check the hardware

```bash
nproc; free -h; df -h /; ss -tlnp | grep -E ':(80|443|11434|5432|6333|6379)\s'
```

| Requirement | Minimum | Why |
| --- | --- | --- |
| vCPU | **4** | The gateway alone reserves 2.0; generation needs its own core |
| RAM | **16 GB** | App tier limits total 6.5 GB *before* a ~6 GB model |
| Swap | ≥ 2 GB | Zero swap turns memory pressure into an OOM kill with no warning |
| Disk | **50 GB** free | ~16–18 GB for images, weights and 14-day metrics |
| Ports 80, 443 | **free** | The compose file publishes exactly these |

If `free -h` shows less than 16 GB, stop and read
[`AI_HARDWARE_CAPACITY_REPORT.md`](../../AI_HARDWARE_CAPACITY_REPORT.md). A 7B Q4 model needs about
6 GB to exist; that is not tunable.

**If ports 80/443 are already in use** by an existing web server, you have two options. Either
displace it, or keep it as the edge and reverse-proxy to AURA — in which case remove the `nginx`
service from the compose file, publish the gateway on a loopback port instead, and add an upstream
to the existing server. The second is the right call on a shared host, and it means the TLS section
below is handled by the existing nginx rather than by this stack.

---

## 1. Get the right repository

This step exists because it is the one that has already gone wrong. On `srv1794216`, `/opt/aura` is
not a git checkout at all, and the checkout nested at `/opt/aura/aura_backend` tracks a **Hugging
Face Space** (`git@hf.co:spaces/…/aura-ai-backend`), not GitHub. Its HEAD commit does not exist in
this repository. Pulling GitHub `main` into it is not an update, it is a collision between two
unrelated histories.

**Do not try to reconcile them. Clone fresh alongside, and cut over.**

```bash
# 1a. A deploy key, so a private repo can be fetched without a user credential
ssh-keygen -t ed25519 -C "$(hostname)-aura-deploy" -f /root/.ssh/aura_deploy -N ""
cat /root/.ssh/aura_deploy.pub
```

Add that public key at **GitHub → the repo → Settings → Deploy keys → Add deploy key**. Read-only is
sufficient. *This is a manual step and nothing below works until it is done* — an unauthorised key
gets `ERROR: Repository not found`, which is GitHub declining to confirm the repo exists rather than
a genuine 404.

```bash
# 1b. Teach ssh which key to use for this host alias
cat >> /root/.ssh/config <<'EOF'
Host github-aura
  HostName github.com
  User git
  IdentityFile /root/.ssh/aura_deploy
  IdentitiesOnly yes
EOF
chmod 600 /root/.ssh/config

# 1c. Verify the key is accepted BEFORE cloning. Expect:
#     "Hi <owner>/<repo>! You've successfully authenticated, but GitHub does not provide shell access."
ssh -T git@github-aura

# 1d. Clone fresh. A new directory, not on top of the old one.
git clone git@github-aura:anasibrahimgnedy-collab/AURA-AI.git /opt/aura-stack
cd /opt/aura-stack
git log --oneline -1
```

Keep the old tree until the new stack is verified. It is your rollback.

---

## 2. Secrets

```bash
cd /opt/aura-stack/deploy
./scripts/bootstrap-secrets.sh
ls -l secrets/          # five files, mode 600
```

Generates `postgres_password`, `redis_password`, `qdrant_api_key`, `jwt_secret` and
`grafana_admin_password` from `/dev/urandom`. It is idempotent — an existing file is kept, not
regenerated, so re-running it after a partial failure will not lock you out of your own database.

**Back these up now, somewhere the backups are not stored.** Losing `jwt_secret` invalidates every
session; losing `postgres_password` locks you out of your data.

Nothing in this stack has a secret default. A missing value fails to start rather than running with
a publicly-known password — that is deliberate, and it is why this step cannot be skipped.

---

## 3. Configure

```bash
cp .env.example .env
$EDITOR .env
```

Set at minimum:

| Variable | Value |
| --- | --- |
| `AURA_DOMAIN` | the hostname TLS will be issued for |
| `AURA_WORKERS` | 4 on a 4-vCPU host |
| `AURA_LOCAL_MODEL` | `qwen2.5:7b-instruct-q4_K_M` (default), or a smaller tag on a smaller box |
| `AURA_MODEL_ALLOWLIST` | the model tags a request may name. Empty means unrestricted |

Leave `AURA_EMBEDDING_PROVIDER=sentence-transformers`. The `hash-embed-v1` alternative needs no
model and no torch, which is why it is the settings default for tests — its retrieval quality is
materially worse and it is not a production choice.

Do **not** add a provider whose `kind` is a hosted vendor. It will be rejected at construction, and
the build fails if such a value reaches a config template (six static self-hosting layers, see
`tests/architecture/test_no_external_inference.py`).

---

## 4. TLS

Follow [Deployment Guide §3](deployment-guide.md#3-tls-certificate). On a host where another server
already owns 443, skip this entirely — that server terminates TLS and proxies to AURA, and this
stack should not be publishing 443 at all (see §0).

---

## 5. Pull the model *first*

```bash
docker compose -f docker-compose.prod.yml --env-file .env up -d ollama
docker compose -f docker-compose.prod.yml exec ollama ollama pull qwen2.5:7b-instruct-q4_K_M
docker compose -f docker-compose.prod.yml exec ollama ollama list
```

Deliberately ahead of everything else. It is a ~4.7 GB download and the slowest step by a wide
margin; doing it first means a slow or failed pull costs you nothing else, and it proves the host
can actually hold the model before you have twelve other containers competing for the same memory.

Watch `free -h` while the first generation loads it. If available memory approaches zero, stop here
— with no swap the next thing that happens is an OOM kill, and it will not necessarily be Ollama
that gets killed.

---

## 6. Bring up the data tier, then migrate, then the app

Order matters. The gateway process *will* start against an unmigrated database — it is the
readiness probe that refuses, returning 503 with `checks.schema` naming the revision it expected
and the one it found. That is deliberate: a container that exits on a missing schema restarts
forever and tells you nothing, while a container that stays up and reports itself unready keeps
its logs and its `/health/ready` body available for you to read. The container healthcheck polls
that same endpoint, and nginx declares `depends_on: gateway: condition: service_healthy`, so an
unmigrated schema stops the edge from starting at all rather than exposing a gateway that cannot
serve. Run 6b before 6c and this never arises.

Note that three tables — `aura_documents`, `aura_chunks`, `aura_memories` — are created by the
application itself on first connect, so their presence is not evidence that migrations have run.
Check `alembic_version`.

```bash
cd /opt/aura-stack/deploy
DC="docker compose -f docker-compose.prod.yml --env-file .env"

# 6a. Data tier alone
$DC up -d postgres redis qdrant
$DC ps                       # all three healthy before continuing

# 6b. Schema. Forward-only; the migrations are additive.
$DC run --rm --no-deps -T gateway alembic upgrade head

# 6c. Application tier
$DC up -d gateway worker-realtime worker-ingestion beat

# 6d. Observability last — it is the tier you can most afford to lose
$DC up -d prometheus grafana loki promtail alertmanager node-exporter

# 6e. Edge, only if this stack owns 80/443
$DC up -d nginx
```

If a step fails, the tiers below it are still running and still serving. That is the reason for the
order.

---

## 7. Verify

```bash
# Liveness — is the process up
curl -fsS http://localhost:8000/health/live

# Readiness — are its dependencies actually reachable. This is the one that matters.
curl -fsS http://localhost:8000/health/ready | jq

# The legacy v1/v2 health endpoint reports each dependency separately
curl -fsS http://localhost:8000/health | jq '.dependencies'

# Inference, end to end
$DC exec ollama ollama run qwen2.5:7b-instruct-q4_K_M "reply with the single word: ready"
```

`/health/ready` failing while `/health/live` passes means the app is up and a dependency is not —
read the body, it names which. That split is why a transient database blip does not get the
container killed.

Then the security posture, from [Deployment Guide §5](deployment-guide.md#5-verify-the-security-posture):

```bash
# Nothing but 80/443 should be exposed
ss -tlnp | grep -vE '127\.0\.0\.1|::1'

# No security bypass active — must be empty
curl -fsS http://localhost:8000/metrics | grep -i 'security_bypass' || echo "  clean"
```

---

## 8. Point the client at it

The Flutter client's backend URL is **compile-time** (`String.fromEnvironment` in
`lib/core/config/config.dart`). There is no runtime override and no settings screen — an installed
APK cannot be redirected, it must be rebuilt.

`flutter run` / IDE debug defaults to `http://localhost:8000`. Production builds **must** inject
`https://$AURA_DOMAIN` — the same hostname TLS was issued for and nginx `server_name` uses.
That is the production domain (`aureon-ai.tech`). Do not bake a bare IP into the APK.

```bash
# from the repo root, after AURA_DOMAIN is set in deploy/.env
./deploy_client.sh          # or .\deploy_client.ps1 on Windows

# TLS must already match that hostname:
./deploy/scripts/verify-client-hostname.sh
```

Equivalent one-liner (still `https://$AURA_DOMAIN`, never an IP):

```bash
flutter build apk --release --dart-define=BACKEND_URL=https://$AURA_DOMAIN
```

Use HTTPS. Android blocks cleartext HTTP by default, so a bare `http://IP:8000` will fail in the app
even though `curl` is perfectly happy with it. On a shared host, the vhost in
[`host-proxy.conf.example`](../../deploy/nginx/host-proxy.conf.example) must use the same
`server_name`.

---

## 9. Rollback

Nothing in §6 destroys the previous deployment — it runs alongside on different container names.

```bash
cd /opt/aura-stack/deploy
docker compose -f docker-compose.prod.yml --env-file .env down     # stop the new stack
# the old stack under /opt/aura/docker-compose.vps.yml is untouched and still running
```

The two migrations are additive (`0002` adds nullable columns with no backfill), so old code
tolerates the new schema and a code rollback does not require a schema rollback.

Once the new stack has served real traffic for long enough to trust it, retire the old one — and
delete `/opt/aura.backup-20260804` while you are there.

---

## 10. Afterwards

- **Run one restore drill.** `deploy/scripts/restore.sh` has never been executed. A backup you have
  not restored from is a hypothesis. This is a couple of hours and it is the cheapest reliability
  improvement available.
- **Shorten Prometheus retention** to 14 days on a disk under 100 GB.
- **Add `pip-audit` to CI.** Hours of work, and it is the largest single gap in the security score.
- **Measure before enabling the cross-encoder reranker.** Its 100–300 ms figure is inferred, not
  measured, and it competes with the generator for CPU.

---

Related: [Deployment Guide](deployment-guide.md) ·
[Hardware Capacity Report](../../AI_HARDWARE_CAPACITY_REPORT.md) ·
[Administrator Guide](administrator-guide.md) ·
[Troubleshooting](troubleshooting-guide.md)
