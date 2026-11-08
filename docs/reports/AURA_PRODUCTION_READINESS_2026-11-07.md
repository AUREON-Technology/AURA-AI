# AURA production readiness check — 2026-11-07

**Scope:** the whole system (backend on Railway, VPS stack, Flutter client, host daemon, operator
console, CI/CD), checked against the repository's own evidence rule: *built* and *tested* are not
*proven*.
**Code:** `main` at `772f9ef`. **Live:** `aureon-ai.tech` reports `ffbbb9f` (three docs/CI commits
behind `main`, no application code difference).
**Probed:** 2026-11-07, 21:00–21:30 UTC, from a cloud session with no production credentials.
Every live statement below is a GET against a public or health route. Nothing was changed on any
live system.
**Baseline it extends:** the 2026-10-31 full-system audit
(`docs/reports/audit/full-system-audit/`, readiness 2.10 / 5, verdict *not production-ready*).

---

## 1. Verdict

**Not production-ready.** The verdict of the 2026-10-31 audit stands. Two of its Phase 0 items
have moved, both operational and both good news:

- **The production schema is at `0019`, the code's head.** `/health/ready` shows `schema.revision
  0019`, `healthy: true`. The ADR-032 tables exist live, so the user data-key endpoint and the
  agent's personal-OS tools can work for the first time. The status docs still call this pending.
- **GitHub Actions runners are assigned again.** Repo Hygiene executed its steps on 2026-11-06 and
  2026-11-07, and an Admin CI run dispatched during this check (run #3, 2026-11-07 21:24 UTC, executed every step and passed in about two minutes, the first Admin CI run ever). CB-44's
  "jobs die in 2–4 s with no log" signature is gone for now. What has *not* happened: Backend,
  Client and Host Daemon CI have had no trigger since 2026-10-31 (their path filters did not match
  the three docs/CI merges), so no application suite has run in CI since `dc87beb` (2026-10-28).

Everything else that made the system unready on 2026-10-31 is unchanged, and this check adds live
confirmation for three of those findings:

| Blocker | Live evidence today | Reference |
| --- | --- | --- |
| Deploys ship with no gate | `main` unprotected; Railway auto-deploys every push; `Deploy Control Plane` has failed on every run (its `preflight` job never passes); only #208 is open | FSA-020, GAP-005 / CB-02 |
| The one healthy provider cannot serve sensitive turns | `hosted1` healthy, `hfspace` **HTTP 503**, `local_healthy: false`. `hosted1`'s declared ceiling is `internal` (Phase 6 report), so every memory, RAG, tool, Engineer and image turn has no eligible provider. Only context-free chat is served | FSA-024, CB-16/17 |
| Retrieval runs on the hash embedder in production | readiness reports `index: aura-knowledge__v1__hash-embed-v1__384`. Not refused by `AuraSettings.validate()`; code still defaults to `hash-embed-v1` | FSA-031 |
| `/metrics` is readable by anyone | `GET /metrics` → **HTTP 200** with the full Prometheus exposition. The gate exists in code but is inert without `AURA_METRICS_TOKEN` | FSA-009 |
| OpenAPI schema is public | `GET /docs` → **HTTP 200** (FastAPI default `docs_url`, never disabled). Low severity on its own; it is a free route map for an attacker | new, P3 |
| Historical secrets unrotated | `aura_backend/credentials.json` and `.env` remain recoverable from history (`5b37323`, `d2b92f0`); no rotation record anywhere in `docs/` | FSA-002 |
| Taint does not cross agent delegation | no `inherited_taint` in `aura_core/`; `remember_fact` still in `_READ_TOOLS`; `RateLimitMiddleware` still stacked on the gateway. None of the audit's Phase 0 code PRs has landed | FSA-001, 005, 017 |
| VPS deploy path dead | `Deploy VPS` fails in its `blocked` job on every run; `169.58.188.43` resets TLS connections; `dev.aureon-ai.tech` does not resolve; `deploy/vps_known_hosts.tmp` still tracked | FSA-023 / CB-03, FSA-113 |
| Operator console unreachable | `/console/` → 404 on Railway; no other live target | FSA-042, GAP-007 |
| No load, backup or alerting evidence on the live topology | nothing new in `docs/reports/` since the audit; GAP-011 / GAP-041 (P0) open | FSA-028/029, GAP-028 |

The audit's five-way split still describes the system: architecture high, implementation
medium-high, integration medium-low, **operations low**, production readiness **no**.

---

## 2. What was checked

### 2.1 Live production (`aureon-ai.tech`, `server: railway-hikari`)

| Probe | Result |
| --- | --- |
| `GET /health/live` | 200 `{"status":"alive"}` |
| `GET /health/version` | 200, `git_sha ffbbb9f…`, `git_ref main`, deployment `010b0ccf`, `identified: true` |
| `GET /health/ready` | 200, `healthy: true`, `schema.revision 0019`, blueprint `active` v2, 45 tools / 18 prompts / 9 agents, voice `piper-en+piper-ar` + `faster-whisper-tiny.en`, `live: false` |
| providers in `/health/ready` | `hosted1` healthy (74 ms); `hfspace` **503**; `local_healthy: false` (no Ollama) |
| `GET /metrics` (no token) | **200** — open |
| `GET /docs` | **200** — open |
| `GET /api/v3/capabilities`, `/knowledge/documents`, `/knowledge/cag`, `/knowledge/graph`, `/admin/service-keys` (no token) | 401 — auth enforced |
| `GET /api/v1/chat` | 404 — no legacy surface |
| `GET /console/`, `GET /` | 404 |
| Security headers on `/health/live` | HSTS (2 y, includeSubDomains), `X-Frame-Options: DENY`, `nosniff`, `Referrer-Policy`, `Permissions-Policy` present; no CSP (by design, see `security_headers.py`) |
| CORS with `Origin: https://evil.example` | no `Access-Control-Allow-Origin` echoed |
| `dev.aureon-ai.tech` | does not resolve |
| `https://169.58.188.43/health/live` (VPS) | connection reset |

