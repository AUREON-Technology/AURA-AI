# Authentication — business interview and decisions (2026-11-22)

Mode: DISCOVER. Source of every `USER-CONFIRMED` row: the owner's answers in the 2026-11-22
session. Nothing here changes code.

## Decisions confirmed by the owner

| ID | Question | Confirmed answer | Affects |
|---|---|---|---|
| D1 | How should operator (Admin/SuperAdmin) second-factor protection work? | **Required in code.** The console signs in through Firebase and asks for the TOTP code; the backend refuses an operator session that did not pass a second step. | SEC-001, `/auth/web/token`, `aura_admin/app/login`, `services/security.py` |
| D2 | Where does revocation state live? | **Postgres is the source of truth** for revocations and erasures; Redis stays a fast cache. New Alembic migration with a working `downgrade()`. | DATA-001, `services/jwt_auth.py`, `alembic/versions` |
| D3 | Maximum session age? | **Absolute cap by audience:** operators 7 days, customers 90 days, regardless of activity. | SEC-002, `issue_token_pair`, refresh path |
| D4 | What does phase 1 deliver for ending sessions? | **Full session control:** a sessions list and "sign out everywhere" on every platform; revocation also revokes the Firebase refresh tokens; the backend verifies with `check_revoked` and keeps a short access-token denylist. | FR-001, SEC-003, client auth screens |
| D5 | Account deletion re-authentication? | **Require a recent sign-in** (and the TOTP step if enrolled); the server also deletes the Firebase user. | SEC-004, `api/account.py` |
| D6 | Who may use the console? | **Admin and above.** The backend's `/web/token` floor is raised to Admin so both layers agree. The desktop sign-in floor is not changed (separate constant). | BR-001, `rbac.py`, tests |
| D7 | Does the operator 7-day cap apply to a daemon token? (asked during planning) | **Yes: cap and renew.** An operator's daemon token lives at most 7 days and renews in-band while the device is approved and entitled; customers' daemon tokens follow the 90-day cap. | INT-002, WP-8 |

Exact numbers still to be set by the owner: the "recent sign-in" window for D5 (recommended 5 minutes)
and the denylist lifetime for D4 (recommended: the access-token TTL, 15 minutes).

## Recommendations (R1 to R8 approved by the owner on 2026-11-22: "Approve, plan it")

These are reversible technical fixes with one sensible answer. The owner approved them for planning;
implementation still needs its own go-ahead.

| ID | Recommendation |
|---|---|
| R1 | An Access-page role assignment, and any email-list grant, must require a verified Google identity at assignment time and bind to that identity's subject (finding F2). |
| R2 | Record audit events for sign-in success and failure, refresh reuse, `/revoke`, device revoke, pairing approve and self-service deletion. |
| R3 | Put every auth route behind the shared limiter (not per-worker dicts), add limits to `/token`, `/web/token`, `/refresh` and `/desktop/pair/poll`, and make the pairing wrong-code limit unconditional. |
| R4 | The console's refresh sends a `retry_key`, as the backend's lost-response grace expects. |
| R5 | A 429 or 503 on sign-in shows its reason and never opens the app without a session; release builds show the refusal detail. |
| R6 | Give the desktop daemon a renewal path before its 30-day token ends (it never sends the `reauth` frame). |
| R7 | Refuse `TESTING=True` in a production-like environment. |
| R8 | Store refresh tokens hashed rather than as raw Redis keys. |

## Open or deferred

| ID | Item | Status | Blocks |
|---|---|---|---|
| O1 | Which Firebase sign-in methods are enabled on the live project | `UNKNOWN` (no live probe) | Release |
| O2 | Whether Railway's Redis evicts keys | `UNKNOWN`; moot once D2 is built | Nothing after D2 |
| O3 | Whether sign-in works live after the 2026-10-30 migration | `UNKNOWN` (already listed in BACKEND_STATUS "Unverified") | Release |
| O4 | Phone/SMS sign-in | `DEFERRED` to phase 1b by the SaaS spec | Nothing |
