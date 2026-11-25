# KAN ticket rewrite: drafts for review (2026-11-21)

These are draft descriptions for the 16 open tickets in the Jira project **KAN (AURA AI Version 3.0.0)**.
Nothing has been written to Jira yet. In each ticket's section, the text after its `---` line
replaces that ticket's description.

## How these drafts were made

**Sources:**
- Code on `main` at `1671021c` (same as `origin/main`), read on 2026-11-21 by four read-only reviewers.
- `docs/reports/agent-architecture-audit-2026-11-21/` (findings A1–C4).
- `docs/reports/AGENTS_AUDIT_2026-11-16.md`.
- `docs/reports/audit/tools-audit-2026-11-16/`.
- `docs/reports/audit-remediation-2026-11-18.md`.

**Live check:** `api.aureon-ai.tech/health/ready` is healthy and serving `aead16c8`.

**Testing:** no new test was written. A few existing suites were run; each ticket says where.

**Labels used in every ticket:**
- CONFIRMED: read in the code, or reproduced.
- SUSPECTED: inferred, not checked.
- "Built", "tested" and "proven" keep their repo meaning. Proven needs live evidence.

**The Done tickets (KAN-2, 3, 4, 5, 6, 8, 9, 18, 19) are left unchanged.**

## Blocker found during the review (not in any ticket yet)

`main` cannot import its tool registry. So the gateway cannot boot, and the backend suite fails
at collection.

**Proposal:** create a new Bug (Highest) and link it from KAN-11 and KAN-15. The same text is also
copied into KAN-11.

Details:
- **The license scanner fails to import (CONFIRMED).**
  - `aura_core/tools/open/dependency_license_compliance_scanner.py:2` imports
    `Any, Dict, List, Optional`, but lines `:123-125` use `Set[...]` and `Tuple[...]`.
  - `import aura_service` therefore fails with `NameError: name 'Set' is not defined`
    (reproduced).
  - Introduced by `fee9dc96`; `aead16c8` still had `Set, Tuple`.
  - The same change restored older method bodies (`:174`, `NameError: 'lic'`) and reverted the
    ADR-036 fixes. 13 `TestLicenseScanner` tests and 4 drift-monitor tests fail.
- **Committed merge-conflict markers (CONFIRMED).**
  `aura_core/tools/open/gpu_resource_scheduler.py:1,15` contains `<<<<<<< HEAD` …
  `>>>>>>> f8b3e927`.
- **Live state.** Production still serves `aead16c8` and is healthy, so Railway appears to be
  keeping the last good deploy. Every merge to `main` after `fee9dc96` will fail to start until
  this is fixed.

---

## KAN-10: System's Tools - Audit

---
**Goal.** Close the open findings of the 2026-11-16 tools audit
(`docs/reports/audit/tools-audit-2026-11-16/README.md`). Every registered tool should work on each
deployment it is offered on, or not be offered there.

**Already fixed since that audit (do not redo):** T-01…T-05, T-11, T-16.

**Open findings (CONFIRMED in code, 2026-11-21):**
- **T-06:** `fetch_url`, `get_weather`, `list_files` and the built-in `read_file` are dead code.
  - They are registered only when `workspace=`, `allowed_http_hosts=` or `enable_weather=` is
    passed, and `container.py:1694` passes none of them.
  - `AURA_HTTP_ALLOWED_HOSTS` is read nowhere in `config/`, although
    `agents/registry.py:144,230` claims it enables `fetch_url`.
  - The client still shows chips for these tools (`tool_call_chip.dart:10-13`), and
    `GET /api/v3/agents` advertises them (KAN-21).
- **T-07:** Engineer tools are reachable under `orchestrator`, which gives every tool a 60 s
  deadline (`kernel.py:1648`). `run_command` declares 605 s.
- **T-08:** the per-turn tool cap is not enforced on the fallback path or across delegation.
- **T-09:** turning tools off does not stop a resumed confirmation.
- **T-10:** MCP tools with colliding names are dropped silently.
- **T-12:** there are no `propose_*` tools over live voice or `/chat`.
- **T-13:** tools are advertised even when no runner exists.
  - Railway has no Docker sandbox.
  - Without a paired desktop, `write_file` and `run_command` fail at call time.
  - `generate_and_run_tests` and `analyze_test_quality` are granted to the Engineer but are off by
    default (`config/settings.py:1663`).
- **T-14:** there is no git push path without Docker.
- **T-15:** date validation in the personal-OS tools is weak.
- **T-17:** `GET /api/v3/tools` has no client caller.
- **T-18:** tool schemas use two naming conventions for the same fields.
- **T-19:** the client does not handle `confirmation_invalid`.
- **Disk quota:** `WorkspaceManager.enforce_disk_quota` (`tools/workspaces.py:277`) has no caller,
  so durable workspaces are never quota-checked.

**Fix.**
- For each finding, wire the tool, gate it to the deployments that can run it, or unregister it.
- For T-13, hide a tool from the planner when its runner is absent, rather than letting it fail at
  call time.
- Add a test: every name in a persona grant is registered by a default container, or is on an
  explicit list of conditionally registered tools together with its enabling setting.

**Acceptance criteria**
- In the tools-audit README, each T-id above is marked FIXED with the test that pins it, or WON'T
  FIX with a one-line reason.
- On a Railway-like config (no Docker, no paired desktop), `GET /api/v3/tools` lists no tool that
  cannot run there.
- The grant-vs-registered test exists. It fails on today's `fetch_url` grant until that is resolved.
- Backend `pytest` and the legacy `unittest` suite pass locally, with counts quoted in the PR.

**Related:** KAN-15 (broken file and command tools), KAN-11 (new tools), KAN-20, KAN-21.

---

## KAN-11: Organizing Tools - Integration

---
**Status note.** Move this back to **To Do** or **In Progress**. The integration is not done, and it
currently breaks `main` (section 1).

**1. Blocker: `main` cannot boot (fix this first).**
- **The license scanner fails to import (CONFIRMED).**
  - `aura_core/tools/open/dependency_license_compliance_scanner.py:2` lacks `Set, Tuple`, which
    lines `:123-125` use.
  - `import aura_service` therefore fails with `NameError`. The chain is `tools/__init__` →
    `builtin.py:37` → `analysis.py:33`.
  - Introduced by `fee9dc96`.
  - The same merge restored older method bodies (`:174`, `NameError: 'lic'`) and reverted the
    ADR-036 fixes. 13 license-scanner tests and 4 drift-monitor tests fail; for example, NaN
    accuracy reports `ok` instead of `invalid_input`.
- **Committed merge-conflict markers (CONFIRMED).** `aura_core/tools/open/gpu_resource_scheduler.py:1,15`
  contains them, so `tests/unit/test_open_tools_expanded.py` fails to collect with a SyntaxError.
- **Live state.** Production still runs `aead16c8` and is healthy. Every later merge will fail to
  start until this is fixed.

**2. The 25 tools added on 2026-11-19 ("feat: add … tool") are dead code (CONFIRMED).** They are
plain synchronous classes under `aura_core/tools/open/`. None is imported, registered as a
`FunctionTool`, or granted to a persona.
- **With some tests** (in the file that currently fails to collect):
  ambiguity_and_clarification_detector, architectural_consistency_checker,
  background_task_manager, mobile_artifact_summarizer, multi_language_ast_analyzer,
  project_conventions_loader, push_notification_dispatcher, remote_session_controller,
  repo_semantic_indexer, scope_and_diff_guard, skills_rules_workflows_loader, ui_visual_verifier.
- **With no tests:** acceptance_criteria_verifier, archive_extractor, artifact_feedback_ingestor,
  artifact_generator, automated_quiz_generator, binary_file_inspector, file_type_detector_router,
  notebook_reader, project_status_qa_tool, source_content_explainer, stale_context_detector,
  structured_data_parser, verification_evidence_collector.

**3. Unsafe to register as they are (CONFIRMED):**
- `background_task_manager.py:31-45` runs `subprocess.Popen` in the gateway with
  `os.environ.copy()`, so the child process inherits the provider keys and the JWT secret.
- `push_notification_dispatcher.py:138-139` POSTs to any URL (an SSRF risk).
- `binary_file_inspector`, `notebook_reader`, `file_type_detector_router` and
  `stale_context_detector` read arbitrary paths on the gateway host.
- `remote_session_controller` duplicates remote control (WP8), with in-memory state only.

**Fix.**
1. Restore `Set, Tuple`, and restore the `dd2acbab` bodies of the license scanner and the drift
   monitor. Resolve the conflict in the GPU file.
2. Add an architecture test that imports every module under `aura_core/tools/open/`, and fails on
   any line starting `<<<<<<< ` or `>>>>>>> `.
3. Triage each of the 25 tools with ADR-036's tiers (`docs/architecture/036-open-analysis-tools.md`),
   and record each decision in ADR-036:
   - Text in, JSON out: wrap it in `tools/analysis.py` (async via `asyncio.to_thread`, with a
     length cap on input) and grant it per persona.
   - Path, process or network access: supersede it, defer it to an `aura_hostd` RPC, or delete it.
4. Extend `tests/architecture/test_superseded_open_tools_unused.py` so every module under `open/`
   must be wrapped, superseded or deferred.

**Acceptance criteria**
- `python -c "import aura_service"` exits 0, and the full `pytest` run collects with 0 errors.
- These pass: `test_open_tools_fixes.py`, `test_analysis_tools.py`,
  `test_superseded_open_tools_unused.py` and `test_open_tools_expanded.py`.
- Every module under `open/` is classified in ADR-036, and a test pins the classification.
- Each newly registered tool has a persona grant and its own test.
- `TestPlannerPromptBudget` (7,000-character ceiling) still passes.
- None of the four unsafe modules is reachable from a persona.

**Related:** KAN-10, KAN-20, KAN-14 (planner prompt size).

---

## KAN-14: The Execution Lifecycle Time - Audit & Debug

---
**Goal.** A planned turn should spend its time running tools, not waiting on scheduling,
sequential writes or an oversized planner prompt. The time spent in each stage should be visible.

**Current problems (CONFIRMED unless marked)**
1. **A batch waits for its slowest step.**
   - `execution/engine.py:234` takes up to `max_parallel` ready steps, and `:273` runs them with
     `asyncio.gather`.
   - A step that becomes ready mid-batch waits for the whole batch to finish.
2. **A 120 s per-pass cap cuts Engineer runs short.**
   - `container.py:479-484` builds `ExecutionEngine` without `deadline_s`, so the 120 s default
     applies (`engine.py:172`).
   - The Engineer's per-call and loop deadlines are 960 s.
   - After any batch longer than 120 s, the remaining steps get `deadline_exceeded`, which
     `agentic_loop.py:60-65` treats as terminal, so there is no replan.
3. **Tool time eats into the answer's time budget.**
   - The turn's generation budget (`llm/deadline.py`) starts at the planner call and keeps running
     while tools execute.
   - With the recommended 45 s budget (`docs/deployment/control-plane.md:305`), a chat turn with
     one 40 s tool can end in `turn_deadline`.
