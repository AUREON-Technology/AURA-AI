# Engineer agent: prioritized implementation plan

This plan comes out of DISCOVER. Nothing in it is implemented. File lists are estimates until
implementation. Each work package (WP) is meant to be one reviewable PR with its own tests. All
of them must pass `pytest`, the legacy `unittest`, `flutter analyze` + `flutter test`, and
`dart analyze --fatal-infos` + `dart test` locally. Each PR states that CI did not run, if it
still does not.

## Order and dependencies

```
WP0 evidence baseline ─┐
WP1 consent (G-03) ────┼─► WP6 enable Claude + roles on Railway (G-01, G-02) ─┐
WP2 spend cap (G-08) ──┘                                                       │
WP3 daemon safety (G-04, G-05, G-06, G-16, G-17) ─► WP4 isolation default (G-10)
WP5 GitHub branch guard (G-07)                                                 │
WP7 Linux IDE (G-09) ──────────────────────────────────────────────────────────┤
WP8 remote control (G-11)  needs WP3                                           │
WP9 client robustness (G-12, G-14, G-15, G-18, G-21)                           │
                                                   WP10 end-to-end evidence ◄──┘
```

**Hard rule:** WP6, which turns Claude on, ships only after WP1 and WP2 are deployed.
Otherwise code reaches Anthropic without consent, and with no per-user cap.

## Release blockers (P0 and P1)

### WP0: Evidence baseline (G-20, part)
- **Tasks:**
  - Regenerate `docs/guides/api-reachability.md`, so `main`'s suite is green.
  - Trigger Backend, Client and Host Daemon CI on a PR that touches each tree, and record the
    run ids.
  - Owner: turn on branch protection that requires those checks.
- **Acceptance:** three green CI runs, linked from `docs/status/`.

### WP1: Anthropic consent (G-03; SEC-02, DATA-01, UX-01, UX-02)
- **Backend:**
  - Alembic migration `aura_engineer_consents` (user_id, notice_version, accepted_at,
    withdrawn_at), with a working `downgrade()`.
  - A port, an in-memory adapter and a Postgres adapter.
  - Routes `GET`, `POST` and `DELETE /api/v3/engineering/consent`.
  - The kernel refuses Engineer-workload turns without consent, before routing, with
    `consent_required`.
  - Erasure on account deletion.
- **Client:** a notice sheet in the Engineer panel and before the first persona use on
  mobile/web; Settings → Engineer → withdraw; strings in `app_en.arb`.
- **Docs:**
  - The privacy copy from `32a6735`.
  - ADR-033 amendment (Claude may hold the `sensitive` ceiling for the Engineer, behind consent).
  - ADR-030 §8.
  - `control-plane.md` §3.
- **Tests:**
  - Kernel refusal and withdraw.
  - Migration round-trip on a real Postgres (`tests/load`).
  - Client widget tests.
- **Prerequisite:** O-03, the Anthropic terms to quote.

### WP2: Per-user daily token cap (G-08; NFR-02, FR-09)
- **Backend:**
  - `AURA_PROVIDER_<N>_USER_TPD` (0 = off).
  - A per-user counter beside `QuotaTracker`, Redis-backed, UTC day.
  - Reserve the worst case, settle on usage.
  - Refuse with `user_quota_exhausted` (`reset_at`).
- **Client:** a distinct "Daily Engineer limit reached, resets at …" state.
- **Tests:** `FrozenClock` unit tests; a real-Redis two-process test under `tests/load`.