### 2.2 CI/CD (GitHub Actions, `main`)

| Workflow | Last run on `main` | What it shows |
| --- | --- | --- |
| Repo Hygiene | #21 at `772f9ef`, 2026-11-07 20:36 UTC | **Executed, green** (actionlint + tracked-artefacts, real runner ids). #16 at `ced341b` executed and failed on SC1003, fixed by #210 |
| Admin CI | #3, `workflow_dispatch` at `772f9ef`, 2026-11-07 21:24 UTC (this check) | **Executed, green** on a real runner: checkout, Node setup, `npm ci`, `tsc --noEmit`, `next build`, Playwright chromium install, e2e suite. First Admin CI run ever (the two earlier runs died in the no-runner window). This is the evidence GAP-007 / CB-30 asked for |
| Backend CI | #431 at `ffbbb9f`, 2026-10-31 | all four jobs failed in 2 s with no log (no-runner signature). No trigger since |
| Client CI | #173 at `9257e46`, 2026-10-31 | same signature. No trigger since |
| Host Daemon CI | #66 at `3adb5a8`, 2026-10-31 | same signature. No trigger since |
| Agent Evaluation | #187 at `9257e46`, 2026-10-31 | same signature |
| Deploy Control Plane (Railway) | #141 at `ffbbb9f` | `preflight` failed (as on every run); `deploy` skipped. Production still moved to `ffbbb9f` through Railway's own GitHub auto-deploy |
| Deploy VPS | #250 at `ffbbb9f` | `blocked` job failed; `deploy` skipped (host-key change, CB-03) |
| Verify Hosted Inference | never run | GAP-064 stays `built_not_proven` |

Last CI that actually executed an application suite on `main`: `dc87beb` (2026-10-28), Backend CI
green (3848 passed), Client `analyze-and-test` green (381), `build-android` out of disk,
`build-windows` red, Host Daemon Linux hung on the PTY isolate. Twenty-two merges have shipped since
without a CI run.

Open pull request: #208 (draft, `anas/friendly-mayer-7h7aav`, client-only: the turn plan now reaches
the client). Its Client CI runs also died in the no-runner window.

### 2.3 Local suites run in this check (not CI)

