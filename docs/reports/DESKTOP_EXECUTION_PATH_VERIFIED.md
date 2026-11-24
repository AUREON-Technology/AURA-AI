# The desktop execution path, verified against the real daemon

Status: **built and proven on Linux, against the real `aura_hostd`.** Not proven on macOS or Windows.

## Why this exists

An audit found that "the actual desktop execution path was not verified". Every earlier test of the
Engineer's `write_file`, `edit_file`, `write_files`, `run_command` and git tools used an in-memory
`SandboxRunner`. None of them started the Dart daemon (`aura_hostd`) or crossed the WebSocket relay
(`/api/v3/host/agent`, `aura_core/adapters/host_agent.py`), so a green suite said nothing about whether
the desktop path works or whether its containment holds.

`aura_backend/tests/e2e/test_desktop_path_real.py` closes that. Run it with
`scripts/real_desktop_path.sh` (it builds the daemon, picks free ports, and sets `AURA_REAL_DESKTOP=1`).
Without that variable every test skips, so the default `pytest` run needs no Dart SDK.

## What ran against what

| Part of the path | In the suite |
|---|---|
| Gateway | **Real.** `aura_service.app`, the production assembly with its real authenticator, real pairing endpoints and real `/api/v3/host/agent` route, served by a real uvicorn on a loopback port. It runs in the test's own event loop so the test can also reach `app.state.aura` (tool registry, kernel). |
| Daemon | **Real.** `aura_hostd` as a child process (the AOT binary), with its own config directory and home. Its environment is deliberately dirty (five canary secrets). |
| Pairing | **Real.** `aura_hostd auth pair` prints a code, the rig approves it at `/api/v3/auth/desktop/pair/approve` with a real operator JWT, the daemon polls and stores the device token the gateway issued. |
| Transport | **Real.** The daemon's `dart:io` WebSocket client to uvicorn's WebSocket stack. |
| Tools | **Real.** `ToolRegistry.execute` for the tool-level tests. `AIKernel.run` for the Engineer-turn tests (policy, planning, desktop routing). |
| Human approvals | **Real daemon, scripted human.** The rig calls the daemon's own loopback control API (`GET /approvals`, `POST /approvals/<id>/respond`, bearer token from `control.json`), as the desktop app does. |
| Language model | **Stand-in.** The planner is scripted (as in `tests/unit/test_confirmation_binding.py`). The model is not on the desktop path. |
| Storage | **Stand-in.** The container's in-memory adapters. Not on the desktop path. |
| Filesystem, git, processes | **Real.** A temp folder stands in for the user's folder; real `git` repos in it; processes read back from `/proc`. |

Every assertion is on what happened on disk or in the process table, not on a tool's own report.
Where a test needs to see the daemon's own error code, it reads the frame off the wire (a recording
subclass of `HostAgentSession` keeps a tape of inbound frames; `resolve` still does exactly what it did).

## Results by area

Totals with `AURA_REAL_DESKTOP=1`: **94 collected, 91 passed, 1 skipped, 2 xfailed (strict), 0 failed**, in about
65 s. The skipped test is the approval-expiry check, which waits out the daemon's fixed five-minute timeout;
with `AURA_REAL_DESKTOP_SLOW=1` it ran and **passed** (309 s). Without the gate variable: 94 skipped.

1. **Pair and connect.** The daemon pairs through the real flow, the registry shows the operator's device
   online, the stored token has `scope: host_agent` and is listed as a paired desktop, and the same token is
   refused (401/403) on an ordinary HTTP route. A forged token is closed with 4401. Killing and restarting the
   daemon reconnects with the stored token and no new pairing. Bouncing the gateway (uvicorn shut down, a second
   server started on the same port) makes the daemon reconnect by itself. A `SIGKILL` of the daemon fails an
   in-flight call within seconds, not after its timeout.
