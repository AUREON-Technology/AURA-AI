# Desktop app status

**As of 2026-10-28 (evening), `main` at `2337577`.** Covers the Flutter client `aura_client/` on
Windows, Linux and macOS, plus `aura_hostd/` (the local daemon that lets the Engineer agent work
on the user's real files and terminal, ADR-011). Evidence scale and refresh steps:
[README.md](README.md).

**Re-read against commit:** `b2e14c4` (2026-11-08) for every row dated 2026-11-07 or later (their live checks came first; the re-read confirmed that tree holds what they describe); older rows carry the commit they were checked at. Refresh this line whenever a row is re-verified (audit FSA-120).

## Summary

**Update 2026-11-08 (`main` at `772f9ef`):** production-readiness assessment in
[`docs/reports/desktop-ide-production-readiness-2026-11-08.md`](../reports/desktop-ide-production-readiness-2026-11-08.md).
Verdict: not production-ready. Runners are assigned again (Repo Hygiene and Admin CI ran on
11-06/07), but Client CI and Host Daemon CI have had no trigger since 2026-10-31, so no desktop
suite has run in CI since `dc87beb`. Run locally on Linux with the CI pins:
- client: analyze clean, 549 passed, 4 skipped (at `6bc04b6`, with the new shell test; 563
  passed, 3 skipped after the scrim fix);
- hostd: analyze clean, 258 passed (at `6bc04b6`), and the suite exits in 16 s (the PTY hang
  no longer reproduces);
- PR #185's fixes are on `main`.

A new test pumps the real IDE shell, so it renders at all three breakpoints in LTR and RTL. It
found one low-severity defect, since fixed: the floating-panel scrim covered the activity bar, so
reaching another destination took two clicks. Client suite after the fix: 563 passed, 3 skipped.
The assessment also
flagged the per-process `HostAgentRegistry` under the default 4 workers as a functional blocker
for the Engineer agent, not only a security remainder. A cross-worker relay over Redis now
addresses it (ADR-011 amendment, 2026-11-08). It is built and tested against a real Redis,
not seen live.

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
| Windows client build | TESTED (CI, gating) | Green on 2026-11-08: tests and the release bundle on #213 (Client CI run 177), #214 and #215. `continue-on-error` removed (M8), so a red Windows job now fails the run. It blocks a merge only once branch protection requires it (N1, owner). |
| macOS build | BUILT, unverified | No CI job. `macos/Runner/Release.entitlements` turns on the app sandbox without `com.apple.security.network.client`, so a sandboxed build likely cannot reach the backend (code-read). |
| Desktop login (Windows/Linux) | BUILT; unit-TESTED | Loopback PKCE (`core/auth/desktop_oauth_client_io.dart`) plus `POST /api/v3/auth/desktop/login`. Since 2026-11-16 (F-09, owner decision; TESTED locally) only Developer and above may sign in, and a non-operator's existing desktop session ends at its next refresh (ADR-011 amendment). One recorded live run failed with `client_secret is missing` (ADR-011); `34f0fc4` changed that since, but it is unproven end to end. macOS uses `google_sign_in`. |
| Sign-out wipe and revoke | TESTED | `5153394` (F-05), `test/sign_out_wipe_test.dart`. Desktop-only `Desktop*` tables are not wiped. |
| Desktop IDE (explorer, editor, git, debug, diagnostics, palette) | BUILT | Enabled on Windows only (`core/platform/aura_platform.dart:14`). Widget tests run on Linux CI. Phase-0 audit rows are code-read only. Since 2026-11-08, `test/desktop/desktop_shell_smoke_test.dart` renders the whole shell (forced Windows, 3 sizes, LTR/RTL); passes locally, not CI-run. Client modules `agent`, `control_center`, `debug`, `git`, `host`, `output`, `terminal` have no tests. 2026-11-17 (Arabic plan stage 2, unmerged): every IDE string is localized (Egyptian Arabic), and the terminal, editor, output, debug console and diffs stay left-to-right under an Arabic UI; tested headless only. |
| Engineer agent panel / host panel / pairing approval | BUILT | `lib/desktop/agent/agent_panel.dart`, `lib/desktop/host/host_panel.dart`, `host_daemon_page.dart:147` |
| hostd: pairing, RPC, approval queue, consent tiers, DAP debug | TESTED (Linux) | Host Daemon CI 40719101516, Linux: 220 passed |
| hostd: containment / command policy | TESTED on Linux; failing on Windows | `627a03d` (F-01 daemon half). On Windows, "rejects the filesystem root" fails because the denylist names only `C:\` and the runner's root is `D:\` (`containment.dart:27-44`). |
| hostd: git and per-task worktrees (D8) | TESTED on Linux; failing on Windows | 8.3 path mismatch, and errno 32 file locks in the fetch-timeout test |
| hostd: PTY (ConPTY, POSIX) | BUILT | The POSIX reader-isolate hang is fixed on `main` (`4b1968e`): the Linux suite exits in 20 s locally (2026-11-08). Not CI-run; ConPTY unverified. |
| hostd: supervisor / restart-on-crash | BUILT; TESTED locally (M8.8, part) | `core/hostd/hostd_supervisor_io.dart` restarts a crashed daemon with exponential backoff (1 s up to 30 s), never after exit 0 (deliberate shutdown), gives up on exit 64 or after 5 crashes in a row, and the IDE host panel shows "restarting" or "stopped after repeated crashes". `test/core/hostd/hostd_supervisor_io_test.dart` includes a real process killed with SIGKILL and restarted (POSIX). Hot exit (the rest of M8.8) is in its own row below. |
| Editor hot exit / unsaved-buffer recovery | BUILT; TESTED locally (M8.8) | A dirty buffer is backed up about 1 s after the last edit to the local `DesktopUnsavedBuffers` table (schema 14), encrypted with `EncryptionService`, and never stored in the clear. It is dropped on save or discard, and restored dirty when the workspace reopens. The editor says so, and warns when the file changed on disk since. Wiped at sign-out. `test/desktop/hot_exit_test.dart` covers the kill-app case. Not run on a real Windows desktop. |
| Packaging / installer / signing / updater | BUILT, not yet run in CI (M8.10, part) | `.github/workflows/desktop-package.yml` builds a per-user Inno Setup installer for Windows (`aura_client/windows/installer/aura.iss`) and a Linux tar.gz. Each has `aura_hostd` compiled and bundled next to the app, and a `.sha256` file. Signing runs only if the `AURA_WINDOWS_SIGNING_CERT` and `AURA_WINDOWS_SIGNING_PASSWORD` secrets exist; there is no certificate yet, so builds are unsigned. The app now finds its bundled daemon by default (`core/hostd/hostd_bundled_path_io.dart`), and a path saved in Settings still wins. The Linux job's steps ran locally; the Windows job has only run in CI. Updates: a `desktop-v*` tag publishes a GitHub Release with a `desktop-update.json` manifest. The IDE offers newer versions, downloads them checksum-verified, and reinstalls silently; rollback is a manual dispatch (`docs/guides/desktop-releases.md`). The update logic is unit-tested; the publish and rollback jobs have not run yet (no tag), and no installed copy has updated itself. Still open: AppImage/deb. |
| Desktop Engineer end to end | OPEN | GAP-026; M9 needs a committed real-machine report |

## Build, release and verification

- **Client CI** (`client-ci.yml`, Flutter 3.44.6): all four jobs were green on 2026-11-08
  (#213, #214, #215), Windows and Android included. `build-windows` now gates the run.
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
     - ~~the desktop app's own sign-in token is still a full 30-day session~~: FSA-004 (`ba17ad4`)
       now issues a 15-minute access token plus a device-bound refresh token, with in-app device
       revoke. Tested in its authoring session; not CI-run or seen live;
     - ~~the session registry is still per process while `AURA_WORKERS=4`~~: the host-agent
       relay (`adapters/host_agent_relay.py`) reaches a session from any worker when
       `AURA_REDIS_URL` is set. Built and tested (unit and real-Redis `-m load`); not seen live.
       Railway needs its Redis plugin's URL for this, which has not been checked.
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
   - CB-26: no signed bundle, no installer. Restart-on-crash is built (see the capability table).
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
5. F-09 remainder: both halves now built (FSA-004 token; the host-agent relay). Prove them
   live: a paired daemon against a deployment running more than one worker.
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