### WP3: Daemon safety (G-04, G-05, G-06, G-16, G-17; SEC-03, -04, -05, -08, -09, -10)
- **Daemon:**
  - **`task_key`:** single-segment validation and post-resolve containment.
  - **Folders:** one live `WorkspaceRoots` shared by the control server and the handler.
  - **Environment:** a minimal environment allowlist for `run` (and for the supervisor's spawn).
  - **Outside paths:** in trusted folders, argument path resolution for every verb, outside →
    approval.
  - **Process control:** tree kill on timeout and cancel.
  - **`.git` protection:** a wider `.git` denylist; git run with
    `-c core.fsmonitor= -c core.hooksPath=<empty>` in untrusted folders; diagnostics gated on
    trust.
- **Backend:** validate `session_id` (pattern, length) in `ChatRequest`.
- **Client:** honest trusted-folder label (UX-04).
- **Tests:** one hostd test per acceptance criterion in `03`.

### WP4: Isolated worktree by default (G-10; FR-08)
- The client sends `desktop_task_isolation: true` from the IDE panel and from remote turns.
- Agent Review and the Control Center reflect each task.
- **Tests:** request-body test; a hostd worktree reuse test across turns.

### WP5: GitHub default-branch guard (G-07; SEC-06, FR-07)
- `github_commit_files` refuses the default branch and builds one commit through the Git Data
  API (tree → commit → ref).
- The daemon policy routes any agent `git push` to approval.
- **Tests:** `test_engineer_tools.py`, `test_github_boundary.py` and hostd `command_policy_test`
  cases.

### WP6: Turn on Claude and roles on Railway (G-01, G-02; NFR-01, FR-01, NFR-06)
- **Owner (Railway dashboard):** set
  - `AURA_ALLOW_CLAUDE_INFERENCE=true`;
  - a `kind=claude` slot (model, API-key env, `MAX_DATA_CLASSIFICATION=sensitive`,
    `POOLS=coder,general_quality`, RPM/TPD, `USER_TPD`);
  - `AURA_ENGINEER_AGENT_ROLES=Developer,Admin,SuperAdmin`;
  - then confirm `AURA_REDIS_URL` and that there is no "relay off" warning.
- **Repo:**
  - Document all of it in `control-plane.md`.
  - A test pinning Engineer → SENSITIVE, and that only a sensitive-ceiling provider serves it.
- **Acceptance:**
  - `/health/ready` lists the Claude slot healthy.
  - A live Engineer turn completes.
  - `/api/v3/agents` shows `usable:true` for a Developer and `false` for a User.

### WP7: Desktop IDE on Linux (G-09; FR-10)
- `isDesktopIde` covers Linux. macOS shows "not supported yet" instead of blank.
- Shell smoke test for Linux.
- Linux tar.gz from `desktop-package.yml`.
- **Real-machine checks:** Ubuntu PTY, file dialog, daemon supervisor.

### WP8: Remote desktop control (G-11; FR-11, SEC-07, UX-06)
- **Design note first:** an ADR-011 amendment.
- **Backend:**
  - Turn origin is derived from the device-bound access token (desktop device id vs. other).
  - `create_task` carries `origin`.
  - The policy holds every host-routed side effect of a remote turn for confirmation.
  - `GET /api/v3/host/folders` relays the daemon's granted folders.
- **Daemon:**
  - A `remote_control` flag per daemon, set only from the local control server and the desktop
    UI.
  - It refuses remote-origin tasks when the flag is off.
  - Remote tasks never use the trusted tier.
- **Client:**
  - A desktop toggle.
  - A mobile/web folder picker and daemon status.
  - The existing `EngineerConfirmationCard` for approvals.
- **Tests:** a forged-origin test, opt-in on/off tests, a revoke-while-connected test.

## Before GA (P2)

### WP9: Client and daemon robustness (G-12, G-14, G-15, G-18, G-21, G-13)
- Rehydrate pending confirmations, and honor `retryable:false`.
- De-duplicate steps by `step_id`, and keep the persona label on approval.
- A `refused` daemon state, 1011 for server errors at auth, a tappable status bar Re-pair, and
  the pairing dialog closes on error.
- Gate `pair/approve` on `engineer_agent`.
- Prune the approval queue and cap `read_file`.
- Invalidate `agentsProvider` on resume.
- Run `desktop-package.yml` on a `desktop-v*` tag.

### WP10: End-to-end evidence (G-20; NFR-03, NFR-04)
- Write `deploy/scripts/` or a checklist for the GAP-026 session.
- Run it on a real Windows 11 machine and a real Ubuntu desktop against `api.aureon-ai.tech`,
  covering every NFR-04 item.
- Commit `docs/reports/desktop-engineer-e2e-<date>.md`.
- Update GAP-026, the `docs/status/` files and ADR-011's status line.

## After launch (P3)

G-22 (docs), G-23 (run records), G-24 (minor defects), G-25 / O-02 (localization), and DEF-01 /
G-19 (server workspaces).
