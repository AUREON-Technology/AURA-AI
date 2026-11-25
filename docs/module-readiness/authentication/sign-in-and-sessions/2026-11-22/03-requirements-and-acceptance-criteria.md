# Authentication — requirements and acceptance criteria (2026-11-22)

Source column: `D#` is an owner decision in `02-business-interview-and-decisions.md`; `R#` is a
recommendation awaiting approval; `SPEC` is an existing approved document.
Priority: P1 blocks customer launch, P2 required before general availability.

## Business rules

| ID | Requirement | Acceptance criteria | Source | Pri |
|---|---|---|---|---|
| BR-001 | The console MUST admit only Admin and SuperAdmin. `/auth/web/token` MUST refuse a lower role. | A Developer's `/web/token` returns 403 and no pair is issued; the console and the backend use one constant; a test pins it. | D6 | P2 |
| BR-002 | A customer session MUST resolve to platform role `User` at issue, refresh and every request. | Existing `test_consumer_web_sign_in` stays green. | SPEC (customer-sign-in.md) | P1 |

## Security

| ID | Requirement | Acceptance criteria | Source | Pri |
|---|---|---|---|---|
| SEC-001 | An operator session MUST have passed a second factor. An operator with TOTP enrolled MUST be able to sign in to the console. | The console signs in via Firebase with a code step; a raw Google token for an operator is refused with a clear reason; an operator without a second factor cannot get a console session; an enrolled operator can. | D1 | P1 |
| SEC-002 | A session MUST end at an absolute age: 7 days for operators, 90 days for customers, however often it refreshes. | The session start time survives rotation; a refresh after the cap returns 401 with a reason the client turns into the sign-in screen; both limits are configuration. | D3 | P1 |
| SEC-003 | Ending a session MUST hold: revoking a family also revokes the Firebase refresh tokens; the backend MUST verify Firebase tokens with `check_revoked`; a revoked or logged-out access token MUST stop working within the denylist window. | After "sign out everywhere", neither a refresh nor a Firebase re-exchange opens a session; the old access token is refused; tests cover logout, revoke-all and the re-exchange path. | D4 | P1 |
| SEC-004 | Account deletion MUST require a sign-in under the recent-sign-in window (and the TOTP step if enrolled) and MUST delete the Firebase user. | A deletion with a stale sign-in returns a typed error and the app asks to sign in again; after deletion the Firebase uid no longer exists. | D5 | P1 |
| SEC-005 | A role granted by the Access page or an email list MUST bind to a verified Google identity, not to an address typed into an unverified sign-up. | A squatter who registers an operator's address with an unverified email gets no role; the assignment API refuses an unverified identity. | R1 | P1 |
| SEC-006 | Every auth route MUST be rate limited through the shared limiter. | `/token`, `/web/token`, `/refresh` and `/desktop/pair/poll` return 429 beyond the limit across workers; pairing wrong-code limits apply unconditionally. | R3 | P2 |
| SEC-007 | `TESTING=True` MUST NOT enable mock auth or the attestation bypass in a production-like environment. | Boot fails, or the flag is ignored, when `environment_is_production_like()`. | R7 | P2 |
| SEC-008 | Refresh tokens SHOULD be stored hashed. | No raw refresh token appears as a Redis key or Postgres value. | R8 | P2 |

## Data integrity

| ID | Requirement | Acceptance criteria | Source | Pri |
|---|---|---|---|---|
| DATA-001 | Revoked devices, erased accounts and revoked families MUST be durable: Postgres is the source of truth and Redis is a cache. | A revocation survives a Redis flush and an eviction; `alembic upgrade head` and the downgrade round-trip pass on a real Postgres; the check reads Postgres on a cache miss. | D2 | P1 |

## Functional and integration

| ID | Requirement | Acceptance criteria | Source | Pri |
|---|---|---|---|---|
| FR-001 | A signed-in person MUST be able to list their sessions and sign out everywhere, on every platform. | A sessions list appears in the app; the action ends every session of that person; an operator's SuperAdmin revoke does the same. | D4 | P1 |
| INT-001 | The console's refresh MUST send a `retry_key`. | A dropped refresh response followed by a retry does not revoke the family. | R4 | P2 |
| INT-002 | The desktop daemon MUST renew its credential before expiry, and an operator's daemon token MUST NOT outlive the operator cap (7 days); a customer's follows the 90-day cap. | The daemon never hard-stops unannounced; renewal is refused after revoke, downgrade or at the cap; an expiring daemon warns the app first. | R6, D7 | P2 |
| INT-003 | Sign-in MUST audit its events (success, failure, refresh reuse, revoke, device revoke, pairing approve, deletion). | Each event appears in the audit log with actor, time and result. | R2 | P2 |

## User experience

| ID | Requirement | Acceptance criteria | Source | Pri |
|---|---|---|---|---|
| UX-001 | A refused or rate-limited sign-in MUST show its reason and MUST NOT open the app without a session. | A 429 and a 503 on sign-in show retry guidance; release builds show the backend's refusal detail; a screen reader announces the error. | R5 | P2 |

## Environment

| ID | Requirement | Acceptance criteria | Source | Pri |
|---|---|---|---|---|
| OPS-001 | Customer sign-in MUST work on every shipped platform. | `FIREBASE_CREDENTIALS_JSON` set on Railway; iOS and macOS `GoogleService-Info.plist`; a web Firebase config; macOS `network.client`; a real sign-in recorded on each platform. | SPEC (customer-sign-in.md) | P1 |