4. **The planner prompt lists every allowed tool** (`planning/engine.py:201`).
   - Measured: a plain caller gets 46 tools, about 14.5K characters (~4.3K tokens). The Engineer
     gets 57 tools, about 17.5K characters (~5.2K tokens).
   - The prompt is sent again on every replan (up to 8), and it is not held to
     `max_prompt_tokens`.
   - SUSPECTED: on the VPS's CPU model (~11 tok/s prefill) it cannot finish within the 180 s
     timeout.
5. **The planner sees no history, and replans see no tool output.** `planning/engine.py:210` builds
   the conversation from the current message alone (`Conversation.of([Message.user(...)])`), and a
   replan at `:415` gets only `"Completed: {description}"`.
6. **Planned non-tool steps are skipped.** There are no handlers for generate, retrieve, recall or
   verify steps (`engine.py:518-527`), so they cost planner tokens and do nothing.
7. **Writes on the step path are sequential.** Each one is awaited in turn:
   - Per tool call: an audit write and an event publish (`tools/registry.py:515-516`).
   - `RunRecorder`: about k+9 to 2k+9 writes per pass for k tool calls (`run_recorder.py:188-330`).
   - The kernel: one Engineer-run write per report, before the answer is generated
     (`kernel.py:1464`).
   - SUSPECTED: the wall-clock cost is not measured.
8. **One tool semaphore per worker is shared by all users** (`tools/registry.py:124`, 4 slots).
9. **The time cannot be broken down.**
   - `StageTiming.tools` and `StageTiming.reasoning` are never set.
   - `timings["planning"]` covers planning, execution and every replan together (`kernel.py:563`,
     `857`).
   - The generation metrics have no `purpose` label (`metrics.py:770`), so planner calls and answer
     calls cannot be told apart.

**Fix**
- **Scheduling:** start each step as soon as its dependencies succeed
  (`asyncio.wait(FIRST_COMPLETED)`), keeping `max_parallel` and `_taint_ordered`.
- **Deadlines:** give `ExecutionEngine` a deadline of `min(loop deadline remaining, setting)`.
  Treat a per-pass deadline that leaves loop time as replannable.
- **Budgets:** separate the planning and tool budget from the answer's generation budget, or pause
  the clock while tools run.
- **Planner prompt:**
  - Send only the persona's grant and the top-k tools relevant to the goal, and no desktop-only
    tools when no desktop is paired.
  - Cache the rendered tool block, and cap the whole prompt.
  - Give replans short excerpts of step results.
  - Allow only `tool` and `respond` step kinds, or implement the others.
- **Writes:** batch the RunRecorder and Engineer-run writes into one per pass. Move the audit and
  event writes to the outbox.
- **Concurrency:** a per-user tool limit, plus a larger global one.
- **Telemetry:** record `tools`, `reasoning`, `replan` and `plan_ms`. Add a `purpose` label
  (planner, answer, reasoning, repair, summary) to `aura_llm_generation_*`.

**Acceptance criteria**
- **Scheduling test** (fake tools, FrozenClock): s1 takes 5 s, s2 takes 1 s, and s3 depends on s2
  and takes 1 s. s3 finishes at 2 s, and the total is 5 s or less.
- **Deadline test:** in an Engineer plan with a 130 s step and a dependent step, the dependent step
  runs (no `deadline_exceeded`) while loop time remains.
- **Budget test:** a chat turn with a 40 s tool and a 45 s budget still answers.
- **Prompt-size test:** the planner prompt is at most 6,000 characters for a plain caller and at
  most 8,000 for the Engineer.
- **Write-count test:** with a counting fake store, there are at most 3 awaited writes per
  execution pass.
- **Dashboard:** it shows `aura_stage_duration_seconds{stage="tools"|"planning"|"replan"}`, and the
  planner's p95 via `purpose="planner"`.

**Depends on:** the KAN-11 import blocker. **Related:** KAN-29, KAN-27.

---

## KAN-15: All broken tools - Debug (parent)

---
**Deadline:** 2026-12-15 (see the comment).

**Root causes behind the subtasks (CONFIRMED):**
1. There is no Engineer `read_file` tool, so the model reads files with `run_command "cat"`. That
   taints the turn, so every later desktop write needs a confirmation. It also garbles non-ASCII
   text on Windows. → KAN-16
2. `run_command` reports success for commands that failed or timed out, and its truncation drops
   stderr and the timeout marker. → KAN-17
3. `edit_file` cannot match text in CRLF files, rewrites line endings in Docker, and can overwrite
   the user's own edits made between its read and its write. → KAN-25
4. Creating a file silently overwrites an existing one, and `/workspace/...` paths double the
   folder in Docker. → KAN-26
5. `main` cannot import the tool registry (see KAN-11, section 1). Nothing can be tested or
   deployed until that is fixed.

**What already works (do not redo):**
- The backend and the daemon agree on RPC names and payloads.
- The timeouts line up: 605 s, plus a 330 s approval window on desktop.
- Cancel frames work.
- Containment, `.git` protection, the remote-control `confirmed` floor and the approval diff all
  pass (hostd `task_handler_test.dart`, 100/100).

**Acceptance criteria**
- KAN-16, KAN-17, KAN-25 and KAN-26 are closed against their own criteria.
- One fake-daemon end-to-end test runs read → edit → run → create in a single Engineer turn, on a
  fixture with CRLF line endings and UTF-8 Arabic text. It passes on Windows and on Linux.
- Host Daemon CI and Backend CI each show an executed, green run for the change. Otherwise, the PR
  states that CI did not run and quotes the local test counts.

---

## KAN-16: read_file Tool - Fix

---
**Problems (CONFIRMED)**
1. **The Engineer has no `read_file` tool.**
   - The grant (`agents/registry.py:269`) lists write, edit, run and git tools only.
   - The prompt says "To read a file in this folder, use `run_command` (e.g. `cat path`)"
     (`prompts/library.py:225`).
   - The read RPC exists end to end, with a 2 MiB cap: `adapters/host_agent.py:392-399` →
     `aura_hostd/lib/src/rpc_client.dart:270` → `task_handler.dart:508-535`. Only `edit_file` uses
     it.
   - A plan step that names `read_file` fails with "Tool 'read_file' is not available".
2. **The built-in `read_file` (`tools/builtin.py:354-376`) is dead code.** It is registered only
   when `workspace` is passed (`builtin.py:643-644`), which `container.py` never does. It would read
   the gateway's own directory anyway.
3. **Reading taints the turn.**
   - `run_command` is tagged `untrusted_content` (`tools/coding.py:607`).
   - So after a `cat`, every desktop `write_file`, `edit_file` or `run_command` needs a confirmation
     (`tools/policy.py:132-135`).
   - In an untrusted folder the daemon asks as well, so each edit needs two approvals.
4. **Non-ASCII text is garbled on Windows.** hostd decodes command output with `SystemEncoding()`
   (`task_handler.dart:655,658`). UTF-8 Arabic text plus "café" came back as `Ù…Ø±Ø­Ø¨Ø§ cafÃ©`
   (reproduced with code page 437).

**Fix**
- Add an Engineer `read_file(path, start_line?, max_lines?)` in `build_sandbox_tools`, over
  `sandbox_runner.read_file`:
  - not side-effecting, gated on `engineer_agent`;
  - line-numbered output, capped at 40K characters with an explicit truncation marker.
- Grant it to `engineer`, and update `prompts/library.py:225` and the `edit_file` description.
- Decide and document its taint rule (as an ADR-006 note). Either:
  - tag it `untrusted_content` and accept the confirmations; or
  - leave it untainted for files inside a granted root on the user's own machine.
- In hostd, decode process output as UTF-8 (`utf8.decoder` with `allowMalformed`). Fall back to the
  code page only when the bytes are not valid UTF-8.

**Acceptance criteria**
- The Engineer's declarations include `read_file`. A kernel test shows a planned `read_file` step
  reaching `TaskHandler.readFile` through a fake daemon.
- A file over 2 MiB returns `ok=False` with the daemon's message.
- On Windows, reading a UTF-8 Arabic file returns it byte-exact, both via `read_file` and via
  `run_command "cat"`.
- A `ToolPolicy.evaluate` test pins the chosen taint rule.

---

## KAN-17: run_command Tool - Fix

---
**Problems (CONFIRMED unless marked)**
1. **Commands that failed or were killed report success.**
   - `tools/coding.py:628-632` returns `"exit_code=…"` as text, so the registry reports `ok=True`
     (`registry.py:517`). That covers exit 1, a hostd timeout (−1, `task_handler.dart:686`) and a
     Docker timeout (124, `docker_sandbox.py:378`).
   - The kernel then records the run as `SUCCEEDED` (`kernel.py:1444-1445`).
   - The history page shows it green (`engineer_history_page.dart:141-143`).
   - The agentic loop does not replan.
2. **Truncation keeps the wrong end of the output.**
   - The registry keeps the head (`registry.py:494-497`), and stderr comes after stdout
     (`coding.py:630-631`).
   - With more than 20K characters of stdout, the stderr and the daemon's "killed after Ns" and
     "output truncated" markers (`task_handler.dart:704-709`) are lost.
   - This contradicts the comment at `coding.py:76-84`.
3. **Output is garbled on Windows** (same cause as KAN-16, problem 4).
4. **Commands and writes can land in different folders.**
   - With no `workspace_root` (phone and web turns), `run` uses the first granted root
     (`task_handler.dart:567-569`).
   - `write_file` and `read_file` pick the longest root that contains the path (`:385`, `:518`;
     `containment.dart:302-315`).
   - With two granted roots, `write_file x.py` followed by `python x.py` can run in different
     folders.
5. **The orchestrator cuts `run_command` off at 60 s** (`kernel.py:1648`), while the tool advertises
   up to 600 s.
6. **SUSPECTED: a flaky test.** `test_call_streaming_idle_timeout_resets_on_each_frame` failed in 1
   of 3 runs on Windows.

**Fix**
- Return `exit_code` and `timed_out` in the result metadata. Mark the step and the EngineerRun
  FAILED when the exit code is non-zero or the command timed out, so the loop can replan.
- Order the output as `exit_code`, then stderr (tail-capped), then stdout. Keep both the head and
  the tail of stdout, with an explicit "… N chars elided …" marker.
- Decode output as UTF-8 in hostd (shared with KAN-16).
- When no root is bound and more than one is granted, the daemon refuses with a named error instead
  of guessing.
- Give `engineer_agent` tools the Engineer's deadline under any persona, or remove them from the
  orchestrator (KAN-10, T-07).

**Acceptance criteria**
- `run_command "exit 3"` marks the step FAILED, records the EngineerRun as `failed`, and has
  `exit_code=3` in its metadata.
- With 50K characters of stdout and `ERR` on stderr, the result contains `ERR` and the elision
  marker.
- On Windows, UTF-8 output is byte-exact.
- With two granted roots and no `cwd`, the daemon refuses with a named error code.
- The idle-timeout test passes 20 out of 20 runs on Windows.

