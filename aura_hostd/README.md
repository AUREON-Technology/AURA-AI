# aura_hostd

Local daemon for AURA's desktop Engineer agent. It pairs with the backend's
`/api/v3/host/agent` WebSocket (`aura_backend/aura_core/api/host_agent.py`) and lets the
Engineer persona's `write_file`/`edit_file`/`run_command`/`git_*` tools act on this machine's
real files and terminal instead of the server-side Docker sandbox. See
`docs/architecture/011-desktop-engineer-agent.md` for the full design and what is and is not
implemented yet.

## Status

Implemented: WebSocket client, workspace-root grants, path containment, hard command denylist,
trust + YOLO consent tiers, streamed `run_command` output, desktop pairing
(`auth pair <code>`), and a loopback control plane for the Flutter UI (`127.0.0.1:8765`).

Still deferred:

- **A real PTY** — `stdbuf -oL -eL` line-buffering approximation only (Linux when available).
- **Interactive per-call approve UI** — YOLO / trust / server-confirmed stand in until the
  Flutter prompt queue lands.
- **Windows `run_command` without a POSIX shell** — backend still sends `["sh", "-c", ...]`.
- **Packaged installers** — run via `dart run` / `dart compile exe` for now.

## Usage

```bash
dart pub get

# Pair with a code from the AURA Desktop "Desktop Engineer" screen
dart run bin/aura_hostd.dart auth pair <CODE> --backend-url https://your-aura-backend

# Or paste a bearer token directly (operator / legacy)
dart run bin/aura_hostd.dart auth set-token <token> --backend-url https://your-aura-backend

dart run bin/aura_hostd.dart grant /path/to/a/project        # untrusted: read-only commands only
dart run bin/aura_hostd.dart trust /path/to/a/project         # allow commands beyond the allowlist
dart run bin/aura_hostd.dart yolo on                          # allowlist off; denylist + containment stay

dart run bin/aura_hostd.dart roots
dart run bin/aura_hostd.dart run                               # WS + control plane until killed
```

Config and granted roots persist under the platform's usual per-user config directory (see
`lib/src/config.dart::resolveConfigDir`). Override the control port with
`AURA_HOSTD_CONTROL_PORT`.

## Testing

```bash
dart test
```

Host Daemon CI (`.github/workflows/hostd-ci.yml`) is the verifier when no local Dart SDK is
available.
