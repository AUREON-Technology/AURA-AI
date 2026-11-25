# Authentication — as-is system map (2026-11-22)

Branch `anas/knowledge-wp4-fetchers`. Read-only discovery. "Verified" means a code path was read.
Items from the three discovery agents that I did not re-read are marked `AGENT`.

## Detected stack and layers

| Layer | Path | Stack | Commands |
|---|---|---|---|
| Backend gateway | `aura_backend/` | Python 3.11, FastAPI, Alembic, Redis, Postgres | `pytest` (default `testpaths = tests`); legacy `TESTING=True python -m unittest discover` |
| Customer app | `aura_client/` | Flutter 3.44.6, Riverpod, Firebase Auth | `flutter analyze`, `flutter test` |
| Operator console | `aura_admin/` | Next.js, Playwright (mock gateway) | `npm run lint`, `npm run test:e2e` |
| Desktop daemon | `aura_hostd/` | Dart | `dart analyze --fatal-infos`, `dart test` |
| Identity provider | Firebase Auth (external) | Google, Apple, email/password, TOTP | owner steps in `docs/deployment/customer-sign-in.md` |

## Endpoints (`aura_backend/api/v2/auth.py`, prefix `/api/v3/auth`)

| Route | Caller | Rate limit | Attestation |
|---|---|---|---|
| `POST /nonce` | anyone | 30/min per address, per worker | no |
| `POST /token` | Google or Firebase token | none | required |
| `POST /web/token` | operator only | none | no |
| `POST /consumer/token` | any verified identity, capped to `User` | 10/min per worker | no |
| `POST /refresh` | refresh token (optional `retry_key`) | none | no |
| `POST /revoke` | refresh token in body | none | no |
| `POST /desktop/pair/start` | anyone | 10/min | no |
| `POST /desktop/pair/approve` | access token with host-agent capability | wrong codes, 5/hour, only when the customer switch is on | no |
| `POST /desktop/pair/poll` | `pairing_id` | none | no |
| `GET /desktop/devices`, `POST /desktop/devices/revoke` | own devices | none | no |
| `POST /desktop/exchange`, `/desktop/login`, `/desktop/handoff`, `/desktop/firebase-exchange` | see backend notes | 10/min | no |
| `POST /api/v3/account/delete` (`api/account.py`) | access token plus `confirm: "DELETE"` | none | no |

## Token model (`services/jwt_auth.py`)

- Access token: HS256, 15 minutes, claims `sub`, `email`, `name`, `picture`, `role`, `iat`, `exp`,
  `email_verified`, `channel`, `iss`, `aud`. No `jti`, no `kid`, one symmetric key. `AGENT`
- Refresh: one atomic `GETDEL` per use; reuse revokes the family; an INT-012 retry with a matching
  `retry_key` inside 5 minutes is forgiven. Every rotation grants a fresh 30 days (no absolute cap).
  Stored as raw Redis keys, not hashed. `AGENT`
- Daemon token: 30 days, scope `host_agent`, no refresh token. `AGENT`
- Redis runs `allkeys-lru` in both compose files (`deploy/docker-compose.prod.yml:1259`). Verified.
  Revocation markers are `setex` keys (`jwt_auth.py:51, 771, 795, 815`). Verified.
- No production secret means the service refuses to boot (`aura_service.py`). `AGENT`

## Role resolution (`services/rbac.py`, verified)

The result is the highest of: the email lists (only for a verified Google identity), `roles.json`,
the `users.role` Access-page assignment, and the token's own `role` claim. Consumer channels
force `User`. `CONSOLE_MIN_ROLE = "Developer"` (`rbac.py:15`); the console login route requires
`is_admin`, which is Admin and above (`api/admin_platform.py:562`).

## Client behaviour (`AGENT` unless marked)

| Platform | Methods in code | Works today? |
|---|---|---|
| Android | Google, email via Firebase, attested `/token`, biometric unlock | Plausible; not device-verified |
| iOS | Google, Apple, email, attested `/token` | No: no `GoogleService-Info.plist` |
| macOS | Google, Apple, email, `/consumer/token` | No: no plist, entitlements lack `network.client` |
| Web | Google and Apple popup, email, `/consumer/token` | No: no Firebase web config |
| Windows, Linux | Google loopback, browser hand-off | Loopback works through the backend; the hand-off needs the web config |

Tokens: refresh token in `flutter_secure_storage`; the access token lives in memory only. Refresh is
on demand with single-flight and a 30-second margin. The console keeps `aura_access` and
`aura_refresh` in HttpOnly, SameSite=Strict cookies and refreshes in middleware and the proxy. The
daemon stores a 30-day token in a plaintext `config.json` (chmod 600) and sends it in a first
WebSocket frame.

## Tests (local evidence, 2026-11-22)

`aura_backend/.venv` (Python 3.11.16, pytest 9.2.0), temp on `E:`:

| Group | Result |
|---|---|
| `tests/security` (19 files) | 502 passed, 0 failed |
| root `test_jwt_auth.py`, `test_desktop_login.py`, `test_device_pairing.py`, `test_role_assignment_integration.py` | 49 passed, 6 skipped (need Postgres or Docker) |
| four unit files: consumer sign-in, web token, console bootstrap, desktop OAuth audience | 29 passed |

Not run: client, console and hostd suites, `test_security.py`, legacy `unittest`, certification and
load suites. The root `test_*.py` files are not collected by a bare `pytest` (`testpaths = tests`).
No test calls the `POST /auth/revoke` route in the default suite (verified by search).
Logs: `E:/aura_audit_tmp/auth_*.log` and `.xml`.

## Not investigated

Live behaviour on `aureon-ai.tech` and the console host; the Firebase project's enabled providers;
Railway's Redis policy; Apple, Google Play and Fawry sign-in coupling beyond what the docs say.