---

## KAN-25: edit_file Tool - Fix

---
**Problems (CONFIRMED)**
1. **CRLF files cannot be edited, and Docker rewrites their line endings.**
   - Matching is an exact `current.count(find)` (`tools/coding.py:532`; hostd
     `task_handler.dart:476`), with no newline normalisation.
   - So a model's `find` that uses only `\n` matches 0 times in a CRLF file.
   - In Docker, `read_text` and `write_text` (`docker_sandbox.py:428,436`) use universal newlines,
     so a successful edit converts the whole file to LF.
2. **Edits can overwrite the user's own changes.**
   - `edit_file` reads in one RPC and writes in another (`coding.py:531,539`), instead of using
     the daemon's atomic find-and-replace (`write_files`, `task_handler.dart:466-482`).
   - In a trusted folder, nothing compares the file against what was read.
   - The daemon's staleness check (`:873-881`) runs only on the untrusted approval path.
3. **A `find` string copied from `cat` output on Windows never matches**, because that text is
   garbled (KAN-16, problem 4). Every edit after a `cat` also needs a confirmation (KAN-16,
   problem 3).
4. **The tool description sends the model to `cat`** (`coding.py:515-517`).

**Fix**
- Implement `edit_file` as a single `write_files([{path, find, replace}])` call: one RPC on desktop,
  and Docker already validates and writes there.
- Normalise CRLF to LF for matching only, then write back in the file's original line-ending style.
  In Docker, read and write bytes rather than universal-newline text.
- Point the description at the new `read_file` (KAN-16).

**Acceptance criteria**
- Editing a CRLF file with an LF `find` succeeds, and the file keeps CRLF, on hostd and on Docker.
- When a test changes the file between the read and the write, the edit is refused rather than
  applied over the change.
- `edit_file` on a desktop issues exactly one RPC (counted by a fake daemon).

---

## KAN-26: create-file Tool - Fix

---
**Scope note.** There is no `create_file` tool. Creating a file goes through one of:
- `write_file` (`tools/coding.py:492-511`), which overwrites and creates parent folders;
- `write_files` with `content` operations (`:542-586`);
- the hostd RPCs `write_file` and `write_files` (`task_handler.dart:375-414`, `:423-506`).

**Problems (CONFIRMED)**
1. **There is no create-only mode.**
   - "Create X" silently replaces an existing X in a trusted folder and in Docker
     (`task_handler.dart:410-412`, `docker_sandbox.py:426-428`).
   - Only the untrusted-folder approval diff would show it.
2. **Paths starting with `/workspace/` double the folder in Docker.**
   - `_resolve_within` strips only the leading `/` (`domain/files.py:35`).
   - The scratch folder is mounted at `/workspace` (`docker_sandbox.py:58,276-277`).
   - So `write_file("/workspace/app.py")` lands at `/workspace/workspace/app.py`.
3. **The byte count is wrong, and hostd writes are not atomic.**
   - "Wrote {len(content)} bytes" counts characters (`coding.py:511`). hostd's real `bytes_written`
     is ignored.
   - hostd writes with `writeAsStringSync`, with no temporary file and rename.
4. **The client confirmation card has no `write_files` case** (`engineer_confirmation_card.dart:177-206`).
5. **`enforce_disk_quota` is never called** (see KAN-10).

**What already works:** containment, `.git` protection, the remote-control `confirmed` floor and
the approval diff (hostd tests 100/100).

**Fix**
- Add `if_exists: "overwrite" | "fail"` to `write_file` and `write_files`, and pass it through to
  hostd. The description tells the model to use `fail` for new files.
- Strip a leading `/workspace/` in `_resolve_within`.
- Return the daemon's byte count. In hostd, write via a temporary file and rename.
- Call `enforce_disk_quota` before writes and commands in durable workspaces.
- Add a `write_files` summary to the confirmation card.

**Acceptance criteria**
- `write_file(if_exists="fail")` on an existing path returns `ok=False` and leaves the file
  unchanged, on both runners.
- `/workspace/a.py` resolves to `<scratch>/a.py` (unit test).
- A workspace over its quota refuses `write_file` with the quota error.
- The confirmation card shows a `write_files` call with each path and operation (widget test).

---

## KAN-20: Review & Edit Your Tools

---
**Scope (so this does not duplicate KAN-10).** KAN-10 fixes how tools behave. This ticket fixes the
tool *surface*: what the model and the user see.

**Model-facing problems (CONFIRMED)**
- `edit_file`'s description points to `cat` (`tools/coding.py:515-517`). `write_file`'s description
  says nothing about create-only.
- Tool schemas use two naming conventions for the same fields (tools audit T-18).
- The planner prompt shows every granted tool, about 4.3K–5.2K tokens (KAN-14, problem 4). The
  descriptions are the main cost.
- `remember_fact`'s rule "only things the user stated" exists only in its description
  (`tools/builtin.py:271-293`). Nothing in code checks it (architecture audit B5).

**User-facing problems (CONFIRMED)**
- The tool chip has icons for tools that are never registered
  (`aura_client/.../tool_call_chip.dart:10-13`).
- The confirmation card has no `write_files` case (`engineer_confirmation_card.dart:177-206`).
- The Engineer history page shows failed commands as green (`engineer_history_page.dart:141-143`;
  the cause is in KAN-17).
- `GET /api/v3/tools` has no client caller (T-17), so the app cannot list which tools are
  available.

**Fix**
- **Tool descriptions:** rewrite each Engineer tool's description as one sentence of purpose, its
  arguments, what it returns, and when not to use it. Use one naming convention for paths, content
  and limits.
- **`remember_fact`:**
  - Require the fact to be supported by the guarded user message, passed in
    `ToolContext.metadata`.
  - Tag its provenance as `user_stated` or `model_inferred`, and rank the two differently at
    recall.
- **Client:**
  - Show chips and confirmation cards for exactly the registered tools, driven by
    `GET /api/v3/tools`.
  - Show failed runs in red.

**Acceptance criteria**
- A test asserts that every registered tool's description is at most N characters and uses the
  agreed field names.
- `remember_fact` with a fact that is absent from the user message is refused, or stored with
  `confidence < 0.5` and tagged `model_inferred`.
- Widget tests cover:
  - one chip per registered tool, and none for unregistered ones;
  - a `write_files` confirmation card;
  - a failed run shown in red.

**Related:** KAN-10, KAN-15, KAN-14.

---

## KAN-21: System's Agents - Audit

---
**Already fine (do not redo):**
- All 8 personas resolve to real prompt templates (`prompts/library.py:538-546`).
- The client lists personas live from `GET /api/v3/agents` and covers all 8 server ids, plus
  generated `bp_*` ids.
- The picker locks personas the caller can't use.
- When the server falls back to another persona, a banner says so (`message_bubble.dart:286-291`).
- Delegation intersects grants correctly, guards depth first, refuses unknown targets by name, and
  inherits taint (`tools/delegation.py:98-158`).

**Problems (CONFIRMED unless marked)**

1. **Some personas don't run their tools on ordinary questions.**
   - Planning is forced only for the Engineer (`max_plan_iterations > 1`, `kernel.py:1631-1640`).
   - Every other persona relies on `needs_plan` (`planning/engine.py:160-179`). It returns `False`
     for any message under 40 characters, unless an English `propose_*` regex matches
     (`tools/personal_os.py:45-101`). For longer messages it needs English multi-step markers
     (`:43-50`).
   - **Examples:** "How much did I spend on food this month?" never calls `list_finance_records`.
     An Arabic request like "ذكّرني بموعد الطبيب غدًا" ("remind me about the doctor's appointment
     tomorrow") never proposes a task. "Yes, add it" has no referent, because the planner sees only
     the current message (`planning/engine.py:470-471`).
   - The Engineer hit the same failure in production, and it was fixed only for the Engineer.
   - Architecture audit A2.
2. **"No tool ran" is enforced only by prompt text.**
   - `PLANNING_FAILED_NOTE` (`kernel.py:322-326`) is added only when planning *raised*. When
     planning was *skipped*, the model gets no signal.
   - `_CORE_IDENTITY`'s rule "Never claim you ran a tool" (`prompts/library.py:26-30`) is the only
     other control.
   - Nothing in code catches an answer of "Done, I added the task" when no action exists.
   - Architecture audit A1.
3. **Some persona grants name tools that production never registers.**
   - `fetch_url` (Research Agent) needs `allowed_http_hosts`, which `container.py:1694` never
     passes, and no setting reads `AURA_HTTP_ALLOWED_HOSTS`.
   - `generate_and_run_tests` and `analyze_test_quality` (Engineer) are off by default
     (`settings.py:1663`).
   - `GET /api/v3/agents` returns the raw grant (`api/v3.py:1694-1696`), so it advertises
     `fetch_url` to clients.
   - The only test checks the other direction (`tests/unit/test_agents.py:328`).
4. **Research Agent's risk ceiling rests on a false premise.** It is set to CONFIDENTIAL because
   "fetch_url is CONFIDENTIAL" (`agents/registry.py:229,234`). The tool is declared PUBLIC
   (`builtin.py:469`), and nothing else in the grant is above PERSONAL.
5. **Telemetry records the requested persona, not the resolved one** (`TurnTelemetry(agent=request.agent)`,
   `kernel.py:1049`, `682`). Quality data attributes turns that fell back to the orchestrator to the
   persona the user asked for.
6. **SUSPECTED: the orchestrator gets Engineer tools without the Engineer's controls.**
   - A caller with `engineer_agent` gets every Engineer tool on an orchestrator turn
     (`allowed_tools=None`, `registry.py:190`).
   - The workload is classed INTERACTIVE (`llm/workload.py:61` keys on `agent == "engineer"`).
   - So the consent check (`kernel.py:1385-1400`), the 216-call tool budget, the replan budget and
     the `engineer` memory scope do not apply.
   - This may be intended (`v3.py:414-417`); it needs a decision.
7. **Prompt and template drift.**
   - The orchestrator template's `user_profile` is never passed (`prompts/library.py:56,65` vs
     `kernel.py:2178-2193`).
   - No prompt tells the orchestrator when to use `delegate_to_agent`. Delegation happens only if
     the planner happens to pick it.
   - Four templates are never used: `task.intent_classification`, `task.memory_extraction`,
     `task.query_expansion`, `task.reflection`. No automatic persona routing exists; the persona is
     the client's choice.
