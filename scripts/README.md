# Scripts

Operational helpers that run from the repository root. Each script resolves paths relative to
the repo root, so they work whether invoked directly or via the thin wrappers at the repository
root (`deploy_client.sh`, `deploy_client.ps1`, etc.).

| Script | Purpose |
| --- | --- |
| `deploy_client.sh` / `.ps1` | Production Flutter build with `BACKEND_URL` from `deploy/.env` |
| `run_client_device.ps1` | Physical Android debug run against production HTTPS |
| `deploy_rules.ps1` | Deploy Firebase rules from `firebase/` |
| `init_infra.ps1` | Legacy GCP/Firebase bootstrap (historical) |

Firebase rules live in `firebase/` and are referenced by `firebase.json` at the repo root.
