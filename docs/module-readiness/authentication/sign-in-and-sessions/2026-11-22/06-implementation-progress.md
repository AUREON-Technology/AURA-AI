# Authentication — implementation progress (2026-11-22, updated as work lands)

Spec: `05-implementation-plan.md`. Branch `anas/auth-batch1`, worktree `E:\AURA-AI-auth-batch1`,
based on remote `main` at `514279aa`. **Nothing is committed yet** (the owner asked to continue
through the gaps first). The reports in this folder are in the main checkout, untracked.

## Status by work package

| WP | State | Evidence |
|---|---|---|
| R7 `TESTING=True` ignored in production | **Done** | 16 new tests |
| WP-5 verified role binding | **Done** | 28 new tests; real-Postgres role tests (18) pass |
| WP-7 shared limits, atomic pairing, wrong-code limit | **Done** (existing per-worker limiters on sibling routes not migrated) | 34 new tests |
| Console `retry_key` (R4) | **Done** | 8 new Playwright unit tests; ESLint not run |
| WP-6 auth audit events | **Done** (pre-verification `/token` failures not audited) | 20 new tests incl. the erased-identity privacy test |
| WP-1 durable revocations | **Done for markers**; device ownership index still Redis-only | migration `0033`; 35 tests on real Postgres 16 incl. the Redis-flush drill |
| WP-2 caps, session control (backend) | **Done** (clients not yet) | migration `0034` round trip on real PG16; 37 new tests; `GET /auth/sessions`, `POST /auth/sessions/{id}/revoke`, `POST /auth/sessions/revoke-all`; `sid` claim; absolute cap anchored on Firebase `auth_time`; logout now ends the access token at once; Firebase `check_revoked` + `revoke_refresh_tokens` |
| WP-4 deletion re-auth (backend) | **Done**, behind `AURA_ACCOUNT_DELETE_REQUIRES_REAUTH` (off) | 40 new tests; fresh-token check (same account, within `AURA_REAUTH_WINDOW_S` default 300, second factor if enrolled), Firebase user deleted after local erasure |
| WP-3 operator MFA (backend) | **Done**, behind `AURA_OPERATOR_MFA_REQUIRED` (off) | 21 new tests; `/web/token` and the desktop sign-in accept an operator only with `firebase.sign_in_second_factor`. The console's Firebase sign-in + enrolment screens are **not built** (need a Firebase project; cannot be verified here) |
| WP-8 daemon renewal | **Done** (server + daemon; the app's expiry warning is WP-9) | server: 31 new tests, `POST /auth/desktop/devices/renew`, operator daemon token 7 days / others 30, role re-resolved from a server-side pairing record; daemon: `token_renewer.dart`, 24 new Dart tests, analyzer clean, `token_renewal` in `/status` |
| WP-10 hashed refresh tokens (R8) | **Done** | `h1:<sha256>` storage identifiers everywhere; legacy plaintext records still read until they expire; 27 new tests plus the whole existing rotation/revocation suite green |
| WP-9 clients (Flutter app, console) | **Done**, see "WP-9" below; console e2e specs written but **not run** (no installable `node_modules` here) | Backend: D6 console floor Admin, 419 related tests green. App: `flutter analyze` clean; targeted tests green (see below) |

## WP-9: what the clients now do

**Backend (D6).** `/auth/web/token` now needs `CONSOLE_WEB_MIN_ROLE = "Admin"` (`services/rbac.py`);
the desktop sign-in keeps `CONSOLE_MIN_ROLE = "Developer"`. Before this a Developer got a console
session the console threw away (INT-042). New `tests/security/test_console_web_role_floor.py`
(each role against each floor, and a Developer refused on the console / admitted on the desktop
gate); `test_console_operator_bootstrap.py` now expects the Admin floor in the refusal text.

**Flutter app.**
* `session_expired` / `session_revoked` (401 from `/refresh` or a silent `/token`) end the session
  for good (`TokenManager._endByServer` → `AuthNotifier.endSessionByServer`): no silent
  re-exchange, signed out of Firebase too, and the login page says why (`loginSessionEndedCap`,
  `loginSessionEndedRevoked`). Without that, per-session "sign out" would be undone by the app
  re-exchanging from the still-signed-in identity.
* Sign-in answered 429 or 503 → `SessionStart.retryLater` → `SignInUnavailable`: the person is not
  let into an app with no session, the previous session is left alone, and the login page says to
  wait a minute. A network failure is still `unreachable` (offline sign-in is unchanged).
* `X-Device-Name` (`core/auth/device_label.dart`, platform only, never a hostname) on every sign-in
  exchange, so the sessions list can tell devices apart.
* Settings → "Where you're signed in" (`/sessions`, also reachable on the desktop app): the list
  with this device marked and each session's cap date, "Sign out" per session, "Sign out
  everywhere" (and a notice when the identity provider could not be told, instead of claiming
  everything is closed). Ending this device's own session signs it out locally.
