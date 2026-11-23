# ADR-039 — Declarative tool policy, review profiles, workspace snapshots, a kill switch, context redaction and a real egress allowlist

**Status:** Proposed (built, not proven) · **Date:** 2026-11-23 · **Related:** ADR-006 (trust
channels, confirmation), ADR-011 (desktop engineer agent), ADR-013/014 (engineer workspaces,
`workspace_local`), ADR-030 (hosted inference, data classification), ADR-036 (open analysis tools)

## Context

Six rows of the agent-tools inventory were partly built. Each one had a mechanism in the right
place and a missing last step:

| Row | What existed | What was missing |
|---|---|---|
| 32 command allowlist / policy engine | `ToolPolicy` decides by tool *name*, capability, taint, sensitivity; the Dart daemon has its own hard-coded command rules | any rule over a tool's *arguments*, in data an operator can change |
| 34 secrets redaction | tool output and logged arguments are redacted | credentials in retrieved chunks, memories, loaded history, the user's own pasted text, and what session history stores |
| 36 network egress | a tinyproxy allowlist (`deploy/sandbox/filter`) behind an `internal` network; `AURA_SANDBOX_ALLOWED_EGRESS_HOSTS` parsed | the setting was read by nothing; the compose comment said the proxy "forwards only to" it, which was untrue |
| 39 snapshot / rollback | durable workspaces, `git_commit` | a way for the agent to save and return to a state that is not a commit |
| 62 review policy | one confirmation policy for everyone | a per-user choice of how much to be asked |
| 90 kill switch | per-call cancel frames to the desktop | one call that stops everything a user's agent is doing |

The standing constraints of this change: backend and API only (no client, no `aura_hostd`); every
new behaviour that alters an existing turn is behind a setting that defaults to the old behaviour;
no new tables, no new dependencies; no existing policy check, confirmation, containment or taint
rule weakened.

## Decision

### 1. Declarative rules (`AURA_TOOL_POLICY_FILE`) — `tools/policy_rules.py`

A JSON or YAML (`safe_load`) file of rules, each naming a tool (glob allowed), an argument (a
dotted path; `ops[].path` walks a list), and `deny` / `confirm` / `allow_only` regexes, optionally
scoped by `personas`, `sensitivities` and `host_routed`. `ToolPolicy.evaluate` consults them
**before** its own checks, and a rule can only produce *deny* or *require confirmation*:

- a deny rule wins even over a confirmed call and over every later check;
- a confirm rule is an extra gate: after the base policy has allowed a call, it holds it for the
  user (a call the user has already confirmed passes);
- `allow_only` is a *mismatch → deny or confirm* rule (`on_mismatch`), never a grant; it matches
  with `fullmatch`, because a search would let `pytest; rm -rf /` satisfy `pytest`;
- nothing a rule says can make a denied, capability-less or tool-declared-confirm call easier.

The decision's reason names the rule (`policy_rule_deny:<id>`) and the registry's audit event
carries the `rule` and the `autonomy` profile in its `detail`. An argument named `path` (or
`*_path`) is matched in both its raw and its normalised spelling, so `./.github/x` and
`a/../.github/x` do not slip past `^\.github/`.

**Start-up validation.** `ToolPolicy.from_settings` loads the file when the container is built. A
bad file stops the process, and the message lists *every* problem with its rule index
(`rule[3]: ...`). Unknown keys are errors (a typo such as `"denny"` must not become a rule that
silently does nothing).

**Regexes cannot hang the gateway.** Python's `re` has no timeout and a thread cannot be
cancelled, so safety is structural. A pattern is refused if it is over 256 characters, uses a
back-reference, nests a repeat inside a repeat, repeats an ambiguous alternation (`(a|aa)+`), or
has more than three unbounded repeats. At call time a value longer than the rule's
`max_value_chars` (default 4096) is not scanned: it *fails closed* (a confirmation). The rule that
matters most is the first one: the validator is a heuristic over the parsed pattern, not a proof,
and the value cap is what bounds the worst case it misses.

### 2. Per-user review profile — `tools/autonomy.py`, `ToolContext.autonomy`

