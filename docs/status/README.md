# AURA system status

**As of 2026-11-07, `main` at `772f9ef` (live probe and CI re-read in `docs/reports/AURA_PRODUCTION_READINESS_2026-11-07.md`; the per-component files below were last fully refreshed 2026-10-28 at `2337577`).** These files record what is true now: what runs
where, what CI says, what is proven, and what is next. They are snapshots, not a roadmap. When
they disagree with the code, the CI history or a live probe, those win, and the file should be
corrected in the same change.

**Re-read against commit:** `b2e14c4` (2026-11-08) for every row dated 2026-11-07 or later (their live checks came first; the re-read confirmed that tree holds what they describe); older rows carry the commit they were checked at. Refresh this line whenever a row is re-verified (audit FSA-120).

| Component | File | One line |
| --- | --- | --- |
| Backend (`aura_backend/`, `deploy/`) | [BACKEND_STATUS.md](BACKEND_STATUS.md) | Live on Railway at `ffbbb9f`, ready, **schema `0019` (head) since at latest 2026-11-07**; `hfspace` 503 so only `internal`-ceiling inference is up; `/metrics` and `/docs` open; runners assigned again but no application suite CI-run since `dc87beb`; VPS deploy blocked |
| Desktop app (`aura_client` desktop + `aura_hostd`) | [DESKTOP_APP_STATUS.md](DESKTOP_APP_STATUS.md) | Linux built in CI at `dc87beb`; Windows/hostd fixes merged but never CI-run; nothing packaged. 2026-11-08 assessment: not production-ready |
| Mobile app (`aura_client` Android/iOS) | [MOBILE_APP_STATUS.md](MOBILE_APP_STATUS.md) | 2026-11-14 release pass: account deletion, backup-key, guard and live-voice fixes, 673 tests green locally, not CI-run; iOS still blocked on owner items (Google client id, team id, Sign in with Apple, privacy policy) |
| Web console (`aura_admin/`) | [WEB_CONSOLE_STATUS.md](WEB_CONSOLE_STATUS.md) | Built and wired into both stacks; not served anywhere reachable; **Admin CI green once** (run #3, dispatched 2026-11-07: typecheck, build, Playwright e2e) |

## The evidence scale

The repository's standing rule is that "built" and "tested" are not "proven". Every status line
uses the strongest evidence that exists:

- **PROVEN**: a committed live, production or real-device report shows it working.
- **TESTED**: automated tests that actually run in CI pass.
- **BUILT**: the code exists, but nothing has verified it for real.
- **OPEN**: planned or missing.

## System-wide facts (checked live on 2026-10-28, ~21:20 UTC)

- **Update 2026-11-07 (live probe, `main` at `772f9ef`; full detail in
  `docs/reports/AURA_PRODUCTION_READINESS_2026-11-07.md`):**
  - `aureon-ai.tech` serves `ffbbb9f`; `/health/ready` is `healthy: true` with **`schema.revision
    0019`**, the code's head. The ADR-032 item below is no longer pending live. How `0017`→`0019`
    was applied (dashboard pre-deploy step or a manual upgrade) is not recorded; the Railway deploy
    log for the #206/#207 deploys settles it. `scripts/move_personal_os_records.py` has no run record.
  - `hosted1` healthy, `hfspace` still **503**, no Ollama. `hosted1`'s ceiling is `internal`, so
    memory, RAG, tool, Engineer and image turns have no eligible provider (FSA-024). The live
    retrieval index is `hash-embed-v1` (FSA-031).
  - **`GET /metrics` returns 200 without a token** (`AURA_METRICS_TOKEN` unset on Railway, FSA-009)
    and **`GET /docs` returns 200** (FastAPI default, never disabled). Auth on `/api/v3/*` holds (401).
  - **Runners are assigned again.** Repo Hygiene executed on 2026-11-06 (#16, failed on SC1003,
    fixed by #210) and 2026-11-07 (#21, green). Admin CI run #3 (dispatched 2026-11-07) executed
    and is green, its first run ever. Backend, Client, Host Daemon and Agent Evaluation CI have had
    **no trigger since 2026-10-31** (path filters), so no application suite has run in CI since
    `dc87beb`; their last runs still show the no-runner signature.
  - Local suites on `772f9ef` (not CI): backend pytest green apart from one architecture test that
    the 2026-10-31 audit docs themselves tripped (a quoted PEM marker; fixed in the same change as
    this note); legacy unittest 133 OK (37 skipped); `flutter analyze` clean, `flutter test` 538
    passed, 3 skipped; hostd `dart analyze --fatal-infos` clean, `dart test` 231 run, 230 passed; the one failure
    (debug console) passes once `debugpy` is installed as CI does; admin `next build` OK.
  - Deploy Control Plane and Deploy VPS still fail on every run (`preflight` / `blocked` jobs).
    `main` is still unprotected. Open PR: #208 (draft, client).

- **Pending, 2026-10-31: ADR-032 adds migration `0019` (`aura_user_data_keys`).** It is not on
  `main` yet. After the merge that carries it, the Railway pre-deploy step must take production to
  `0019`, and `/health/ready` must show `schema.revision 0019`. Until it does, the key endpoint and
  the agent's personal-OS tools fail. ADR-032 also ends zero-knowledge for the synced personal-OS
  fields: the server now holds each user's data key, wrapped by `AURA_DATA_ENCRYPTION_KEYS`.
- **Update 2026-10-30: the production database matches the code again (CB-43 resolved).** The
  owner ran `alembic upgrade head` over `railway ssh` (`0013 → 0014 → 0015 → 0016`, clean). Live
  `/health/ready` showed `healthy: true`, `schema.revision 0016`, release `6417c43`, deployment
  `11e5d66b`; only `hfspace` returns 503. Why every deploy had skipped it: Railway does not run
  `preDeployCommand` in a shell, so the inline `echo … && alembic upgrade head && …` ran only the
  echo (the deploy log shows `MIGRATION START` then `Starting Container` 0.19 s later) and still
  passed. `railway.json` now calls `scripts/railway-predeploy.sh`, BUILT and TESTED against a
  stand-in `alembic`. Railway ignores `railway.json` for this service (no config file linked), so on
  2026-10-31 the owner set the same command in the dashboard. Seen running on a live deploy
  (deploy `4afdd9a8` at `9bfaa22`, 2026-10-31 08:40 UTC): `MIGRATION OK` at `0016 (head)`. Applying an actual pending revision is still
  unobserved (BACKEND_STATUS.md, Open issues 1). *Was (2026-10-28):* live at
  `2337577` with schema `0013` against an expected `0015`.
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
- **Open pull requests (as of 2026-10-28): none.** (2026-11-07: #208 is open, draft.) #78 (desktop loopback login) and #88 (D8 per-task worktrees) were
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
