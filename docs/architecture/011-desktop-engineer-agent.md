# ADR-011 — The desktop Engineer agent executes on the user's own machine, not a server sandbox

**Status:** Partially implemented (backend bridge + daemon core + output streaming; no client UI or pairing auth yet — see "Implementation status") · **Date:** 2026-09-08

## Context

The Engineer persona (`aura_core/agents/registry.py`) and its tools (`aura_core/tools/coding.py`
— `write_file`, `edit_file`, `run_command`, `git_*`, GitHub read/write, MCP bridging) already
exist and are already wired through the same planning → execution → `ToolRegistry`/`ToolPolicy`
path every other tool uses. What they act on is the gap: `Container._build_sandbox_runner`
(pre-this-ADR) built a `DockerSandboxRunner` — an ephemeral, network-restricted container **on
the server**, torn down at the end of the turn. The tools say so out loud in their own
descriptions: *"not the user's real files, a scratch directory."*

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

**What's still not built:**

- **Interactive tiered consent.** There is no UI yet to prompt a human per call, so the daemon
  cannot implement "auto for reads, diff-and-approve for writes" as designed. In its place, each
  workspace root is either untrusted (only a small read-only inspection allowlist runs —
  `lib/src/command_policy.dart` — with `find`/`env` deliberately excluded from that allowlist
  despite looking read-only, since both can mutate or execute arbitrary commands without needing
  any of the blocked shell metacharacters) or explicitly marked `trusted`
  (`aura_hostd trust <path>`), which allows anything not on the hard denylist. This is a real,
  disclosed scope cut, not a weakening of the containment guarantee: path containment itself is
  unconditional and applies identically regardless of trust.
- **Windows command execution.** The backend always sends `["sh", "-c", "<command>"]` over the RPC
  (`tools/coding.py::run_command`'s existing behavior, unchanged by this ADR) and the daemon execs
  that argv directly; a Windows machine without a POSIX-compatible shell on `PATH` (no WSL/Git
  Bash) cannot run commands via this path yet. Filesystem operations and workspace grants are
  unaffected — this only blocks `run_command`.
- **Desktop pairing auth.** `services/security.py` still rejects every non-mobile platform at
  attestation. The daemon expects a bearer token supplied out of band
  (`aura_hostd auth set-token`) until the pairing flow this ADR describes actually exists.
- **The Flutter desktop UI** that supervises the daemon (workspace picker, diff review, live
  terminal) — nothing yet drives `aura_hostd` other than its own CLI.

`dart test` covers path containment (including symlink-escape and `..`-traversal attempts, and
that containment does not require the target to already exist), the command-policy allowlist
(including the `find -delete`/`find -exec` and `env`-laundering gaps a naive "starts with a safe
verb" check would have missed, and chained/redirected/substituted commands via allowlisted verbs),
`run`'s `onChunk` output matching the aggregated result exactly, the real 500,000-character
daemon-side output cap (exercised with real output, not a lowered test-only threshold), and the
WebSocket wire protocol end-to-end against a local in-process server, including a `run` call's
`stream` frames arriving before its terminal `result`. This environment had no Dart toolchain to
actually run `dart pub get`/`dart test`/`dart analyze` itself (confirmed: `dart`/`flutter` both
absent) — `.github/workflows/hostd-ci.yml` is the real verification, per this repo's own
conventions for Dart/Flutter changes written without a local toolchain. The backend half is
different: this environment does have a working `pytest`, so every backend claim above (the new
port, the session/runner/composite streaming behavior, `run_command`'s streaming path and its
truncation) was actually run, not asserted — see the previous section.

See `docs/AURA_UPGRADE_ROADMAP.md` for the up-to-date status of what is and is not built.