2. **Engineer tools through the relay.** `write_file` (Arabic, CJK, emoji, CRLF, overwrite), `edit_file`
   (exactly-one-match, zero and many matches leave the file untouched, CRLF byte-exact, non-UTF-8 file untouched),
   `write_files` (applied; atomic when one operation is invalid or escapes), `run_command` (stdout/stderr/exit
   code, UTF-8 and non-ASCII command lines, invalid bytes, a PDF through `cat`), `git_status`/`git_diff`/
   `git_commit`/`git_checkout_branch` on a real repository (checked with the test's own `git`), and a scripted
   Engineer turn through `AIKernel` that wrote a file and ran a command on the user's folder.
3. **Containment on the daemon.** `../x`, nested `..`, absolute paths, symlinks out of the root (file, directory,
   dangling, and one the agent created itself), writes into `.git/hooks`, `.git/config` and a `.git` file (also
   through a symlink to the hooks directory and through `write_files`/`edit_file`), a workspace outside every
   granted root, a sibling folder whose name starts with the root's, a root revoked mid-task, and a path with no
   granted root. Each was refused with the daemon's own code (`outside_workspace`, `protected_git_hooks`,
   `protected_git_config`, `protected_git_dir_file`) and nothing appeared outside the folder.
4. **Trust tiers and approvals.** An untrusted root runs the read-only allowlist with no prompt, queues anything
   else, and refuses chaining/redirection/substitution outright. An approved command or write runs; a rejected
   one does not (file absent on disk). A write whose target changed after the request is voided. An approval the
   server abandoned (cancel frame) or that was pending when the socket dropped never runs, even after a late
   "yes". An unanswered approval expires after five minutes and never runs. `git push`, in six spellings, always
   asks, even in a trusted root, and a rejected push leaves the bare remote empty; an approved push lands.
   `rm -rf /`, `rm -rf $HOME`, `rm -fr ~`, `rm -r -f /`, `rm --recursive --force /`, `curl ... | sh` and
   `wget ... | sh` are refused with `command_hard_denied`; a PATH shim for `rm` and `curl` logs any invocation and
   the log stayed empty, so they were never started. A destructive verb aimed outside the folder is refused. A
   command naming a path outside a trusted folder is held for the human. Writes to `~/.ssh` did not happen.
5. **Resources.** A timed-out command is killed with its whole process tree (background job, foreground job and
   an orphaned grandchild, checked in `/proc`). Cancelling the tool call, and cancelling an Engineer turn in the
   kernel, kill the running command. Output is capped at exactly 500,000 characters with a truncation note. A 60 MB
   flood and an endless `yes` return in time, the daemon's RSS stays under 400 MB, and the daemon keeps serving.
