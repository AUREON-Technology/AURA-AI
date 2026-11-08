# Desktop app status

**As of 2026-10-28 (evening), `main` at `2337577`.** Covers the Flutter client `aura_client/` on
Windows, Linux and macOS, plus `aura_hostd/` (the local daemon that lets the Engineer agent work
on the user's real files and terminal, ADR-011). Evidence scale and refresh steps:
[README.md](README.md).

## Summary

**Update 2026-11-08 (`main` at `772f9ef`):** production-readiness assessment in
[`docs/reports/desktop-ide-production-readiness-2026-11-08.md`](../reports/desktop-ide-production-readiness-2026-11-08.md).
Verdict: not production-ready. CI is still not executing (CB-44: Client CI #170–#174 and Host
Daemon CI #62–#66 end in 2–4 s with no log). Run locally on Linux with the CI pins:
- client: analyze clean, 544 passed, 4 skipped;
- hostd: analyze clean, 232 passed, and the suite exits in 20 s (the PTY hang no longer
  reproduces);
- PR #185's fixes are on `main`.

A new test pumps the real IDE shell, so it renders at three sizes in LTR and RTL. It found one
low-severity defect: the floating-panel scrim covers the activity bar. The assessment also
flags the per-process `HostAgentRegistry` under the default 4 workers as a functional blocker
for the Engineer agent, not only a security remainder.

There is a lot of desktop code, and none of it is proven end to end. GAP-026 is
`built_not_proven`, and there is no desktop or hostd end-to-end report in `docs/reports/`.

On 2026-10-28, desktop code ran in CI for the first time since 2026-09-11:
- **Linux:** the client analyzes clean, passes 381 tests and builds a release bundle.
- **Windows:** red.
- **hostd on Linux:** passes 220 tests, then hangs.

Since then #185 merged fixes for all three red jobs (Windows 8.3-path canonicalization
`bbc028a`; hostd drive-root denylist and Windows failures `130b58f`; POSIX PTY reader isolate
stopped on dispose `4b1968e`; `rpc_client` connect wait `e512b4e`). **None of them has run in CI**:
Actions stopped assigning runners around 19:50 UTC (CB-44), so the CI rows below still describe
`dc87beb`.

Open PRs #78 (Linux/Windows loopback login) and #88 (D8 per-task worktrees) were closed as
superseded: `main` already carries both, reworked (Unit 7 `desktop_oauth_client_io.dart`; D8/D9
`isolated_task_registry.dart` with the Agent Review panel, which keeps the worktree on disk as the
durable record instead of #88's backend `HostTaskStore` + `reopen_task` RPC).

The live backend's database was migrated to `0016` on 2026-10-30 (CB-43 resolved; see the Backend
file), so the schema no longer blocks desktop sign-in. No real sign-in has been probed since.

Nothing is packaged, signed or released for any desktop platform, and macOS is not built in CI at
all.

ADR-032 (2026-10-31; built and tested locally, not deployed or CI-run) applies to the desktop app
too, because the code is shared. On its next sync after upgrading, the app fetches the account
data key, moves local data off the per-device key, and then shares that key with the user's other
devices. This fixes the multi-device key-loss wipe. It also means the server can read the synced
personal-OS fields, which are no longer zero-knowledge. Production needs migration `0019` before
any of this works.

## Capabilities

| Area | Status | Evidence |
| --- | --- | --- |
| Linux client build | TESTED (build only) | Client CI 40719101490: `build-linux` produced `build/linux/x64/release/bundle/aura_client`; not packaged |
| Windows client build | BUILT; CI red (non-gating) | `build-windows` is `continue-on-error`. 371 passed, 10 failed, all in `test/core/security/path_containment_test.dart` (8.3 short path `RUNNER~1` vs the long path). The build step never ran. |
| macOS build | BUILT, unverified | No CI job. `macos/Runner/Release.entitlements` turns on the app sandbox without `com.apple.security.network.client`, so a sandboxed build likely cannot reach the backend (code-read). |
| Desktop login (Windows/Linux) | BUILT; unit-TESTED | Loopback PKCE (`core/auth/desktop_oauth_client_io.dart`) plus `POST /api/v3/auth/desktop/login`. One recorded live run failed with `client_secret is missing` (ADR-011); `34f0fc4` changed that since, but it is unproven end to end. macOS uses `google_sign_in`. |
| Sign-out wipe and revoke | TESTED | `5153394` (F-05), `test/sign_out_wipe_test.dart`. Desktop-only `Desktop*` tables are not wiped. |
| Desktop IDE (explorer, editor, git, debug, diagnostics, palette) | BUILT | Enabled on Windows only (`core/platform/aura_platform.dart:14`). Widget tests run on Linux CI. Phase-0 audit rows are code-read only. Since 2026-11-08, `test/desktop/desktop_shell_smoke_test.dart` renders the whole shell (forced Windows, 3 sizes, LTR/RTL); passes locally, not CI-run. Client modules `agent`, `control_center`, `debug`, `git`, `host`, `output`, `terminal` have no tests. |
| Engineer agent panel / host panel / pairing approval | BUILT | `lib/desktop/agent/agent_panel.dart`, `lib/desktop/host/host_panel.dart`, `host_daemon_page.dart:147` |
| hostd: pairing, RPC, approval queue, consent tiers, DAP debug | TESTED (Linux) | Host Daemon CI 40719101516, Linux: 220 passed |
| hostd: containment / command policy | TESTED on Linux; failing on Windows | `627a03d` (F-01 daemon half). On Windows, "rejects the filesystem root" fails because the denylist names only `C:\` and the runner's root is `D:\` (`containment.dart:27-44`). |
| hostd: git and per-task worktrees (D8) | TESTED on Linux; failing on Windows | 8.3 path mismatch, and errno 32 file locks in the fetch-timeout test |
| hostd: PTY (ConPTY, POSIX) | BUILT | The POSIX reader-isolate hang is fixed on `main` (`4b1968e`): the Linux suite exits in 20 s locally (2026-11-08). Not CI-run; ConPTY unverified. |
| hostd: supervisor / restart-on-crash | OPEN | `core/hostd/hostd_supervisor_io.dart` has no restart logic (CB-26) |
| Packaging / installer / signing / updater | OPEN | No installer, signing or updater, and hostd is not bundled (CB-26, M8.10). Only local scripts exist (`scripts/deploy_client_desktop.{sh,ps1}`). |
| Desktop Engineer end to end | OPEN | GAP-026; M9 needs a committed real-machine report |

## Build, release and verification

- **Client CI** (`client-ci.yml`, Flutter 3.44.6): `analyze-and-test` and `build-linux` are
  green. `build-windows` is red. The run as a whole is red because `build-android` runs out of
  disk.
- **Host Daemon CI** (`hostd-ci.yml`): no green run since 2026-09-11. Pinned to Dart 3.12.2 on
  2026-10-30 (was 3.11.4), the SDK Flutter 3.44.6 ships, so daemon and client share a toolchain.
  Locally on 3.12.2: `dart analyze --fatal-infos` clean; `dart test` 231 passed, 1 failed (the
  debugpy case, because debugpy was not installed locally; CI installs it). Not CI-run (CB-44).
  - Linux: tests pass, then the PTY isolate hangs until the 15-minute timeout.
  - Windows (`continue-on-error`): 212 passed, 5 failed.
- **Releases:** none for desktop. `release-apk.yml` is Android only.
- **Repository hygiene:** untracked on 2026-10-30 (F-37/F-29, CB-13): `aura_hostd/aura_hostd` (a
  7.3 MB Windows executable with no extension), `aura_hostd/build/aura_hostd.log{,.err}` and
  `aura_client/scratch_test_*.tmp`. They stay in history. `scripts/check_tracked_artifacts.py`
  (run by `repo-hygiene.yml` on every PR and by `tests/unit/test_repo_hygiene.py`) now fails if
  any binary, log or file over 5 MB is tracked again.
- **Stale docs:** `aura_hostd/README.md` (says there is no PTY and no pairing), and
  `aura_client/README.md` (still the Flutter template).

## Open issues (most severe first)

1. **F-01 part 2 / CB-05 and F-21: built and tested locally, not CI-run.**
   - **F-01 part 2:** a host-routed `workspace_local` side effect in a tainted turn now needs
     confirmation, and hostd refuses writes under `.git/hooks/` and to `.git/config` (ADR-014
     amendment).
   - **F-21:** non-streaming `run` now waits for the `result` frame past the `stream` frames.
2. *(see 1)*
3. **F-09 / AR-15: mostly built and tested locally, not CI-run.**
   - Pairing now issues a token scoped to the host-agent socket, and every HTTP route refuses it.
   - The host and voice sockets re-check their token every 60 s, so expiry, device revocation or
     a lost `engineer_agent` closes them with 4401.
   - On 4401, hostd stops and asks to be paired again.
   - Still open:
     - the desktop app's own sign-in token is still a full 30-day session;
     - the session registry is still per process while `AURA_WORKERS=4`.
4. **Windows defects found by CI (new).**
   - Drive roots other than `C:\` can be granted as workspace roots.
   - 8.3 short-name path mismatches, in the client and in hostd.
   - The hostd Linux PTY test hang.
5. **macOS (new, code-read).** The network entitlement is missing.
6. **Others.**
   - F-36 (2026-10-30, TESTED locally on Linux, not run on Windows): the IDE's explorer indent,
     tab-bar and debug-console padding, git panel and floating shell panels now use directional
     (start/end) layout, so an RTL locale mirrors the IDE. The hostd page's Browse and Save
     icon buttons have tooltips. No one has looked at the IDE under RTL, because no widget
     test pumps the IDE shell that way.
   - F-26: built (see 3); backoff now resets only after a connection that stayed up 30 s.
   - UX-09 / UX-17: `/ide` renders nothing off Windows. The forced `/today` → `/ide` redirect
     is fixed on 2026-10-30 (F-34, TESTED locally, not run on Windows): the IDE preference only
     picks the landing screen after sign-in, the IDE palette has "Leave IDE (Open Today)", and
     the Home palette has "Open Desktop IDE".
   - F-29 / GAP-010: tracked binaries.
   - CB-26: no restart-on-crash, no signed bundle, no installer.
7. **Stale PRs #78 and #88.** Open drafts last touched in 2026-09, with no merge base with `main`.
   Their features already landed by other routes. Close them as superseded.

## Fixed on PR #185 (merged; not CI-run)

- **Client, Windows:** `path_containment.dart` canonicalizes the root like the candidate, so 8.3
  short paths match. Client CI run 145 was fully green, including Windows tests and the Windows
  release bundle (the first time that step was reached).
- **hostd:** the POSIX PTY reader isolate is woken through a pipe on dispose, so the Linux suite
  exits (229 passed in 28 s locally); every drive root is denied as a workspace root; git worktree
  paths are canonical; a timed-out command kills its whole tree (`taskkill /T`). Host Daemon CI
  has not run on these commits yet.

## Changed since 2026-10-15

- `627a03d`: hostd untrusted tier hardening (allowlist, path containment, config files written
  `0600`).
- `5153394`: sign-out revokes the refresh token and wipes local data.

## Next steps

1. ~~Fix the `PosixPtySession._readLoop` hang~~ (fixed in `4b1968e`, passes locally). Once CI runs again, get Host Daemon CI green on Linux and Windows (M8.0).
2. Fix F-21: accumulate `stream` frames until `result`. Add a backend test with a fake daemon.
3. Finish F-01: a routing-aware `workspace_local` exemption and a `.git/hooks` / `.git/config`
   write denylist, plus an amendment to ADR-014.
4. Fix the Windows failures (deny every drive root, canonicalize 8.3 paths), then make the Windows
   jobs gating.
5. F-09 remainder: scope or shorten the desktop app's own token, and share the host-agent
   registry across workers.
6. Build hostd in CI with a checksum (the tracked binaries are gone as of 2026-10-30). Add the
   macOS entitlement, or declare macOS unsupported.
7. Record the GAP-026 end-to-end session on real Windows and Linux machines, then close #78 and
   #88.

## Unverified

- Desktop login end to end on a real machine.
- Interactive terminals (ConPTY or POSIX).
- Whether a Windows release bundle builds; that step was never reached.
- Anything on macOS.
- Rendering, accessibility and performance.
