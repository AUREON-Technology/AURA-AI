# Host Migration Runbook — moving to a larger VPS

Moving an existing AURA deployment to a host that can actually run it. Written against the
`srv1794216` → Contabo Cloud VPS 3 (8 vCPU / 24 GB) move, but the structure is provider-agnostic.

Read [`AI_HARDWARE_CAPACITY_REPORT.md`](../../AI_HARDWARE_CAPACITY_REPORT.md) first for why the move
is necessary. Once you are on the new host, [`provisioning-runbook.md`](provisioning-runbook.md)
takes over.

> **This host is shared.** VELVET — a separate Django application with its own Postgres and a
> host-level nginx — runs on the same machine and owns ports 80, 443 and 8080. Every step below is
> written to leave it running. If Velvet breaks, the migration has failed regardless of what AURA
> is doing.

---

## Phase 0 — One question to Contabo, before anything else

**Ask support: does upgrading to Cloud VPS 3 keep the same IP address, or is it a migration to new
hardware?**

The answer decides which of two very different jobs this is:

| Answer | What it means | Effort |
| --- | --- | --- |
| **Same IP, in-place** | Reboot into a bigger machine. Disk may need manual expansion. | ~1 hour |
| **New IP / new server** | Full data migration and a cutover. DNS, TLS and the mobile app all change. | ~half a day |

Do not guess this. If the IP changes and you have not planned for it, every client with the old
address — including the Flutter app, whose `BACKEND_URL` is compile-time — stops working with no
error that points at the cause.

Also confirm the **disk size** of the target plan and whether the filesystem is grown automatically
or left for you (Phase 4).

---

## Phase 1 — Inventory, before touching anything

You cannot back up what you have not found. Three things on this host are not where a fresh install
would put them, and all three are easy to lose.

```bash
mkdir -p /root/aura-migration && cd /root/aura-migration

# The compose file that actually defines the running stack (NOT the one in the repo)
cp /opt/aura/docker-compose.vps.yml . 2>/dev/null || echo "MISSING — find it: docker compose ls"

# Every volume, and which container owns it. Named volumes survive `down`; bind mounts follow
# the host path; anonymous volumes are silently lost on recreate.
docker volume ls > volumes.txt
for c in aura_backend aura_redis aura_celery_worker aura_celery_beat; do
  echo "=== $c ==="
  docker inspect "$c" --format '{{json .Mounts}}' | python3 -m json.tool
done > mounts.txt 2>&1

# Environment, including any secret that exists only as a running container's env
for c in aura_backend aura_celery_worker; do
  echo "=== $c ==="; docker inspect "$c" --format '{{range .Config.Env}}{{println .}}{{end}}'
done > env.txt 2>&1

# Which Postgres holds what. There appear to be TWO on this box: a host-level one on
# 127.0.0.1:5432 and a containerised velvet_postgres. Establish which is which before moving either.
ss -tlnp | grep 5432 > postgres-listeners.txt
sudo -u postgres psql -c '\l' > postgres-host-databases.txt 2>&1 || echo "no host postgres" >> postgres-host-databases.txt
docker exec velvet_postgres psql -U postgres -c '\l' > postgres-velvet-databases.txt 2>&1

# Velvet's edge configuration — this is what must keep working
cp -r /etc/nginx/sites-enabled ./nginx-sites-enabled 2>/dev/null
nginx -T > nginx-full-config.txt 2>&1

ls -la; echo "inventory captured in $(pwd)"
```

### The three things that are easy to lose

**1. Vector data may be inside the container.** `services/rag.py` falls back to
`QdrantClient(path="local_qdrant_storage")` — a **relative** path. Unless the running compose file
mounts a volume over it, the entire vector index lives in the container's writable layer and is
destroyed by any `docker compose up --force-recreate`. Check `mounts.txt`:

```bash
docker exec aura_backend sh -c 'ls -la local_qdrant_storage 2>/dev/null | head' \
  || echo "not present — Qdrant Cloud is probably in use; check QDRANT_HOST in env.txt"
```

If it exists and is not on a mount, copy it out **now**:

```bash
docker cp aura_backend:/app/local_qdrant_storage ./local_qdrant_storage
```

**2. The learning corpus has the same problem.** `settings.data_dir` defaults to `"."`, so
`learning_logs.json` and the compiled datasets are written inside the image. Same fix:

```bash
docker cp aura_backend:/app/learning_logs.json . 2>/dev/null || echo "none"
```

