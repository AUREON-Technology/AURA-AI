# Security: whole-platform readiness triage (DISCOVER)

- **Date:** 2026-11-26 · **Branch:** `claude/module-readiness-security-7wzy08` · **HEAD:** `6756fbd6`
- **Mode:** DISCOVER (read-only; no application code changed)
- **Depth:** triage across `aura_backend`, deploy/supply chain, `aura_client`, `aura_web`, `aura_admin`, `aura_hostd`. Scope chosen by the user (option 4: whole-platform sweep).
- **Method:** targeted code reads and greps by three parallel investigations, validated against `CLAUDE.md`, `docs/reports/security-audit.md` and `docs/roadmaps/AURA_CRITICAL_BACKLOG.md`.

## Readiness decision: NO-SHIP for a "production-ready" claim

Reason: unresolved P1 items, and no executable evidence was produced in this run.

- **P1-1 (CB-02):** `main` has no branch protection and Railway auto-deploys every push with no CI gate. Taken from docs; cannot be checked from the repo.
- **P1-2 (CB-08 remainder):** the dev stack mounts prod's postgres, redis and qdrant credential files (`deploy/docker-compose.dev.yml:225-229`). Verified in code.
- **P1-3 (CB-50 item 6, from the backlog, not re-verified):** the production Postgres password was printed into an agent transcript on 2026-11-22. Whether it has been rotated is UNVERIFIABLE from here.

Not a code-quality verdict: the backend request path is notably hardened (see below). The blockers are release-process and credential hygiene, plus missing evidence.

## What was verified (by reading code)

Backend: roles from verified claims, capabilities fail closed; JWT algorithm/iss/aud pinned; atomic refresh rotation with replay revoking the family; service keys deny by default; admin routes all carry server-side auth (AST scan); per-request workspace and tenant checks; IDOR ownership on export, approvals, agent runs; single-use approval binding; taint blocking side-effecting tools; parameterised SQL; hardened sandbox flags; security headers and CORS. Deploy: all GitHub Actions pinned to SHAs, no `pull_request_target`, SSH host key pinned and fail-closed, nginx TLS/headers sound, hash-pinned Python lockfiles, `pip-audit` in CI. Admin: no HTML-injection sinks, CSRF floor, no SSRF in the proxy.

## What was NOT verified

- **No tests were run.** No venv, Python 3.13 on host vs 3.11 locked, no Flutter/Dart SDK. The 20 architecture tests and `tests/security` have no result from this run.
- No pen test, no live-host probing, no CSP smoke test, no device checks.
- Not read: Firebase/Google exchange beyond audience checks, `voice_live.py` in depth, `SharingService`, `api/v2/sync` encryption, Flutter attestation, deep-link/OAuth handlers, client TLS pinning, `aura_hostd` containment and command policy.
- Real-Qdrant isolation probe (CB-40), Railway proxy hop count, and live deploy state (CB-50) are runtime facts.

Details: `04-gap-and-risk-register.md`.
