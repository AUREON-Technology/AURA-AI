# Gap and risk register: security triage 2026-11-26

Status values: GAP, PARTIAL, UNVERIFIABLE. Evidence is from reading code only; nothing was executed.

## P1: release blockers

| ID | Finding | Evidence | Status | Fix and verification |
|---|---|---|---|---|
| SEC-T-01 | No branch protection on `main`; Railway deploys every push with no CI gate (CB-02, CB-44). | `CLAUDE.md` deploy section; backlog CB-02. Settings item, not in repo. | UNVERIFIABLE (docs agree) | Owner enables protection, requires reviewed PR plus CI, and gates Railway on CI. Verify in repo settings. |
| SEC-T-02 | Dev stack mounts prod's `postgres_password`, `redis_password`, `qdrant_api_key` files. | `deploy/docker-compose.dev.yml:225-229` | GAP | Give dev its own credentials; keep prod/dev parity test in `tests/unit/test_compose_security_parity.py`. |
| SEC-T-03 | Production Postgres password exposed in an agent transcript 2026-11-22; rotation not evidenced. | Backlog CB-50 item 6 | UNVERIFIABLE | Owner rotates and records it. |

## P2

| ID | Finding | Evidence | Status |
|---|---|---|---|
| SEC-T-04 | Second authenticator signs/verifies with a public-constant key and trusts claimed role/capabilities. Exported, no callers (latent admin forgery if wired in). | `aura_core/security/authenticator.py:79-176`, `security/__init__.py:11` | GAP (latent). Delete it or make it fail closed. |
| SEC-T-05 | Desktop installer checksum is served from the same origin as the installer; installer proceeds when Authenticode is invalid. | `aura_web/site/desktop-ide/assets/install.js:31-54`; `scripts/AURA-Desktop-Setup.cmd:181-192`; `desktop-main-channel.yml:140-175` | GAP. Fail closed once signing secrets exist. |
| SEC-T-06 | Web build likely stores the refresh token in script-readable browser storage (no `WebOptions`). | `aura_client/lib/core/security/aura_secure_storage.dart:12-16` | UNVERIFIABLE; check the package's web backend. |
| SEC-T-07 | `aura_hostd` control server trusts any same-user process that can read `control.json` (files, roots, approvals). Documented as accepted. On Windows only the `%APPDATA%` ACL protects it. | `aura_hostd/lib/src/control_server.dart:161-194,299`; `config.dart:159-167` | PARTIAL; accepted risk needs an owner and review date. |
| SEC-T-08 | Six prod services run as root (`user: "0:0"`); dev has the same pattern. | `deploy/docker-compose.prod.yml:447,1016` and others | GAP (intentional, test-pinned). |
| SEC-T-09 | Dockerfile base images float; no `HEALTHCHECK`; backup image runs as root. | `aura_backend/Dockerfile:4,52`; `aura_admin/Dockerfile:1,8,33`; `deploy/backup/Dockerfile:4`; `deploy/spaces/inference/Dockerfile:16,18` | GAP |
| SEC-T-10 | No JS or Dart vulnerability scanning, CodeQL, dependency-review or Dependabot. | `.github/workflows/`; no `dependabot*` | GAP |
| SEC-T-11 | VPS deploy blocked by an unexplained SSH host-key change; pin correctly not updated. | `deploy/known_hosts`; `deploy-backend.yml:122,170` | VERIFIED fail-closed; owner must establish cause. |
| SEC-T-12 | Sandbox broker and Ollama dev gate built but never run on Docker (CB-46). | Backlog CB-46 | UNVERIFIABLE |

## P3

| ID | Finding | Evidence |
|---|---|---|
| SEC-T-13 | JWT secret shorter than 32 bytes only warns. | `aura_service.py:195-226` |
| SEC-T-14 | Legacy tokens without `iss`/`aud` accepted until 2026-12-15; remove after that date. | `services/jwt_auth.py:140-151` |
| SEC-T-15 | 30-day paired-daemon token freezes its role; device revocation is checked on every call. | `jwt_auth.py:657-672,1061-1075`; `rbac.py:171-173` |
| SEC-T-16 | Refresh tokens stored raw in Redis; with Redis down, a per-process fallback breaks shared replay detection across workers. | `jwt_auth.py` (`_refresh_store_set`, `:293`) |
| SEC-T-17 | Pairing-approval limit is conditional and per-process; 8-char codes over a 31-char alphabet make brute force impractical. | `api/v2/auth.py` ~640; `device_pairing.py:49,121` |
| SEC-T-18 | `--forwarded-allow-ips` defaults to `*`; per-IP limits depend on Railway adding exactly one hop. | `aura_backend/Dockerfile:191`; `security/rate_limit.py:202-229` |
| SEC-T-19 | `fetch_url` resolves DNS twice (rebinding window); allowlist-gated, redirects off. | `tools/builtin.py` ~805 |
| SEC-T-20 | No WebSocket Origin check or frame-size cap; token is in the first frame, so CSWSH is moot. | `host_agent.py`, `voice_live.py` |
| SEC-T-21 | `/app/` CSP broad on `img-src`, `connect-src`, `style-src 'unsafe-inline'`; script policy tight. | `aura_web/nginx/security-headers-app.conf` |
| SEC-T-22 | Outbox in `SharedPreferences` may hold task text in plaintext at rest; `debugPrint` of exceptions. | `agent_task_outbox.dart`; `auth_provider.dart` |
| SEC-T-23 | Admin cookies `secure` only when `NODE_ENV=production`; proxy forwards all request and response headers. | `aura_admin/src/lib/session.ts:6`; `app/api/v3/[...path]/route.ts:44-48` |
| SEC-T-24 | Step-up flow uses an implicit `id_token`; confirm server-side nonce validation. | `aura_admin/src/lib/control.ts:93-130` |
| SEC-T-25 | A service-account key was committed in history (`5b37323`); confirm revoked. Firebase client key should be restricted in the Google console. | git history; `google-services.json:31` |
| SEC-T-26 | Docs are stale: CB-07 appears fixed, and the CB-08 "backup mounts all secrets" claim is outdated. | `control-plane-deploy.yml:74-81`; `docker-compose.prod.yml:1582` |

## Open decisions (interview, not yet asked)

1. What release gate applies: a customer-facing launch, a private beta, or internal use?
2. Is an external pen test required before launch (the audit's own recommendation)?
3. Accepted-risk owners and review dates for SEC-T-07 (local daemon trust) and SEC-T-08 (root containers).