**3. `/opt/aura/aura_backend` is not this repository.** It tracks a Hugging Face Space
(`git@hf.co:spaces/…/aura-ai-backend`); its HEAD commit does not exist in the GitHub repo. **Do not
migrate this directory and do not try to pull into it.** Archive it as a reference and clone fresh
on the new host (Phase 5).

```bash
tar czf hf-space-checkout.tgz -C /opt/aura aura_backend
```

---

## Phase 2 — Back up the databases

```bash
cd /root/aura-migration

# Host-level Postgres, all databases and roles
sudo -u postgres pg_dumpall > postgres-host-all.sql

# Velvet's containerised Postgres
docker exec velvet_postgres pg_dumpall -U postgres > postgres-velvet-all.sql

# Redis, if anything in it matters. Mostly cache and rate-limit state — but the refresh-token
# store also lives here, so skipping this logs every user out.
docker exec aura_redis redis-cli SAVE
docker cp aura_redis:/data/dump.rdb ./redis-dump.rdb

# Verify the dumps are non-trivial before trusting them
ls -lh *.sql *.rdb
head -5 postgres-host-all.sql
```

**Copy this whole directory off the server.** A backup that only exists on the machine you are about
to rebuild is not a backup.

```bash
# from your workstation
scp -r root@152.239.118.239:/root/aura-migration ./aura-migration-$(date +%F)
```

---

## Phase 3 — Quiesce

```bash
cd /opt/aura
docker compose -f docker-compose.vps.yml stop
docker compose ls          # aura should show 0 running; velvet untouched
```

Stop, do not `down`. `stop` leaves containers and anonymous volumes intact, so the rollback in
Phase 8 is a single `start`.

---

## Phase 4 — The upgrade

### If Contabo said "in-place, same IP"

Trigger the upgrade in the Contabo control panel and let it reboot. Then:

```bash
nproc; free -h; df -h /          # confirm 8 vCPU / 24 GB and the new disk size
```

**The filesystem does not always grow with the disk.** If `df -h /` still shows 48 GB:

```bash
lsblk                                        # find the real device and partition size
growpart /dev/sda 1                          # extend the partition
resize2fs /dev/sda1                          # ext4 — use xfs_growfs for xfs
df -h /                                      # confirm
```

**Add swap. This host had none**, which is why memory pressure here is an OOM kill rather than a
slowdown. With a language model in the picture that matters:

```bash
fallocate -l 4G /swapfile && chmod 600 /swapfile && mkswap /swapfile && swapon /swapfile
echo '/swapfile none swap sw 0 0' >> /etc/fstab
sysctl -w vm.swappiness=10 && echo 'vm.swappiness=10' >> /etc/sysctl.conf
free -h
```

Swap is insurance, not capacity — if the model is paging you have already lost. `swappiness=10`
keeps the kernel from using it for anything but genuine pressure.

### If Contabo said "new server"

Provision the new box, then on it:

1. Install Docker Engine + Compose plugin, nginx, and Postgres if Velvet needs a host-level one.
2. `scp` the `aura-migration` directory across.
3. Restore: `sudo -u postgres psql -f postgres-host-all.sql`, and for Velvet
   `docker exec -i velvet_postgres psql -U postgres < postgres-velvet-all.sql`.
4. Move Velvet: `/code/VELVET` and its compose file, plus `nginx-sites-enabled` and its TLS
   certificates from `/etc/letsencrypt`.
5. Bring Velvet up and verify it **before** starting on AURA. It is the one with existing users.
6. Repoint DNS, then reissue TLS certificates once the records have propagated.

Keep the old server running until the new one is verified. Paying for two boxes for a week is
cheaper than an outage.

---

## Phase 5 — Deploy the full stack

Now you have the headroom the stack was designed for. Hand off to
[`provisioning-runbook.md`](provisioning-runbook.md), starting at §1 — a **fresh clone from GitHub**,
not the Hugging Face checkout.

Two things specific to this host:

**Port collision.** The host nginx owns 80 and 443 for Velvet, and `docker-compose.prod.yml`
publishes exactly those. Remove the `nginx` service from the compose file and let the existing nginx
reverse-proxy to the gateway on loopback:

