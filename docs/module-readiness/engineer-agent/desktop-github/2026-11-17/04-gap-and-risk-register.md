# Engineer agent: gap and risk register

Severity scale: P0 = the feature cannot serve users or must not ship; P1 = a release blocker
(security or a core journey); P2 = required before GA; P3 = after launch.

**How each finding was verified:**
- *Verified* means re-checked by hand in this session.
- *Mapped* means from a read-only subagent code map, cited but not re-checked.

## P0

### G-01: No model may serve an Engineer turn in production
- **Requirements:** NFR-01. **Verified.**
- **Evidence:**
  - `kernel.py:1208-1209` starts an Engineer turn at SENSITIVE, and `router.py:359-361` drops
    providers below that ceiling.
  - Live `/health/ready`: `hosted1` has ceiling `internal` (`settings.py:508-512`), and `hfspace`
    returns `HTTP 503`.
  - `docs/deployment/control-plane.md:272-276` records the owner accepting this on 2026-11-08 as
    chat-only.
- **Impact:** every Engineer turn ends in 503 `capability_unavailable`. The feature does not
  exist for users today.
- **Fix (D-02):** add a `kind=claude` slot with:
  - `AURA_ALLOW_CLAUDE_INFERENCE=true`;
  - `MAX_DATA_CLASSIFICATION=sensitive` (boot warns, `settings.py:578-581`);
  - `POOLS` including `coder` or a general pool. A `reasoning`-only slot is dropped as
    `pool_mismatch` (`router.py:475-477`).
  - Also add a test pinning Engineer → SENSITIVE, and amend ADR-033, whose default is
    `internal`.
- **Order:** do this only after G-03; enabling Claude before consent ships would send code to
  Anthropic without consent.

### G-02: Production may grant the Engineer to nobody, and the relay is unverified
- **Requirements:** FR-01, NFR-06. **Verified** (code); **unverifiable** live.
- **Evidence:**
  - `AURA_ENGINEER_AGENT_ROLES` defaults empty (`settings.py:1214`). Only `vps-update.sh:277-282`
    fills it.
  - `docs/deployment/control-plane.md` never mentions it.
  - The relay needs `AURA_REDIS_URL` (`container.py:1419-1447`); with the default 4 workers and
    no relay, about 3 in 4 turns miss the desktop.
- **Fix:** set `AURA_ENGINEER_AGENT_ROLES=Developer,Admin,SuperAdmin` on Railway, add it to the
  deploy guide, and confirm there is no "relay off" warning at boot. **Owner action.**

### G-03: No consent mechanism, and shipped privacy copy would become false
- **Requirements:** SEC-02, DATA-01, UX-01, UX-02. **Verified.**
- **Evidence:** no consent model exists in the backend or the client. `32a6735` tells users
  Engineer turns stay self-hosted.
- **Fix (D-03):** a consent record (migration), a refusal in the kernel before routing for the
  Engineer workload, a notice on every client, a withdraw control, the copy and ADR updates.

### G-26: A sensitive-ceiling Claude slot would also receive non-Engineer data
- **Requirements:** SEC-11. **Verified.** Added in PLAN.
- **Evidence:**
  - `DEFAULT_WORKLOAD = WorkloadHint()` has no pool (`llm/workload.py:39`).
  - The router excludes a pooled provider only when a pool *is* requested (`router.py:475-477`).
  - Unclassified generations count as SENSITIVE (ADR-030).
- **Impact:** done as written, D-02 would send unhinted background generations (and any other
  pool-less call) to Anthropic, beyond what the owner agreed to.
- **Fix:** T-1, a per-provider workload allowlist. This must land in the same release as, or
  before, WP6.

## P1

### G-27: Nothing grants the Engineer from a plan, and desktop sign-in refuses customers
- **Requirements:** FR-01 (customers), FR-18. **Verified.** Added in PLAN.
- **Evidence:**
  - `aura_service.py:128-129` grants by role only.
  - #246 only removes `engineer_agent`.
  - `api/v2/auth.py:242-251` ends any non-operator desktop session (F-09).
- **Fix:** T-6, plus an ADR-011 amendment recording that F-09 is narrowed for plan-entitled
  customers.

### G-28: Desktop sign-in is Google-only
- **Requirements:** FR-20. **Verified.** Added in PLAN.
- **Evidence:** `services/google_oauth.py`, `/auth/desktop/login` and `/auth/desktop/exchange`.
- **Fix:** T-7.

### G-04: The daemon's `task_key` escapes the granted folder
- **Requirements:** SEC-03. **Verified.**
- **Evidence:**
  - `task_handler.dart:88-90` does not validate the key.
  - `isolated_task_registry.dart:122` does `p.join(root,'.aura-workspaces',taskKey)`; an absolute
    key replaces the path.
  - An existing directory becomes a bound root with the parent's trust and no containment check
    (`:125-153`).
  - `task_key` is the chat request's free-form `session_id` (`adapters/host_agent.py:299-301`;
    `api/v3.py:232`).
