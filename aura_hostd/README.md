# aura_hostd

Local daemon for AURA's desktop Engineer agent. It pairs with the backend's
`/api/v3/host/agent` WebSocket (`aura_backend/aura_core/api/host_agent.py`) and lets the
Engineer persona's `write_file`/`edit_file`/`run_command`/`git_*` tools act on this machine's
real files and terminal instead of the server-side Docker sandbox. See
`docs/architecture/011-desktop-engineer-agent.md` for the full design and what is and is not
implemented yet.

## Status

This is the headless daemon core: the WebSocket client, workspace-root grants, path containment,
a hard command denylist, a trust-based command policy, and streamed `run_command` output. It does
**not** yet have:

- **A real PTY** — `run_command` wraps commands with `stdbuf -oL -eL` when that GNU-coreutils
  binary is on `PATH` (Linux; generally absent on a stock macOS, skipped on Windows) to force line
  buffering for common C-based CLI tools, but this is an approximation, not a real PTY: no raw
  terminal mode, no resize, no job control, and no effect on an interpreted runtime (Python's `io`
  layer, notably) that decides its own stdout buffering independent of libc.
- **Interactive tiered consent** — there is no UI yet to prompt a human per call. Until one
  exists, a workspace is either untrusted (only a small read-only command allowlist runs) or
  explicitly marked `trusted` (`aura_hostd trust <path>`), which allows any command that is not on
  the hard denylist. See `lib/src/command_policy.dart`'s module doc for the reasoning.
- **Desktop pairing auth** — the backend has no pairing-token flow yet either
  (`docs/AURA_UPGRADE_ROADMAP.md` J4), so this daemon expects a bearer token supplied out of band.
- **Anything that renders the streamed output** — it now genuinely flows from this daemon to the
  backend (a `run` call sends `stream` frames as output arrives, then a terminal `result`), but no
  client exists yet to show it to anyone.

## Usage

```bash
dart pub get

# One-time setup
dart run bin/aura_hostd.dart auth set-token <token> --backend-url https://your-aura-backend
dart run bin/aura_hostd.dart grant /path/to/a/project        # untrusted: read-only commands only
dart run bin/aura_hostd.dart trust /path/to/a/project         # allow commands beyond the allowlist

dart run bin/aura_hostd.dart roots                            # list granted workspace roots
dart run bin/aura_hostd.dart run                               # connect and serve until killed
```

Config and granted roots persist under the platform's usual per-user config directory (see
`lib/src/config.dart::resolveConfigDir`).

## RPC methods and remote control

The backend calls `create_task`, `run`, `write_file`, `write_files`, `delete_file`, `rename_file`,
`read_file`, `list_roots` and `init_git` over the WebSocket (`lib/src/task_handler.dart` has the details). `list_roots` needs
no task and returns `{"roots": [{"path", "trusted", "is_git"}], "remote_control": <bool>}`.

**Isolation needs git (WP4, D-13).** An isolated task (`isolate: true` with a `task_key`) works on
its own git worktree of the folder. In a folder that is not a git repository with at least one
commit it is refused with code `isolation_needs_git`, never run in the live folder instead.
`is_git` says where isolation works. "Initialize git here" runs `git init`, `git add -A` and a
first commit with the person's own git identity (`AURA <aura@localhost>` only if git has none):
from the desktop through the control server's `POST /git/init` `{"root": <granted folder>}`, from
the phone or web through `init_git` `{path, confirmed: true}`, which also needs remote control on.
Either returns `{"ok": true, "commit": <sha>}`; a folder that already has a commit is refused with
`already_a_git_repo`.

At most 100 approvals wait on this computer at once (G-21); beyond that a request is refused with
`approval_queue_full`. A decided approval is no longer kept in memory.

**Remote control (WP8)** lets the Engineer drive this computer from the phone or the web. It is
**off** until turned on here: `remote_control` in `config.json`, changed only through the local
control server's `POST /remote_control` `{"enabled": <bool>}` (bearer token, loopback only). `GET
/status` reports it to a caller holding the token. With it off, a `create_task` with
`origin: "remote"` fails with `remote_control_disabled: ...`, and so does the next call of a remote
task already running. A remote task never uses the trusted tier, the read-only allowlist or this
computer's approval queue. Everything refused in every folder (hard denylist, destructive commands
outside the folder, `.git` hooks and config, containment) stays refused. Every other `run` or write
needs `confirmed: true`, meaning the person approved that call on the phone or web; without it the
call fails with `confirmation_required: ...`. A desktop task ignores `confirmed`.

## Rebuilding after a change

There is no CI/CD that redeploys this daemon onto your own machine — every change under
`aura_hostd/lib` or `aura_hostd/bin` requires a manual rebuild before it takes effect locally:

```bash
cd aura_hostd
dart pub get
dart compile exe bin/aura_hostd.dart -o build/aura_hostd.exe   # or the platform-appropriate output name
```

Then confirm the Desktop IDE's Full Settings → "aura_hostd executable" path points at that exact
output file, and **restart** the daemon (Stop, then Start, from the Host panel or Full Settings) —
an already-running process keeps executing the code it started with; it does not hot-reload.
`build/` is gitignored, so a `git pull` never overwrites your local build with someone else's —
you always need to rebuild yourself after pulling a change here.

## Testing

```bash
dart test
```

Tests exercise path containment (including symlink-escape and `..`-traversal attempts), command
policy (denylist, allowlist, trust, and the streaming-specific `find`/`env`/chaining bypasses
found and closed during review), the filesystem RPC handlers, the daemon-side output cap
(exercised with real ~600KB output, not a lowered test-only threshold), and the WebSocket wire
protocol end-to-end against a local in-process server, including a `run` call's `stream` frames
arriving before its terminal `result` — no mocking of `dart:io`. This environment did not have a
Dart toolchain available to run `dart pub get`/`dart test` itself; Host Daemon CI
(`.github/workflows/hostd-ci.yml`) is the actual verification, per this repo's own conventions for
Dart/Flutter changes written without a local toolchain.
