# Deploy scripts

Operational helpers for the VPS production and development stacks. Run from the repository
root unless noted.

| Script | Purpose |
| --- | --- |
| `bootstrap-secrets.sh` | Generate `deploy/secrets/*` for first prod bring-up |
| `bootstrap-host.sh` | Host prep (Docker, firewall) |
| `vps-update.sh` | Pull `main`, migrate, rebuild prod stack, health-check |
| `renew-tls-dual-san.sh` | On VPS: expand Let's Encrypt cert with prod + dev SANs (deploy/) |
| `seed-dev.sh` | Seed Loop B data on **`aura-dev` only** (refuses prod project) |
| `backup.sh` / `restore.sh` | Backup cycle (invoked by backup service): encrypted, manifested, GFS-retained sets + verified offsite copy; restore verifies and decrypts a set first |
| `backup-lib.sh` | Shared set primitives (age encryption, SHA-256 manifests, retention, offsite verify); sourced, not run |
| `backup-fetch.sh` | Pull a set back from offsite: newest set across all `BACKUP_OFFSITE_TARGETS`, first copy that verifies keyless; prints its path for `restore.sh` / `restore-drill.sh` |
| `backup-open.sh` | `--verify` a set without a key, or decrypt one (Qdrant/Redis recovery) |
| `restore-drill.sh` | Throwaway-Postgres restore drill; records timing (RTO evidence) with `DRILL_RECORD` |
| `tests/test_backup_crypto.sh` | Docker-free tests for `backup-lib.sh` (run in Backend CI) |
| `render-sandbox-filter.sh` | Build the workspace proxy's filter: `deploy/sandbox/filter` + the validated hosts of `AURA_SANDBOX_ALLOWED_EGRESS_HOSTS` (run by the `sandbox-proxy-filter` compose service; try it by hand with `--hosts a.example.com --check`, or without `--check` to see the file). Docker-free tests: `aura_backend/tests/unit/test_render_sandbox_filter.py`. Not yet run inside the compose stack: on the VPS, `docker compose --profile sandbox up sandbox-proxy-filter sandbox-proxy`, then `docker exec <proxy> cat /etc/tinyproxy/rendered/filter` and a `curl -x http://sandbox-proxy:8888 https://<listed host>` / `<unlisted host>` from a workspace network |
| `ci-deploy.sh` | CI entrypoint for automated prod deploy |
| `client_backend_url.sh` / `.ps1` | Print `BACKEND_URL` from `deploy/.env` |

## Dual-environment (prod + dev)

Production stays on `deploy/docker-compose.prod.yml` with `deploy/.env`. Development runs in
parallel via `deploy/docker-compose.dev.yml` and `deploy/.env.development` (see
`.env.development.example`). Prod nginx terminates TLS for both hostnames; dev gateway and
console join the external Docker network `aura_edge`.

After first dev bring-up:

```bash
docker compose -p aura-dev -f deploy/docker-compose.dev.yml \
  --env-file deploy/.env.development run --rm dev-gateway alembic upgrade head
./deploy/scripts/seed-dev.sh
```

Renew dual-hostname TLS on the VPS: `cd deploy && ./scripts/renew-tls-dual-san.sh` (see `docs/guides/deployment-guide.md`). Google OAuth dev origin setup is documented there too.

When local SSH to the VPS (port 22) is blocked, use **GitHub Actions → VPS Dev Ops** (`workflow_dispatch`) — same `VPS_SSH_KEY` as Deploy VPS — to run TLS renewal, dev stack bring-up, alembic, and seed.
