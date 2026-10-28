# AURA system status

**As of 2026-10-28, `main` at `dc87beb`.** These files record what is true now: what runs where,
what CI says, what is proven, and what is next. They are snapshots, not a roadmap. When they
disagree with the code, the CI history or a live probe, those win, and the file should be
corrected in the same change.

| Component | File | One line |
| --- | --- | --- |
| Backend (`aura_backend/`, `deploy/`) | [BACKEND_STATUS.md](BACKEND_STATUS.md) | Live on Railway at `main`; the VPS deploy is blocked; CI green |
| Desktop app (`aura_client` desktop + `aura_hostd`) | [DESKTOP_APP_STATUS.md](DESKTOP_APP_STATUS.md) | Linux builds in CI; Windows and hostd CI red; nothing packaged |
| Mobile app (`aura_client` Android/iOS) | [MOBILE_APP_STATUS.md](MOBILE_APP_STATUS.md) | Analyze and 381 tests green; Android CI build out of disk; no iOS pipeline |
| Web console (`aura_admin/`) | [WEB_CONSOLE_STATUS.md](WEB_CONSOLE_STATUS.md) | Built and wired into both stacks; not served anywhere reachable; CI never ran |

## The evidence scale

The repository's standing rule is that "built" and "tested" are not "proven". Every status line
uses the strongest evidence that exists:

- **PROVEN**: a committed live, production or real-device report shows it working.
- **TESTED**: automated tests that actually run in CI pass.
- **BUILT**: the code exists, but nothing has verified it for real.
- **OPEN**: planned or missing.

## System-wide facts (checked live on 2026-10-28)

- **`aureon-ai.tech` is served by Railway**, not the Contabo VPS. The response header is
  `server: railway-hikari`. `/health/version` reports `git_sha dc87beb…` with a Railway deployment
  ID. `/health/ready` reports `environment: production`, schema revision `0013`, and two
  providers:
  - `hosted1`: healthy.
  - `hfspace`: `HTTP 503` (the GPU Space is asleep or down).
  
  There is no `local` Ollama on this target. CLAUDE.md still describes the VPS as production.
- **The VPS takes no deploys.** Deploy VPS has failed on every run since at least 2026-09-27, and
  no successful run appears back to 2026-09-14. Since 2026-10-18 it fails with
  `REMOTE HOST IDENTIFICATION HAS CHANGED … Host key verification failed`. The host 169.58.188.43
  now presents ED25519 `SHA256:6dFAzu5+m0A5GSFG5QRt/rNSyZ2JleERZNeRoBWLk2g`. Whether that change is
  a reinstall or an interception is the owner's to establish before `deploy/known_hosts` is
  updated. `dev.aureon-ai.tech` no longer resolves in DNS.
- **CI on `main` at `dc87beb`:**
  - **Backend CI:** green (3848 pytest passed, 56 skipped; legacy unittest 100 OK; pip-audit
    clean; docker-build).
  - **Client CI:** red. It has runners again, contrary to CLAUDE.md's old note.
    - `analyze-and-test`: green, 381 passed.
    - `build-linux`: green.
    - `build-android`: runner out of disk.
    - `build-windows`: 10 path-containment test failures.
  - **Host Daemon CI:** red.
    - Linux: 220 tests passed, then the job hung on a PTY isolate until the 15-minute timeout.
    - Windows: 5 failures.
  - **Admin CI and Verify Hosted Inference:** have never run.
- **`main` is unprotected** (GAP-005 / CB-02).
- **Newest full audit:** `.claude/audits/2026-10-26-full-699f27b/audit-report.md`. It found 38
  findings, 18 of them High. Since then F-15 and F-05 are fixed, and F-01 and F-02 are partly
  fixed (`627a03d`, `2d42388`, `19244e6`, `5153394`).

## How to refresh

1. Re-probe `/health/version` and `/health/ready` on the live domain.
2. Read the latest runs of every workflow on `main`.
3. Re-read `docs/production/gap_register.yaml` and the newest audit under `.claude/audits/`.
4. Update the affected file and the date at the top of each file you touched.