```nginx
# /etc/nginx/sites-available/aura
server {
    listen 443 ssl http2;
    server_name aura.your-domain;

    # SSE streaming: buffering here holds tokens until the response completes,
    # which turns a streaming chat reply into a long silence followed by a wall of text.
    proxy_buffering off;
    proxy_read_timeout 300s;

    location / {
        proxy_pass http://127.0.0.1:8080;
        proxy_http_version 1.1;
        proxy_set_header Host              $host;
        proxy_set_header X-Real-IP         $remote_addr;
        proxy_set_header X-Forwarded-For   $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
        proxy_set_header Connection        "";
    }
}
```

Publish the gateway on `127.0.0.1:8080` instead of `80:80`/`443:443`. **Not 8000** — that is
Velvet's Django, and **not 8080** either if `nginx -T` shows it already bound there; pick a free
loopback port and keep the proxy in step with it.

**Memory budget on a 24 GB box, with Velvet resident:**

| | |
| --- | --- |
| Velvet (measured, current) | ~1.5 GB |
| Ollama + 7B Q4 | ~6 GB |
| App tier (gateway, 2 workers, beat) | ~6.5 GB |
| Data tier (Postgres, Qdrant, Redis) | ~2.5 GB |
| Observability | ~1.5 GB |
| **Total** | **~18 GB of 24 GB** |

That leaves ~6 GB of headroom, which is comfortable rather than generous. If it gets tight, the
observability tier is the first thing to shed — it is the one whose absence you can detect.

---

## Phase 6 — Restore the data that was not in a database

```bash
# Vector index, if it was on local disk (Phase 1). Copy it to a bind-mounted host path this
# time, so the next recreate does not destroy it.
mkdir -p /opt/aura-stack/data/qdrant
cp -r /root/aura-migration/local_qdrant_storage/* /opt/aura-stack/data/qdrant/

# Learning corpus — and set AURA_DATA_DIR in .env so it stops being written into the image
mkdir -p /opt/aura-stack/data/learning
cp /root/aura-migration/learning_logs.json /opt/aura-stack/data/learning/ 2>/dev/null
```

If you moved to the real Qdrant service rather than the on-disk fallback, the index is not portable
between them — rebuild it instead:

```bash
curl -fsS -X POST http://localhost:8080/api/v3/admin/index/rebuild -H "Authorization: Bearer $TOKEN"
```

---

## Phase 7 — Verify

```bash
# Velvet first. It has users.
curl -fsS -o /dev/null -w '%{http_code}\n' https://velvet-domain/

# Then AURA
curl -fsS http://localhost:8080/health/live
curl -fsS http://localhost:8080/health/ready | jq
curl -fsS http://localhost:8080/health | jq '.dependencies'   # ollama must be reachable:true

# Resources under a real load, not at idle
free -h; docker stats --no-stream
```

`dependencies.ollama.reachable` being `true` is the single check that distinguishes this deployment
from the old one. It was never true on the old host, because there was no Ollama.

Then rebuild the mobile client — **this is what fixes the 404s**, and no server-side change does:

```bash
flutter build apk --release --dart-define=BACKEND_URL=https://aura.your-domain
```

---

## Phase 8 — Rollback

```bash
# Stop the new stack
cd /opt/aura-stack/deploy && docker compose -f docker-compose.prod.yml down

# Restart the old one — Phase 3 used `stop`, so it is intact
cd /opt/aura && docker compose -f docker-compose.vps.yml start
```

On a new-server migration, rollback is repointing DNS at the old box, which is why it stays running
for a week.

Retire the old stack only after the new one has served real traffic. Then clean up:
`/opt/aura.backup-20260804`, the archived HF checkout, and the migration directory — **after**
confirming the off-server copy of the backups still exists.

---

## What this does not solve

The move buys headroom, not speed. Eight CPU cores generate an estimated 8–15 tok/s on a 7B Q4
model — **an estimate, not a measurement** — and long RAG contexts are the part that hurts most,
because time-to-first-token is dominated by prompt processing rather than generation rate.

Measure `aura_llm_ttft_seconds` before deciding whether a GPU is needed. Two numbers to judge it
against, and they are not the same: `AuraSlowTimeToFirstToken` fires at **p95 > 8 s**, while the
Performance Report's operating *target* is 3 s. Renting a GPU for two hours to find out where you
land costs about a dollar; buying one costs about $300 a month.

---

Related: [Provisioning Runbook](provisioning-runbook.md) ·
[Hardware Capacity Report](../../AI_HARDWARE_CAPACITY_REPORT.md) ·
[Deployment Guide](deployment-guide.md) · [Maintenance Guide](maintenance-guide.md)
