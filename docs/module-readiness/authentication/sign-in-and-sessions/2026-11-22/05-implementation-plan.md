# Authentication — implementation plan (2026-11-22)

Mode: PLAN. No application code has been changed. Implementation needs a separate explicit go-ahead
(FULL or IMPLEMENT). Inputs: decisions D1 to D6 and approved recommendations R1 to R8
(`02-business-interview-and-decisions.md`), requirements in `03-...`, findings in `04-...`.

Sizes are relative (S, M, L), not time estimates; nothing here is measured.
Numbers still defaulted, owner may change (they are configuration, not code):
recent-sign-in window 5 minutes (D5), access-token denylist 15 minutes (D4).

## Design findings that shape the plan

1. **Durable state is the foundation.** `services/jwt_auth.py` is synchronous Redis code, called
   from about eight sites in `api/v2/auth.py` and `api/admin_platform.py`. D2, D3 and D4 all need a
   session record, so one Postgres store (WP-1) carries all three.
2. **The session cap must anchor on Firebase's `auth_time`.** After a refused refresh the apps
   silently re-exchange from the still-signed-in Firebase identity (`token_manager.dart:275`), which
   would restart a 90-day clock forever. Firebase ID tokens carry `auth_time` (the last interactive
   sign-in) and it does not move on token refresh. The cap is therefore
   `auth_time + cap` for Firebase sign-ins, enforced on the exchange as well as on refresh. A raw
   Google token has no `auth_time`, so its cap anchors on the session record's start time.
3. **The console floor needs its own constant.** `CONSOLE_MIN_ROLE = "Developer"` also gates desktop
   sign-in (`auth.py:561`) and the operators list (`admin_platform.py:189`). D6 concerns only the
   console, so WP-9 adds a separate `CONSOLE_WEB_MIN_ROLE = "Admin"` and leaves the desktop floor.
4. **Operator MFA needs an enrolment path before enforcement.** Enforcing first would lock every
   operator out. WP-3 ships behind `AURA_OPERATOR_MFA_REQUIRED` (default off) and flips it after the
   operators have enrolled.
5. **Decision D7** (owner, 2026-11-22): the 7-day operator cap also applies to an operator's daemon
   token, which renews in-band (WP-8). Customers' daemon tokens follow the 90-day cap.

## Work packages

### WP-A — Owner steps and platform configuration (OPS-001, F3). Size S code, M owner effort

Parallel with everything; blocks launch, not development.
- Owner: `FIREBASE_CREDENTIALS_JSON` on Railway; enable Google, Apple and Email/Password; Identity
  Platform with TOTP (also needed for D1); Apple Services ID; the console's Google origin;
  import existing users (`scripts/import_users_to_firebase.py`). Steps are in
  `docs/deployment/customer-sign-in.md`.
- Code, after the owner supplies the config: web Firebase options, iOS and macOS
  `GoogleService-Info.plist`, macOS `network.client` entitlement.
- Done when: a real sign-in on Android, iOS, macOS, web, Windows and Linux is recorded in
  `docs/reports/` with the date, build and result.

### WP-1 — Durable auth state and session registry (D2, F1). Size L. Blocks WP-2, WP-4, WP-6

- Migration `0033` (head is `0032`): `auth_sessions` (session id, subject, channel, device id,
  started at, absolute expiry, last refreshed, revoked at, reason) and `auth_revocations`
  (kind: device, account_erased, account_reinstated; subject; effective at; expires at). Working
  `downgrade()` that restores `0032` exactly.
- New async store in `services/` with a real in-memory adapter for tests. Writes go through to
  Postgres and to Redis. Reads use Redis first, then Postgres on a miss, with a short negative cache
  (about 10 seconds) so a Redis miss costs one query per subject per interval, not per request.
- Keep today's failure semantics: issuance fails closed, per-request checks fail open on a Redis
  blip but now read Postgres, and `AuthStateUnavailable` still answers a retryable 503.
- Access and refresh tokens carry a session id (`sid`). A token without one is accepted until the
  existing legacy cutoff (2026-12-15), then refused.
