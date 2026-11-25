# Authentication — executive summary (2026-11-22)

**Mode:** DISCOVER (read-only; no application code changed). Branch `anas/knowledge-wp4-fetchers`.
**Scope:** customer sign-in, the operator console, desktop pairing and the sessions behind them
(backend, Flutter app, Next.js console, Dart daemon). Assumed target, from the SaaS Phase 1 spec:
paying customers plus operators. Not tested live.

## Readiness decision: NO-SHIP for customer launch

Several P1 gaps are open (register F1 to F7), and nothing was verified against the live deployment.
The identity design itself is sound: Firebase-verified identity, short-lived access tokens, rotating
refresh tokens with reuse detection, and server-side role resolution with a customer cap. Local
evidence is good: 580 auth tests passed with no failures (502 + 49 + 29), with 6 skipped for want of Postgres.

## Main risks

1. **Revocation can vanish.** It lives in Redis with `allkeys-lru` eviction (F1).
2. **Operators have no second factor, and one who enrols TOTP is locked out** (F4).
3. **Sessions never end and cannot be ended by the customer** (F5, F6).
4. **A role can be captured by registering an operator's address** once Firebase email/password is on (F2).
5. **Customer sign-in does not work on iOS, macOS or web** until the owner steps are done (F3).
6. Account deletion needs no recent sign-in (F7).

## Owner decisions this session

D1 operator MFA required in code; D2 Postgres source of truth for revocation; D3 session caps (7-day
operators, 90-day customers); D4 full session control; D5 recent sign-in for deletion; D6 console is
Admin and above; D7 the operator cap also covers daemon tokens. Recommendations R1 to R8 approved.
Details in `02-business-interview-and-decisions.md`.

## Reports

- `01-as-is-system-map.md`
- `02-business-interview-and-decisions.md`
- `03-requirements-and-acceptance-criteria.md`
- `04-gap-and-risk-register.md` (with traceability and remediation order)
- `05-implementation-plan.md` (ten work packages, sequence, verification and release gate)

## Not verified

Client, console and hostd suites, `test_security.py`, legacy `unittest`, certification and load suites
were not run. The six role-assignment tests that need Postgres were skipped. No live probe was made.
Findings marked `AGENT` in the register were read by a discovery agent and not re-read by me.

## Next

The plan is written. Implementation needs an explicit go-ahead (FULL or IMPLEMENT), and the owner
still sets two numbers (defaults: 5-minute recent sign-in, 15-minute denylist). Suggested first
batch, all small and independent: WP-5 (verified role binding), WP-7 (shared rate limits), the R7
part of WP-10, and the console `retry_key` fix, alongside the owner steps in WP-A.