* Account deletion (D5): the first request carries no token; a 401 `reauth_required` makes the app
  ask the person to sign in again (their usual method, password for email-only accounts, the
  second factor when enrolled) and repeat with the fresh `id_token`. Works whether or not the
  server flag is on. `identity_provider_deleted: false` is reported ("holds no AURA data").
* `token_renewal: repairRequired` from the daemon's `/status` counts as "needs re-pair" in the
  desktop panels (`HostdStatusSnapshot.needsRepair` / `canPair`).

**Console.** The layout now separates "the gateway says the session is not valid" (redirect to
login, with `?reason=expired|revoked` when the gateway names it, and `role` for a 403) from "the
gateway did not answer" (5xx, 429, unreachable): a notice that retries itself, cookies untouched
(F18). Login page and both languages carry the new messages.

**Found by the tests, fixed.** A 23 px overflow of the dialog button row ("Sign out everywhere"
next to "Keep"); a 43 px overflow of "Sign in and delete" at phone width (now "Continue"); the
tile's spinner kept spinning while the confirmation dialog waited for the person.

**Verified.** `flutter analyze`: no issues. Tests: `token_manager_test` 23, `sign_in_session_test`
14, `auth_session_integrity_test` 20 (7 new), `sessions_test` 14 (new), `account_deletion_test` 20
(12 new), `hostd_status_test` + host panel/repair 20, `arabic_locale_test` 7. Console:
`tsc` shows no error in the touched files (the shared `node_modules` lacks `lucide-react` etc., so
the rest of the project cannot be type-checked here); ESLint and the Playwright specs added to
`tests/e2e/session-gate.spec.ts` (outage notice, `session_expired`/`session_revoked` reasons, plain
401) were **not run**.

**Left for later (WP-9).**
* The desktop (Windows/Linux) app has no Firebase identity to confirm with, so with
  `AURA_ACCOUNT_DELETE_REQUIRES_REAUTH` on, deleting an account there says to delete it from the
  phone or the web. Decide before the flag goes on whether desktop needs its own confirmation.
* `token_renewal: failing` is exposed (`HostdStatusSnapshot.renewalFailing`) but no panel warns on
  it yet; a token that cannot be renewed shows as expired only when it does.
* Console: no `session_*` mapping when the *middleware* refresh is refused (it redirects to
  `/login` without a reason); Firebase sign-in/TOTP screens (WP-3 console half) are still unbuilt.

## Interpretation to confirm: how D7 and D3 fit together for daemons

D3 caps an operator's *session* at 7 days and D7 says the same cap covers an operator's daemon
token, "renewing in-band while the device is still approved and entitled". Read literally together,
a daemon that renews forever has no absolute end. I built it that way: **the token lives 7 days
(operator) / 30 (others) and renews at half-life for as long as the device is not revoked, the
account is not erased and the owner is still admitted (re-checked at every renewal)**. There is no
separate absolute lifetime for a paired daemon. If you want one (for example re-pair every 90
days), it is one number checked in the renewal route.

Residual risk: a stolen unexpired daemon token can be renewed by the thief until the device is
revoked. Mitigations in place: renewal is rate limited, audited (`auth.device.renew`) and refused
once the device is revoked or the owner demoted. Revoking the device is the remedy.