- Update the eight call sites. A retention job purges rows past the longest token lifetime.
- Tests: real PG16 contract; migration round trip; "flush Redis, revocation still enforced";
  "evict the marker, revoked device still refused"; fail-open and fail-closed behaviour pinned;
  load check of the negative cache against the capacity target (GAP-011).
- Done when: the `postgres` CI job and a local PG16 run pass, and a flush drill on a staging stack is
  recorded in `docs/reports/`.

### WP-2 — Session caps and session control, backend (D3, D4; F5, F6). Size L. Needs WP-1

- Absolute cap by audience (config: 7 days operators, 90 days customers), anchored per finding 2.
  Past the cap: 401 with a typed `session_expired` reason, on refresh and on exchange.
- `GET /auth/sessions`, `POST /auth/sessions/{id}/revoke`, `POST /auth/sessions/revoke-all`. A
  SuperAdmin revoke uses the same code. `/auth/revoke` also revokes the session.
- Revoking calls Firebase `revoke_refresh_tokens`; Firebase verification uses `check_revoked=True`;
  a revoked `sid` enters a denylist for the access-token lifetime (15 minutes).
- Tests: logout, revoke-all, the Firebase re-exchange after revoke-all refused, an old access token
  refused inside the window, cap at the exact boundary, and the first default-suite test of the
  `/auth/revoke` route.
- Done when: those tests pass and nothing in `tests/security` regresses.

### WP-3 — Operator second factor (D1; F4). Size L. Needs owner prerequisite (Identity Platform)

- Backend: `/auth/web/token` accepts an operator only from a Firebase token whose
  `firebase.sign_in_second_factor` claim shows TOTP; a raw Google token for the console is refused
  with a clear reason. Gated by `AURA_OPERATOR_MFA_REQUIRED`.
- Console: Firebase sign-in with a TOTP step in `aura_admin/app/login`, and an enrolment page
  (requires a verified email and a recent sign-in). Keeps the cookie model.
- Rollout: enrol every operator, then flip the flag. Break-glass: an operator removes a lost factor in
  the Firebase console, and the removal is recorded (as in the customer runbook).
- Tests: Playwright against the mock gateway gains an MFA step; backend tests for the claim check,
  the refused raw token and the flag off/on.
- Done when: an enrolled operator signs in on a staging console, and an unenrolled one is refused
  once the flag is on.

### WP-4 — Account deletion re-authentication (D5; F7). Size M. Needs WP-1

- The client sends a fresh Firebase ID token with the deletion request. The server checks the uid,
  `auth_time` within the window (default 5 minutes) and, if enrolled, the second-factor claim, then
  erases locally (durable erasure from WP-1) and deletes the Firebase user. If the Firebase deletion
  fails, record it and retry; never report success silently.
- Client: re-sign-in dialog (including the TOTP step) before deletion, in English and Arabic.
- Tests: stale sign-in refused with a typed error; sole team owner still refused; the Firebase user
  gone afterwards (fake Admin SDK); a Firebase failure queues a retry.

### WP-5 — Verified role binding (R1; F2). Size S to M. Independent, do early

- At Access-page assignment, resolve the target through the identity provider and refuse unless the
  account has a verified Google identity; bind the role to that subject. Email-list grants keep their
  existing verified-Google rule.
