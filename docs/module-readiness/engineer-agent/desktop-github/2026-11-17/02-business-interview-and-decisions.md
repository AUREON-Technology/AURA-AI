# Engineer agent: interview and decision log

Mode: DISCOVER. Date: 2026-11-17. `main` at `0d6c53e`. Every decision below was made by the owner
in this session, answering one question at a time. Each question was asked only after the code
and docs had been read.

Source key: `USER` = the owner's answer in this session.

## Decisions

| ID | Question | Decision | Source | Affects |
|---|---|---|---|---|
| D-01 | Which Engineer form must be production-ready? | **Desktop (paired `aura_hostd`) + GitHub API tools, on Railway** (`api.aureon-ai.tech`). Server-side Docker workspaces (ADR-013/014, GAP-025) are **deferred** and out of this gate. | USER (recommended option) | Scope of every requirement |
| D-02 | Which model serves Engineer turns? | **Claude (ADR-033)**, raised to the `sensitive` ceiling, with a pool the Engineer's `coder` request can use. Users' code, file contents and command output go to Anthropic. | USER (not the recommended option, which was self-hosted `hfspace`) | NFR-01, SEC-01, ADR-033 amendment, privacy copy |
| D-03 | What must a user agree to before code goes to Anthropic? | **One-time opt-in.** A clear notice before the first Engineer turn. The acceptance is stored server-side and can be withdrawn. The backend refuses Engineer turns without it. | USER (recommended) | SEC-02, UX-01, DATA-01 |
| D-04 | Who gets the Engineer? | **Developer, Admin, SuperAdmin** (`AURA_ENGINEER_AGENT_ROLES`). This matches the desktop sign-in gate (F-09). | USER (recommended) | FR-01 |
| D-05 | How is Claude spend bounded? | **A per-user daily token cap** on the Claude slot, counted in Redis, with a clear "limit reached, resets at …" message. The provider-wide TPD/RPD stays as the hard ceiling. The Engineer's per-turn budgets are unchanged. | USER (recommended) | NFR-02, FR-09 |
| D-06 | What does a "trusted" desktop folder mean? | **An honest full shell.** It stays a real shell, and the label says plainly that it can reach anything the account can. Commands get a minimal environment (PATH, HOME, locale; not the user's tokens). Any argument that resolves outside the folder needs approval. | USER (recommended) | SEC-04, SEC-05, UX-04 |
| D-07 | Where may the Engineer write in a repo? | **Never the default branch.** `github_commit_files` refuses the default branch and makes one commit for all files. On the desktop, any `git push` by the agent always needs approval. Changes reach `main` only through a PR a human merges. | USER (recommended) | SEC-06, FR-07 |
| D-08 | Which desktop platforms launch? | **Windows + Linux.** macOS is out. | USER (not the recommended option, which was Windows only) | FR-10, NFR-05 |
| D-08b | How do Linux users use the Engineer? | **Enable the existing Desktop IDE on Linux**, giving the same panel, folder binding, terminal and approvals as on Windows. | USER (recommended) | FR-10 |
| D-09 | Where do desktop edits land? | **An isolated git worktree per Engineer chat, by default.** The user reviews and merges from Agent Review. This requires the `task_key` path-escape fix first (G-04). | USER (recommended) | FR-08, SEC-03 |
| D-10 | Can phone and web drive the desktop? | **Yes: remote desktop control is allowed.** Mobile and web get a folder picker and an approvals UI. | USER (not the recommended option, which was GitHub-only off the desktop) | FR-11, UX-06 |
| D-10b | What extra guard do remote turns get? | **Opt-in, and every remote action needs approval.** Remote control is off until it is enabled on the desktop itself (per daemon, revocable there). Remote turns never use the trusted tier, so every write and command needs approval. | USER (recommended) | SEC-07 |
| D-10c | Where are remote approvals given? | **On the remote device** (phone or web). The desktop opt-in and device revoke are the guards against a stolen session. | USER (recommended) | SEC-07, UX-06 |

## Consequences the owner accepted with these choices

- **D-02 reverses a promise shipped today.** Commit `32a6735` (2026-11-17) changed the client copy
  to say Engineer turns stay on self-hosted models. That copy, `docs/deployment/control-plane.md`
  §3 and ADR-030 §8 all become false once the Claude slot is raised. D-03 is the mitigation.
- **D-02 makes Anthropic the Engineer's single point of failure.** No other provider has the
  `sensitive` ceiling while `hfspace` is down. An Anthropic outage, or a hit on the TPD ceiling,
  means no Engineer.
- **D-10 + D-10c: a stolen phone or web session, on an account whose desktop has remote control
  enabled, can approve its own commands.** The guards are the desktop opt-in, device revoke, and
  the 15-minute access token (FSA-004). The owner accepted this residual risk; D-10c's re-auth
  option was declined.
- **D-08 brings Linux into scope.** Linux has had no real-machine Engineer run at all (no PTY or
  file-dialog check on a real Linux desktop).

## Not asked: technical or for later (the agent's call, flagged for PLAN)

- **How a remote turn's approval reaches the daemon (D-10c).** Daemon approvals are loopback-only
  today. Recommendation: for a remote-origin turn, the backend holds every host-routed side
  effect in the existing confirmation flow (`pending_tool_calls`, which mobile already renders
  with `EngineerConfirmationCard`). The daemon enforces the opt-in itself (G-11). To settle in
  PLAN mode.
- **Code-signing certificate for the Windows installer.** No certificate exists, so installers
  are unsigned and SmartScreen will warn. This needs an owner purchase. It is recorded as open
  item O-01, not a blocker the owner was asked about.
- **Arabic localization of the Engineer UI.** The Engineer UI is English-only. The rest of the
  app has RTL tests. This was not asked, so it is recorded as open item O-02 and not treated as
  a requirement.

## Deferred

| ID | Item | Blocks this release? |
|---|---|---|
| DEF-01 | Server-side Engineer workspaces (GAP-025), including the host-git hook defect G-19 | No. Still a P1 before that feature ships. |
| DEF-02 | macOS desktop | No |
