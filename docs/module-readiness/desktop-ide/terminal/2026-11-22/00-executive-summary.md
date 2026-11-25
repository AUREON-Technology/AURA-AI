# Desktop IDE Terminal — module readiness, discovery (2026-11-22)

**Mode:** DISCOVER (no application code changed). **Branch:** `fix/kan-owner-tasks`.
**Verdict: NO-SHIP for general availability. CONDITIONALLY usable for a trusted, Windows-only pilot** once G-02 is decided and G-07 has a
CI-run test. No P0 was found. Two P1s remain open (G-02, G-07).

## Scope (working assumption — confirm if wrong)

The integrated terminal of the Desktop IDE: the bottom-panel **Terminal** tab. A user types into an
`xterm` view, the app talks to the loopback `aura_hostd` daemon over a WebSocket, and the daemon runs
the user's own shell in a PTY (Windows ConPTY, Linux/macOS POSIX). Intended users: developers using the
Desktop IDE on their own machine, including Arabic-UI users.

Out of scope: the agent's `run_command` tool (separate path, ADR-011, uses `minimalEnvironment`), the
chat `SandboxTerminalView` (display-only text block), the server-side Docker sandbox, the debug console.

## Detected stack and layers

| Layer | Path | Stack | Commands used |
|---|---|---|---|
| Terminal UI + state | `aura_client/lib/desktop/terminal/`, `desktop/state/state_notifiers.dart` (`TerminalNotifier`) | Flutter, Riverpod `StateNotifier`, `xterm` pkg, Drift | `flutter test` (not run this session) |
| Daemon client | `aura_client/lib/core/hostd/hostd_terminal_client_io.dart` | Dart, `web_socket_channel` | existing test `test/core/hostd/hostd_terminal_client_io_test.dart` |
| Daemon route + manager | `aura_hostd/lib/src/control_server.dart` (`GET /terminal`), `terminal_session_manager.dart` | pure Dart | `dart test` (not run this session) |
| PTY backends | `pty_session.dart` (Win32 ConPTY), `posix_pty_session.dart` (libc FFI + Perl helper) | Dart FFI | probe in scratchpad |

Backend (`aura_backend`) is **not involved**: the terminal never touches the gateway.

## What exists (verified by reading code)

- `start` frame → trust check against granted roots → PTY spawn → `started`/`output`(base64)/`exited`/`error` frames; `input`, `resize`, `close` frames.
- A session cannot outlive its WebSocket (`finally { teardown() }`).
- Trust gate: root must be granted **and** trusted, re-read from disk on each connection; bearer token + loopback-Host check on the route.
- The user's own terminal deliberately inherits the full environment (`process_env.dart` doc; owner decision D-06). Not a finding.
- Client: one auto-started session per trusted workspace, scrollback persisted to Drift and replayed on next open (as an ended session with a Restart button), localized EN/AR, kept LTR under RTL.

## Evidence actually gathered this session

| Check | Result | Environment |
|---|---|---|
| ConPTY under an automation harness (non-interactive token) | Shell output leaked to the harness console, 0 frames, `exit 7` reported as 0. **Discarded as invalid**, matches the warning in `bin/pty_smoke_test.dart`. | Windows 11, harness |
| Real `TerminalSessionManager` + ConPTY + WebSocket, from the app's interactive Terminal panel | `start` works (pid returned); `exit 7` → `exited` frame code **7**; closing the socket **killed a foreground `ping` child** within 4 s. | Windows 11 interactive, Dart 3.12.2 |
| Same run: terminal width honoured, Unicode echo intact, resize-before-start | **Inconclusive** (my regexes did not allow for ConPTY's ANSI repaint). Not reported as pass or fail. | same |
| `utf8.decode(chunk, allowMalformed: true)` on a frame split mid-character | Produces 2 × U+FFFD for a 5-letter Arabic word; a streaming decoder produces 0. | Dart 3.12.2 |

Not run: client `flutter test`, hostd `dart test`, any real app window, macOS, Linux desktop.

## Top risks

1. **G-02 (P1)** Terminal scrollback (up to 500 000 characters per session) is stored unencrypted in the local DB, while chat content in the same DB is encrypted. Terminal output routinely contains secrets.
2. **G-07 (P1, verification)** No automated test covers the client terminal module or the daemon's `/terminal` protocol on any platform; ConPTY has no CI coverage.
3. **G-01 (P2)** Output is decoded per frame, so multi-byte characters split across frames become `�`. The mechanism is proven; end-to-end reproduction is not.

Full list: `04-gap-and-risk-register.md`. Decisions: `02-business-interview-and-decisions.md`.
