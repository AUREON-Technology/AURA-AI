# aura_hostd

Local daemon for AURA's desktop Engineer agent. It pairs with the backend's
`/api/v3/host/agent` WebSocket (`aura_backend/aura_core/api/host_agent.py`) and lets the
Engineer persona's `write_file`/`edit_file`/`run_command`/`git_*` tools act on this machine's
real files and terminal instead of the server-side Docker sandbox. See
`docs/architecture/011-desktop-engineer-agent.md` for the full design and what is and is not
implemented yet.

## Status

This is the headless daemon core: the WebSocket client, workspace-root grants, path containment,
a hard command denylist, and a trust-based command policy. It does **not** yet have:

- **PTY / streamed terminal output** — `run_command` currently runs to completion and returns
  full stdout/stderr, not a live stream.
- **Interactive tiered consent** — there is no UI yet to prompt a human per call. Until one
  exists, a workspace is either untrusted (only a small read-only command allowlist runs) or
  explicitly marked `trusted` (`aura_hostd trust <path>`), which allows any command that is not on
  the hard denylist. See `lib/src/command_policy.dart`'s module doc for the reasoning.
- **Desktop pairing auth** — the backend has no pairing-token flow yet either
  (`docs/AURA_UPGRADE_ROADMAP.md` J4), so this daemon expects a bearer token supplied out of band.

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
policy (denylist, allowlist, trust), the filesystem RPC handlers, and the WebSocket wire protocol
end-to-end against a local in-process server — no mocking of `dart:io`. This environment did not
have a Dart toolchain available to run `dart pub get`/`dart test` itself; Client CI
(`.github/workflows/client-ci.yml`) is the actual verification, per this repo's own conventions
for Dart/Flutter changes written without a local toolchain.