| Suite | Toolchain | Result |
| --- | --- | --- |
| Backend `pytest` (default suite, in-memory substrate) | Python 3.11 venv built from `requirements.lock` with `--require-hashes` (uv needed `--index-strategy unsafe-best-match` to resolve the PyTorch CPU index, as pip does) | **4369 passed, 56 skipped, 1 failed.** The failure is `tests/architecture/test_no_committed_client_secrets.py` (`private key` case): the 2026-10-31 audit's `system_architecture.md`, line 117, quoted a PEM private-key header marker verbatim, so Backend CI on `main` has been red-by-content since `ced341b` without anyone seeing it. Fixed in this change (the quote is now prose); the test passes on the patched tree, so the suite is green at `772f9ef` plus this fix |
| Backend legacy `unittest` (`TESTING=True`) | same venv | **133 run, OK (37 skipped)** |
| Flutter client `flutter analyze` | Flutter 3.44.6 stable (the CI pin), Drift codegen regenerated | **No issues found** |
| Flutter client `flutter test` | same | **538 passed, 3 skipped, 0 failed** (59 s) |
| hostd `dart analyze --fatal-infos` | Dart from the same SDK | **No issues found** |
| hostd `dart test` (15-minute cap, as in CI) | same | **231 run: 230 passed, 1 failed** on the bare host. The failure is the debug-console case, which spawns `python -m debugpy`; with `debugpy` installed as `hostd-ci.yml` does, `control_server_test.dart` passes 47/47. No PTY-isolate hang on Linux: the run finished in 12 s, so the #185 fix holds here |
| Admin console `npm ci` + `next build` | Node 22.23 | **build OK** (10 routes). `npm run lint` is interactive because the project has no ESLint config; Admin CI uses `tsc --noEmit` instead, so the `npm run lint` line in CLAUDE.md does not describe a working command |

These are local results. Under the evidence rule they are TESTED-in-a-session, not CI-TESTED, and
nothing in them is PROVEN on the live target.

### 2.4 Source checks against the audit's Phase 0 code list

All negative — none of these has landed since `ffbbb9f` (the only merges since are #209 docs, #210
actionlint, and the audit itself):

- FSA-001 delegation taint: no `inherited_taint` anywhere in `aura_core/`.
- FSA-017: `_READ_TOOLS` in `aura_core/agents/registry.py` still contains `remember_fact`.
- FSA-005: `RateLimitMiddleware` still added in `aura_service.py`; no `--forwarded-allow-ips`.
- FSA-031: `embedding_model` defaults to `hash-embed-v1`; no production refusal.
- FSA-113: `deploy/vps_known_hosts.tmp` still tracked.
- FSA-032 follow-up: `scripts/move_personal_os_records.py` has no run record in `docs/`.

---

## 3. Readiness gates

| Gate | State | Basis |
| --- | --- | --- |
| Tests pass | **Local only** | §2.3, all green after the one-line audit-doc fix. No application CI run since `dc87beb`; Backend CI would have been red on `main` since `ced341b` had it run |
| CI gates merges and deploys | **FAIL** | `main` unprotected; Railway auto-deploy ungated; Deploy Control Plane never passes |
| Schema matches code live | **PASS** | `0019` on both sides. Whether the dashboard pre-deploy step or a manual `alembic upgrade` applied `0017`→`0019` is not recorded; the Railway deploy log for the #206/#207 deploys should show `revision before: 0016 … after: 0019` |
| Inference serves every turn class | **FAIL** | only an `internal`-ceiling provider is healthy; `hfspace` 503 |
| Retrieval uses a real embedder live | **FAIL** | `hash-embed-v1` |
| Secrets and exposure | **FAIL** | `/metrics` open, `/docs` open, historical key and `.env` unrotated, dev stack parity items from the audit unverified |
| Two P1 security defects (FSA-001, FSA-002) closed | **FAIL** | neither moved |
| Observability and alerting on the live target | **FAIL** | metrics exist but nothing scrapes Railway; no alert route |
| Backup / restore drilled on live data | **FAIL** | drills exist only for the VPS stack that takes no deploys |
| Capacity measured on the live topology | **FAIL** | GAP-011 / GAP-041 open; worker count on Railway unknown |
| Rollback path | **Partial** | Railway can redeploy a previous deployment; migration downgrade is tested on PG16 locally, never live |
| Clients | **Partial** | analyze clean, tests green locally; nothing packaged or signed; no device report; iOS/macOS have no pipeline |
| Operator console | **FAIL** | unreachable on every live target |
| Desktop Engineer path | **Unproven** | GAP-025/026; hostd suite result in §2.3; no live report |

