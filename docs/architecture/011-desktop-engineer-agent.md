# ADR-011 — The desktop Engineer agent executes on the user's own machine, not a server sandbox

**Status:** Partially implemented (backend bridge + daemon core + output streaming + desktop pairing auth for both the daemon and the app itself + a first workspace-management UI + Linux/Windows desktop build scripts and CI + D5 interactive tiered consent + Windows `run_command` shell resolution + an idle-connection keepalive + honest connection-status reporting distinct from pairing state; live terminal streaming to a client and diff review outside of chat are not built — see "Implementation status") · **Date:** 2026-09-08 (connection-reliability and status-accuracy fixes added 2026-09-26)

## Context

The Engineer persona (`aura_core/agents/registry.py`) and its tools (`aura_core/tools/coding.py`
— `write_file`, `edit_file`, `run_command`, `git_*`, GitHub read/write, MCP bridging) already
exist and are already wired through the same planning → execution → `ToolRegistry`/`ToolPolicy`
path every other tool uses. What they act on is the gap: `Container._build_sandbox_runner`
(pre-this-ADR) built a `DockerSandboxRunner` — an ephemeral, network-restricted container **on
the server**, torn down at the end of the turn. The tools say so out loud in their own
descriptions: *"not the user's real files, a scratch directory."* (ADR-013 later replaced that
ephemeral server path with durable per-(user, repo) workspaces when the sandbox profile is
enabled; this ADR is about moving execution onto the user's machine instead.)

That makes the Engineer persona useful for "write and test a snippet" but not for "be a real
software engineer on any project on my computer" — the ask this ADR answers. Doing that requires
touching the user's actual filesystem and a real terminal, on a real, possibly long-lived
project, from a desktop client.

## Decision

### Execution moves to the user's machine; the port does not change

`aura_core/ports/coding.py::SandboxRunner` was already the exact seam: `create_task` / `run` /
`write_file` / `read_file` / `destroy`, used identically by every tool in `tools/coding.py`
regardless of what implements it. Swapping the implementation changes *where* code runs without
touching `AIKernel`, `ExecutionEngine`, `ToolRegistry`, `ToolPolicy`, or `agents/registry.py` —
proof that the hexagonal boundary (ADR-001) is doing its job.

The desktop app runs a local daemon (`aura_hostd`) that opens an **outbound** WebSocket to
`/api/v3/host/agent` — outbound so a machine behind NAT/no forwarded port still works, the same
reason `voice/live` is a server-push channel rather than the reverse. The server then calls
*into* that connection — `adapters/host_agent.py::HostAgentSandboxRunner` — exactly the way
`build_sandbox_tools` already calls into `DockerSandboxRunner`; the tool layer does not know or
care which one it is talking to.

`CompositeSandboxRunner` is what is actually wired into `container.sandbox_runner`: it prefers a
connected desktop session for the calling user and falls back to the server-side Docker sandbox
(or refuses, with a clear message) when none is connected. The choice is made once per task, at
`create_task`, and fixed for that handle's lifetime — a handle does not migrate runners mid-task.

### Containment is enforced by the daemon, never trusted from the server

This is the central safety decision, stated explicitly because it is easy to get backwards: **a
confused or compromised backend must not be able to touch the user's whole filesystem.**
Server-side `ToolPolicy` still evaluates every call exactly as it does today — capability grant,
sensitivity ceiling, taint (below) — before this bridge is ever reached. That gate answers "is
this call authorized at all." It does not and cannot answer "is `/etc/passwd` a path this user
meant to grant" — only the daemon, running on the machine in question, can answer that.

So the daemon independently enforces its own containment: explicit workspace roots the user
grants, path resolution that checks containment *after* resolving symlinks (matching the
discipline `tools/builtin.py::build_file_tools`'s `resolve_within` already uses for the read-only
workspace tools), and a hard denylist no consent tier — including an explicit "trust this
workspace" mode — is allowed to lift. A path outside every granted root is refused, never merely
confirmable; the user adds a root instead of the agent working around the boundary.

### Trust channels and taint are unaffected

`run_command`'s output still carries `tags=("untrusted_content",)` (`tools/coding.py`), so it
still taints the turn exactly as it does against the Docker sandbox today, and a subsequent
side-effecting tool still needs confirmation under a tainted context (ADR-006). A real machine's
command output is not more trustworthy than a container's; if anything the daemon's own consent
tiers are an *additional* layer on top of the existing taint/confirm rule, not a replacement for
it.

### Desktop identity is a pairing token, not attestation

`services/security.py` rejects every platform that is not `android`/`ios` at attestation
(`_reject_attestation("unsupported_platform")`), and there is no Play-Integrity/App-Attest
equivalent for a desktop OS worth relying on. Requiring the same mobile attestation path for
desktop would either block desktop entirely or force weakening attestation everywhere. Instead,
desktop login uses a device-scoped pairing token: the app shows a one-time code, the daemon
exchanges it for a narrower, revocable token — the same shape `/api/v3/auth/web/token` already
uses for the browser's non-attested path. This is a real relaxation of the attestation
requirement for one platform, made deliberately and narrowly rather than silently, and confined
to identity — it grants no bypass of `ToolPolicy`, taint, or the daemon's own containment.

## Consequences

- `container.sandbox_runner` is now always non-`None` (`CompositeSandboxRunner`), so
  `write_file`/`edit_file`/`run_command`/`git_*` register whenever the caller has the
  `engineer_agent` capability, regardless of whether this deployment has opted into
  `sandbox.enabled`. A caller with no connected desktop app and no configured Docker sandbox
  gets a clear error from `create_task`, not an invisible tool — registered-but-unusable is a
  better failure than silently absent.
- The engineer agent's risk profile changes materially for a desktop-paired user: `run_command`
  on a real machine is the most dangerous capability in the product. The daemon-side containment
  described above is the mitigation, and it is a release gate, not a nice-to-have — verified by a
  negative test (a path outside every granted root must be denied even with the daemon's most
  permissive consent mode enabled).
- Nothing changes for a user who never pairs a desktop app: `CompositeSandboxRunner` falls back
  to the pre-existing Docker sandbox behavior exactly as before.

## Implementation status

The backend bridge (`adapters/host_agent.py`, `api/host_agent.py`, the `container.py` rewiring,
and the `SandboxRunner.create_task(user_id=...)` port extension) is implemented and tested
(`tests/unit/test_host_agent.py`, `tests/unit/test_engineer_tools.py`).

The `aura_hostd` daemon (top-level `aura_hostd/`, a pure-Dart package) now exists and implements
its core: an outbound WebSocket client speaking the exact frame protocol
`HostAgentSession`/`api/host_agent.py` expect, workspace-root grants persisted locally
(`aura_hostd grant <path>`), path containment enforced *after* resolving symlinks
(`lib/src/containment.dart`, matching `tools/builtin.py::resolve_within`'s discipline) with a hard
denylist on what can ever be granted as a root (`/`, `/etc`, the home directory itself, etc.), and
the `write_file`/`read_file`/`run_command` RPC handlers themselves.

**Streaming.** `run_command` output now streams end to end rather than only arriving as one blob
after the command exits:

- `ports/coding.py` adds `SandboxOutputEvent` and an optional `StreamingSandboxRunner` Protocol
  (`run_streaming`) -- purely additive over `SandboxRunner.run`, detected via `isinstance` so
  every runner that does not implement it (the Docker sandbox, the in-memory test fake) keeps
  working exactly as before. `CompositeSandboxRunner.run_streaming` is always structurally present
  but degrades per-handle: it delegates to the underlying runner when that runner streams, and
  otherwise replays an ordinary `run` result as one chunk plus `done` -- callers never need two
  code paths.
- `HostAgentSession` gained `call_streaming`, a second RPC calling convention alongside `call`:
  it yields every `stream` frame for a call as it arrives, then the terminal `result` frame, and
  stops. Its wait between frames is an *idle* timeout, not a fixed deadline -- a command that is
  actively streaming output is making progress by definition; the daemon's own per-command
  timeout is what actually bounds how long the command may run.
- `aura_hostd`'s `run` RPC handler now takes an `onChunk` callback; the WebSocket client
  (`lib/src/rpc_client.dart`) wires it, for the `run` method specifically, to send a `{"type":
  "stream", "id", "stream": "stdout"|"stderr", "data"}` frame per chunk as the command produces
  it, before the terminal `result` frame. The daemon enforces its own output cap
  (`_maxOutputCapChars = 500000` in `lib/src/task_handler.dart`) independent of the backend's own
  cap while draining a streamed run (`_RUN_COMMAND_STREAM_CAP_CHARS = 200_000` in
  `tools/coding.py`) -- past either cap, output is still drained from the process (so a full pipe
  cannot block the child and reintroduce the orphan-hang class of bug the timeout-kill path
  already had to fix once) but no longer captured or forwarded.
- `tools/coding.py::run_command` prefers `run_streaming` whenever the wired sandbox runner offers
  it; the tool's own return shape is unchanged (one string), so nothing above the tool layer
  needs to know streaming happened. Backend-side verification is real here, not asserted: this
  environment does have a working Python/pytest toolchain (unlike Dart/Flutter), so every claim
  above was actually run -- `pytest`, `tests/architecture` (including the fitness test that
  every port method be `async def`, extended for `run_streaming`'s deliberate exception, matching
  `LLMProvider.stream`'s existing one), and the legacy `unittest` suite all pass, 13 new tests, no
  regressions.

**PTY.** Not implemented as a real PTY -- no raw terminal mode, no resize, no job control, no
`isatty()` lie to the child process. `lib/src/task_handler.dart::_lineBuffered` wraps a command
with `stdbuf -oL -eL` when that binary is on `PATH` (GNU coreutils; present on Linux, generally
absent on a stock macOS, silently skipped on Windows), which forces line buffering for common
C-based CLI tools (git, grep, make, ...) so streamed output shows real incremental progress
instead of one flush at the end. It does not help an interpreted runtime that decides its own
stdout buffering independent of libc (Python's io layer is the common case) -- a real PTY remains
future work, named here rather than conflated with what `stdbuf` actually provides.

**Still nothing renders any of this.** The streaming data now genuinely flows daemon -> backend,
verified end-to-end over a real WebSocket in `dart test`'s `rpc_client_test.dart` (auth frame,
then interleaved `stream` frames, then the terminal `result`) and in `pytest`'s
`test_host_agent.py`/`test_engineer_tools.py` on the backend side -- but no SSE `step` frame or
client UI consumes it yet. `run_command`'s tool-facing behavior is therefore unchanged from a
model's point of view: still one string, returned once the command finishes. Wiring per-chunk
progress through the kernel's `StepEvent`/SSE machinery to something a client can render is
deferred until Unit 8 actually has a client to render it for -- a protocol nobody consumes is not
progress, and this ADR does not claim otherwise.

**Tool descriptions no longer overclaim isolation.** Before this, `write_file`/`run_command`/
`clone_repository`'s descriptions unconditionally told the model "not the user's real files" and
"the sandbox cannot reach the user's real network" -- true only for the Docker-fallback path, and
actively false once `CompositeSandboxRunner` routes a task to a paired desktop app instead. Since
that routing decision is made per task (whichever runner `create_task` picks for that caller), not
per deployment, a single static description has to describe both possibilities rather than assert
one. `tools/coding.py` now does: each description says the workspace is the user's own machine
when a desktop app is paired, falling back to the server Docker workspace (persistent per
ADR-013 when `AURA_SANDBOX_ENABLED` is on; otherwise unavailable) otherwise --
including keeping the fallback's real network restriction intact rather than dropping it entirely.
Locked in by `tests/unit/test_engineer_tools.py::TestSandboxToolDescriptionsAreHonestAboutRouting`.

**Desktop pairing auth.** Implemented on both sides. `services/device_pairing.py` (new) is the
RFC-8628-shaped device authorization grant this ADR's "Desktop identity is a pairing token, not
attestation" section describes: `start_pairing()` hands back a short human-readable `user_code`
plus an opaque `pairing_id`; `approve_pairing()` requires the approving caller already hold a
valid access token (mobile or web -- proof of an existing authenticated session standing in for
the device attestation a desktop OS has no equivalent for) and binds the pairing to that
identity; `poll_pairing()` is consumed exactly once on its first successful read after approval.
`services/jwt_auth.py::issue_device_token` mints the resulting token: 30 days, no refresh token
(a device token's revocability comes entirely from a `device_id` + owner record checked on every
`verify_access_token` call, not a renewable session the server can decline to renew), and
`revoke_device` checks the requesting caller actually owns the device before revoking, returning
the same failure for "unknown device" and "wrong owner" so the endpoint can't be used to probe
for someone else's device_id. Mounted as `POST /api/v3/auth/desktop/pair/{start,approve,poll}`
and `/desktop/devices/revoke` on the existing `api/v2/auth.py` router. 19 tests in
`test_device_pairing.py` (legacy `unittest` tree, mirroring `test_jwt_auth.py`'s approach) cover
the service layer, token issuance/verification/revocation, and the full HTTP round trip through a
real (unmocked) FastAPI app; full `pytest`, `pytest tests/architecture`, and the legacy suite all
pass with no regressions.

Daemon side: `aura_hostd auth pair [--backend-url <url>]` (`lib/src/pairing_client.dart`, new)
runs the start -> show-code -> poll -> save-token flow using `dart:io`'s `HttpClient` (no new
dependency), printing the code for the person to enter, then polling at the server-advised
interval until approval or the code's `expires_in` elapses -- an abandoned pairing attempt raises
rather than polling forever. On success it saves the token the same way `auth set-token` would
have, so nothing downstream of `DaemonConfig.authToken` needed to change.

**Desktop app login (Unit 7).** Distinct from the pairing flow above, which is for `aura_hostd` --
a headless daemon with no browser to run an OAuth flow in. `aura_client` on desktop does have a
browser, so its own login uses a real RFC 8252 loopback-redirect OAuth 2.0 + PKCE exchange against
Google directly (`aura_client/lib/core/auth/desktop_oauth_client_io.dart`), rather than the
pairing flow's "approve from an already-authenticated session" shape. This needs a *second* real
Google OAuth client -- Google restricts the loopback-IP redirect URI (`http://127.0.0.1:<port>/`)
this flow requires to "Desktop app"-type clients, and the existing `google_client_id` is a "Web
application"-type client used for the mobile/web login path, so it can't be reused for this
redirect. `aura_backend/config.py` gained `google_desktop_client_id` (env
`AURA_GOOGLE_DESKTOP_CLIENT_ID`). A real "Desktop app"-type client was registered 2026-09-10
(Google Cloud project `aura-ai-anas-gnedy`) and is now the hardcoded default, the same way the
mobile/web `google_client_id` above it always has been -- an OAuth client id is not a secret, so
it needs no deploy-time configuration; `aura_client/lib/core/config/config.dart`'s
`googleDesktopClientId` carries the matching id as its own hardcoded default. **Correction found
live, not in review:** RFC 8252 and Google's own guidance say a "Desktop app" client is public
and PKCE alone should be enough at the token endpoint -- but this registered client's token
endpoint actually rejects a secret-less exchange with `invalid_request: client_secret is
missing`. So `desktop_oauth_client_io.dart`'s token exchange sends the client secret alongside
PKCE after all; `config.dart` gained a matching `googleDesktopClientSecret` -- unlike the client
id, deliberately NOT hardcoded (Google's "not meant to be confidential" stance on installed-app
secrets is not the same claim as "safe to commit to git history"), injected only via
`--dart-define=GOOGLE_DESKTOP_CLIENT_SECRET=...` that `scripts/deploy_client_desktop.sh`/`.ps1`
resolve from an untracked `deploy/.env` entry, the same way `BACKEND_URL` is already handled. The
backend never sees or needs this secret -- `verify_identity_token` only checks the client id as
an audience. And
`services/security.py::verify_identity_token` now accepts either client's audience rather than a
single hardcoded one (signature/issuer/expiry checks are unaffected). The resulting real Google
identity has no device attestation behind it (no desktop equivalent exists, same as the pairing
flow), so `POST /api/v3/auth/desktop/login` (new, `api/v2/auth.py`) mints the same kind of
narrower, revocable device-scoped token the pairing flow issues to `aura_hostd`
(`issue_device_token`) rather than a full session pair -- and unlike `/web/token`, it is not
gated to console operators, since every AURA user should be able to log into the desktop app.
Client side, `TokenManager` gained `exchangeDesktopLogin`/`applyPersistedDesktopToken`: with no
refresh token to renew this one, the issued token is persisted client-side (secure storage) for
reuse across app launches instead of going through the normal refresh machinery, and
`AuthNotifier.signInWithDesktopOAuth` plus a platform-gated login button
(`core/auth/platform_login_support.dart`, following
`chat_header_overflow_menu.dart::chatHeaderShowsLiveVoice`'s existing precedent for this kind of
platform allowlist) complete the flow -- `google_sign_in` stays the login path on macOS/mobile/web,
unaffected. The `dart:io`-dependent implementation lives behind the same conditional-export
pattern (`dart.library.ffi`) `core/database/connection/open_connection.dart` already established,
so the web build never pulls it in. Backend verification is real: 8 new tests
(`test_desktop_login.py`), full `pytest`/`tests/architecture`/the legacy suite (100 tests) all
green -- this pass also caught and fixed a pre-existing flaky assertion in Unit 6's own
`test_device_pairing.py` (its user-code regex excluded a character the alphabet actually
includes; confirmed fixed over 30 repeated runs). Client-side verification is written but
unconfirmed locally (12 new tests across `desktop_oauth_client_io_test.dart`,
`platform_login_support_test.dart`, and additions to `token_manager_test.dart`) -- no Dart/Flutter
toolchain in this environment, Client CI is the real check.

**Desktop workspace management UI (Unit 8).** `aura_hostd` had no local surface for a UI to
query -- only its CLI and the outbound WebSocket to the backend -- so this unit adds one:
`aura_hostd/lib/src/control_server.dart` binds a loopback-only HTTP server on an ephemeral port,
writes `{port, token}` (a random per-run secret) to `control.json` in the daemon's config dir so
a same-machine process can find it, and exposes `GET /status`, `GET`/`POST /roots`
(grant/revoke/trust, through the exact `WorkspaceStore` the CLI already uses), `GET /list` and
`GET /read` (a read-only file browser, containment-checked via `WorkspaceRoots.requireContained`
exactly like every other filesystem RPC in this daemon -- no separate path-handling logic that
could bypass it), and a two-phase `POST /pair/start` + `GET /pair/status` pairing flow. The split
into two calls (rather than one call that blocks until approved, which is what `pairing_client
.dart` originally offered) exists because a UI in a *different process* needs the code back
*before* approval happens, not after; `pairing_client.dart` was refactored to expose
`startPairing`/`pollPairingOnce` as separate functions for this, with `pairWithCode` (still what
the CLI's own `auth pair` uses) now a thin wrapper composing both, behavior unchanged.
`bin/aura_hostd.dart`'s `run` command starts the control server immediately, whether or not a
token exists yet, and connects to the backend once one does -- either already, or later via
`/pair/status` reporting approved -- without needing `run` restarted.

On the client, `aura_client/lib/core/hostd/` (new) adds `HostdControlClient` (the HTTP client for
the endpoints above), `HostdSupervisor` (`Process.start`s `aura_hostd run` and captures its
stdout/stderr -- the first process-supervision code in this app; there was no existing precedent
to follow), and Riverpod wiring including a polled status snapshot and a capped in-memory log
buffer. `HostDaemonPage` (new, reachable via a new toolbar action on the existing Engineer
history screen) ties these together: start/stop, pairing (shows the code in a dialog while
polling `/pair/status`), root grant/revoke/trust, and a link into `HostFileBrowserPage` (new) --
a read-only file browser reusing the existing `SandboxFileView` chat widget rather than
duplicating it. The root-trust toggle is labeled for what it actually does ("trusted -- commands
run without per-call confirmation, still confined to this folder"), not as a "YOLO" mode: Unit
5's daemon-side consent tiers were never built, so there is no separate runtime mode a "YOLO"
label would honestly describe yet, only the coarser trusted/untrusted split that already exists.
The three new `dart:io`-touching client files sit behind the same `dart.library.ffi`
conditional-export pattern `core/auth/desktop_oauth_client.dart` (Unit 7) established, so the web
build never pulls in `dart:io`. 18 new daemon-side tests (`control_server_test.dart` plus
additions to `pairing_client_test.dart`) and 8 new client-side tests
(`hostd_control_client_io_test.dart`, `hostd_providers_test.dart`) -- both unverified locally, no
Dart/Flutter toolchain here, `hostd-ci.yml`/Client CI are the real checks.

**Deliberately deferred, not fabricated:** interactive diff review of a *pending* agent change
(already covered where it matters most -- chat's `engineer_confirmation_card` +
`SandboxDiffView`, before a write ever lands -- so this unit's file browser only shows current
on-disk state, not a diff); a real live-streaming terminal panel in the client (Unit 4's
daemon-to-backend streaming still has no client consumer, and building one wasn't this unit's
target); restart-on-crash logic in `HostdSupervisor` (it starts and stops a process, but does not
watch for and recover from a crash); and a bundled `aura_hostd` binary (there is none -- a person
configures the executable path themselves, the same disclosed-until-configured shape
`google_desktop_client_id` already uses on the backend).

**Interactive tiered consent (D5) — now built.** Three tiers, `lib/src/command_policy.dart`: a
hard denylist that no tier can lift (`rm -rf`, fork bombs, `mkfs`, pipe-to-shell, ...); a
read-only inspection allowlist (`ls`/`cat`/`grep`/`git status`/... — `find`/`env` deliberately
excluded despite looking read-only, since both can mutate or execute arbitrary commands without
needing any of the blocked shell metacharacters) that runs unattended in an untrusted workspace;
and, for everything else in an untrusted workspace, a `needsApproval` tier that queues the call
(`lib/src/approval_queue.dart`) for a human to approve or reject through the Desktop IDE, with a
5-minute timeout if nobody answers — wired end-to-end via `status_bar.dart` + `state_notifiers.dart`
+ `hostd_providers.dart`. A workspace explicitly marked `trusted` (`aura_hostd trust <path>`)
still bypasses the allowlist/queue entirely, same as before. Path containment itself remains
unconditional and applies identically regardless of trust or tier.

**Windows command execution — now built.** `tools/coding.py::run_command` still always sends
`["sh", "-c", "<command>"]` over the RPC; on Windows, `lib/src/task_handler.dart` now resolves a
real `sh.exe` (Git for Windows' `Git\bin\sh.exe`/`Git\usr\bin\sh.exe`, or whatever `where sh`
reports) and substitutes it into the argv, rather than failing to spawn at all. **Known
limitation:** output run through that resolved `sh.exe` reports paths in MSYS form
(`/c/Users/...`), which will not string-match the native Windows paths (`C:\Users\...`) that
`write_file`/`read_file`/`edit_file` use directly — an Engineer session correlating a path across
both should not rely on exact string equality. No cheap translation exists in this codebase today.

**Connection-status accuracy and keepalive (2026-09-26).** A live-session bug report ("Engineer falls back to Orchestrator" with the Desktop IDE showing "Host Connected" while every `run_command` failed with "no desktop app is connected") traced to two distinct problems, both now fixed:

- **The daemon's `/status` endpoint conflated "has a stored pairing token" with "has a live socket to the backend."** `HostAgentClient` (`lib/src/rpc_client.dart`) now tracks its own `isConnected` state — `true` only while the outbound WebSocket to `/api/v3/host/agent` is actually open, `false` before the first connect, immediately on `stop()`, and again the instant the server drops the connection. `control_server.dart`'s `/status` reports this as a new `backend_connected` field, alongside the pre-existing `authenticated` field, so the two can now legitimately disagree — exactly the case a reconnect-backoff window after a dropped connection produces. `aura_client`'s `HostdStatusSnapshot` (`core/hostd/hostd_providers.dart`) reads both independently (an old daemon build predating this field defaults `backendConnected` to `false`, never silently `true`); `status_bar.dart`, `host_panel.dart`, and `host_daemon_page.dart` all show a distinct "Reconnecting to server..." state when `authenticated && !backendConnected`, instead of the misleading green "Host Connected" that previously covered both cases.
- **The connection was dropping every ~300 seconds with no application traffic in between.** Confirmed against Railway logs: a paired session cycled connected → disconnected → reconnected on an almost-exact 300s period whenever no tool call happened to be in flight — the classic symptom of an idle WebSocket getting silently dropped by an intermediary with nothing keeping it alive. `HostAgentClient` now sets `dart:io`'s `WebSocket.pingInterval` (default 20s, `rpc_client.dart`) on every connection, which sends real protocol-level ping frames on its own schedule regardless of application traffic; every conformant WS server (uvicorn included) answers with a pong transparently, with no backend change needed. Confirmed live: a paired session held stable past the previous 300s failure point (7+ minutes with zero disconnects) after this shipped.

Both are covered by `dart test`: `rpc_client_test.dart`'s `isConnected` group (4 cases: initial false, true post-connect, false after a server-side drop, false on `stop()`) and `pingInterval` group (default value, and that a custom interval reaches the actual connected socket, not just the constructor argument); `control_server_test.dart` asserts `backend_connected` defaults `false` and can be `true` while `authenticated` stays `false`. `aura_client`'s `hostd_status_test.dart` exercises the parsing logic directly (`backend_connected: true`, `false`, and the field missing entirely) without needing the platform-conditional `HostdControlClient`/provider machinery.

**Windows `run_command` spawn-failure hardening (2026-09-26).** A separate live report showed `run_command` failing with `ProcessException: The system cannot find the file specified` for `sh -c "cat README.md"` on a Windows-paired desktop, despite `where sh` resolving correctly and the same repro succeeding when run directly against `TaskHandler` in isolation. The precise live trigger was not conclusively root-caused (two independent reproduction attempts both succeeded, and the resolved shell path existed on disk) — rather than claim a fix for a bug that couldn't be reproduced, `task_handler.dart` was hardened so a recurrence is immediately diagnosable instead of opaque: `_findWindowsShell()` now validates `where sh`'s output with `File(...).existsSync()` before trusting and caching it (a stale or shadowed `PATH` entry no longer poisons the cache for the daemon's whole lifetime), and `run()`'s `Process.start` call now catches `ProcessException` and rethrows a diagnostic `RpcError` naming the resolved executable, the working directory, and whether each actually exists on disk at the moment of failure. If this recurs, the new error message is the next debugging input, not another blind investigation.

**What's still not built:**

- **Live terminal streaming and interactive diff review in the client.** `HostDaemonPage` (Unit
  8) covers daemon supervision, pairing, and root management, but the daemon-to-backend output
  streaming Unit 4 built still has no client that renders it incrementally, and reviewing what
  the agent is *about to* change stays a chat-time flow (`engineer_confirmation_card`), not
  something this screen does. Approving a pairing code (`POST /api/v3/auth/desktop/pair/approve`)
  still means a raw authenticated API call from *some* already-signed-in client — there is no
  screen anywhere yet for that specific step, on either the daemon's pairing flow or (not
  applicable there, since the app authenticates itself directly) Unit 7's own login.
  `services/security.py`'s own `verify_device_integrity` is untouched by either identity path —
  both pairing and desktop app login are separate, parallel paths through `api/v2/auth.py`'s
  `/desktop/*` endpoints, not a new branch of attestation.

**Desktop OAuth client registered (2026-09-10), then corrected against a real error.** A real
"Desktop app"-type Google OAuth client now backs Unit 7's login — `google_desktop_client_id` is
hardcoded in `aura_backend/config.py`, matching `googleDesktopClientId` hardcoded in
`aura_client/lib/core/config/config.dart`, the same way the pre-existing mobile/web
`google_client_id` has always been hardcoded on both sides. This closes the one deployment-
configuration gap this ADR previously listed as still owed. Running it for the first time
surfaced a real correction to this ADR's own design section (see above): the token exchange
needed a client secret after all — Google's token endpoint rejected a PKCE-only request with
`invalid_request: client_secret is missing`, contradicting the "public client, no secret needed"
assumption this ADR made at design time. `config.dart` gained `googleDesktopClientSecret` --
unlike the client id, deliberately never hardcoded or committed, only injected at build time via
`deploy/.env` (untracked) and `--dart-define` -- and `desktop_oauth_client_io.dart` now sends it.
What remains
unproven is that the rest of the flow (past this specific error) runs end to end on a real
machine (see "Exit criteria" below) — no Dart/Flutter toolchain in this environment has run any
of Unit 7's client code itself, only reviewed it and reacted to the error a real run produced.

`dart test` covers path containment (including symlink-escape and `..`-traversal attempts, and
that containment does not require the target to already exist), the command-policy allowlist
(including the `find -delete`/`find -exec` and `env`-laundering gaps a naive "starts with a safe
verb" check would have missed, and chained/redirected/substituted commands via allowlisted verbs),
`run`'s `onChunk` output matching the aggregated result exactly, the real 500,000-character
daemon-side output cap (exercised with real output, not a lowered test-only threshold), and the
WebSocket wire protocol end-to-end against a local in-process server, including a `run` call's
`stream` frames arriving before its terminal `result`, and (`pairing_client_test.dart`) the
`auth pair` flow against a local fake HTTP server: the happy path (one pending poll, then
approved), a session that expires before approval, a poll that reports `"expired"`, and a
non-2xx response from `/pair/start` -- plus, now, `startPairing`/`pollPairingOnce` directly.
`control_server_test.dart` (Unit 8) covers the control server end-to-end over real HTTP:
token enforcement on every endpoint but `/status`, grant/trust/revoke round-tripping through the
same `WorkspaceStore` the CLI uses (including the denylist refusal), `/list`/`/read` containment
(inside a root succeeds, a `../` escape is refused with 403, an ungranted root 404s), and the
two-phase pairing endpoints reporting pending then approved and calling the daemon's
`onPaired` callback. This environment had no Dart toolchain to
actually run `dart pub get`/`dart test`/`dart analyze` itself (confirmed: `dart`/`flutter` both
absent) — `.github/workflows/hostd-ci.yml` is the real verification, per this repo's own
conventions for Dart/Flutter changes written without a local toolchain. The backend half is
different: this environment does have a working `pytest`, so every backend claim above (the new
port, the session/runner/composite streaming behavior, `run_command`'s streaming path and its
truncation) was actually run, not asserted — see the previous section.

**Packaging and CI (Unit 9).** `hostd-ci.yml` (Dart analyze + test) has gated every `aura_hostd`
change since Unit 3, and `client-ci.yml`'s `analyze-and-test`/`build-android` jobs have gated
`aura_client` since before this ADR. What Unit 9 adds: a `build-linux` job in `client-ci.yml`
(build-only, same unsigned/no-packaging-step pattern `build-android` already uses) so a
Linux-desktop-only regression — a plugin with no Linux implementation, a missing native
dependency — is caught in CI instead of only at release time, plus
`scripts/deploy_client_desktop.sh` (Linux, and macOS when run on Darwin) and
`scripts/deploy_client_desktop.ps1` (Windows) alongside the existing `deploy_client.{sh,ps1}`,
resolving `BACKEND_URL` the same way. Deliberately out of scope, consistent with Unit 8's own
disclosed cut: neither script builds, packages, or bundles `aura_hostd` — a person still builds
it themselves (`dart compile exe`) and configures the resulting binary's path in the client, the
same shape `google_desktop_client_id` already uses on the backend. macOS and Windows CI jobs
(equivalent to `build-linux`) are not added — Linux is the practical desktop target named
throughout this ADR, and GitHub-hosted macOS/Windows runners cost meaningfully more per minute
than the Linux runner `build-android`/`build-linux` already use, which is not a cost this unit's
scope obviously earns; add them if and when macOS/Windows desktop support is actually claimed.

**Deployment constraint: one gateway worker process, or the paired daemon is invisible to most
requests.** `HostAgentRegistry` (`adapters/host_agent.py`) is an in-process dict, held per
`Container` (`container.py`), with no Redis or cross-process fan-out. The daemon's WebSocket is a
single long-lived connection terminated by exactly one uvicorn worker, so only that worker's
registry knows the session exists. Any chat turn served by a different worker resolves no session
and falls through to "no desktop app is connected and no server-side sandbox is configured" --
intermittently, in proportion to the worker count, which reads from the outside like a daemon that
keeps dropping rather than a routing gap. `aura_backend/Dockerfile` defaults to
`--workers ${AURA_WORKERS:-4}`, so any deployment of that image (the Railway Control Plane
included) needs `AURA_WORKERS=1` for the desktop Engineer agent to work reliably. Making this
work under multiple workers means routing a tool call to whichever worker holds the socket
(Redis pub/sub, or a dedicated single host-agent process) -- not built, and not implied by
anything above.

**Version skew between the app and the daemon is now visible instead of silent.** `/status`'s
`backend_connected` field postdates the daemon binary a person may still have installed --
`aura_hostd` ships no auto-update and is built by hand (`dart compile exe`, per Unit 9's disclosed
cut), so a rebuilt `aura_client` routinely polls an older daemon. The client first read that
field as `body['backend_connected'] as bool? ?? false`, which made a missing field
indistinguishable from a reported `false`: a stale daemon that was genuinely connected (the
backend's own `host agent connected` log confirmed the socket was open and registered) pinned
every status surface to "reconnecting..." indefinitely, pointing at a network fault that did not
exist. `HostdStatusSnapshot.backendConnected` is therefore `bool?`, with `null` meaning "the
daemon did not say", rendered as "update aura_hostd" rather than as a reconnect, in all three
surfaces (`status_bar.dart`, `host_panel.dart`, `host_daemon_page.dart`).

See `docs/AURA_UPGRADE_ROADMAP.md` for the up-to-date status of what is and is not built.
