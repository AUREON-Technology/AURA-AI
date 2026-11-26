# Implementation plan: security, private beta gate

Gate: private beta (decision D-02). Files and layers are estimates until verified during implementation. Nothing here is approved for implementation; this is a plan only (DISCOVER mode).

## 1. Release blockers for the beta (P1)

| WP | Scope | Findings | Owner action or change | Verification |
|---|---|---|---|---|
| WP-1 | Branch protection on `main`: require a reviewed PR and the CI checks that run; stop Railway auto-deploying unprotected pushes (gate on CI or switch to deploy-on-tag). | SEC-T-01 | Repository and Railway settings; owner only. | Settings screenshot or API read; a test PR is blocked without review. |
| WP-2 | Rotate the production Postgres password and record the rotation. | SEC-T-03 | Owner, then Railway variable update and redeploy. | `/health/ready` healthy afterwards; old credential refused. |
| WP-3 | Give dev its own Postgres, Redis and Qdrant credentials; extend `tests/unit/test_compose_security_parity.py` to fail if dev mounts a prod secret file. | SEC-T-02 | `deploy/docker-compose.dev.yml`, `deploy/scripts/bootstrap-secrets.sh`, the test. | The parity test passes; the dev stack boots with its own secrets (needs the VPS). |

Sequencing: WP-1 first, so WP-3 reaches `main` through a gated path. WP-2 is independent and can run in parallel.

## 2. Required before beta expansion (P2)

| WP | Scope | Findings | Notes |
|---|---|---|---|
| WP-4 | Delete `aura_core/security/authenticator.py` (and its export) or make it fail closed; add an architecture test that nothing imports a forgeable key path. | SEC-T-04 | Zero callers, so low risk. Run `pytest tests/architecture`. |
| WP-5 | Confirm the web refresh-token storage backend. If script-readable, move to a short-lived in-memory token with a cookie-based refresh, or accept the risk with a stated reason. | SEC-T-06 | Check the `flutter_secure_storage` web backend first; verify with `node scripts/web-app-smoke.js`. |
| WP-6 | Installer: make `AURA-Desktop-Setup.cmd` fail closed on an invalid signature once signing secrets exist; publish the signer subject. | SEC-T-05 | Depends on obtaining the signing secrets. |
| WP-7 | Add dependency scanning: `npm audit` for `aura_admin`, a Dart/pub audit, CodeQL, dependency-review and Dependabot. | SEC-T-10 | Workflow changes; verify on a PR. |
| WP-8 | Pin Dockerfile base images by digest; add `HEALTHCHECK`; drop root in the backup image. | SEC-T-09 | Verify with the `docker-build` CI job. |
| WP-9 | Establish the cause of the VPS host-key change before any `known_hosts` edit. | SEC-T-11 | Owner only; do not update the pin to force a pass. |
| WP-10 | Run `deploy/scripts/verify-engineer-workspaces.sh` and commit the report; start the sandbox profile. | SEC-T-12 | Needs a working VPS deploy (WP-9). |
| WP-11 | Record owner and review date for accepted risks SEC-T-07 and SEC-T-08, or migrate the root services to non-root. | SEC-T-07, SEC-T-08 | Decision OD-01. |

## 3. Evidence still needed (not defects, just unproven)

- A real run of `pytest tests/architecture tests/security` and the legacy `unittest` suite on the locked Python 3.11 environment. Needs a PR touching `aura_backend/` so CI proves it, or a local venv.
- The cross-identity isolation probe against a real Qdrant server (CB-40).
- A CSP smoke test of `/app/` and a Flutter analyze/test run.
- Reading the areas this triage skipped: Firebase/Google exchange, `voice_live.py`, `SharingService`, sync encryption, client attestation, daemon containment and command policy.

## 4. After the beta starts (P3 and follow-ups)

Close the stale docs rows (CB-07, CB-08, SEC-T-26); remove the legacy-token window after 2026-12-15 (SEC-T-14); hash refresh tokens in Redis and fix the multi-worker fallback (SEC-T-16); add WebSocket Origin and frame caps (SEC-T-20); tighten the admin proxy header handling and cookie flag (SEC-T-23); confirm the old service-account key is revoked and the Firebase key is restricted (SEC-T-25). An external penetration test, before public launch (OD-03).