6. **Environment hygiene.** The positive control reads `/proc/<daemon>/environ` and finds all five canaries. No
   command (`env`, `printenv`, `export -p`, a read of `/proc/self/environ`, a read of the parent's environ) shows
   any canary value or name. The variables a command sees are exactly the daemon's allowlist plus the three the
   daemon's own `stdbuf` wrapper sets (`LD_PRELOAD`, `_STDBUF_E`, `_STDBUF_O`).
7. **Remote control (phone/web origin).** Off by default: a remote `create_task` is refused with
   `remote_control_disabled`, and the switch is a local, token-gated call the backend cannot make. Once on, every
   `run`/`write_file`/`write_files` of a remote task needs `confirmed: true` (only a JSON `true` counts; not even
   the read-only allowlist is waived). A confirmed remote call in an *untrusted* root runs without the desk's
   queue. Hard refusals (denylist, `.git` hooks/config, containment, destructive-outside) stay refused when
   confirmed. The registry itself holds a remote turn's call until the person confirms. A desktop task cannot
   waive its own approval with `confirmed`. Switching remote control off stops the next call of a remote task
   already under way. An unknown origin is `invalid_origin`.

## Bugs found, and what was done

| # | Where | Finding | Resolution |
|---|---|---|---|
| 1 | daemon `task_handler.dart` | **Any command printing a byte that is not valid UTF-8 failed the whole call** (`FormatException: Invalid UTF-8 byte`). `cat file.pdf`, `git show` of a binary, a legacy code-page tool. The strict decoder threw in the stream listener, the output was lost, and a still-running command then stalled on a full pipe. | Fixed: output is decoded with `Utf8Decoder(allowMalformed: true)` (U+FFFD) on POSIX; Windows keeps the system decoder. Dart test added. |
| 2 | backend `adapters/host_agent.py` | **A timed-out command looked like a crash.** The daemon appends `command killed after Ns timeout` / `output truncated` only to the aggregated output of its terminal `result`; the streaming path (the only path `run_command` uses) read just the exit code, so the model saw `exit_code=-1` and empty stderr. | Fixed: the unstreamed remainder of the terminal frame's stdout/stderr is yielded before `done`. Unit test added. |
| 3 | daemon `containment.dart` | A dangling symlink (target does not exist) surfaced as a raw `Cannot resolve symbolic links` error with no code. It was refused only by accident, and a dangling link that stays inside the root could not be written through. | Fixed: the link is followed by hand and judged by where it leads (outside: `outside_workspace`; inside: allowed; loop: violation). Dart test added. |
| 4 | daemon `bin/aura_hostd.dart`, `task_handler.dart`, `control_server.dart` | **Stopping the daemon (SIGTERM, `POST /shutdown`) left the agent's commands running as the user.** | Fixed: the daemon kills every running command tree before exiting. A dropped socket still does not (deliberate, INT-007). Dart test added. |

No policy was weakened. Every fix makes something refuse more clearly, report more, or clean up more.

## Design limits, measured and recorded as strict `xfail`

* A child that starts its own session (`setsid`) leaves the command's process group and survives a timeout.
  Closing it needs cgroups or job objects, which a user-space daemon does not have portably.
* A daemon killed with `SIGKILL` (crash, OOM killer, power loss) cannot clean up; the commands it started are
  orphaned and keep running. A graceful stop is covered (bug 4). Closing this needs a parent-death signal in the
  launcher (Linux-only).

## Observations a reader should know (not changed, not failures)

* `write_file` into `.git/HEAD`, `.git/index`, `refs/` and `objects/` is **allowed**. The guard covers hooks and
  config (the code-execution routes) and a `.git` file, not repository corruption. A test that expected `HEAD`
  to be refused was wrong about the policy, and overwrote HEAD in its scratch repo to prove it.
* Any command with an argument naming an absolute path outside the folder (`head -c 10 /dev/zero`, `ls /etc`,
  `cat /etc/hostname`) is held for the human for up to five minutes, **even in a trusted folder and even when it
  is harmless**, and the model gets no signal while it waits. That is the SEC-05 design; the cost is that a
  command which reads `/dev/zero` or `/proc` looks like a hang. Two of this suite's own first drafts hit it.
* A command runs as the user, so it can read the daemon's own `config.json` (the device token) at a guessable
  path. Environment hygiene stops *inherited* secrets; it cannot hide files the same user owns.
* There is no `POST /agents/stop-all` route in this tree. "Turn stop" is exercised as what a stopped turn does to
  the in-flight call: the tool task is cancelled, the relay sends a `cancel` frame, the daemon kills the command.

## What a reviewer should distrust

* **Linux only.** The daemon's Windows path (no perl process groups, `taskkill`, the system decoder) and macOS were
  not run. Host Daemon CI on those runners is the check for them.
* **One process.** The gateway and the tests share a process and an event loop. The WebSocket is real, but a turn
  was driven by calling the kernel and registry directly, not through `POST /api/v3/chat`, so the HTTP layer in
  front of an Engineer turn (rate limits, body limits, SSE) is not part of this proof.
* **The approver is scripted.** The desktop app's approval UI was not exercised, only the daemon API it calls.
* **The daemon is the AOT build** (`dart compile exe`); `dart run` is supported by the rig but was not used for
  the recorded runs.
* **`websockets`.** The venv used lacked it, though `requirements.lock` pins it (17.0.1). The script installs
  exactly that pin, hash-checked, into a side directory (`aura_backend/.real_desktop_lib`, git-ignored), never
  into the venv.
* **PATH shims** for `rm` and `curl` are in front of the daemon's commands, to make "was it ever started" a thing
  that can be read off a log. They forward to the real binaries except for the two destructive shapes.
* The `strict` xfails only say the limits still hold today. If one starts passing, the test fails and says so.
