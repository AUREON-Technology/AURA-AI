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
| `backup.sh` / `restore.sh` | Local backup cycle (invoked by backup service) |
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
  --env-file deploy/.env.development run --rm gateway alembic upgrade head
./deploy/scripts/seed-dev.sh
```

Renew dual-hostname TLS on the VPS: `cd deploy && ./scripts/renew-tls-dual-san.sh` (see `docs/guides/deployment-guide.md`). Google OAuth dev origin setup is documented there too.
