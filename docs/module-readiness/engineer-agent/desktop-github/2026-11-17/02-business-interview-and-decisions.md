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

### Added in PLAN mode (same day)

| ID | Question | Decision | Source | Affects |
|---|---|---|---|---|
| D-11 | Open PR #246 makes Engineer a Pro/Team plan feature, but its access still came only from the D-04 roles. Who gets the Engineer? | **Operators (Developer, Admin, SuperAdmin) and paying customers**: a User-role person whose active workspace's plan includes Engineer (Pro or Team in #246). **The desktop is included for them**, which reverses F-09 (2026-11-16, desktop sign-in for Developer and above only) for plan-entitled customers. | USER. The first answer was "Operators only … + the paying customers"; it was clarified in a follow-up question. | FR-01, FR-18, FR-19, SEC-12 |
| D-12 | Customers who signed up with Apple or email can't use Google-only desktop sign-in. What should launch do? | **Desktop sign-in goes through the same Firebase providers as web** (Google, Apple, email), so every paying customer can sign in on the desktop. | USER (recommended) | FR-20 |

### Added in IMPLEMENT (WP4)

| ID | Question | Decision | Source | Affects |
|---|---|---|---|---|
| D-13 | Isolation needs git. What should the Engineer do in a granted folder that is not a git repository? | **Offer "Initialize git here".** The Engineer refuses to work in a non-git folder until it is a repository, with a one-click "Initialize git here" in the IDE and in the phone's folder bar. It runs `git init` and makes a first commit of the folder's current files. D-09's promise holds: every change is reviewable and undoable. | USER (recommended) | FR-08 |

**D-11 supersedes D-04** where they differ. D-04's roles still hold for operators, and plan
entitlement is a second source of `engineer_agent`.

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

- **D-11 puts the daemon in front of the public.** Paying customers will run `aura_hostd` on
  their own machines. G-04, G-05 and G-06 were blockers already; for customers they are not
  negotiable. The customer launch also depends on #246 merging (plans, entitlements).
- **D-11 and D-12 make the customer Engineer depend on #246 and on a new desktop sign-in path.**
  The operator Engineer does not. The plan therefore ships operators first and customers second
  (stage 2 in `05`).

## Technical decisions made in PLAN (the agent's; reversible, recorded for review)

| ID | Decision | Why |
|---|---|---|
| T-1 | **The Claude slot serves the Engineer workload only.** A new per-provider workload allowlist, `AURA_PROVIDER_<N>_WORKLOADS=engineer`, is enforced in the router's capability filter. | Raising Claude to `sensitive` would otherwise make it eligible for every unhinted generation, because `DEFAULT_WORKLOAD` has no pool (`llm/workload.py:39`) and unclassified generations count as SENSITIVE. That would send non-Engineer user data to Anthropic, beyond D-02 (G-26). |
| T-2 | **Consent is checked in the kernel where the persona is resolved**, so it covers a delegated Engineer sub-turn too. A `consent_required` refusal happens before planning, so no provider call is made. | One enforcement point that every path passes through (chat, stream, approval resume, delegation). |
| T-3 | **Turn origin comes from the access token's signed `platform` and `device_id` claims** (`services/jwt_auth.py:532,556`), carried on `Principal`. A turn is desktop-origin only when `platform == "desktop"` and the daemon serving it is paired to the same user. | This is server-held and cannot be claimed by the client (SEC-07). |
| T-4 | **A remote turn's approvals use the existing confirmation flow** (`pending_tool_calls` + `EngineerConfirmationCard`). The daemon gets `origin` in `create_task`. It refuses remote-origin tasks unless its local `remote_control` flag is on, and never applies the trusted tier to them. | Reuses the tested approval path, and keeps the opt-in enforced on the user's machine. |
| T-5 | **The per-user cap lives beside `QuotaTracker`**, keyed `(provider, user, UTC day)` on the cache port, and is reserved and settled like the provider counters. It uses the `user_key` the router already has (`router.py:611`). A refusal is `user_quota_exhausted` (429, `reset_at`). | Same accounting model as the existing quotas; counts across workers through Redis. |
| T-6 | **Plan-based Engineer access is decided in one function**, `engineer_entitled(principal)`, built on #246's `PlanService`. <br>• A **turn** uses the active workspace's plan. <br>• **Pairing, the host socket and desktop sign-in** need Engineer in **any** workspace the user belongs to. | A Team customer whose personal workspace is Free must still be able to pair a desktop for team work. |
| T-7 | **Desktop Firebase sign-in is a browser hand-off.** The desktop opens the hosted web sign-in, which returns a one-time code to the existing loopback listener. The backend exchanges it, like `/auth/desktop/exchange` (#243). | To verify at implementation: the Flutter `firebase_auth` plugin does not appear to support Linux, or Apple and Google provider sign-in on Windows. A browser hand-off avoids both. |
| T-8 | **Migrations are numbered at implementation time.** #246 and #250 both claim `0021` today; the consent table takes the next free number after whichever lands. | Avoids a collision with two open PRs. |

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
