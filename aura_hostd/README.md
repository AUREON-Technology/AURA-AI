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
