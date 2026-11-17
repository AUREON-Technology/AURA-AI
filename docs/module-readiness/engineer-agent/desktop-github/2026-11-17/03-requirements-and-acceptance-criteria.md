# Engineer agent: requirements, acceptance criteria and traceability

Scope: Desktop (Windows + Linux) and GitHub Engineer on Railway (D-01, D-08). Each requirement
names its source decision. Status is against `main` at `0d6c53e`, using the statuses `PASS`,
`GAP`, `PARTIAL`, `UNVERIFIABLE` and `NOT APPLICABLE`. Gap IDs refer to
`04-gap-and-risk-register.md`.

## Actors

- **Engineer user:** role Developer, Admin or SuperAdmin (D-04). Uses the desktop app on Windows
  or Linux, or phone or web for remote control.
- **Operator:** sets Railway configuration (Claude slot, roles, caps).
- **Anthropic:** third-party processor of Engineer turns (D-02).

## User journeys

1. **First use (desktop).**
   1. Sign in on the desktop and pair the daemon.
   2. Grant a folder.
   3. Open the Engineer panel and see the Anthropic notice (D-03); accept it.
   4. Ask for a change. The agent works in an isolated worktree (D-09).
   5. Review the diff in Agent Review and merge it, or discard it.
2. **GitHub change.**
   1. Connect GitHub.
   2. Ask for a fix in a repo. The agent reads files and creates a branch.
   3. One confirmation covers the commit (one commit for all files) and another the draft PR.
   4. The default branch is never written (D-07).
3. **Remote control.**
   1. On the desktop, enable remote control for this daemon (D-10b).
   2. On the phone, pick the Engineer and a folder.
   3. Every write and command shows an approval card on the phone (D-10c).
   4. Disable remote control on the desktop, and remote turns are refused at once.
4. **Failure paths.**
   - The daily token cap is reached.
   - Anthropic is down or rate-limited.
   - The daemon is offline, or paired to a stale server.
   - An approval has expired.
   - The app restarts with an approval pending.
   - Consent is withdrawn.
   - The user lacks the role.

## Requirements

### Access and model

**FR-01 (D-04, revised by D-11).** Production MUST grant `engineer_agent` to:
- Developer, Admin and SuperAdmin (`AURA_ENGINEER_AGENT_ROLES`);
- a User-role person whose **active workspace's** plan has `engineer: true` (#246's `PlanLimits`).

No one else gets it.
- *Acceptance:* `GET /api/v3/agents` shows `engineer` as:
  - `usable:true` for a Developer;
  - `usable:true` for a User in a Pro workspace;
  - `usable:false` for a User in a Free workspace;

  checked live on `api.aureon-ai.tech`.
- *Status:*
  - **UNVERIFIABLE** for operators: the Railway environment is not visible, and the default is
    empty. → G-02
  - **GAP** for customers: nothing grants the capability from a plan; #246 only removes it.
    → G-27

**FR-18 (D-11).** Desktop sign-in, desktop refresh, pairing approval and the host socket MUST
admit a User-role person who is entitled to the Engineer in any workspace they belong to (T-6).
They keep refusing everyone else, exactly as F-09 does today.
- *Acceptance:*
  - A Pro customer can sign in to the desktop and pair.
  - A Free customer gets 403 with a plain reason.
  - A customer downgraded to Free:
    - loses the desktop session at the next refresh;
    - loses the host socket at the next 60 s re-check (4401).
- *Status:* **GAP.** `api/v2/auth.py:207-251` (`is_console_operator`). → G-27