8. **Delegation limits.**
   - Delegating write work always fails, because any sub-turn that needs a confirmation is refused
     (11-16 audit, F8; deliberate, but users aren't told).
   - SUSPECTED: `delegation_deadline_s = 180` is too short for a sub-turn on the VPS CPU model.
   - SUSPECTED: a sub-turn calls `kernel().run()` directly and skips `admit_turn` and plan
     metering.
9. **Long-term memory drifts from what the user said.**
   - A correction can be swallowed: at cosine ≥ 0.94, `remember()` keeps the *old* text and raises
     its confidence (`memory/engine.py:136-151`). Architecture audit B4.
   - Raw, unguarded input goes into history. The guarded text is used for one turn, but
     `request.message` is stored and replayed (`kernel.py:670`, `1023`, `1240-1250`).
     Architecture audit B3.
10. **Nothing is proven live, and eval covers little.**
    - No `verify-engineer-workspaces` report (GAP-025) and no hostd end-to-end report (GAP-026)
      exist.
    - `agent_tasks.jsonl` runs every case as the orchestrator.
    - Eval measures `/chat`, while the app uses `/chat/stream`, which runs a different pipeline
      (architecture audit C4).
    - 11-16 audit: F2, F5.
11. **Smaller issues.**
    - `coding_agent` has only read tools, but its prompt promises working code (F10).
    - The client's `AgentConfigRepository` and its Drift table are dead code (F13).
    - An empty answer with no tool calls ends as SUCCESS and is persisted (`kernel.py:958-976`,
      `1019-1027`; architecture audit C1).

**Fix**
1. **Planning gate:**
   - Arabic patterns in `_INTENT_PATTERNS` and `_MULTI_STEP_MARKERS`, plus a read-intent matcher
     (spent, show, list, how much) for the `list_*` tools.
   - Or replace the regex gate with a cheap classifier call, made only when tools are offered.
   - Pass the last assistant message into the goal.
   - Plan when the previous turn offered an action.
2. **Action claims:** after generation, if the answer claims a completed action and no succeeded
   side-effecting step or pending confirmation exists, append a fixed correction (sent as a
   `revision` frame), or at least add the degraded reason `unbacked_action_claim`.
3. **Grants:**
   - Wire or drop `fetch_url` and the off-by-default tools.
   - Have `/agents` return `allowed_tools ∩ registered`.
   - Set Research Agent's ceiling from its actual tools.
4. **Telemetry:** record `resolved_agent`, keeping `requested_agent` as a separate field.
5. **Orchestrator and Engineer tools:** decide which. Either exclude `engineer_agent` tools from the
   orchestrator, or apply the consent check and the Engineer workload whenever one is planned.
6. **Prompts:**
   - Supply `user_profile`, or remove it.
   - Add one line to the orchestrator prompt about when to use delegation.
   - Wire or delete the four unused templates.
7. **Memory and history:**
   - Store the guarded text in history.
   - On a near-duplicate memory with different text, store the new record and mark the old one
     superseded.
   - Treat an empty answer as `empty_generation`: return fallback text and don't persist it.
8. **Eval:**
   - Add per-persona cases to `agent_tasks.jsonl`, with an `agent` field and a check on the
     resolved agent.
   - Run the gate through `stream`.
   - Produce the GAP-025 and GAP-026 reports.
9. **Cleanup:**
   - Merge, rename or describe `coding_agent` honestly.
   - Delete `AgentConfigRepository`.
   - State the delegation write limit wherever delegation is described.

**Acceptance criteria**
- **Planning gate:** kernel tests show these turns plan and call their tools:
  - `finance_advisor` with "How much did I spend on food this month?" calls `list_finance_records`;
  - `health_companion` calls `list_health_records`;
  - `task_planner` calls `list_tasks`.
- **Arabic and follow-ups:** `needs_plan` returns `True` for at least 5 Arabic imperatives per
  `propose_*` tool, and for "yes, add it" / "نعم أضفها" ("yes, add it") after an offered action.
- **Action claims:** a turn with tools offered and planning skipped, where the model says "I've
  added the task", ends with `degraded_reasons` containing `unbacked_action_claim`. A turn whose
  `propose_task` step succeeded is not flagged.
- **Grant test:** every persona's granted names are registered by a default container, or are on
  an explicit list of conditionally registered tools. `/agents` for Research Agent does not list
  `fetch_url` by default.
- **Risk ceilings:** each persona's ceiling is at most the highest sensitivity among its registered
  tools, unless a waiver records why.
- **Fallback telemetry:** a turn requested as `engineer` without the capability records
  `agent == "orchestrator"` and `requested_agent == "engineer"`.
- **Orchestrator decision**, whichever way it goes: either `run_command` is absent from an
  orchestrator turn's declarations, or such a turn without consent is refused with
  `consent_required`.
- **Stored history:** a message containing Unicode tag characters is stored as the guarded text.
- **Corrections:** with the real embedder (an opt-in test), "manager is Sara" followed by "manager
  is Omar" makes recall return "Omar".
- **Eval:** each specialist persona has at least 3 cases, and the stream path runs in the gate.

**Related:** KAN-27 (history and context), KAN-10, KAN-22.

---

## KAN-22: System's APIs - Audit & Integration

---
**Route inventory.**
- The backend has 161 method+path pairs: `aura_core/api/*.py` under `/api/v3`, plus `api/` and
  `api/v2/`.
- `docs/guides/api-reachability.md` reports 64 routes with no caller found. Checked by hand:
  - **43 really have no caller:**
    - 23 Track AP console routes: `/admin/blueprints*` (9), `/admin/connectors*` (8),
      `/admin/twin/*` (6).
    - 11 other admin routes: `POST /admin/billing/run-cycle`, `GET /admin/jobs/{task_id}`,
      `POST/DELETE /admin/knowledge/documents/{id}/cag-pin`, `GET /admin/refresh/latest`,
      `GET /admin/scorecard`, `GET/POST /admin/service-keys` and `POST …/{key_id}/revoke`,
      `PUT /admin/workspaces/{id}/plan`, and `GET /admin/learning/jobs/{job_id}`.
    - 9 app routes: `/agents/runs` and `/agents/runs/{run_id}`, `/knowledge/graph` and
      `/knowledge/graph/entities/{key}`, `/tools`, and four `/learning/*` routes.
  - **6 are server-only by design:** the billing webhooks, `POST /chat` for service keys, and the
    GitHub OAuth callback.
  - **15 are false negatives:** the `/workspaces/{id}/*` routes are called through the `_ws(id)`
    builder.
- No client or console call targets a path or method the server lacks.

**Already fine (do not redo):**
- The client handles all 8 stream frame types (`sse_agent_client.dart:52-82`).
- Confirmations (`approval_ids` and `pending_tool_calls[].approval_id`) match on both sides.
- The error envelope is parsed by both the app and the console.
- Token refresh (`refresh_token` and `retry_key`) matches.
- Knowledge upload fields and `/engineering/runs` fields match.
- The sync cursor protocol matches.

**Problems (CONFIRMED unless marked)**
1. **The reachability matrix ignores HTTP methods and how path parameters are filled.** It credits
   `GET /admin/learning/jobs/{job_id}` to a call that builds `/admin/learning/datasets/${digest}`
   (`api-reachability.md:44`). `test_api_reachability.py:35` checks only that every row is
   triaged. The backlog's CB-30 still says "16 uncalled routes".
2. **Stream errors lose `retryable` and the retry wait.**
   - `sse_agent_client.dart:66-73` drops `retryable`.
   - An overload refused after the 200 response, which carries `details.retry_after`
     (`domain/errors.py:305-338`), ends as a generic failure (`chat_repository.dart:756`).
   - The `revision.reason` field is not parsed.
3. **`Retry-After` is dropped on rate-limited routes.** `AuraApiError.fromResponse(...)` is called
   without the header in `knowledge_center.dart:303, 429, 447, 461, 471` and `voice_client.dart:188`.
4. **Desktop pair-approve skips the shared client.** `hostd_pairing_flow.dart:77-87` makes a bare
   `http.post` call:
   - no attestation or workspace headers;
   - no retry on 401;
   - no timeout;
   - it shows the raw status and JSON body in the UI, including the server's structured 403
     `plan_required` and 409 bodies.
5. **Attestation headers do nothing on v3 data routes.** `X-Device-*` is read only in
   `services/security.py:469-471`, which only `POST /api/v3/auth/token` uses. About 20 client
   classes send the headers for nothing, and CLAUDE.md presents this as the pattern to copy.
6. **Inconsistent client classes.**
   - `X-Aura-Workspace` is sent only by `AuraApiClient`, `KnowledgeCenter` and `SseAgentClient`.
   - Retry on 401 is missing in `account_client`, `desktop_devices_client`, `data_key_client` and
     `chat_images`.
   - SUSPECTED: harmless until a workspace-metered route is called from one of these classes.
7. **`/agents` and `/tools` disagree.** `/agents` advertises unregistered tools (KAN-21, problem 3)
   and adds plan-granted Engineer access. `/tools` filters on capabilities only
   (`v3.py:1638`, `1665`).
8. **SUSPECTED: input handling.**
   - `GET /admin/approvals?status=<bad>` returns 500, not 422 (`api/admin.py:351-352`).
   - Upload reads the whole file before checking its size (`v3.py:1037`).
   - The upload's `http.Client` is never closed (`knowledge_center.dart:384`).

**Mismatch table (main flows)**

| Flow | Server | Client | Status |
|---|---|---|---|
| Stream `error.retryable` | `kernel.py:1112-1126` | not parsed (`sse_agent_client.dart:66`) | gap |
| Stream overload `details.retry_after` | `errors.py:331` | only logged (`chat_repository.dart:707`) | gap |
| `revision.reason` | `kernel.py:999` | ignored | minor |
| `plan` vs `plan_summary` | `v3.py:812` / `kernel.py:1087` | both read (`turn_meta.dart:130`) | OK, naming drift |
| `/agents.allowed_tools` | raw grant (`v3.py:1694`) | parsed, unused | advertises unregistered tools |
| Retry-After on knowledge and voice | sent | dropped | gap |
| Pair approve | 403 `plan_required` envelope | raw body shown | gap |
| Attestation on v3 routes | read only on `/auth/token` | sent everywhere | has no effect |
| `X-Aura-Workspace` | resolved on every route | 3 of about 20 classes send it | inconsistent |

**Fix**
1. Make `scripts/api_reachability.py` match on method, and count a path parameter only when it is
   interpolated. Teach it the `_ws(id)` builder. Regenerate the matrix, and correct CB-30.
2. For each of the 43 uncalled routes, decide one of:
   - wire it (the console for service keys, scorecard, workspace plan and the AP routes; the app
     for `/tools`, `/agents/runs`, `/knowledge/graph`);
   - remove it (the four legacy `/learning/*` routes);
   - mark it `declared`, with an owner.
3. Add `retryable` and `retryAfter` to `SseChatEvent`. Show a retry card with the wait time for a
   retryable overload.
4. Pass `Retry-After` at every `fromResponse` call, or move `KnowledgeCenter` and `VoiceClient`
   onto `AuraApiClient` (extending it for multipart).
5. Move pair-approve onto `AuraApiClient`, and map its 403 and 409 bodies through `AuraFailure`.
6. Decide on attestation: enforce it server-side where it matters, or stop sending it from data
   clients and correct CLAUDE.md. Send `X-Aura-Workspace` from every authenticated client, or
   document which routes ignore it.
7. Make `/agents` and `/tools` use the same capability set and the registered-tool intersection.
8. Return 422 for bad `/admin/approvals` filters. Bound the upload read. Close the upload client.

**Acceptance criteria**
- The regenerated matrix matches by method:
  - `GET /admin/learning/jobs/{job_id}` shows `none`;
  - the `/workspaces/{id}/*` routes show `called`;
  - CB-30's count equals the matrix's count of genuinely uncalled routes.
- Every remaining `none` row is wired (with a caller file:line) or triaged `server-only` or
  `declared`, with an owner.
- **Dart test:** an `error` frame `{"error":"overloaded","retryable":true,"details":{"retry_after":7}}`
  yields `retryable == true` and a `retryAfter` of 7 s. The chat UI shows a retry card with the
  wait.
- **Dart tests:** a 429 with `Retry-After: 9` on `KnowledgeCenter.search`, `listDocuments` and
  `setCagPin`, and on `VoiceClient.transcribe`, gives `AuraApiError.retryAfter == 9s`.
- **Dart test:** a pair-approve 403 `plan_required` shows the localized message, never raw JSON. A
  401 retries once after a token refresh.
- Every client class calling `/api/v3/*` (other than the auth routes) goes through `AuraApiClient`,
  or adds `workspaceHeaders`, or is listed in a documented exemption.
- **Backend test:** `GET /api/v3/admin/approvals?status=bogus` returns 422.

**Related:** KAN-21, KAN-10 (T-17, T-19), KAN-24.

---

## KAN-23: System's Storage - Audit & Migration

---
**The main stores can't silently fall back to memory.** `settings.py:2459-2469` refuses to boot
staging or production when `vector_backend`, `document_backend` or `memory_backend` is `memory`,
and every store in `container.py` has a Postgres branch. The problems are elsewhere.

**Problems (CONFIRMED unless marked)**
1. **The boot check doesn't cover the cache backend.**
   - `container.py:715-725` falls back to `InMemoryCache`.
   - That cache then holds tool approvals (`container.py:633`) and rate limits
     (`aura_service.py:400-403`).
   - With 4 workers, an approval stored on one worker can't be found by another, and every rate
     limit multiplies by 4.
   - The kernel also has its own in-memory approval fallback (`kernel.py:420-422`).
   - Railway is documented as using Redis, so this is unguarded rather than live.
2. **Auth state falls back to process memory for the life of the process.**
   - `infrastructure/redis_client.py:59-63` caches `None` after one failed ping.
   - From then on, refresh tokens go to `_local_refresh_store`, and erasure checks read
     `_local_erased_accounts` (`services/jwt_auth.py:159-163`, `292-300`, `918`).
   - The strict check (`:905-909`) fails closed only on an exception, not when the client is
     `None`.
3. **Some data lives on the container's disk.**
   - Training datasets go to `FileDatasetStore('/var/lib/aura/datasets')` (`container.py:527`),
     while the records that point to them are in Postgres.
   - `POST /api/v3/learning/log` writes `learning_logs.json`, where the last writer wins
     (`services/learning_pipeline.py:14-22`).
   - SUSPECTED: Railway has no volume, so both are lost on every redeploy.
4. **Four downgrades delete data without a guard:**
   - `0020` (workspaces)
   - `0024` (entitlements and usage)
   - `0025` (`aura_payments`, `:73-75`)
   - `0028` (shared conversations)

   `0019` and `0030` already refuse to run while rows exist.
5. **Two data-access layers write the same tables.**
   - The SQLAlchemy models (`infrastructure/db/models.py`, used by `api/v2/sync.py`) and raw
     asyncpg in `aura_core` both write users, tasks, insights and GitHub identities
     (`postgres_app_users.py:29`, `postgres_synced_personal_os.py:308`, `postgres_insights.py:47`,
     `postgres_github_identity.py:89`).
   - Rules such as `sync_txid` stamping exist twice.
   - Each process holds two connection pools.
6. **Two data migrations have not been run.**
   - `scripts/move_personal_os_records.py` (ADR-032) has no recorded run, so the old
     `aura_personal_os_records` rows are invisible in the app.
   - Live `/health/ready` today shows the index `aura-knowledge__v1__hash-embed-v1__384`, while
     the embedder is `multilingual-e5-small`. `scripts/rebuild_index.py` still needs to run.
7. **Memories are stored twice.**
   - The client writes to `aura_memories`, and also queues an encrypted copy to `memories.fact_enc`
     (`memory_repository.dart:292-297`).
   - On pull, only deletions are applied for `memories` (`sync_client.dart:431`), so that copy is
     never read.
8. **Several tables grow without bound.**
   - Only audit events, telemetry and idempotency keys have retention (`celery_app.py:207-221`).
   - These have no delete path other than account erasure: `aura_engineer_runs`,
     `aura_agent_steps`, `aura_feedback`, `aura_store_events`, `aura_usage_counters`, `insights`.
   - Sync tombstones are kept forever (`api/v2/sync.py:460-463`).
   - SUSPECTED: the Railway `beat` and worker services that run the purges are not running.
9. **Some data is stored as plaintext at rest:**
   - `aura_shared_conversations.messages` (`0028:28`)
   - `aura_feedback.prompt` and `.response`
   - `aura_engineer_runs` (by design)
   - graph entities, insights payloads and document chunks (CB-22)
10. **There are no backups on Railway.** `docs/deployment/backups.md` says the backup job was
    built and tested locally, not run on Railway (GAP-028, CB-21).
11. **The schema state has not been re-checked.** The code's head is `0030`. The last recorded live
    reading was `0029` (2026-11-18, `BACKEND_STATUS.md:75`). Live `/health/ready` today returns
    `schema: null`.
12. **Client (Drift):**
    - `schemaVersion` is 15, with 14 hand-written upgrade steps and no `SchemaVerifier` tests
      (`database.dart:374`, `583-659`).
    - Sign-out does not clear the five `Desktop*` tables, which hold folder paths, open tabs and
      terminals (`wipeUserData()`, `:507`).
    - `DesktopAgentState` is unused.
    - `TelemetryLogs` is never pruned.
    - The pre-v6 re-encryption pass swallows errors (`:704-716`).
    - The sync cursor protocol matches the server; there is no defect there.

**Fix**
1. Make the boot check refuse `cache_backend=memory` in production, and remove the kernel's
   in-memory approval fallback there.
2. Reconnect Redis on use. In production, refuse to issue tokens (503 `auth_state_unavailable`)
   when the client is `None`, and remove the local token and erasure stores.
3. Move datasets and learning logs to Postgres or object storage, or refuse to boot without a
   volume. Retire `services/learning_pipeline.py` in favour of `aura_core/learning/curation.py`.
4. Add a "refuse while rows exist" guard to the downgrades of 0020, 0024, 0025 and 0028.
5. On Railway, run `move_personal_os_records.py --dry-run`, then the real run, then
   `rebuild_index.py`, and commit the counts. Stop the client queuing `memories` upserts, and plan
   a migration that drops `memories.fact_enc`.
6. Add purge jobs for engineer runs and steps, feedback, store events, usage counters, insights and
   old tombstones. Confirm that `beat` runs on Railway.
7. Encrypt shared conversations and feedback, or record in an ADR why they stay plaintext.
8. Create the Railway backup cron per `backups.md`, and run one restore drill.
9. Drift:
   - commit schema snapshots, and add `SchemaVerifier` tests for v1→v15;
   - clear the `Desktop*` tables in `wipeUserData`;
   - drop `DesktopAgentState` (v16);
   - cap `TelemetryLogs`.
10. Choose one data-access layer for the shared tables: move v2 sync onto the asyncpg adapters, or
    put the shared column rules in one module.

**Acceptance criteria**
- **Live schema:** `/health/ready` shows `schema.revision 0030`, and the pre-deploy log shows
  `revision before: 0029 … after: 0030` and `=== MIGRATION OK ===`.
- **Cache and auth (unit tests):**
  - production with `AURA_CACHE_BACKEND=memory` raises `ConfigurationError`;
  - with Redis unreachable at boot, `issue_token_pair` returns 503 and nothing is written locally.
- **Downgrade guard (Postgres 16 test):** `alembic downgrade 0025→0024` with one `aura_payments` row
  fails.
- **Datasets:** a dataset created before a redeploy still exists after it.
- **Data migration:**
  - a committed report shows the move script's dry-run and real-run counts, with 0 errors;
  - an agent-created task shows up in the app after one sync on a real account;
  - `/health/ready.index` names `multilingual-e5-small`.
- **Retention:** a Postgres test shows the purge deleting engineer runs older than the window, and
  the Railway beat log shows the purge firing.
- **Backups:** a committed restore-drill report covers Railway Postgres and Qdrant Cloud.
- **Client:** `flutter test` includes passing v1→v15 migration tests, and after sign-out every
  `Desktop*` table is empty.

---

## KAN-24: Flutter & System's Design - Study & Audit

---
**Already fine (do not redo):**
- The ARB keys match exactly: 1462 in English and 1462 in Arabic.
- There are no directional `EdgeInsets`, `Positioned` or `TextAlign` values, and RTL layout is
  guarded by `test/ux/rtl_layout_test.dart`.

**Problems (CONFIRMED).** Counts cover `lib/features`, `lib/desktop` and `lib/core`, excluding the
design system and generated files.

1. **Screens bypass the design system.**

   | Raw widget | Files | Hits | Use instead |
   |---|---|---|---|
   | `ElevatedButton`/`TextButton`/`OutlinedButton`/`FilledButton` | 33 | 72 | `AuraButton` |
   | `Card(` | 16 | 28 | `AuraCard` |
   | `SnackBar`/`ScaffoldMessenger` | 13 | 38 | `AuraSnackbar` |
   | `AlertDialog` | 9 | 12 | `AuraDialog` |
   | `ListTile` | 11 | 16 | `AuraListTile` |
   | `TextField`/`TextFormField` | 15 | 24 | `AuraTextField` |

   - Hardcoded values instead of tokens:
     - 311 literal `fontSize:` values in 81 files;
     - 205 numeric `SizedBox` sizes;
     - 140 numeric `EdgeInsets`;
     - 40 `BorderRadius.circular(n)`;
     - 43 hardcoded `'Courier'` or `'monospace'` fonts.
   - Examples: `desktop/git/git_panel.dart` (8 raw buttons), `analytics_page.dart:154`,
     `goals_page.dart:173`, `login_page.dart:403`.
2. **There are two theme systems, and four screens are dark-only.**
   - The legacy `AuraTheme` (`core/theme/theme.dart:6-14`) drives `analytics_page.dart:26`,
     `profile_page.dart:64`, `finance_summary_cards.dart` and `health_daily_dashboard.dart`.
   - Those screens also hardcode `Colors.white` text, which is unreadable in the light theme, the
     default (`main.dart:94`).
3. **Some widgets are duplicated.**
   - `_SmallAuraButton` (`desktop/host/host_panel.dart:288`) exists because `AuraButton` has no
     size option.
   - Empty states are hand-rolled: `_EmptyWorkspaces`, `_EmptyRecent`, `_EmptyEditorPlaceholder`,
     `EmptyStateChat`.
   - There are 67 `CircularProgressIndicator` uses against 8 `AuraSkeleton` uses.
4. **Arabic gaps.**
   - Plan-limit messages are built from English strings and an English month list
     (`core/network/aura_failure.dart:162-195`).
   - The hardcoded-text ratchet scans only `lib/features`, not `lib/desktop`.
   - `knowledge_center_page.dart:230` shows the raw `doc.status`.
   - `finance_transaction_history.dart:141` uses a fixed `yyyy-MM-dd` date format.
5. **Accessibility gaps.**
   - Some tap targets are 40×40 with `shrinkWrap` (`chat_header_action_button.dart:22-25`,
     `sync_status_view.dart:147`).
   - Some `GestureDetector`s have no semantics (`automations_page.dart:106`,
     `editor_tab_bar.dart:126`, `resize_handle.dart:70`).
   - 20 font sizes are 9–10 px, and there is no text-scale handling.
   - `login_page.dart:531` loads the Google logo from `upload.wikimedia.org`, a third-party request
     on the sign-in screen.
6. **State management is mixed.**
   - The legacy `StateNotifier` (via `flutter_riverpod/legacy.dart`) is used in 9 files, including
     15 notifiers in `desktop/state/state_notifiers.dart`.
   - There are 124 `setState` calls in 44 files.
7. **Some screens and routes are dead.**
   - Since `1571e978` (2026-11-17), these have no importer: `home_page.dart` (650 lines),
     `life_hub_page.dart`, `calendar_page.dart`, `tasks_page.dart`, `finance_page.dart`,
     `health_page.dart` and `memory_bank_dialog.dart`.
   - 8 alias routes only redirect (`router.dart:587-606`).
   - `/ide/workspace` is reached only by tests.
   - **Consequence:** the personal-OS UI that ADR-032's sync feeds can't be reached in the app.
8. **Some files are very large:** `state_notifiers.dart` (1,470 lines), `chat_repository.dart`
   (1,054), `token_manager.dart` (730), `router.dart` (720), `git_panel.dart` (688).
9. **SUSPECTED:** the IDE has never been checked under RTL, and Arabic font fallback is unverified
   on a device.

**Fix**
1. Add sizes (compact and regular) to `AuraButton`, and delete `_SmallAuraButton`.
2. Move the four `AuraTheme` screens to `AuraPalette.of(context)`, and delete
   `core/theme/theme.dart`.
3. Add source-scan ratchets (like the existing ones in `test/ux`) for raw buttons, `Card`,
   `SnackBar`, `AlertDialog`, `TextField`, `Colors.*`, literal `fontSize:` and `'Courier'`. Lower
   them with each PR.
4. Localise `aura_failure.dart`, using intl month names. Extend the text ratchet to `lib/desktop`.
5. Use 48 dp minimum tap targets. Add `Semantics(button: true, label: …)` to tappable
   `GestureDetector`s. Bundle the Google logo as an asset.
6. Port the 9 `StateNotifier` files to `Notifier`, and drop `flutter_riverpod/legacy.dart`.
7. Decide what happens to the hidden personal-OS pages:
   - re-route them, for example under `/you`; or
   - delete them along with their alias routes.

   Wire up or delete `/ide/workspace`.
8. Split `state_notifiers.dart` into one notifier per file, and split `chat_repository.dart` into
   streaming and persistence.

**Acceptance criteria**
- **Theme:** `grep AuraTheme lib/` returns nothing, and `core/theme/theme.dart` is removed. A
  light-theme golden test of `/analytics` and `/profile` has no text below 4.5:1 contrast.
- **Ratchets:** the tests exist, with ceilings at or below today's counts: buttons 72, `Card` 28,
  `SnackBar` 38, `fontSize` 311, `Courier` 43.
- **Button size:** `AuraButton(size: compact)` has a test, and `_SmallAuraButton` is gone.
- **Arabic:** a widget test of a 402 plan-limit refusal under `Locale('ar')` contains no Latin
  letters.
- **Tap targets:** a test checks that every tappable `IconButton`, `InkWell` and `GestureDetector`
  has a hit area of at least 48×48 and a semantics label.
- **State management:** `grep flutter_riverpod/legacy.dart lib` returns nothing.
- **Dead code:** a scan of `lib` for unreferenced files reports 0. Every `GoRoute` has an in-app
  caller or a documented deep-link reason.
- **File size:** no file under `lib/` exceeds 800 lines.
- **Checks:** `flutter analyze` is clean and `flutter test` is green, with counts quoted in the PR.

---

## KAN-27: Context Window - Improve

---
**Problems (CONFIRMED)**
1. **One global prompt cap applies to every provider.**
   - `max_prompt_tokens` defaults to 400 (`config/settings.py:807`, `882`), a size chosen for CPU
     Ollama. Railway sets it to 4096.
   - There is no per-provider cap, so Claude (200K) and Groq are held to the 8K Space's budget.
2. **The context window is the minimum over all configured providers, healthy or not.**
   - The docstring of `kernel.py:2201-2214` says "healthy", but the code takes all of
     `router.provider_names`.
   - It also ignores the turn's workload, so an Engineer turn routed to `claude1` is sized like
     `hfspace`.
3. **When a cap is set, the window is ignored and the cap itself is not enforced.**
   - `context/engine.py:150-158` ignores the window whenever a cap exists.
   - `max_output_tokens` (the API allows up to 8192, `api/v3.py:285`) is never checked against
     the window minus the prompt.
   - The router checks eligibility with the unclamped value (`router.py:506`), while the payload is
     clamped (`domain/llm.py:530`).
   - `_fit_to_cap` (`engine.py:607-617`) only drops history, and a prompt still over the cap is sent
     as it is.
4. **Conversation memory has a hole.**
   - The prompt keeps the last 4 messages (`settings.py:1066`).
   - The episodic window keeps 12, and summarising starts only at 20 (`settings.py:1059-1060`,
     `memory/session.py:288-312`).
   - So messages 5–12 back are in neither the prompt nor the summary.
   - `_compress_history` deliberately adds no omission notice (`engine.py:416-418`).
   - Architecture audit B1.
5. **The summary is given SYSTEM authority.**
   - It is inserted as "Earlier in this conversation: …" on `TrustChannel.SYSTEM`
     (`engine.py:461-473`).
   - Yet it is written from assistant text, which may quote documents and tool output.
   - Its fallback is the raw last 4,000 characters of the transcript, and despite its docstring it
     is unmarked (`memory/session.py:378-394`).
   - Architecture audit B2.
6. **Tool output can vanish without a note.**
   - Tool items are not pinned (priority 30, `engine.py:349-364`).
   - They are capped at 20% of the budget (`domain/context.py:159`): about 696 tokens at 4096, and
     about 68 at 400.
   - When truncated, only the head is kept.
   - `pack()` can drop them entirely with no note to the model, because `_execution_notes` names
     only unexecuted or failed steps.
   - This was recorded in production: `docs/reports/flame-link2-live.md:21`. Architecture audit A3.
7. **The planner gets no history** (`planning/engine.py:210`), so "do it" or "yes, add it" has no
   referent.

**Fix**
- Size each turn from the providers it is eligible for (by workload and data classification),
  counting only healthy ones.
- Add a per-provider prompt cap (`AURA_PROVIDER_<N>_MAX_PROMPT_TOKENS`) derived from the window
  minus the maximum output minus a margin. Keep the global value as an upper bound.
- Clamp `max_output_tokens` to the window minus the prompt, and use the clamped value for
  eligibility.
- Enforce the cap: trim tool output and knowledge first, then raise a typed `ContextOverflowError`.
- Align the history windows so every message is either shown verbatim or summarised. Otherwise add
  a one-line omission marker.
- Move the summary onto the DATA channel, inside the turn fence. Mark the fallback
  `[unsummarised transcript excerpt]`. Taint a turn whose summary came from a tainted turn.
- When a tool result is dropped, add a pinned note ("the output of <tool> did not fit; say you
  could not show it") and the degraded reason `tool_output_dropped`. For large results, keep both
  the head and the tail, or a summary.
- Give the planner the last N messages.

**Acceptance criteria**
- **Window sizing:**
  - an Engineer turn with `claude1` eligible is assembled with a cap of at least 100K;
  - a chat turn with only `hosted1` and `hfspace` uses the healthy minimum;
  - an unhealthy provider does not shrink the window.
- **Cap enforcement:** an oversized system prompt never produces `prompt_tokens > cap`. It is
  trimmed, or raises `ContextOverflowError`.
- **Output clamping:** `max_output_tokens=8192` on an 8K window is clamped, and the turn does not
  fail with `context_window_too_small`.
- **History:** with 8 exchanges and the default settings, every exchange is shown verbatim,
  summarised, or covered by the omission marker.
- **Summary channel:** the summary item's channel is not SYSTEM, and the fallback starts with its
  marker.
- **Dropped tool output:** at a 400-token cap with a 2K-token tool result, the prompt has either a
  marked truncated result or the pinned note, never neither. The `done` frame carries
  `tool_output_dropped`.
- **Large tool results:** a 20K-character result keeps both its start and its end, or a summary.

**Related:** KAN-28, KAN-14, KAN-21.

---

## KAN-28: Tokens Handling - Improve

---
**Problems (CONFIRMED)**
1. **Production never counts tokens exactly.**
   - `tiktoken` is not in the lockfiles, so `get_tokenizer` always falls back to
     `HeuristicTokenizer` (`context/tokenizer.py:196-200`).
   - Even if installed, it would use `cl100k_base`, the wrong vocabulary for Qwen, gpt-oss and
     Claude.
   - `counts_are_exact` and `tokenizer_name` (`context/engine.py:623-628`) are never read.
2. **Three estimators disagree.**
   - The context engine uses the heuristic.
   - The router's window check and the quota reservation use `len/4` (`domain/messages.py:171`,
     `router.py:125`).
   - The stream fallback also uses `len/4` (`openai_compatible.py:346-350`).
   - Measured: 2,020 characters of Arabic count as 910 against 505, and 1,220 characters of code as
     619 against 305. The router and the quota undercount Arabic and code by about 45–50%.
3. **The same text is counted repeatedly.**
   - History is counted 3 times (`engine.py:407, 477, 604`), and the system prompt twice.
   - `_fit_to_cap` recounts the whole prompt after each dropped message (`:607-611`).
   - The CPU cost is small (assembly p50 1.45 ms). The real problem is inconsistent counts.
4. **The usage figures in the `done` frame don't agree with each other.**
   - `prompt_tokens` is 0 when the provider sends no usage (`kernel.py:1102`), while telemetry uses
     the assembled count (`:1040`).
   - `completion_tokens = len(collected)` counts stream chunks, not tokens (`:1041-1043`).
   - `cost_usd` covers every generation in the turn, but the token counts cover only the answer.
     `run` does the same (`:663`, `689-690`).
5. **Estimated usage is not marked when streaming.**
   - The non-stream path sets `usage_estimated` (`openai_compatible.py:279`); the stream path does
     not.
   - The estimate then goes into `quota.settle` (`router.py:1221`) and `aura_llm_tokens_total` as
     if the provider had reported it.
6. **The quota under-reserves for Arabic and code.**
   - The per-user daily allowance `USER_TPD`, which the Claude slot depends on, reserves `len/4`
     plus the full output allowance, then settles to the reported usage (`llm/quota.py:339-364`).
   - There is no token billing; `billing/service.py` is seat-based.
7. **Most generations are not counted per turn.**
   - Planner, replan, reasoning and summariser generations don't count against
     `max_generations_per_turn` (`router.py:248-257`).
   - `aura_llm_generation_tokens_total` has no `purpose` label.

**Fix**
- **Tokenizers:** use one tokenizer per provider family, behind the existing `Tokenizer` port, and
  select it by the routed provider:
  - the Space's HF `tokenizer.json` for Qwen;
  - `o200k` for gpt-oss;
  - Anthropic's `count_tokens`, or a calibrated factor, for Claude.

  Use the same tokenizer in the router's eligibility check and the quota estimate, and remove
  `approx_tokens` from those paths.
- **Counting:** count each item once, store the count on the item, and update the total by
  subtraction in `_fit_to_cap`.
- **Turn usage:** sum a `TurnUsage` over every generation, with a breakdown by purpose.
  `done.usage` and `TurnTelemetry` report the turn total.
- **Estimates:** flag estimated usage on stream chunks and in telemetry, and replace the
  chunk-count fallback.
- **Generation cap:** count engine generations (planner, replan, reasoning, summary) against a
  separate per-turn cap.

**Acceptance criteria**
- **Accuracy:** per provider kind, the estimate is within ±5% of the reference tokenizer on an
  English, an Arabic and a code corpus (pinned test). `counts_are_exact` is correct for each
  tokenizer.
- **Consistency:** for one request, the router, the quota and the context engine report the same
  prompt-token figure.
- **Turn totals:** with fake providers returning known usage for the planner, the answer and a
  repair, `done.usage` and telemetry equal the sum. `usage_estimated=true` when the provider
  reports nothing.
- **Counting once:** a call-counting tokenizer sees each history message at most once per
  `assemble` call.
- **Quota:** `USER_TPD` refuses an Arabic request whose real cost exceeds the remaining allowance.

**Related:** KAN-27, KAN-29.

---

## KAN-29: Latency Time - Maximize (reduce end-to-end latency)

---
**What is measured today:**
- Nothing on the live topology (`PROVIDER_PERFORMANCE_AUDIT_2026-11-08.md:11-19`, GAP-041).
- `hosted1`: a single sample, with TTFT 1,179 ms and completion 4,806 ms.
- The VPS sandbox (A4): p50 56.3 s and p95 70.5 s.
- Pipeline overhead: 4.56 ms at p95, but only against in-memory adapters.

**Targets:** TTFT p95 under 2.5 s (S3), and answer p95 under 15 s (S4) (`PRODUCTION_SLO.md:24-25`).
The turn histogram's buckets stop at 60 s, and it has no planned/unplanned label.

**Problems (CONFIRMED unless marked)**
1. **Before the first token, the stream awaits these one after another:**
   - confirmation claim
   - Engineer consent
   - input guard
   - context gathering (whose parts do run in parallel)
   - classification
   - context guard
   - tool declarations, which call `_desktop_active`
   - planning and execution
   - Engineer-run writes
   - system prompt rendering, which calls `_desktop_active` again (`kernel.py:1608`, `2175`)
   - `_assigned_model`, which costs 3 store reads on every turn, even with no registered model
     (`mlops/model_registry.py:372-373`, `kernel.py:2276`)
2. **The desktop check is repeated:** twice per turn, and again on every tool call
   (`container.py:1673`). With the relay, each one is a Redis round trip
   (`adapters/host_agent.py:267`).
3. **History summarisation is a full generation inside the turn.** `episodic.append` awaits
   `_summarise` (`memory/session.py:294`) before the `done` frame (`kernel.py:1019-1025`), inside
   the 45 s budget.
4. **The `done` frame waits on writes:** the session classification (`:1039`) and the telemetry
   sink (`:1074`).
5. **`/chat` can chain many generations.**
   - Tree-of-thoughts makes about 14 sequential generations (`reasoning/engine.py:353`).
   - Self-consistency makes 3 samples plus a verification.
   - None of these count toward `max_generations_per_turn` (`router.py:248-257`).
6. **The message is embedded 2–4 times:** by memory recall (`memory/engine.py:240`), by retrieval
   (`knowledge/engine.py:702`), and by the semantic cache (`llm/cache.py:335`, `386`).
7. **`/chat` and `/chat/stream` run different pipelines.**
   - The cache and the reasoner run only on `run`.
   - Eval measures `run` (`evaluation/harness.py:310`), which is not the path the app uses.
   - Architecture audit C4.
8. **SUSPECTED: wasted work on the chat-only launch.** Memory and retrieval run before
   classification. Their content makes the turn `sensitive`, which `hosted1` refuses, so the turn
   pays for them and then fails with `capability_unavailable`.
9. **Planner prompt size and execution scheduling:** see KAN-14.

**Fix**
- **Measure first:** run the Phase 3 load test on Railway. Extend the turn buckets past 60 s, and
  add a `planned` label.
- **Repeated lookups:**
  - Resolve the desktop session and the workload once per turn, and reuse them.
  - Cache `_assigned_model` per process with a short TTL, and skip it when the registry is empty.
- **Work after `done`:** run summarisation after `done`, in a background task outside the turn
  budget. Send the telemetry and classification writes after `done` too.
- **Generations:** run tree-of-thoughts evaluations in parallel, and count engine generations
  against a separate cap.
- **Embedding:** embed the message once per turn and share the vector.
- **Serviceability:** check whether the turn can be served (its classification against the
  eligible providers) before doing memory and retrieval work.
- **Eval:** run the eval gate through `stream` as well as `run`.

**Acceptance criteria**
- **Load test:** a committed `docs/reports/` load test on Railway at 1, 5 and 10 users shows TTFT
  p95 under 2.5 s (`aura_llm_ttft_seconds`) and unplanned answer p95 under 15 s
  (`aura_turn_duration_seconds{planned="false"}`).
- **Summariser:** with a summariser that sleeps 5 s, `done` arrives within 100 ms of the last
  token.
- **Store latency:** with every store adding 50 ms, the extra TTFT over the baseline is at most 2
  round trips.
- **Lookups (counting fakes):** `registry.resolve` is called once per turn, and the message is
  embedded once per turn.
- **Generation cap:** a tree-of-thoughts turn stays within the configured total generation cap.
- **Eval:** the eval harness has a stream mode, and the gate runs it.

**Depends on:** KAN-14 (execution), KAN-28 (generation counting).

---

# Proposed subtasks (draft, 2026-11-21, not created yet)

How these would be created:
- Each row becomes a Jira **Subtask** under its parent, using the summary shown.
- Its description is the "Done when" text, plus a pointer to the item in the parent's description
  that it covers.
- Each subtask is assigned to its parent's assignee, with the parent's priority. No due dates are
  set.
- Work that appeared in more than one ticket is kept in one place only. Where a parent mentions it,
  the "Lives in" column says where it went.

**Four tickets can't get subtasks.** KAN-16, KAN-17, KAN-25 and KAN-26 are already subtasks of
KAN-15, and Jira doesn't allow subtasks under subtasks. My proposal: turn each one's "Fix" list into
a checklist in its own description. Alternatively, leave them as they are.

Total: **83 new subtasks** under 12 parents.

## KAN-10 System's Tools - Audit (10, assignee Princess)

| # | Summary | Done when |
|---|---|---|
| 1 | Wire or remove the dead built-in tools (T-06) | `fetch_url`, `get_weather`, `list_files` and the built-in `read_file` are either registered by a real setting (`AURA_HTTP_ALLOWED_HOSTS` added) or removed, along with their grants and client chips |
| 2 | Hide tools whose runner is absent (T-13) | On a config with no Docker and no paired desktop, the planner and `GET /tools` offer no tool that can't run; off-by-default exec tools aren't advertised |
| 3 | Add the grant-vs-registered tool test | The test fails if a persona grant names a tool that a default container doesn't register (unless it is on the allowlist, with its setting) |
| 4 | Keep Engineer tools out of the orchestrator, or give them the Engineer deadline (T-07) | An orchestrator turn either doesn't declare `run_command`, or runs it with the Engineer deadline; the decision is recorded |
| 5 | Enforce the per-turn tool cap everywhere (T-08, T-09) | The cap applies on the fallback path and across delegation, and a resumed confirmation is refused when tools are off (tests) |
| 6 | Report MCP tool name collisions (T-10) | A collision is logged and visible in `/health/ready`, and no tool is dropped silently |
| 7 | Personal-OS tools over voice and /chat, with date validation (T-12, T-15) | `propose_*` works over live voice and `/chat`; invalid dates are refused with a clear error (tests) |
| 8 | Git push path without Docker (T-14) | A desktop or GitHub-API path pushes a confirmed branch, or the tool is hidden when it can't run |
| 9 | Client: call GET /tools and handle confirmation_invalid (T-17, T-19) | The app reads `/tools`; an expired approval shows a localized message (widget tests) |
| 10 | Enforce the workspace disk quota, and close out the tools-audit README | `enforce_disk_quota` runs before durable-workspace writes and commands, and every T-id in the README is marked FIXED or WON'T FIX |

T-18 (field naming) lives in KAN-20.

## KAN-11 Organizing Tools - Integration (6, assignee you)

| # | Summary | Done when |
|---|---|---|
| 1 | Fix the main boot blocker | `Set, Tuple` are restored; the `dd2acbab` bodies of the license scanner and drift monitor are back; the conflict markers in `gpu_resource_scheduler.py` are resolved; `python -c "import aura_service"` exits 0 and `pytest` collects with 0 errors |
| 2 | Add an import and conflict-marker test for tools/open | An architecture test imports every module under `tools/open/` and fails on `<<<<<<<` or `>>>>>>>` lines |
| 3 | Triage the 25 new tools against ADR-036 | Each module is recorded in ADR-036 as wrapped, superseded, deferred or deleted |
| 4 | Wrap and grant the safe text-in tools | Each tool chosen in #3 is an async `FunctionTool` with capped input, a persona grant and its own test; `TestPlannerPromptBudget` still passes |
| 5 | Retire or defer the unsafe tools | `background_task_manager`, `push_notification_dispatcher`, the four path readers and `remote_session_controller` are not reachable from any persona |
| 6 | Require every tools/open module to be classified | `test_superseded_open_tools_unused.py` fails on any unclassified module |

## KAN-14 The Execution Lifecycle Time - Audit & Debug (8, assignee Eman)

| # | Summary | Done when |
|---|---|---|
| 1 | Schedule plan steps as soon as their dependencies finish | The scheduling test passes: s3 finishes at 2 s, total ≤ 5 s |
| 2 | Tie the execution deadline to the loop deadline | A 130 s Engineer step is followed by its dependent step; a pass deadline that leaves loop time is replannable (test) |
| 3 | Stop tool time eating the answer's budget | A chat turn with a 40 s tool and a 45 s budget still answers (test) |
| 4 | Shrink the planner prompt | Tools are filtered to the grant, top-k and pairing; the tool block is cached; the whole prompt is capped; replans get result excerpts; prompt ≤ 6,000 / 8,000 chars (pinned test) |
| 5 | Restrict plan step kinds to tool and respond | The planner can't emit step kinds that nothing executes, or they get handlers (test) |
| 6 | Batch execution-path writes | ≤ 3 awaited store writes per pass (counting fake); audit and event writes go through the outbox |
| 7 | Per-user tool concurrency limit | One user can't hold every tool slot in a worker (test with two users) |
| 8 | Stage timings and a purpose label on generation metrics | `tools`, `reasoning`, `replan` and `plan_ms` are recorded; `aura_llm_generation_*` has `purpose`; the dashboard shows both |

Planner history lives in KAN-21 #1.

## KAN-15 All broken tools - Debug (2 more, assignee Princess)

It already has KAN-16, KAN-17, KAN-25 and KAN-26.

| # | Summary | Done when |
|---|---|---|
| 1 | Decode hostd command output as UTF-8 | UTF-8 Arabic output is byte-exact on Windows; this is the shared fix for KAN-16 #4, KAN-17 #3 and KAN-25 #3 |
| 2 | End-to-end Engineer file-tool test | A fake-daemon test runs read → edit → run → create in one turn, on a CRLF and UTF-8 Arabic fixture, on Windows and Linux |

## KAN-20 Review & Edit Your Tools (4, assignee Princess)

| # | Summary | Done when |
|---|---|---|
| 1 | Rewrite the Engineer tool descriptions and unify field names (T-18) | One naming convention for paths, content and limits; descriptions no longer point to `cat`; a test checks length and field names |
| 2 | Check remember_fact against what the user said | A fact absent from the user message is refused, or stored as `model_inferred` with confidence < 0.5; recall ranks provenance (test) |
| 3 | Drive tool chips and confirmation cards from GET /tools | One chip per registered tool and none for others; a `write_files` card exists (widget tests) |
| 4 | Show failed Engineer runs in red | A failed run is shown red in the history page (widget test; needs KAN-17) |

## KAN-21 System's Agents - Audit (9, assignee Eman)

| # | Summary | Done when |
|---|---|---|
| 1 | Bilingual planning gate with follow-up context | Arabic intent patterns, a read-intent matcher for `list_*` tools, the last assistant message passed into the goal, and a plan after an offered action; finance, health and task-planner kernel tests and the Arabic and "yes, add it" tests pass |
| 2 | Code check for unbacked action claims | A turn claiming "I've added the task" with no succeeded step gets `unbacked_action_claim`; a real `propose_task` success is not flagged |
| 3 | Align persona grants with registered tools | `/agents` returns only registered tools; Research Agent's ceiling matches its tools; the ceiling test passes |
| 4 | Record the resolved and requested persona in telemetry | A fallback turn records `agent=orchestrator`, `requested_agent=engineer` |
| 5 | Decide on Engineer tools under the orchestrator | The decision is recorded, and either the tools are absent or consent is enforced (test) |
| 6 | Prompt cleanup | `user_profile` is supplied or removed; the orchestrator prompt has a delegation line; the four unused templates are wired or deleted |
| 7 | Store guarded text in history, let corrections supersede, handle empty answers | History stores the guarded text; "Sara → Omar" recall returns Omar (opt-in test); an empty answer gives fallback text, `empty_generation`, and no stored empty message |
| 8 | Per-persona eval cases and a stream eval mode | Each specialist persona has ≥ 3 cases with a resolved-agent check, and the gate runs through `stream` |
| 9 | Agent cleanup and live proof | `coding_agent` merged, renamed or described honestly; `AgentConfigRepository` removed; the delegation write limit is stated; GAP-025 and GAP-026 reports produced (owner, on the VPS) |

## KAN-22 System's APIs - Audit & Integration (8, assignee Ziad)

| # | Summary | Done when |
|---|---|---|
| 1 | Make the reachability matrix method-aware | The script matches by method and understands `_ws(id)`; the matrix is regenerated and CB-30's count is corrected |
| 2 | Triage the 43 uncalled routes | Every `none` row is wired (with a caller), removed, or marked `server-only` or `declared` with an owner |
| 3 | Carry retryable and retry_after through stream errors | The Dart frame test passes, and the chat UI shows a retry card with the wait |
| 4 | Keep Retry-After on the knowledge and voice clients | A 429 with `Retry-After: 9` gives `retryAfter == 9s` on the four calls (Dart tests) |
| 5 | Move desktop pair-approve onto AuraApiClient | A 403 `plan_required` shows a localized message; a 401 retries once (Dart tests) |
| 6 | Decide on attestation and workspace headers | Attestation is enforced or dropped from data clients (and CLAUDE.md corrected); every authenticated class sends `X-Aura-Workspace` or is listed as exempt |
| 7 | Align /agents and /tools | Both use the same capability set and the registered-tool intersection (backend test) |
| 8 | Input-handling fixes | `/admin/approvals?status=bogus` → 422; the upload read is bounded; the upload client is closed |

## KAN-23 System's Storage - Audit & Migration (12, assignee Fares)

| # | Summary | Done when |
|---|---|---|
| 1 | Refuse the in-memory cache in production | Production with `AURA_CACHE_BACKEND=memory` raises `ConfigurationError`; the kernel's in-memory approval fallback is removed there |
| 2 | Fail closed when Redis auth state is unavailable | Redis down at boot → `issue_token_pair` returns 503, and nothing is stored locally (test) |
| 3 | Move datasets and learning logs off the container disk | A dataset survives a redeploy; `services/learning_pipeline.py` is retired |
| 4 | Guard data-deleting downgrades | 0020, 0024, 0025 and 0028 refuse to run while rows exist (Postgres 16 test) |
| 5 | Run the ADR-032 move and the index rebuild on Railway | A committed report shows the dry-run and real counts; an agent-created task shows in the app; `/health/ready.index` names e5 |
| 6 | Stop the duplicate memory sync | The client no longer queues `memories` upserts; a migration drops `memories.fact_enc` |
| 7 | Retention purges, with beat confirmed on Railway | Purge jobs exist for each unbounded table; a Postgres test passes; the beat log shows them firing |
| 8 | Encrypt shared conversations and feedback, or record why not | Encrypted at rest, or an ADR explains why they stay plaintext |
| 9 | Railway backups and a restore drill | The cron is live, and a committed restore-drill report covers Postgres and Qdrant Cloud |
| 10 | Drift migration tests and sign-out cleanup | SchemaVerifier v1→v15 tests pass; `Desktop*` tables are empty after sign-out; `DesktopAgentState` is dropped (v16); `TelemetryLogs` is capped |
| 11 | One data-access layer for the shared tables | The decision is recorded, and the shared column rules live in one place |
| 12 | Verify the live schema after the next deploy | `/health/ready` shows `0030`, and the pre-deploy log shows `MIGRATION OK` |

## KAN-24 Flutter & System's Design - Study & Audit (9, assignee Belala)

| # | Summary | Done when |
|---|---|---|
| 1 | Add AuraButton sizes and remove _SmallAuraButton | `AuraButton(size: compact)` is tested, and `_SmallAuraButton` is gone |
| 2 | Retire the legacy AuraTheme | The four screens use `AuraPalette`; `theme.dart` is deleted; the light-theme contrast golden passes |
| 3 | Design-system ratchet tests | Ratchets for raw widgets, `Colors.*`, `fontSize` and `Courier`, with ceilings ≤ today's counts |
| 4 | Arabic localisation gaps | Plan-limit messages are localised (no Latin letters under `ar`); the text ratchet covers `lib/desktop`; status and date formats are localised |
| 5 | Accessibility fixes | 48 dp tap targets; semantics on tappable widgets; the Google logo is bundled (test) |
| 6 | Move off legacy StateNotifier | No `flutter_riverpod/legacy.dart` import remains |
| 7 | Decide the hidden personal-OS screens and dead routes | Screens re-routed or deleted; the unreferenced-file scan reports 0; every `GoRoute` has a caller or a documented reason |
| 8 | Split the oversized files | No file under `lib/` exceeds 800 lines |
| 9 | Check RTL and Arabic fonts on a device | A short report with screenshots of the IDE and app in Arabic on Windows and Android |

## KAN-27 Context Window - Improve (5, assignee you)

| # | Summary | Done when |
|---|---|---|
| 1 | Per-provider prompt cap and a workload-aware healthy window | An Engineer turn on `claude1` gets ≥ 100K; a chat turn uses the healthy minimum; an unhealthy provider doesn't shrink it (tests) |
| 2 | Clamp output tokens and enforce the cap | 8192 on an 8K window is clamped; an over-cap prompt is trimmed or raises `ContextOverflowError` (tests) |
| 3 | Close the history hole | With 8 exchanges, each is verbatim, summarised or covered by an omission marker (test) |
| 4 | Move the history summary to the DATA channel | The summary isn't SYSTEM; the fallback is marked; a tainted summary taints the turn (tests) |
| 5 | Never drop tool output silently | A dropped or truncated tool result gets a pinned note and `tool_output_dropped`; large results keep head and tail (tests) |

Planner history lives in KAN-21 #1.

## KAN-28 Tokens Handling - Improve (5, assignee you)

| # | Summary | Done when |
|---|---|---|
| 1 | Per-provider tokenizers | Within ±5% of the reference tokenizer on English, Arabic and code per provider kind; `counts_are_exact` is correct |
| 2 | One estimator for router, quota and context | The same prompt-token figure from all three for one request; Arabic `USER_TPD` refusal test passes |
| 3 | Count each context item once | A call-counting tokenizer sees each history message once per `assemble` |
| 4 | Turn-wide usage totals, with estimates marked | `done.usage` and telemetry sum every generation; `usage_estimated` is set when the provider reports nothing |
| 5 | Separate cap for engine generations | Planner, replan, reasoning and summary generations count against their own per-turn cap; a tree-of-thoughts turn stays within it |

## KAN-29 Latency Time - Maximize (5, assignee you)

| # | Summary | Done when |
|---|---|---|
| 1 | Latency baseline on Railway | Turn buckets go past 60 s with a `planned` label; a committed load test at 1, 5 and 10 users reports TTFT and answer p95 |
| 2 | Remove repeated per-turn lookups | Desktop session and workload resolved once; model assignment cached and skipped when the registry is empty (counting tests) |
| 3 | Do post-answer work after done | Summarisation, telemetry and classification run after `done`; with a 5 s summariser, `done` arrives within 100 ms (test) |
| 4 | Embed the message once per turn | One embedding call per turn (counting fake) |
| 5 | Check serviceability before memory and retrieval | A turn that no eligible provider can serve fails before memory and retrieval run (test) |

The stream eval mode lives in KAN-21 #8, and the generation cap in KAN-28 #5.
