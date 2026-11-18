# Launch readiness: phase 1 floor (spec §6.1)

The ten items the spec requires before the first paying user, from
`docs/superpowers/specs/2026-11-16-saas-phase1-design.md` §6.1. Each needs the evidence the spec
names, not just a merged PR. This page says where each item stands and who acts next.

It uses the scale from `docs/status/README.md`:
- **PROVEN** means a committed live report;
- **TESTED** means suites that ran in CI;
- **BUILT** means the code exists;
- **OPEN** means not done.

Every suite below has run only locally, because CI gets no runners (CB-44). So nothing here is
TESTED in that sense yet.

**State as of 2026-11-18.** PR #246 (SaaS plans A–G) is merged. Production at
`api.aureon-ai.tech` serves `61ad66e`, schema `0029`, `healthy: true`. Providers: `hosted1`
healthy, `hfspace` HTTP 503.

| # | Item | Status | Evidence the spec names | What exists | Next action (who) |
|---|---|---|---|---|---|
| 1 | `main` protected; CI required and actually runs | **OPEN** | A Backend CI run on a PR touching `aura_backend/`, executed and green. A branch-protection rule. Railway deploys only after CI | CI jobs end in 1–2 s with `runner_id: 0` on every push (CB-44, still so on 2026-11-18). Railway auto-deploys every `main` push, and #246 went live this way | **Owner:** restore Actions runners (billing/quota on the GitHub account); add a branch-protection rule requiring Backend, Client and Admin CI; in Railway → service → Settings, enable **Wait for CI**, or turn auto-deploy off and deploy from `control-plane-deploy.yml` |
| 2 | No training-permitted model serves customers | BUILT | Live provider list shows no `:free` / `ALLOW_PROMPT_TRAINING=true` slot in a customer pool | `deploy/scripts/verify_gpu_source.py` check 5 reads every provider's `may_train_on_prompts` from `/api/v3/admin/status` | **Owner:** run the verifier with an Admin token, then commit the report |
| 3 | `/metrics`, `/docs` not public | BUILT (seen live) | An unauthenticated request returns 401/404 | Seen 2026-11-18: `/metrics`, `/docs`, `/openapi.json`, `/redoc` → 404. Verifier check 4 | Committed with the item 2 report |
| 4 | Backups and restore on the live database | BUILT | An offsite restore drill on Railway's Postgres within a stated RPO/RTO, with a report in `docs/reports/` | Nightly backup cron image, `deploy/backup/railway.Dockerfile`. Drilled locally against Postgres 18.7 (`docs/deployment/backups.md`) | **Owner:** create the Railway backup service and the B2/R2 buckets (`backups.md` §1); run `restore-drill.sh` against a real set and commit the report. **Owner decision:** the RPO and RTO to state (nightly backup ⇒ RPO ≤ 24 h) |
| 5 | Monitoring and alerting: errors, latency, provider health, payment webhook failures | BUILT | An alert firing in a test | One rule file, `deploy/monitoring/alerts/aura.yml`, now including the `aura-billing` group (SaaS P1-H): cycle stale or failing, rejected or crashing notifications, the Play API down, email failing. Railway runs it through Grafana Cloud (`docs/deployment/observability.md`) | **Owner:** set `AURA_METRICS_TOKEN`, create the Grafana Cloud scrape job `aura-gateway`, load `aura.yml`, add a contact point. **Then fire one alert in a test:** on a staging deployment with billing on, post 4 unsigned bodies to `/api/v3/billing/fawry/notify` within an hour. `AuraBillingNotificationsRejected` fires; commit the notification (screenshot or log) under `docs/reports/` |
| 6 | Capacity measured for the launch target | BUILT | A load run against the live topology for the owner's target | **Target: 10 concurrent** (owner, 2026-11-18). Verifier check 7 sends 10 concurrent turns and requires all 200 with p95 ≤ 60 s | Needs item 9 first (the GPU live), then the same verifier run |
| 7 | SEC-03 secret rotated and removed | BUILT (removed) | Rotated in Google Cloud and removed from code | The `GOCSPX-…` default is gone from `aura_client/lib/core/config/config.dart` (only a warning comment remains; `git grep GOCSPX-` finds no value) | **Owner:** confirm the old desktop OAuth client secret was rotated or deleted in Google Cloud Console. Until then the leaked value stays valid |
| 8 | Cross-workspace isolation suite green | BUILT | §9's isolation tests green | SaaS P1-A: HTTP isolation and IDOR scenarios for every scoped route (`tests/security/test_idor.py`, `tests/integration/test_workspaces_*`), green locally in every plan since | Green in CI once item 1 holds |
| 9 | The sensitive-capable GPU source is live and healthy | **OPEN** | A passing verifier report committed; live `/health/ready` shows it healthy; a document/RAG turn served end to end on production | One L4, always on (owner, 2026-11-18). The deploy workflow enforces the hardware. Verifier checks 1, 2, 3, 6. Live: `hfspace` 503 | **Owner:** the steps in `docs/deployment/inference-plane.md` §9 (L4, never sleep, persistent storage, `HEALTH_PROBE=active`), then the verifier run |
| 10 | Desktop Engineer opened safely | BUILT | ADR-011 amendment merged; tests that a Free or downgraded workspace cannot pair or refresh; tests that a customer desktop session never reaches a console route | Merged with #246: ADR-011 "Customers", `tests/integration/test_desktop_engineer_customers.py`. Off until `AURA_ENGINEER_FOR_CUSTOMERS=true` | Green in CI once item 1 holds |

## Outside §6.1 but before announcing (spec §9)

- **A Fawry staging run.** Two flows, recorded in `docs/reports/`: checkout → notification → active, and reference code → paid → active (`docs/deployment/billing-fawry.md`).
- **One sandbox purchase per store,** mapped to an active subscription (`docs/deployment/billing-stores.md`, test-purchase table).
- **The owner decisions still open** (spec §8):
  - prices and limits (`AURA_PLAN_CATALOGUE`; the live numbers are placeholders);
  - the email provider;
  - ETA e-invoicing and VAT;
  - whether in-app prices match web prices;
  - the legal texts (`TERMS_URL`, `PRIVACY_URL`, `REFUND_URL`).

## Already live after the #246 merge

These need no switch, so check them now:
- **Plan limits apply,** with the placeholder numbers.
- **A team owner cannot delete their account** while they own a team (409 `owns_team_workspaces`).

Everything else stays off until its setting is turned on:
- `FIREBASE_CREDENTIALS_JSON`;
- `AURA_BILLING_ENABLED` plus the Fawry or store settings;
- `AURA_ENGINEER_FOR_CUSTOMERS`;
- `AURA_EMAIL_ENABLED`.
