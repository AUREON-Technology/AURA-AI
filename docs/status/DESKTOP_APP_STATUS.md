# Desktop app status

**As of 2026-10-28, `main` at `dc87beb`.** Covers the Flutter client `aura_client/` on
Windows, Linux and macOS, plus `aura_hostd/` (the local daemon that lets the Engineer agent work
on the user's real files and terminal, ADR-011). Evidence scale and refresh steps:
[README.md](README.md).

## Summary

There is a lot of desktop code, and none of it is proven end to end. GAP-026 is
`built_not_proven`, and there is no desktop or hostd end-to-end report in `docs/reports/`.

On 2026-10-28, desktop code ran in CI for the first time since 2026-09-11:
- **Linux:** the client analyzes clean, passes 381 tests and builds a release bundle.
- **Windows:** red.
- **hostd on Linux:** passes 220 tests, then hangs.

Nothing is packaged, signed or released for any desktop platform, and macOS is not built in CI at
all.

## Capabilities

| Area | Status | Evidence |
| --- | --- | --- |
| Linux client build | TESTED (build only) | Client CI 40719101490: `build-linux` produced `build/linux/x64/release/bundle/aura_client`; not packaged |
| Windows client build | BUILT; CI red (non-gating) | `build-windows` is `continue-on-error`. 371 passed, 10 failed, all in `test/core/security/path_containment_test.dart` (8.3 short path `RUNNER~1` vs the long path). The build step never ran. |
| macOS build | BUILT, unverified | No CI job. `macos/Runner/Release.entitlements` turns on the app sandbox without `com.apple.security.network.client`, so a sandboxed build likely cannot reach the backend (code-read). |
| Desktop login (Windows/Linux) | BUILT; unit-TESTED | Loopback PKCE (`core/auth/desktop_oauth_client_io.dart`) plus `POST /api/v3/auth/desktop/login`. One recorded live run failed with `client_secret is missing` (ADR-011); `34f0fc4` changed that since, but it is unproven end to end. macOS uses `google_sign_in`. |
| Sign-out wipe and revoke | TESTED | `5153394` (F-05), `test/sign_out_wipe_test.dart`. Desktop-only `Desktop*` tables are not wiped. |
| Desktop IDE (explorer, editor, git, debug, diagnostics, palette) | BUILT | Enabled on Windows only (`core/platform/aura_platform.dart:14`). Widget tests run on Linux CI. Phase-0 audit rows are code-read only. |
| Engineer agent panel / host panel / pairing approval | BUILT | `lib/desktop/agent/agent_panel.dart`, `lib/desktop/host/host_panel.dart`, `host_daemon_page.dart:147` |
| hostd: pairing, RPC, approval queue, consent tiers, DAP debug | TESTED (Linux) | Host Daemon CI 40719101516, Linux: 220 passed |
| hostd: containment / command policy | TESTED on Linux; failing on Windows | `627a03d` (F-01 daemon half). On Windows, "rejects the filesystem root" fails because the denylist names only `C:\` and the runner's root is `D:\` (`containment.dart:27-44`). |
| hostd: git and per-task worktrees (D8) | TESTED on Linux; failing on Windows | 8.3 path mismatch, and errno 32 file locks in the fetch-timeout test |
| hostd: PTY (ConPTY, POSIX) | BUILT | The POSIX reader isolate (`PosixPtySession._readLoop`) hangs the Linux test process until the 15-minute CI timeout |
| hostd: supervisor / restart-on-crash | OPEN | `core/hostd/hostd_supervisor_io.dart` has no restart logic (CB-26) |
| Packaging / installer / signing / updater | OPEN | No installer, signing or updater, and hostd is not bundled (CB-26, M8.10). Only local scripts exist (`scripts/deploy_client_desktop.{sh,ps1}`). |
| Desktop Engineer end to end | OPEN | GAP-026; M9 needs a committed real-machine report |

## Build, release and verification

- **Client CI** (`client-ci.yml`, Flutter 3.44.6): `analyze-and-test` and `build-linux` are
  green. `build-windows` is red. The run as a whole is red because `build-android` runs out of
  disk.
- **Host Daemon CI** (`hostd-ci.yml`, Dart 3.11.4): no green run since 2026-09-11.
  - Linux: tests pass, then the PTY isolate hangs until the 15-minute timeout.
  - Windows (`continue-on-error`): 212 passed, 5 failed.
- **Releases:** none for desktop. `release-apk.yml` is Android only.
- **Repository hygiene:** `aura_hostd/aura_hostd` is a tracked Windows executable that cannot be
  reproduced from source. Build logs and `aura_client/scratch_test_*.tmp` are also tracked
  (GAP-010, F-29).
- **Stale docs:** `aura_hostd/README.md` (says there is no PTY and no pairing), and
  `aura_client/README.md` (still the Flutter template).

## Open issues (most severe first)

1. **F-01 part 2 / CB-05.** The server still exempts `workspace_local` side effects from the
   taint check (`aura_core/tools/policy.py:114-115`), and nothing blocks writes to `.git/hooks`.
   The daemon half was fixed in `627a03d`.
2. **F-21 (re-confirmed at HEAD).** Non-streaming `run` returns `''` / `-1`, because
   `HostAgentSession.resolve()` takes the first `stream` frame as the result. Git diff, commit and
   checkout break on a paired desktop.
3. **F-09 / AR-15.** Device tokens:
   - last 30 days and carry full power;
   - the host WebSocket has no `exp` deadline;
   - hostd never re-authenticates;
   - the session registry is per process while `AURA_WORKERS=4`.
4. **Windows defects found by CI (new).**
   - Drive roots other than `C:\` can be granted as workspace roots.
   - 8.3 short-name path mismatches, in the client and in hostd.
   - The hostd Linux PTY test hang.
5. **macOS (new, code-read).** The network entitlement is missing.
6. **Others.**
   - F-26: reconnect backoff resets on server close; close codes are ignored.
   - UX-09 / UX-17: forced `/today` → `/ide` redirect, and `/ide` renders nothing off Windows.
   - F-29 / GAP-010: tracked binaries.
   - CB-26: no restart-on-crash, no signed bundle, no installer.
7. **Stale PRs #78 and #88.** Open drafts last touched in 2026-09, with no merge base with `main`.
   Their features already landed by other routes. Close them as superseded.

## Changed since 2026-10-15

- `627a03d`: hostd untrusted tier hardening (allowlist, path containment, config files written
  `0600`).
- `5153394`: sign-out revokes the refresh token and wipes local data.

## Next steps

1. Fix the `PosixPtySession._readLoop` hang so Host Daemon CI is green on Linux (M8.0).
2. Fix F-21: accumulate `stream` frames until `result`. Add a backend test with a fake daemon.
3. Finish F-01: a routing-aware `workspace_local` exemption and a `.git/hooks` / `.git/config`
   write denylist, plus an amendment to ADR-014.
4. Fix the Windows failures (deny every drive root, canonicalize 8.3 paths), then make the Windows
   jobs gating.
5. F-09 / F-26: a token scope claim, a WebSocket `exp` deadline, hostd re-authentication, and
   close-code handling.
6. Untrack the binaries and build hostd in CI with a checksum. Add the macOS entitlement, or
   declare macOS unsupported.
7. Record the GAP-026 end-to-end session on real Windows and Linux machines, then close #78 and
   #88.

## Unverified

- Desktop login end to end on a real machine.
- Interactive terminals (ConPTY or POSIX).
- Whether a Windows release bundle builds; that step was never reached.
- Anything on macOS.
- Rendering, accessibility and performance.
