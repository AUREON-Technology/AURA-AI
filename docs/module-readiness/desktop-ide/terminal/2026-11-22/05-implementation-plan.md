# Terminal — implementation plan (2026-11-22, for approval; nothing implemented)

Files named are **estimated** until verified during implementation. Order matters: tests first, so later packages are covered.

| WP | Scope | Requirements | Layers / files (est.) | Depends on | Blocks release? |
|---|---|---|---|---|---|
| WP1 | **Test seams and baseline tests.** hostd: a `TerminalSessionManager` protocol test using a fake `PtySessionInterface` (start/input/resize/exit/close, teardown on socket drop, refusals). Client: `TerminalNotifier` tests with a fake `HostdTerminalClient` (output, exit, restart, persist/restore). | FR-001, FR-002, QA-001 | `aura_hostd/test/`, `aura_hostd/lib/src/terminal_session_manager.dart` (injectable PTY factory), `aura_client/test/desktop/` | — | **Yes (G-07)** |
| WP2 | **Stream decode + size handshake.** One streaming `Utf8Decoder` per client; send current size after `started` (backwards compatible with older daemons: it is an ordinary `resize` frame). | FR-006, FR-007 | `state_notifiers.dart`, `hostd_terminal_client_io.dart` | WP1 | No (P2) |
| WP3 | **Encrypt persisted scrollback.** Apply the existing encrypted-text converter to `sessionsJson`, bump the Drift schema version, wipe existing terminal rows in the migration, regenerate Drift code. Truncate on a line boundary. | SEC-001, DATA-001 | `core/database/database.dart` (+ regenerated `.g.dart`), `state_notifiers.dart` | WP1 | **Yes (G-02)** |
| WP4 | **Multi-terminal and close.** Session tab strip, "+", close/kill (calls `closeSession`, sends `close`), end shells on workspace close, daemon cap with a coded error and EN/AR strings. | FR-003, FR-004, FR-005 | `terminal_view.dart`, `state_notifiers.dart`, `terminal_session_manager.dart`, `error_codes.dart`, `*.arb` | WP1, WP2 | No for pilot; yes for GA |
| WP5 | **Measure, then fix, bulk output.** Record frame time and memory for a 50 MB stream; replace per-chunk string concatenation with a bounded chunk buffer only if the numbers justify it. | NFR-001 | `state_notifiers.dart`, `terminal_view.dart` | WP2 | Decided by measurement |
| WP6 | **Docs and hygiene.** Host-panel copy for D-2; accessibility disclosure; delete `bin/pty_smoke_test.dart`; drop the ignored `command` parameter; dispose the old client on `restart`. | SEC-002, UX-001, G-09 | docs, `bin/`, `state_notifiers.dart`, Host panel | — | No |
| WP7 | **Real-machine verification.** Dated checklist on clean Windows 11, Linux desktop and macOS: width, Arabic/Unicode, resize, vim, `git commit`, copy/paste, IME, background child cleanup. Result committed under `docs/reports/`. | QA-001 | docs only | WP1–WP4 | **Yes for any "proven" claim** |

## Status (2026-11-22)

- **WP1 — done locally, uncommitted.** Seams: `TerminalSessionManager(ptyFactory:)`, `TerminalNotifier(clientFactory:)`. Tests: 14 daemon protocol tests, 20 client notifier tests (incl. WP3).
- **WP3 — done locally, uncommitted.** Sealed persistence, fail-closed save, plaintext-row refusal, schema 16 wipe, line-boundary cap. No Drift codegen was needed: the column stays a string and holds the envelope (same precedent as `DesktopUnsavedBuffers.contentEnc`).
- WP2, WP4–WP7: not started.

## Validation commands (project's own)

```powershell
cd aura_hostd; dart pub get; dart analyze --fatal-infos; dart test
cd aura_client; flutter pub get; dart run build_runner build --delete-conflicting-outputs; flutter analyze; flutter test
```

Compatibility: WP2 and WP4 add frames or codes only; older daemons ignore unknown frames and older apps translate unknown codes via the
`message` text. WP3 changes the local DB schema, so it needs a migration test (CLAUDE.md: `build_runner` after touching `database.dart`).

## Release gates

- **Pilot (trusted, Windows):** WP1 and WP3 done; WP7 run on Windows.
- **General availability:** all of the above plus WP4, WP5 decision, and CI actually running the new suites (CI runners have been unreliable since 2026-10-28; run locally and say so if CI is unavailable).