- Tests: the squatter scenario (unverified registration of an operator's address gets nothing) and
  the assignment refusal.

### WP-6 — Auth audit events (R2; F8). Size M. Soft dependency on WP-1 for session ids

- Events: sign-in success and failure, refresh reuse, revoke, device revoke, pairing approve,
  deletion. Use the existing audit adapter that `admin_platform.py` already writes through.
  Subjects are pseudonymised under the existing audit-retention rule; no tokens or raw emails in
  the event body.
- Tests: each event appears once with actor, time and result.

### WP-7 — Shared rate limits and atomic pairing (R3; F9). Size M. Independent

- Put `/token`, `/web/token`, `/refresh` and `/desktop/pair/poll` behind the shared limiter, and move
  the existing per-worker dictionaries onto it. Make the pairing wrong-code limit unconditional and
  make `approve_pairing` atomic (`device_pairing.py:170-184`).
- Tests: limits hold across two simulated workers; the trusted-proxy address is used; the approve
  race is closed.

### WP-8 — Daemon credential renewal (R6, D7; F12). Size M. Needs WP-1 and WP-2

- Token lifetime follows D7: at most 7 days for an operator's daemon, 90 days for a customer's
  (today a fixed 30 days). The existing 60-second socket recheck stays.
- A renewal call authenticated by the still-valid device token returns a new token. Refused if the
  device is revoked, the account is no longer entitled, or the absolute cap is reached. The daemon
  renews before expiry (it also sends the `reauth` frame the server already supports) and the app
  shows a warning if it cannot.
- Operators will have to re-pair once at cutover, because their existing 30-day tokens exceed the cap.
- Tests: renewal succeeds while approved; refused after revoke, after a downgrade and at the cap;
  `dart test` for the daemon, including the renewal timer and the warning.

### WP-9 — Console and client fixes (R4, R5, D6, D4 client, D5 client). Size M to L. Needs WP-2

- Console: send `retry_key` on refresh (F10); `CONSOLE_WEB_MIN_ROLE = "Admin"` in the backend and a
  test pinning both layers (F13); the outage policy in the layout (F18).
- App: a sessions screen with "sign out everywhere"; a `session_expired` reason that also signs the
  person out of Firebase; a 429 or 503 on sign-in shows its reason and never opens the app without a
  session; release builds show the refusal detail; a screen-reader live region for sign-in errors;
  loopback page escaping; strings in `app_en.arb` and `app_ar.arb`.
- Tests: `flutter analyze` clean, `flutter test` with counts quoted, console `npm run lint` and
  `npm run test:e2e`, `dart test` if hostd changes.

### WP-10 — Hardening (R7, R8; F14, F15, F16). Size M

- R7: ignore or refuse `TESTING=True` when `environment_is_production_like()`; check that CI and the
  legacy `unittest` run do not depend on it in such an environment.
- R8: store refresh tokens hashed, with a dual-read window so live sessions are not signed out.
- F16 (key rotation with `kid`, removing the legacy window on 2026-12-15) follows launch.

## Sequence and dependencies

1. **Start now, in parallel:** WP-A (owner), WP-5, WP-7, the R7 part of WP-10, and the `retry_key`
   part of WP-9. They are small, independent and close real P1 and P2 gaps.
2. **Then WP-1.** Everything durable depends on it.
3. **Then WP-2, WP-4 and WP-6** (they can overlap once WP-1 is merged).
4. **Then WP-3, WP-8 and the rest of WP-9.** WP-3 waits on the owner's Identity Platform step.
5. **Release gate** after all of the above.

Deploy backend first. Every backend change is additive: the `sid` claim is optional until the legacy
cutoff, old apps keep calling `/revoke` and `/refresh`, and an old app that is refused with
`session_expired` ends up signed out, not looping. Clients and the console ship afterwards.

## Verification plan and release gate

Local gates for every package: `pytest` (default suite, `tests/security`, `tests/architecture`),
the legacy `TESTING=True python -m unittest discover`, a real PG16 run of
`tests/load/test_migration_roundtrip_postgres.py`, plus the client, console and hostd suites for the
packages that touch them. Say which ran and quote counts; CI is currently unreliable (CB-44).

Evidence still needed before the verdict can change from NO-SHIP:
- the Redis flush drill (WP-1) and a revoke-everywhere drill (WP-2) on a staging stack;
- a live sign-in per platform (WP-A);
- an operator MFA sign-in on the live console host (WP-3);
- the six skipped real-Postgres role tests running against a real Postgres;
- the client, console and hostd suites, and the legacy `unittest` run (none of them ran in this
  discovery).

## Risks

- WP-1 changes the hot path of every request; the negative-cache design needs the load check.
- Forcing re-sign-in at the caps and at the legacy cutoff will sign some people out once; announce it.
- Turning on operator MFA before every operator has enrolled locks the console. The flag and the
  break-glass step exist for this reason.
- Firebase `check_revoked` and `delete_user` add provider calls and a dependency on the provider
  being up; failures must be retryable and visible, not silent.