`strict`, `standard` (today's behaviour, the default), `autonomous`. The kernel reads the caller's
profile once per turn into `ToolContext.autonomy`; `ToolPolicy` applies it:

- **strict** — every side-effecting call is held for confirmation (including workspace-local ones),
  and tools tagged `network` (`NETWORK_TAG`: `fetch_url`, `get_weather`, `clone_repository`,
  `git_push`, every `github_*`, every bridged MCP tool, `docker_build`, `docker_compose`) are
  *denied*, even when confirmed. Reads that do not reach out run as before.
- **standard** — the policy exactly as it was (`tests/unit/test_autonomy_profile.py` compares it
  with a policy that has no profile support over a matrix of tools and situations).
- **autonomous** — honoured only where the operator set `AURA_AUTONOMY_ALLOW_AUTONOMOUS=true`, and
  otherwise treated as `standard`. It never skips a taint, remote-origin, tool-declared or
  non-workspace-local confirmation. **On today's tool set it waives nothing:** every workspace-local
  tool the policy allows is already prompt-free on the server, and a paired desktop's own approval
  cannot be waived from the backend (`task_handler.dart` ignores `confirmed` for a desktop task).
  The profile exists, is gated, and is proven never to be looser than `standard`; it has no
  observable effect yet. A test says so, so that the day a tool gives it something to waive,
  someone has to decide it is safe.

`GET`/`PUT /api/v3/engineering/policy` read and set the caller's own profile (owner-scoped, a
service key is refused, a change is audited with the old and new value). `AURA_AUTONOMY_DEFAULT`
(`strict` or `standard`) is the profile for a user who chose none.

**Where it is stored, and what that costs.** There is no per-user settings table and a schema
change was out of scope, so the profile lives in the `Cache` port with a ten-year TTL. That port is
best-effort by design: a Redis eviction or outage reads as "no value", and the user falls back to
`AURA_AUTONOMY_DEFAULT`. A user who chose `strict` can therefore silently lose that choice. It can
never become `autonomous` that way (a missing value is `standard`, and `autonomous` needs the
deployment switch too). A durable row is the thing this still needs.

### 3. Context, input and history redaction — `safety/guards.py`, kernel

`SecretGuard(block=False)` (redact, never block) joins the CONTEXT chain after the encoding guard
and before the injection guard, and optionally the INPUT chain. The kernel can also redact what it
writes to session history and what it loads from it. Three switches, all off by default:
`AURA_SAFETY_REDACT_CONTEXT_SECRETS` (chunks, memories, loaded history), `…_INPUT_SECRETS` (the
user's message — the model can then not use a key the user pastes), `…_HISTORY_SECRETS` (what is
persisted).

The context chain is also what ingestion uses, so with the switch on a key in a new document is
stored redacted; a document stored before the switch is redacted at read time. The injection guard
still runs on what remains: a chunk with both a key and an instruction is still quarantined, still
taints the turn. The OUTPUT chain (block on a credential, groundedness, leakage) is unchanged.

### 4. A real egress allowlist — `deploy/scripts/render-sandbox-filter.sh`

`filter(out) = filter(base) + one anchored, regex-escaped line per validated host`. POSIX `sh`,
`sed` and `tr` only. A host must be a bare DNS name (≥ 2 labels, letters/digits/hyphens/dots,
lower-cased); a wildcard, URL, path, port, credential, IP address or over-long label is refused,
naming the offender, and nothing is written. The `sandbox-proxy-filter` compose service (both
files, kept at parity) renders into a named volume before `sandbox-proxy` starts
(`service_completed_successfully`), and `tinyproxy.conf` reads the rendered file. With the variable
unset the output equals the static file byte for byte. The gateway applies the same rules at
start-up (`config/settings.py::egress_host_problem`), and a test runs both implementations over one
corpus and requires them to agree.

### 5. Workspace snapshots — `workspace_snapshot`, `list_workspace_snapshots`, `workspace_restore`

A snapshot is a git commit object under `refs/aura/snapshots/<id>`, built in a throwaway index
(`git add -A` there, `write-tree`, `commit-tree`, `update-ref`), so taking one touches no file, no
index, no HEAD and no branch. It holds tracked and untracked-but-not-ignored files. The newest 20
are kept. Everything runs through the same `sandbox_runner` as the other engineer tools, so it is
the same code in a server workspace and on a paired desktop.