- **Impact:**
  - Today it is reachable only by a crafted request or a compromised backend (the threat ADR-011
    designs against).
  - D-09 turns this path on for every chat.
  - Example: `task_key="/home/user"` → `read_file ~/.ssh/id_rsa` with no approval.
- **Fix:** accept a single safe segment only (refuse absolute paths, `..` and separators), check
  containment after resolve, and validate `session_id` on the backend too.

### G-05: Folder revoke, untrust and grant never reach the connected agent
- **Requirements:** SEC-08. **Verified.**
- **Evidence:**
  - The `TaskHandler` gets `workspaceStore.load()` once per connection (`bin/aura_hostd.dart:177`).
  - The control server reloads and saves the store (`control_server.dart:256-277`).
- **Impact:**
  - A folder the user revoked or untrusted stays writable, with no prompt, until the daemon
    reconnects.
  - A folder granted later cannot be isolated: `createTask` ignores a `null` resolve and writes
    into the live checkout (`task_handler.dart:105-107`, mapped).
- **Fix:** share one live `WorkspaceRoots` (or re-read it per call) between the control server
  and the handler.

### G-06: The trusted tier is a full shell that is labelled as confined
- **Requirements:** SEC-04, SEC-05, UX-04. **Mapped.**
- **Evidence:**
  - Only destructive verbs have their arguments checked (`command_policy.dart:132-171`).
  - `Process.start` without `environment:` (`task_handler.dart:327-331`); the supervisor spawns
    the daemon with the app's full environment (`hostd_supervisor_io.dart:50`).
  - The label says "still confined to this folder" (`host_daemon_page.dart:418`).
- **Impact:** secrets in the environment or in `~` can reach command output, which now goes to
  Anthropic (D-02).
- **Fix:** see D-06.

### G-07: Writes can reach the default branch
- **Requirements:** SEC-06, FR-07. **Mapped.**
- **Evidence:**
  - `github_commit_files` has no default-branch refusal and makes one commit per file
    (`github_client.py:168-200`).
  - A desktop `git push` in a trusted folder runs with no prompt and the user's ambient
    credentials (`git_service.dart:536-544`, `command_policy.dart`).
- **Fix:** see D-07.

### G-08: Nothing caps one user's token spend
- **Requirements:** NFR-02. **Verified.**
- **Evidence:** `llm/quota.py` is per provider only; `llm/admission.py:19` limits per-user
  concurrency only.
- **Impact:** one user can exhaust the shared Claude TPD and lock everyone out, and the cost is
  unbounded below the TPD.
- **Fix (D-05):** a per-user daily counter in Redis, checked before reservation.

### G-09: The IDE is Windows-only, and Linux has no approvals UI
- **Requirements:** FR-10. **Mapped.**
- **Evidence:**
  - `aura_platform.dart:14`.
  - `router.dart:612-626` renders blank off Windows.
  - Daemon approvals render only in the IDE status bar, so on Linux they expire after 5 min.
- **Fix (D-08b):** enable on Linux and verify on a real machine. macOS shows "not supported"
  instead of blank.

### G-10: Isolation is never requested
- **Requirements:** FR-08. **Verified.**
- **Evidence:**
  - 0 hits for `desktop_task_isolation` in `aura_client/lib`.
  - The backend defaults to false (`api/v3.py:253`).
  - The Control Center copy promises it (`control_center_panel.dart:142-143`).
- **Fix (D-09):** send it by default, after G-04.

### G-11: Remote control has no origin, opt-in, folder picker or remote approvals
- **Requirements:** FR-11, SEC-07, UX-06. **Verified** (absence).
- **Evidence:**
  - Mobile and web send no `workspace_root`, so the backend uses the first granted folder
    (`api/v3.py:254-258`).
  - No origin concept exists for turns.
  - Daemon approvals are loopback-only.
- **Fix (D-10, D-10b, D-10c), technical design to settle in PLAN:**
  - Origin is derived from the device-bound token.
  - The backend holds every host-routed side effect of a remote turn in the existing
    confirmation flow.
  - The daemon refuses remote-origin tasks unless its local `remote_control` flag is on.
  - A folder-list endpoint serves the phone's folder picker.

