# Scripts

Operational helpers that run from the repository root. Each script resolves paths relative to
the repo root, so they work whether invoked directly or via the thin wrappers at the repository
root (`deploy_client.sh`, `deploy_client.ps1`, etc.).

| Script | Purpose |
| --- | --- |
| `deploy_client.sh` / `.ps1` | Production Flutter build with `BACKEND_URL` from `deploy/.env` |
| `deploy_client_desktop.sh` / `.ps1` | Desktop Flutter build (Linux/macOS via `.sh`, Windows via `.ps1`) with the same `BACKEND_URL` resolution; does not build or bundle `aura_hostd` |
| `run_desktop_stack.sh` / `.ps1` | Build (or `--skip-build`/`-SkipBuild` reuse) `aura_hostd` + the desktop client, then run both together on this machine — the one-shot way to actually use the desktop Engineer agent locally |
| `run_client_device.ps1` | Physical Android debug run against production HTTPS |
| `deploy_rules.ps1` | Deploy Firebase rules from `firebase/` |
| `init_infra.ps1` | Legacy GCP/Firebase bootstrap (historical) |
| `collect-vps-logs.ps1` | Pull last N days of VPS logs into `docs/reports/` (Windows) |

## Dual-environment VPS

Production (`aura` compose project) and development (`aura-dev`) run in parallel on the same
host. Prod nginx routes `dev.<ip>.nip.io` to the dev gateway/console over the shared Docker
network `aura_edge`; Ollama stays on prod to save RAM.

| Script / file | Purpose |
| --- | --- |
| `deploy/docker-compose.dev.yml` | Dev stack — isolated Postgres/Qdrant/Redis, shared Ollama |
| `deploy/.env.development.example` | Template for `deploy/.env.development` |
| `deploy/scripts/seed-dev.sh` | Seed Loop B console data on `aura-dev` only (refuses prod) |
| `aura_backend/scripts/seed_dev_console.py` | Wipes Loop B tables, inserts feedback, runs fine-tune pipeline |

Typical dev bring-up (on the VPS, after prod is running):

```bash
cp deploy/.env.development.example deploy/.env.development
docker compose -p aura-dev -f deploy/docker-compose.dev.yml \
  --env-file deploy/.env.development --profile training up -d
docker compose -p aura-dev -f deploy/docker-compose.dev.yml \
  --env-file deploy/.env.development run --rm dev-gateway alembic upgrade head
./deploy/scripts/seed-dev.sh
```

The Flutter app can switch between prod and dev URLs in **Settings → Server environment**
without rebuilding the APK.

See `docs/guides/deployment-guide.md` § Dual-environment VPS for TLS and Google OAuth steps.
