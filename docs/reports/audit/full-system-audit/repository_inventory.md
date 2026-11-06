# Repository inventory (Phase 0 baseline)

**Audit:** AURA AI full-system engineering audit · **Date:** 2026-10-31 · **Author:** lead auditor
**Baseline:** single Git repository `E:\AURA AI`, remote `origin = github.com/anasibrahimgnedy-collab/AURA-AI`, branch `main`, HEAD `ffbbb9fff777a7b6117236c67d517fd201ac2208` (merge of PR #207, 2026-10-31 13:00 +02:00). 946 commits on `main`; 68 remote branches. No submodules (`git submodule status` empty). (EXECUTED-HERE)

Working tree at baseline: 5 modified files, all Flutter-generated plugin registrants (`aura_client/{linux,windows}/flutter/generated_plugin*`, `aura_client/macos/Flutter/GeneratedPluginRegistrant.swift`); untracked `AGENTS.md` and `.claude/skills/system-audit/`. None is application source. Treated as clean for audit purposes.

## 1. Repositories and worktrees

| Repository / worktree | Path | Branch | Commit | Working tree | Relation to `main` | Audited? |
| --- | --- | --- | --- | --- | --- | --- |
| **AURA-AI (authoritative)** | `E:\AURA AI` | `main` | `ffbbb9f` | 5 generated files modified | — | **Yes (complete, this audit)** |
| worktree `ecstatic-bassi-3e88ff` | `E:\AURA AI\.claude\worktrees\ecstatic-bassi-3e88ff` | detached | `a578d4e` (2026-09-13) | 1 change | ancestor of `main` (merged) | No — stale tool worktree, superseded |
| worktree `hopeful-poincare-112455` | `E:\AURA AI\.claude\worktrees\hopeful-poincare-112455` | detached | `2841edb` (2026-09-13, "restore localhost default for AppConfig.backendUrl") | 1 change | **not an ancestor of `main`** (one unmerged client commit) | No — stale; content not live anywhere |
| worktree `quizzical-yalow-570a1f` | `E:\AURA AI\.claude\worktrees\quizzical-yalow-570a1f` | `claude/quizzical-yalow-570a1f` | `d1d0939` (2026-07-20) | 1 change | ancestor of `main` | No — stale |
| worktree `aura-ai-integration-nasa-project` | `E:\AURA AI.worktrees\aura-ai-integration-nasa-project` | `docs/gate-default-pass` | `26fa624` (2026-10-10) | clean | ancestor of `main` | No — docs-only branch, merged |

**Only one version of AURA is authoritative: `main` at `ffbbb9f`.** The three `.claude/worktrees` entries are leftovers from earlier agent sessions (two already merged, one carrying a single unmerged client commit from September that is now superseded by the runtime backend switcher described in `docs/status/MOBILE_APP_STATUS.md`). Nothing in this audit merges findings across commits: every citation is at `ffbbb9f`.

## 2. Components inside the repository

| Component | Path | Technology | Responsibility | Runtime entry points | Deployment | Key dependencies | Test suites | Audit coverage |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| **Backend / Control Plane** | `aura_backend/` (674 tracked `.py`) | Python 3.11 (Docker `python:3.11-slim`); FastAPI; Celery; Alembic; SQLAlchemy/asyncpg; Qdrant client; Redis | The product: v3 HTTP/WS gateway, AI kernel, retrieval, memory, tools, planning, learning, admin API, sync, auth | `aura_service.py` (uvicorn gateway, `build_app()`), `celery_app.py` + `tasks.py` (workers/beat), `alembic/` (`0001`…`0019`), `scripts/railway-predeploy.sh` | Railway (`railway.json`, dashboard pre-deploy) = live `aureon-ai.tech`; Contabo VPS via `deploy/docker-compose.prod.yml` (path dead since 2026-10-18); HF Space for inference | `requirements.lock` (149 hash-pinned top-level lines), `requirements-dev.txt`, `requirements-train.*` | `pytest` (`tests/{unit,security,e2e,architecture,deployment,integration,load,benchmarks,training,certification}`), legacy root `test_*.py` (unittest, `TESTING=True`) | Complete (passes A–D, F1, F2, G) |
| **Flutter client (mobile/desktop/web)** | `aura_client/` (357 tracked `.dart`) | Flutter 3.44.6 / Dart 3.12.2; Riverpod 3; go_router; Drift | End-user app: chat, voice, knowledge, memory, Personal OS, desktop IDE + Engineer panel | `lib/main.dart`; platform runners under `android/ ios/ linux/ macos/ windows/ web/` | Android APK via `release-apk.yml` (manual); desktop via local `scripts/deploy_client_desktop.*`; iOS/macOS/web: no pipeline | `pubspec.yaml` v3.0.1 (sdk ^3.11.4) | `test/` (Flutter unit/widget/ux ratchets) | Complete (passes E1, E2, E3) |
| **Host daemon** | `aura_hostd/` (30 tracked `.dart`) | pure Dart 3.12 (no Flutter) | Local daemon letting the Engineer agent touch the user's real files/terminal (ADR-011): pairing, RPC over WS to `/api/v3/host/agent`, containment, command policy, PTY, git worktrees, DAP | `bin/aura_hostd.dart run` | Not packaged/bundled; built locally | `pubspec.yaml` v0.1.0 | `test/` (dart test) | Complete (pass E3) |
| **Admin console** | `aura_admin/` (44 tracked files) | Next.js 15 / React 19 / TypeScript; Playwright | Operator console at `/console` (10 pages) proxying to `/api/v3/admin/*` | `next start`; `middleware.ts`; `app/api/auth/login/route.ts` | Compose service `console` (prod/dev) only; **not served by Railway** | `package.json` | `tests/e2e/*.spec.ts` + `mock-gateway.mjs`, `tests/unit/refresh.spec.ts` | Partial (pass E2 reachability; tests not executed, see `testing_and_evaluation.md`) |
| **Deploy stack** | `deploy/` (58 files) | Docker Compose, nginx, shell | Prod/dev compose, nginx templates, sandbox image, backup, verification scripts, HF Space (`spaces/inference-vllm/`) | `deploy/scripts/vps-update.sh`, `bootstrap-*.sh`, `verify-*.sh` | VPS (Contabo) and HF Space | — | `tests/unit/test_sandbox_exec_surface.py`, `test_hosted_provider_compose_plumbing.py`, `tests/deployment/` | Complete (pass F2) |
| **CI/CD** | `.github/workflows/` (12 workflows) | GitHub Actions | backend/client/hostd/admin CI, VPS + Railway + HF deploys, APK release, eval, hosted-inference verification, repo hygiene | — | — | — | — | Complete (passes F2, G) |
| **Documentation** | `docs/` (architecture ADR 001–032, status, roadmaps, reports, deployment, production gap register) | Markdown/YAML | Claims to verify | — | — | — | `tests/unit/test_gap_register*.py` (schema) | Read as claims (all passes) |
| **Firebase rules + helper scripts** | `firebase.json`, `scripts/firebase/`, root `package.json` (`firebase ^12`) | Firebase CLI | Firestore/Storage rules deploy for Google sign-in project; client build/run helpers (`deploy_client*.{sh,ps1}`, `run_desktop_stack.*`, `collect-vps-logs.ps1`, `check_tracked_artifacts.py`) | — | manual | — | — | Reviewed for role only |
| **Root `docker-compose.yml`** | repo root | Compose | Local/legacy single-host stack (`backend, qdrant, redis, postgres, celery_worker, celery_beat`) — **not** the production stack (`deploy/docker-compose.prod.yml`) | — | local only | — | — | Noted; pass F2 |
| **`infra/`** | — | — | **Does not exist** on disk despite `CLAUDE.md` listing "`infra/` (Terraform)" (0 tracked files) | — | — | — | — | Documentation inaccuracy (DOC-01) |
| **`Doc/`** | `Doc/` | Markdown + 2 images | Informal pasted transcripts (Re-Assessment, Modern Agent Systems, Topics to Review) | — | — | — | — | Non-authoritative, per the 2026-09-25 review |
| Stray root files | `ML_ENVIRONMENT_REPORT.md`, `test_personal_os.py`, `aura_backend/learning_logs.json`, `routing_centroids.json`, `sentiment_centroids.json`, `local_qdrant_storage/`, `Deployment_info.txt` | — | Hygiene strays from earlier direct pushes (GAP-010) | — | — | — | — | Pass F1 checks for secrets |

Untracked local files at repo root (git-ignored, EXECUTED-HERE `git check-ignore`): `credentials.json`, `aura-ai-89ab4-firebase-adminsdk-*.json`, `aura-engineer-agent.*.private-key.pem`, `google_client_id.txt`, `aura_backend/credentials.json`. None is tracked at HEAD; whether any was ever committed is checked in `security_and_privacy.md`.

## 3. Toolchains available to this audit (EXECUTED-HERE)

| Tool | Version here | CI pin | Note |
| --- | --- | --- | --- |
| Python (backend venv `aura_backend/venv`) | **3.14.4** | 3.11 (Dockerfile) | venv does not match the lock's interpreter; `prometheus_client` missing; one security module fails collection under this fastapi build — see `verification_log.md` |
| pytest | 9.1.1 | — | |
| Flutter / Dart | 3.44.6 / 3.12.2 (`E:\Flutter\flutter`) | 3.44.6 / 3.12.2 | matches CI |
| Node / npm | 24.14.1 / 11.11.0 | — | `aura_admin/node_modules` present |
| Docker | 29.6.2 CLI | — | daemon not used (read-only audit) |
| Disk | **`C:` 100% full** during the audit | — | shell output and temp files unreliable; all logs redirected to `E:\aura_audit_tmp\` |

## 4. Prior audit material used as the verification checklist

| Document | Baseline | Used for |
| --- | --- | --- |
| `docs/reports/AURA_FULL_SYSTEM_REVIEW.md` | `5db6692` (2026-09-25) | SEC-xx, GAP-xx findings; evidence labels |
| `docs/roadmaps/AURA_CRITICAL_BACKLOG.md` | `5db6692` + updates to 2026-10-31 | CB-01…CB-44 |
| `docs/production/gap_register.yaml` | 2026-09-24→ | GAP-001…GAP-064 (statuses: 33 open, 24 built_not_proven, 3 in_progress, 3 closed_stale, 1 proven — EXECUTED-HERE count) |
| `docs/status/{BACKEND,DESKTOP_APP,MOBILE_APP,WEB_CONSOLE}_STATUS.md` | `2337577`…`9bfaa22` (2026-10-28…31) | F-01…F-37 from the 2026-10-26 audit and their "fixed, not CI-run" claims |
| `docs/reports/FULL_SYSTEM_ARCHITECTURE_AUDIT.md`, `desktop-ide-phase0-audit-2026-09-25.md` | `dd88f95`, ~`5097858` | historical |

Each pass's "Previous-finding recheck" section records the per-item verdict; `gap_analysis.md` consolidates them.
