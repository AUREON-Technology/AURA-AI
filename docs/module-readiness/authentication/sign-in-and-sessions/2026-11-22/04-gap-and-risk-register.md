# Authentication — gap and risk register (2026-11-22)

Severity: P1 blocks customer launch; P2 before general availability; P3 low.
Evidence status: `VERIFIED` (I read the path), `AGENT` (a discovery agent read it and I did not
re-read it), `SUSPECTED`.

| ID | Sev | Requirement | Finding | Evidence | Fix | Status |
|---|---|---|---|---|---|---|
| F1 | P1 | DATA-001 | Revocation, erasure and refresh-family state lives in Redis with `allkeys-lru`; eviction can re-enable a revoked device or erased account. Not recorded in any doc. | `deploy/docker-compose.prod.yml:1259`; `jwt_auth.py:51,771,795,815` | Postgres source of truth + cache (D2) | VERIFIED |
| F2 | P1 once Firebase email/password is on | SEC-005 | An Access-page role skips the verified-email check, so an address squatter can capture an operator role. | `rbac.get_user_role` (146-174); `user_repo.assigned_role`, `get_or_create_user` | Bind roles to a verified Google subject (R1) | VERIFIED (code); scenario conditional |
| F3 | P1 for customers | OPS-001 | No Firebase config on iOS, macOS or web; macOS lacks `network.client`; `FIREBASE_CREDENTIALS_JSON` unset on Railway per docs. | `customer-sign-in.md`; `MOBILE_APP_STATUS.md:165` | Owner steps; record a sign-in per platform | AGENT + docs |
| F4 | P1 | SEC-001 | An operator with TOTP is locked out of the console, and no operator second factor exists. | `services/security.py:399-407`; `login/route.ts` | Console via Firebase + TOTP; backend gate (D1) | AGENT |
| F5 | P1 | SEC-002 | No absolute session lifetime; every rotation grants 30 days. | `jwt_auth.py:533` | 7-day / 90-day cap (D3) | AGENT |
| F6 | P1 | SEC-003, FR-001 | Logout leaves the access token valid 15 minutes; no list or logout-everywhere on phone/web; the app re-mints a revoked session from Firebase; no `check_revoked`. | `token_manager.dart:275`; `security.py:424` | Full session control (D4) | AGENT |
| F7 | P1 | SEC-004 | Deletion needs only a session and `confirm: "DELETE"`; the Firebase user probably survives. | `api/account.py:42` | Recent sign-in; delete Firebase user (D5) | AGENT; Firebase part SUSPECTED |
| F8 | P2 | INT-003 | No audit events for sign-in, refresh reuse, revoke, device revoke, pairing approve or self-service deletion. | backend notes | Add events (R2) | AGENT |
| F9 | P2 | SEC-006 | Rate limits are per-worker in-process; `/token`, `/web/token`, `/refresh`, `/pair/poll` have none; pairing approve is unlimited unless the customer switch is on. | `api/v2/auth.py:172-294,622` | Shared limiter (R3) | AGENT |
| F10 | P2 | INT-001 | Console refresh sends no `retry_key`; a lost response makes the next refresh a replay that revokes the family. | `aura_admin/src/lib/refresh.ts:104-112` vs `auth.py:74-79` | Send `retry_key` (R4) | VERIFIED |
| F11 | P2 | UX-001 | A 429 or 503 on sign-in can open the app with no session; the refusal detail shows only in debug builds. | `auth_provider.dart:353-359`; `login_page.dart:462` | Surface the reason (R5) | VERIFIED (design comment); 429 path AGENT |
| F12 | P2 | INT-002 | The daemon token expires after 30 days with no renewal and no warning. | `pairing_client.dart`; `host_agent.py:190` | Renewal path (R6) | AGENT |
| F13 | P2 | BR-001 | Console gate disagrees: backend Developer+, console Admin+. | `rbac.py:15`; `admin_platform.py:562` | Raise floor to Admin (D6) | VERIFIED |
| F14 | P2 | SEC-007 | `TESTING=True` enables mock auth and the attestation bypass in any environment. | `config.py:61` | Refuse in production (R7) | VERIFIED |
| F15 | P2 | SEC-008 | Refresh tokens are raw Redis keys. | `jwt_auth.py:279-287` | Hash them (R8) | AGENT |
| F16 | P3 | — | No key rotation or `kid`; legacy tokens without `iss` or `aud` accepted until 2026-12-15; synchronous Redis calls and `scan_iter` in async handlers. | `jwt_auth.py:92,602,740,1019` | Rotate keys; remove the legacy window on schedule | AGENT |
| F17 | P3 | — | Loopback page writes the `error` query unescaped; the hostd `auth set-token` takes the token on the command line; no HTTP timeouts in pairing; sign-in errors are not announced to screen readers. | `desktop_oauth_client_io.dart:290-296`; `aura_hostd/bin/aura_hostd.dart:60-62` | Escape, prompt, add timeouts, add live regions | AGENT |
| F18 | P3 | — | The console layout turns a 5xx from `/admin/me` into a login redirect. | `aura_admin/.../layout.tsx` | Align with the middleware's outage policy | SUSPECTED |

## Traceability

| Requirement | Status | Evidence |
|---|---|---|
| BR-001 | GAP | F13 |
| BR-002 | PASS (local) | `test_consumer_web_sign_in` among 29 passing unit tests |
| SEC-001 | GAP | F4 |
| SEC-002 | GAP | F5 |
| SEC-003 | GAP | F6; `/auth/revoke` route untested in the default suite |
| SEC-004 | GAP | F7 |
| SEC-005 | GAP | F2 |
| SEC-006 | PARTIAL | `/consumer/token`, `/nonce`, `/desktop/*` limited; F9 |
| SEC-007 | GAP | F14 |
| SEC-008 | GAP | F15 |
| DATA-001 | GAP | F1 |
| FR-001 | GAP | F6 |
| INT-001 | GAP | F10 |
| INT-002 | GAP | F12 |
| INT-003 | GAP | F8 |
| UX-001 | GAP | F11 |
| OPS-001 | UNVERIFIABLE | no live probe; F3 |

## Remediation order

1. **Release blockers (P1):** D2 durable revocation; D1 operator MFA; D3 session caps; D4 session
   control; D5 deletion re-auth; R1 verified role binding; OPS-001 owner steps and a recorded
   sign-in per platform. The Postgres migration (D2) comes first because D3 and D4 reuse its store.
2. **Before general availability (P2):** R2, R3, R4, R5, R6, R7, R8, BR-001.
3. **After launch (P3):** F16 to F18.