## Rollout flags (both off; turn on in this order)

1. Ship the clients that send `id_token` with a deletion and `X-Device-Name` on sign-in, and that
   treat `session_expired` / `session_revoked` as "sign out of Firebase, ask for a fresh sign-in".
2. `AURA_ACCOUNT_DELETE_REQUIRES_REAUTH=true` (an app-store-visible behaviour: do it only once
   the client above is out, or in-app deletion stops working for installs that send no token).
3. Enrol every operator in TOTP (needs the console screens, Identity Platform and the owner's
   steps), *then* `AURA_OPERATOR_MFA_REQUIRED=true`. Before enrolment this locks the console.
4. Tunable without code: `AURA_SESSION_MAX_AGE_OPERATOR_DAYS` (7), `AURA_SESSION_MAX_AGE_CUSTOMER_DAYS`
   (90), `AURA_REAUTH_WINDOW_S` (300).
| WP-A owner steps | Open (owner) | |

## Residual gaps inside "done" packages

* ~~WP-1/WP-2: device ownership index Redis-only~~ **Closed 2026-11-23.** `aura_auth_devices`
  (migration `0035`, `services/auth_devices.py`) records each paired desktop's owner durably;
  pairing, renewal, desktop sign-in and refresh register the device *before* issuing a token (an
  unwritable registry is a retryable 503, never a token for an unknown device); sign out
  everywhere, per-device revoke and the device list read Redis *and* the registry, and a revoke
  puts the ownership record back in Redis; erasure deletes the rows (the revocation markers stay).
  33 tests including the Redis-flush drill on real Postgres 16. A device paired before 0035 is
  backfilled in the registry at its next renewal or refresh; until then it is only as findable as
  before.
* WP-8: the daemon reconnects (`BackendConnection.restart`) each time it renews, about every 3.5
  days for an operator. The socket authenticates once, at connect, so this is how the new token
  reaches it; a task running at that moment loses its connection and is retried by the backend.
  A `reauth` frame would avoid the drop; not built.
* WP-8: the app does not yet warn when `token_renewal` is `failing` or `repairRequired` (WP-9).
* WP-10: the legacy plaintext lookup in `jwt_auth._lookup_ids` can be deleted 30 days after this
  ships. `refresh_used:` markers are hashed now but still Redis-only (their loss reads a replay as
  "invalid", not as a compromise).
* WP-2: revoking a single session cannot stop a device that is still signed in to Firebase from
  starting a new one; only "sign out everywhere" (which revokes the Firebase refresh tokens) does.
  The sessions screen should say so.
* WP-2: the clients (WP-9) do not yet show the sessions list, and must treat 401 `session_expired`
  / `session_revoked` as "sign out of Firebase and ask for a fresh sign-in", or the silent
  re-exchange loops on the refusal.
* WP-2: `X-Device-Name` is a new, optional request header the clients should send so the list can
  name a device.
* WP-1: refresh-family replay markers (`refresh_used:`) are still Redis-only; their loss means a
  replay of a rotated token is read as "invalid" instead of revoking the family.
* WP-6: a bad Google token or failed attestation on `/auth/token` fails inside a dependency and is
  not audited.
* The migration number `0033` may need renumbering if `main` gains one before merge.

## Local test infrastructure (clean up at the end)

* A throwaway PostgreSQL 16.9 (EnterpriseDB binaries, 314,500,604 bytes, downloaded with the
  owner's permission) is unpacked in `E:\aura_audit_tmp\pg16`, data in `E:\aura_audit_tmp\pgdata`,
  listening on `127.0.0.1:54329` only. Tests use
  `AURA_TEST_DATABASE_URL=postgresql://postgres@127.0.0.1:54329/postgres`. Stop it with
  `pg_ctl -D E:\aura_audit_tmp\pgdata stop`, then delete both folders.
* Run the backend suites with `aura_backend\.venv` from the worktree's `aura_backend` directory,
  `TMP`/`TEMP` on `E:`. `unittest discover` cannot run from the worktree (the shared venv has the
  main checkout on its path): run the legacy modules by name with `PYTHONPATH` set.