**FR-19 (D-11).** Customer Engineer turns MUST count against #246's monthly message meter, as any
turn does. Operators stay unmetered (#246). The D-05 token cap applies to everyone who uses the
Claude slot, operators included.
- *Status:* **GAP** (depends on #246 merging).

**FR-20 (D-12).** The desktop app MUST sign in through the same Firebase providers as web (Google,
Apple, email), via a browser hand-off to the loopback listener (T-7). An existing Google desktop
account keeps its identity (#246 `canonical_subject`).
- *Acceptance:* each of the three providers signs in on Windows and on Linux.
- *Status:* **GAP.** → G-28

**SEC-11 (D-02, T-1).** The Claude slot MUST serve Engineer-workload generations only. Every other
generation, including unhinted ones, MUST exclude it.
- *Acceptance:*
  - A router test: a SENSITIVE generation with no hint, and an interactive one, never select a
    provider whose workloads are `{engineer}`.
  - An Engineer turn does select it.
- *Status:* **GAP.** No workload allowlist exists, and `DEFAULT_WORKLOAD` has no pool. → G-26

**SEC-12 (D-11).** Every customer-facing daemon defect MUST be closed before any customer can pair:
G-04, G-05, G-06, G-16, G-17 and G-18.
- *Status:* **GAP.**

**NFR-01 (D-02).** Every Engineer turn MUST be served by the Claude slot configured with a
`sensitive` ceiling and a pool the `coder` request accepts.
- *Acceptance:*
  - `/health/ready` lists the Claude provider as healthy.
  - A live Engineer turn completes, and its provider is the Claude slot.
  - A test pins Engineer → SENSITIVE (`kernel.py:1208`).
- *Status:* **GAP.** Live Engineer turns end in 503 `capability_unavailable`. → G-01

**SEC-01 (D-02).** A hosted open-weight slot (`hosted1`) MUST still never receive Engineer
content.
- *Acceptance:* the existing router exclusion is unchanged, and a test exists for the
  Engineer + hosted-only case.
- *Status:* **PASS** in the code (`router.py:359-361`). No Engineer-specific test.

### Consent and privacy

**SEC-02 (D-03).** The backend MUST refuse an Engineer turn (and the host WebSocket's tool calls)
from a user who has no current Anthropic consent.
- The refusal has a distinct error kind (e.g. `consent_required`, not retryable).
- *Acceptance:*
  - A turn without consent → refusal, with no provider call made.
  - Accept → the turn proceeds.
  - Withdraw → the next turn is refused.
- *Status:* **GAP.** → G-03

**DATA-01 (D-03).** Consent MUST be stored server-side as a per-user record: version of the
notice, accepted-at, withdrawn-at. It is created by an Alembic migration with a working
`downgrade()`.
- *Acceptance:* migration round-trip on a real Postgres; record survives a restart; erased with
  the account.
- *Status:* **GAP.** → G-03

**UX-01 (D-03).** Before the first Engineer turn, every client (desktop, mobile, web) MUST show
the notice in plain words. It says that code, file contents and command output go to Anthropic,
and that the user can withdraw consent in Settings.
- *Acceptance:* widget tests for show, accept and withdraw; copy in `app_en.arb`.
- *Status:* **GAP.**

**UX-02 (D-02).** The privacy copy MUST be made true:
- the onboarding privacy page and the "Where AURA runs" card (changed in `32a6735`);
- ADR-030 §8;
- ADR-033 (amendment);
- `docs/deployment/control-plane.md` §3.

- *Acceptance:* no shipped string claims Engineer turns stay self-hosted.
- *Status:* **GAP.** → G-03

### Spend

**NFR-02 (D-05).** A per-user daily token cap MUST apply to the Claude slot. It is counted in
Redis across workers and resets at UTC midnight, with the operator setting the value. When a
user reaches it, the turn is refused before any provider call, with a message naming the reset
time.
- *Acceptance:*
  - A unit test with `FrozenClock`.
  - A real-Redis test for counting across two processes.
  - Live: a low cap set on staging produces the message.
- *Status:* **GAP.** Only provider-wide RPM/RPD/TPM/TPD and a per-user concurrency cap exist
  (`llm/quota.py`, `llm/admission.py:19`). → G-08

**FR-09.** Anthropic outages, 429s and the provider-wide TPD MUST surface as a clear "Engineer
unavailable, try later" state, not as a generic connection error with Retry.
- *Status:* **PARTIAL.** Router cooldowns exist; the client maps errors generically. → G-15

### Desktop daemon safety

**SEC-03 (D-09).** `task_key` (and every path-forming RPC parameter) MUST be validated. It must be
a single safe path segment, so an absolute path, `..` or a separator is refused. The resulting
worktree must be contained in its parent folder.
- *Acceptance:* hostd tests for an absolute key, `../..`, and a key whose directory already
  exists outside the folder.
- *Status:* **GAP.** → G-04

**SEC-08.** Granting, revoking or untrusting a folder MUST take effect for the connected agent
without a restart.
- *Acceptance:* a test that revokes while connected; the next `write_file` there is refused.
- *Status:* **GAP.** → G-05

**SEC-04 (D-06).** Agent commands MUST run with a minimal environment (an allowlist: PATH, HOME or
USERPROFILE, locale, TEMP, SystemRoot on Windows, and only what is required to run). They must
not inherit the daemon's environment.
- *Acceptance:* a test runs `env` or `set` and sees no planted secret variable.
- *Status:* **GAP.** → G-06

**SEC-05 (D-06).** In a trusted folder, any command argument that resolves outside the folder
(any verb, not only destructive ones) MUST go to approval.
- *Acceptance:* `cat ~/.ssh/id_rsa` and `echo x > ~/.bashrc` queue for approval; in-folder
  commands do not.
- *Status:* **GAP.** → G-06

**UX-04 (D-06).** The trusted label MUST state that a trusted folder's commands can reach anything
the user's account can, and that outside paths ask first.
- *Status:* **GAP.** `host_daemon_page.dart:418` says "still confined".

**SEC-06 (D-07).** Default-branch writes:
- `github_commit_files` MUST refuse the default branch.
- A `git push` run by the agent on the desktop, by any spelling (including `git -C`, aliases
  and `run_command` chains), MUST always need approval, trusted folder or not.
- *Status:* **GAP.** → G-07

**FR-07 (D-07).** `github_commit_files` MUST create one commit containing all files.
- *Status:* **GAP.** → G-07

**SEC-09.** A command timeout or cancel MUST kill the whole process tree. Cancelling a running
command MUST stop it.
- *Status:* **GAP.** → G-16

**SEC-10.** Writes to git control files MUST be refused everywhere the daemon's `.git`
protection intends, including `.git/modules/*/hooks`, `.git/modules/*/config` and
`config.worktree`. In untrusted folders, allowlisted git commands MUST run with repo-local
executables disabled (e.g. `-c core.fsmonitor= -c core.hooksPath=/dev/null`).
- *Status:* **GAP.** → G-17

### Isolation

**FR-08 (D-09).** Every desktop Engineer chat MUST run in its own worktree and branch by default.
- The client sends `desktop_task_isolation: true`.
- Agent Review shows the task.
- The user's live working tree is untouched until merge.
- *Acceptance:* client test of the request body; an end-to-end run shows the worktree.
- *Status:* **GAP.** The client never sends it, and the Control Center copy promises it. → G-10

### Platforms

**FR-10 (D-08, D-08b).** The Desktop IDE, including the Engineer panel, folder binding, terminal
and approvals dialog, MUST be enabled on Linux as well as Windows. macOS MUST NOT show a blank
`/ide`.
- *Acceptance:*
  - `isDesktopIde` is true on Linux.
  - The shell smoke test runs for Linux.
  - A real-machine end-to-end run on Ubuntu (PTY, file dialog, tar.gz install).
- *Status:* **GAP.** → G-09

**NFR-05.** Release artifacts: a Windows installer and a Linux tar.gz with a bundled daemon and
checksum, produced by CI.
- *Status:* **PARTIAL.** `desktop-package.yml` is built, but never run on a tag and unsigned.
  → G-13, O-01

### Remote control

**FR-11 (D-10).** Mobile and web Engineer chat MUST offer a folder picker. It lists the paired
daemon's granted folders, and the chosen folder is sent as `workspace_root`.
- *Status:* **GAP.** → G-11

**SEC-07 (D-10b, D-10c).**
- The backend MUST know whether a turn comes from the desktop app or from elsewhere, from
  server-held facts such as the device-bound token, never from a client-claimed field.
- A remote turn's host-routed actions:
  - MUST be refused unless remote control is enabled on that daemon (set and revocable only on
    the desktop, enforced by the daemon);
  - when enabled, MUST each need approval regardless of the trusted tier;
  - MUST be approvable on the remote device.
- *Acceptance:*
  - Remote with opt-in off → refused.
  - Remote with opt-in on → every write and command yields a pending approval on the phone.
  - Disabling on the desktop → the next remote call is refused.
  - A forged origin field → treated as remote.
- *Status:* **GAP.** → G-11

**UX-06.** Mobile and web MUST show daemon status (connected, remote enabled or not) in the
Engineer chat.
- *Status:* **GAP.**

### Robustness and UX

**FR-12.**
- Pending confirmations MUST survive an app restart until the server TTL.
- `confirmation_invalid` MUST show "this approval expired", not Retry.
- An approval re-send MUST keep the held turn's persona label.
- *Status:* **GAP.** → G-14

**FR-13.** Each step MUST render once with its latest status.
- *Status:* **GAP.** Duplicated rows, and a spinner left running. → G-14

**FR-14.**
- A daemon paired to a stale server, or stopped by 4401, MUST show an actionable "Pair again"
  state in the IDE.
- A transient server error during WebSocket auth MUST NOT close with 4401.
- *Status:* **GAP.** → G-12

**FR-15.** The pairing dialog MUST close and show the error when approval or status fails.
- *Status:* **GAP.** → G-12

**FR-16.** `pair/approve` MUST refuse a user without `engineer_agent`.
- *Status:* **GAP** (E2-05). → G-18

**FR-17.** A capability granted while the app runs MUST be visible without a restart.
- *Status:* **GAP.** `agentsProvider` is cached and has no retry. → G-15

### Evidence and operations

**NFR-03.** Backend, Client and Host Daemon CI MUST have executed green on the release commit, and
`main`'s own test suite MUST pass.
- *Status:* **GAP.** Backend and hostd CI have not run since `dc87beb`, and `main` fails
  `test_api_reachability`. → G-20

**NFR-04.** A committed end-to-end report (GAP-026) on real Windows and real Linux against
production MUST show:
- pair;
- consent;
- an isolated task write, command and merge;
- an approval round-trip;
- a GitHub branch, commit and draft PR;
- a refusal on the default branch;
- remote control on and off;
- the cap message;
- revoke a folder while connected.

- *Status:* **GAP.** → G-20

**NFR-06.** The Railway relay MUST be on (`AURA_REDIS_URL` set, no "relay off" warning at boot),
so a paired desktop is reachable from every worker.
- *Status:* **UNVERIFIABLE.** → G-02

## Traceability summary

| Status | Count | IDs |
|---|---|---|
| PASS | 1 | SEC-01 |
| PARTIAL | 2 | FR-09, NFR-05 |
| UNVERIFIABLE | 1 | NFR-06 |
| UNVERIFIABLE (operators) + GAP (customers) | 1 | FR-01 |
| GAP | 33 | All the rest, including FR-18…20, SEC-11 and SEC-12 added in PLAN |

38 requirements in total.
