# AURA system status

**As of 2026-10-28 (evening), `main` at `2337577`.** These files record what is true now: what runs
where, what CI says, what is proven, and what is next. They are snapshots, not a roadmap. When
they disagree with the code, the CI history or a live probe, those win, and the file should be
corrected in the same change.

| Component | File | One line |
| --- | --- | --- |
| Backend (`aura_backend/`, `deploy/`) | [BACKEND_STATUS.md](BACKEND_STATUS.md) | Live on Railway at `main` but **not ready: DB schema `0013`, code needs `0015`**; CI not executing; VPS deploy blocked |
| Desktop app (`aura_client` desktop + `aura_hostd`) | [DESKTOP_APP_STATUS.md](DESKTOP_APP_STATUS.md) | Linux built in CI at `dc87beb`; Windows/hostd fixes merged but never CI-run; nothing packaged |
| Mobile app (`aura_client` Android/iOS) | [MOBILE_APP_STATUS.md](MOBILE_APP_STATUS.md) | 381 tests green at `dc87beb`; sync/approval fixes merged since, not CI-run; no iOS pipeline |
| Web console (`aura_admin/`) | [WEB_CONSOLE_STATUS.md](WEB_CONSOLE_STATUS.md) | Built and wired into both stacks; not served anywhere reachable; CI never ran |

## The evidence scale

The repository's standing rule is that "built" and "tested" are not "proven". Every status line
uses the strongest evidence that exists:

- **PROVEN**: a committed live, production or real-device report shows it working.
- **TESTED**: automated tests that actually run in CI pass.
- **BUILT**: the code exists, but nothing has verified it for real.
- **OPEN**: planned or missing.

## System-wide facts (checked live on 2026-10-28, ~21:20 UTC)

- **Production is running code its database does not match.** `aureon-ai.tech` (Railway,
  `server: railway-hikari`) serves `2337577` — current `main` — but `/health/ready` reports
  `healthy: false`: `schema: expected 0015, revision 0013 — database schema is behind the code in
  this image`. Migrations `0014` (`sync_txid` on every syncable table) and `0015`
  (`users.role_assigned_at/_by`) were never applied. Both columns are mapped on the ORM models
  (`infrastructure/db/models.py`), so any query that loads a `User` row or a syncable row fails
  until they exist — sign-in and `/api/v3/sync` are very likely failing for every user right now
  (inferred from the code; not probed with a real account). Both migrations apply cleanly
  `0013 → 0015` on a fresh Postgres 16 (checked locally this session), so the code is not the
  problem: `railway.json`'s `preDeployCommand: alembic upgrade head` either did not run or ran
  against a different database. Only the owner can see the Railway deploy logs. **This is the most
  urgent item in the repository** (CB-43).
- **CI has not executed since ~19:50 UTC on 2026-10-28.** Every job of every workflow on `main`
  for #187, #185, #188 and #143 (Backend CI #392–#397, Client CI #150–#151, Host Daemon CI #60,
  Agent Evaluation #166, both deploy workflows) completed in 2–4 s with no log — the
  no-runner-assigned signature of September's CB-01 (an account-level Actions/billing block). So
  the fixes those PRs carry are TESTED only in the sessions that wrote them, not in CI.
- **Railway deploys without the CI gate.** Live is at `2337577` although Deploy Control Plane
  never succeeded for it; Railway's own GitHub auto-deploy evidently ships every `main` push. The
  "merging to `main` is the deploy, gated on Backend CI" rule in CLAUDE.md holds only for the VPS.
- `hosted1` healthy, `hfspace` `HTTP 503`; no Ollama. `/console` returns 404.
- **The VPS takes no deploys.** Deploy VPS has failed on every run since at least 2026-09-27.
  Since 2026-10-18 it fails with `REMOTE HOST IDENTIFICATION HAS CHANGED … Host key verification
  failed`. The host 169.58.188.43 now presents ED25519
  `SHA256:6dFAzu5+m0A5GSFG5QRt/rNSyZ2JleERZNeRoBWLk2g`. Whether that change is a reinstall or an
  interception is the owner's to establish before `deploy/known_hosts` is updated.
  `dev.aureon-ai.tech` no longer resolves in DNS.
- **Last CI that actually ran on `main` (`dc87beb`):** Backend CI green (3848 passed, 56 skipped;
  legacy unittest 100 OK; pip-audit; docker-build). Client CI: `analyze-and-test` (381 passed) and
  `build-linux` green, `build-android` out of disk, `build-windows` 10 path-containment failures.
  Host Daemon CI: Linux 220 passed then hung on the PTY isolate; Windows 5 failures. Fixes for all
  three red jobs merged in #185 and are unverified by CI. Admin CI and Verify Hosted Inference have
  never run.
- **Local run on `2337577` (this session, not CI):** pytest 3943 passed, 56 skipped, 0 failed; legacy unittest 106 OK (10 skipped). No Flutter or Dart run.
- **Open pull requests: none.** #78 (desktop loopback login) and #88 (D8 per-task worktrees) were
  closed as superseded: both features landed on `main` later in reworked form (Unit 7's
  `desktop_oauth_client_io.dart`; D8/D9's `isolated_task_registry.dart` + Agent Review). #78's one
  piece `main` lacked, the audience test, was ported as
  `aura_backend/tests/unit/test_desktop_oauth_audience.py`.
- **`main` is unprotected** (GAP-005 / CB-02).
- **Newest full audit:** `.claude/audits/2026-10-26-full-699f27b/audit-report.md` (38 findings,
  18 High). Merged fixes since: F-01 (daemon half), F-02 (refresh + Access-page role assignment),
  F-03, F-04, F-05, F-07, F-10, F-11, F-12, F-13, F-14, F-15, F-19.

## How to refresh

1. Re-probe `/health/version` and `/health/ready` on the live domain.
2. Read the latest runs of every workflow on `main`.
3. Re-read `docs/production/gap_register.yaml` and the newest audit under `.claude/audits/`.
4. Update the affected file and the date at the top of each file you touched.