---

## 4. What to do, in order

Owner actions first; they unblock everything and need no code.

1. **Protect `main` and gate Railway.** Required checks = Backend CI, Client CI, Host Daemon CI,
   Admin CI, Repo Hygiene. Turn off Railway's GitHub auto-deploy or enable *Wait for CI*. Then
   trigger one run of each application workflow (open any PR touching `aura_backend/`,
   `aura_client/`, `aura_hostd/`) and record the run ids in `docs/status/`. Until that run exists,
   "runners are back" rests on Repo Hygiene and Admin CI alone.
2. **Set `AURA_METRICS_TOKEN` on Railway** and confirm `GET /metrics` → 401. One variable.
3. **Disable `/docs`, `/redoc` and `/openapi.json` in production** (`FastAPI(docs_url=None, …)`
   when `environment == production`), or put them behind the admin role. Small PR.
4. **Restore a sensitive-capable provider** (fix or replace `hfspace`), or take the ADR-030 §4
   decision explicitly. Until then the product is "chat without memory, documents or tools".
5. **Decide the production embedder** and make `AuraSettings.validate()` refuse `hash-embed-v1`
   in production (FSA-031). Re-index after the switch.
6. **Rotate the historical GCP key and `.env` values and record the date** (FSA-002). Delete
   `deploy/vps_known_hosts.tmp` (FSA-113).
7. **Confirm how `0019` reached production** (deploy log) and run
   `scripts/move_personal_os_records.py --dry-run`, then for real, on Railway.
8. **VPS**: establish whether the host was reinstalled; keep (re-pin by PR) or decommission (rotate
   every secret it shared with Railway).
9. **Land the audit's Phase 0 code PRs**, each small: FSA-001 taint inheritance, FSA-017,
   FSA-005, FSA-004, FSA-006, FSA-007, FSA-008.
10. Then Phase 1 of `remediation_roadmap.md`: make every gate executable and required, add the
    regression tests the October bugs showed were missing, and produce the first real-model
    evaluation report. Only after that can any subsystem move to PROVEN.

---

## 5. Changes to the status ledger made with this report

- `docs/status/README.md` and `docs/status/BACKEND_STATUS.md`: schema `0019` is live (no longer
  pending); live revision `ffbbb9f`; runners are assigned again (Repo Hygiene, Admin CI) while the
  application workflows remain untriggered since 2026-10-31; `/metrics` and `/docs` are open on
  Railway; open PR #208.
- `docs/reports/audit/full-system-audit/system_architecture.md`: the quoted PEM header marker on
  line 117 is now prose, so `tests/architecture/test_no_committed_client_secrets.py` passes again.
- `CLAUDE.md`: the CI paragraph no longer says CI is not executing; it says which workflows have
  been seen executing and which have not run since `dc87beb`.

Nothing in `docs/production/gap_register.yaml` changes status: no gap gained the evidence its exit
condition names.

---

## Appendix — commands and sources

- Live: `curl` against `https://aureon-ai.tech{/health/live,/health/version,/health/ready,/metrics,/docs,/console/,/api/v3/…}`; DNS for `dev.aureon-ai.tech`; TLS to `169.58.188.43`.
- CI: GitHub Actions run and job listings for `main`, `anas/friendly-mayer-7h7aav`, and
  `admin-ci.yml`; job log of Repo Hygiene #16.
- Backend: `uv venv --python 3.11`; `uv pip install --index-strategy unsafe-best-match
  --require-hashes --extra-index-url https://download.pytorch.org/whl/cpu -r requirements.lock`;
  `-r requirements-dev.txt`; `pytest -q`; `TESTING=True python -m unittest discover -p "test_*.py"`.
- Client / hostd: Flutter 3.44.6 stable tarball; `flutter pub get`; `dart run build_runner build
  --delete-conflicting-outputs`; `flutter analyze`; `flutter test`; `dart pub get`; `dart analyze
  --fatal-infos`; `dart test`.
- Admin: `npm ci`; `npm run lint`; `npm run build`.
- Documents: `docs/status/*`, `docs/roadmaps/AURA_CRITICAL_BACKLOG.md`,
  `docs/production/gap_register.yaml`, `docs/reports/audit/full-system-audit/*`,
  `.claude/audits/2026-10-26-full-699f27b/audit-report.md`.