### G-20: No release evidence
- **Requirements:** NFR-03, NFR-04. **Verified.**
- **Evidence:**
  - Backend and hostd CI have not executed since `dc87beb` (CB-44).
  - `main` fails `test_api_reachability::test_the_committed_matrix_is_current` (stale after
    #243; reproduced locally).
  - No GAP-026 end-to-end report exists.
  - `main` is unprotected, so a merge ships to Railway ungated.

## P2

| ID | Finding | Evidence | Fix |
|---|---|---|---|
| G-12 | **Daemon connection states are not actionable.** <br>• 4401 stops the daemon for good, and the UI shows "Reconnecting…" forever. <br>• Any auth exception is mapped to 4401. <br>• The Re-pair button is unreachable in the IDE once the daemon is authenticated against a stale server; the status-bar item has no tap handler. <br>• The pairing dialog sticks on error. | `rpc_client.dart:89-92`; `api/host_agent.py:82-85`; `explorer_panel.dart:104`; `status_bar.dart:78-100`; `host_panel.dart:284-361` (mapped) | Add a `refused` status; send 1011 for server errors; make the status bar tappable; pop the dialog in `catch`. |
| G-14 | **Confirmation and step rendering have defects.** <br>• Pending confirmations are lost on restart. <br>• `confirmation_invalid` offers a Retry that re-sends the message. <br>• An approval re-send carries the wrong persona label. <br>• Step rows are duplicated, and a spinner keeps running. | `chat_repository.dart:550` is the only `addPending` (verified); `:463` appends steps (verified); `step_log_panel.dart:33`; `chat_repository.dart:226-234, 269-272` (mapped) | Rehydrate from `turnMetaJson`; honor `retryable:false`; key steps by `step_id`. |
| G-15 | **Errors and capability changes are handled generically.** <br>• Engineer and Anthropic unavailability show as a generic error. <br>• `agentsProvider` is never refreshed. | `agents_repository.dart:90` (mapped) | Distinct error states; invalidate on resume or pull. |
| G-16 | **Timeouts and cancel leave processes running.** A timeout kills only the shell PID, and cancel does not stop a running command. | `task_handler.dart:373-377, 407-413` (mapped) | Kill the tree (`kill(-pid)`, `taskkill /T`). |
| G-17 | **`.git` write protection can be bypassed.** <br>• Writes to `.git/modules/*`, `config.worktree` and a linked worktree's gitdir file get through. <br>• Allowlisted git in untrusted folders honors `core.fsmonitor` and `diff.external`. <br>• The diagnostics tab runs linters in any folder. | `command_policy.dart:298-313, 75`; `diagnostics_service.dart:21-26` (mapped) | Widen the denylist; pass `-c` overrides to git; gate diagnostics on trust. |
| G-18 | **`pair/approve` does not check `engineer_agent`.** (E2-05) | `api/v2/auth.py:274-285` (mapped) | Refuse with 403. |
| G-21 | **Unbounded memory and reads in the daemon.** The approval queue is never pruned, and `read_file` has no size cap. | `approval_queue.dart:403-430`; `task_handler.dart:250-267` (mapped) | Prune on decision; cap at 2 MB like `/read`. |
| G-13 | **Desktop release packaging is unproven.** Installers are unsigned, and `desktop-package.yml` has never run on a tag. | `docs/status/DESKTOP_APP_STATUS.md` | Run on a tag; O-01 covers the certificate. |

## P3

- **G-22: the docs are stale.**
  - ADR-011 (PTY, consent tiers, restart, pairing screen) and `aura_hostd/README.md`.
  - Daemon code comments, ADR-014 (omits `write_files`), and `docs/status/` (names the apex
    domain).
- **G-23: engineer-run records.**
  - A resumed turn writes new rows, so `pending` and `awaiting_approval` rows are never closed.
  - `github_commit_files` records no SHA.
- **G-24: minor code defects.**
  - `_delegation_grant` has the wrong return annotation.
  - The deadline fallback differs: 600 s vs 960 s (`kernel.py:1628/1565`).
  - `/engineer-daemon/browse` crashes on a deep link.
  - `_revokeRoot` and `_setTrusted` are unguarded.
- **G-25: localization.** The Engineer UI is English-only, and `lib/desktop` is outside the
  hard-coded-text ratchet (see O-02).

## Deferred with server workspaces (DEF-01)

- **G-19 (P1 when GAP-025 resumes):** `HostGitService._run` does not disable repo-local hooks or
  `core.fsmonitor`, and the sandbox can write `.git/hooks` without confirmation. A confirmed
  `git_push` then runs model-written code on the gateway host, with the token in its
  environment. **Verified** (`host_git.py:100-131`).
- **Also:** `clone_repository` and `run_command` can land on different runners when both
  runners exist (mapped).

## Open items (not asked; for the owner)

- **O-01:** a Windows code-signing certificate. Without one, SmartScreen warns on every install.
- **O-02:** whether the Engineer UI needs Arabic or RTL at launch.
- **O-03:** Anthropic data-processing terms (training on API data, retention) to cite in the
  consent notice. Confirm them against your Anthropic agreement before writing the notice text.