`workspace_restore` is `CONFIDENTIAL` + side-effecting and **not** `workspace_local`, so every
restore is confirmed, under every profile, tainted or not (a test runs the matrix). The handler
holds a second lock: it refuses to discard uncommitted changes unless `TOOL_CALL_CONFIRMED` is
set. It saves the current state as a new snapshot first and aborts if that fails (so a restore can
be undone), removes untracked non-ignored files, makes index and tree equal the snapshot
(`read-tree --reset -u`), and puts the index back to HEAD. HEAD and branches never move. Ignored
files (`node_modules`, `.env`) are left alone. A workspace that is not the root of its repository
is refused — restoring rewrites the whole repository's tree, and a granted subfolder of a larger
repo must not have files outside it rewritten.

### 6. Kill switch — `POST /api/v3/agents/stop-all`

Owner-scoped, idempotent, audited, and not replayed by the idempotency cache. It:

- cancels every turn the caller has in flight **on this process**. `AIKernel` registers each
  top-level turn (`kernel/active_turns.py`) and cancels the asyncio task, so the cancellation lands
  in whatever the turn awaits — a model call, a tool inside `wait_for`, a host-routed call, whose
  `finally` already sends the daemon its `cancel` frame. A stopped `run()` returns a normal failed
  result (`turn_stopped`, HTTP 409 on `/chat`); a stopped stream ends with an `error` frame of that
  kind, so the caller is answered rather than disconnected;
- stops and closes the caller's paired-desktop connection (cancel frames for every pending call,
  then close code 4409 — not 4401, which would make the daemon unpair itself);
- with `{"revoke_devices": true}`, revokes the caller's paired daemons through
  `services.jwt_auth.revoke_device`, attached to the container by `aura_service` because
  `aura_core` does not import `services`. The desktop *app's* own sign-in is left alone.

It returns what it stopped. It reports its own limits: `scope: "this_process"`.

## Consequences

- A deployment that sets nothing behaves as before. Every new behaviour is off, empty or
  `standard`. The exceptions are additive and inert until used: new tools (engineer persona only,
  `engineer_agent` capability), two new routes, the `network` tag on existing tools (read only by
  `strict`), the turn registry (bookkeeping), and `turn_stopped` (only after a stop).
- The kill switch adds one extra task-registry entry and a `try` around each top-level turn.
- A snapshot is disk (objects in `.git`), bounded by the 20-snapshot cap and the workspace quota.

## Alternatives considered

- **Let rules grant.** Rejected: an operator file that can *allow* what the code denies is a second
  authority over the same decision. Rules tighten; the code remains the floor.
- **Time-limit regexes.** Not possible in-process without a subprocess per match.
- **Store the review profile in a new table.** The right end state; out of scope here (a migration).
- **Make `autonomous` skip the daemon's approval.** Not available and not wanted: the daemon's
  consent is the user's own computer's, and the backend must not be able to waive it.
- **Snapshots via `git stash create`.** Cannot hold untracked files and needs a HEAD.

## Not proven

Built and tested through real objects, but **not proven** until the following run on a real host:

- the compose services (`sandbox-proxy-filter` → `sandbox-proxy`) and tinyproxy actually refusing
  an unlisted host and accepting a listed one — needs Docker; the script and its output are tested,
  the containers are not;
- the snapshot scripts inside the sandbox image and, above all, on a paired desktop through
  `aura_hostd`, whose own command policy decides whether `sh -c` with these git commands runs
  unattended;
- a stopped host-routed command actually stopping on a real daemon (the `cancel` frame is sent and
  tested on the wire; what the daemon does with a command already running is Dart code this change
  does not touch);
- the stop switch across several gateway workers (it is process-local; the device revocation
  reaches other workers at their next credential re-check);
- the profile surviving a Redis restart (it does not necessarily; see above).

## Not built

- Client UI for any of it (no caller in `aura_client/` or `aura_hostd/`; the new routes are
  triaged "to wire" in `scripts/api_reachability.py`).
- A durable store for the review profile.
- Revoking held confirmations on stop-all (a confirmation is keyed by a random token, not by user).
- Redaction at ingestion of documents already in the store, and redaction of other places a
  credential can persist (engineer-run transcripts, synced client history).
