# ADR-036 — Exposing the `tools/open` code-analysis classes to the agent, in three risk tiers

**Status:** Proposed (built, not proven) · **Date:** 2026-11-20 · **Related:** ADR-006 (trust
channels, confirmation), ADR-008 (human-in-the-loop promotion), ADR-011 (desktop engineer agent),
ADR-013/014 (engineer workspaces, `workspace_local`), ADR-022 (repository code is parsed, never
run) · **Gap:** GAP-066

## Context

Two merges from the `princess` branch (PR #271) added 29 plain Python classes under
`aura_backend/aura_core/tools/open/`: AST and complexity analysis, a security pattern scanner,
dependency and licence checks, a test generator and runner, coverage and mutation scoring, a SQL
linter, Mermaid generation, deployment and dev-server helpers, and a handful of model-ops
utilities. Nothing registered or imported them, so the kernel could not call any of them.

Making them callable is not a wrapping exercise. Review found three things:

1. **They are not tools.** `FunctionTool` accepts only `async` handlers (`tools/base.py`) and
   returns `str`; every class is synchronous and returns a dict.
2. **They have very different risk.** Most are pure text-in/dict-out. Several run project code,
   spawn processes, read arbitrary paths, `eval` strings or import user-named modules, all in the
   gateway process with no container under them.
3. **Some were wrong, and some already exist better.** Bugs that would have become reachable
   (`git diff -- <ref>` always empty, `LGPL-3.0` flagged as `GPL-3.0`, a quality gate that
   approves when nothing ran, a drift monitor that reports "no drift" on NaN) are fixed and pinned
   by `tests/unit/test_open_tools_fixes.py`. Six classes duplicate engines the platform already
   has, in a weaker form.

## Decision

Expose the classes in **three groups, one tool per class** (with an `operation` enum where a class
has independent checks), because `PlanningEngine` renders every available tool into its prompt and
on the CPU-hosted model that prompt is the budget.

### Group A — analysis (`tools/analysis.py`): on by default

Seventeen tools (eighteen since 2026-11-21: see the addendum). Each takes text the caller supplies and returns JSON; none reads a path, spawns a
process, imports user code or keeps state. So none needs a capability or a confirmation, and none
is tagged `untrusted_content`. Inputs are length-capped, because `asyncio.to_thread` cannot be
cancelled — the cap, not the timeout, is the CPU bound.

`analyze_python_ast`, `analyze_code_complexity`, `verify_python_symbols`, `review_code_diff`,
`scan_code_security`, `analyze_change_impact`, `generate_diagram`, `check_requirements`,
`scan_dependency_licenses`, `compare_api_specs`, `inspect_sql`, `analyze_logs`,
`validate_data_records`, `evaluate_quality_gate`, `check_model_drift`, `synthesize_research`,
`plan_rollout`.

Deliberately **not** exposed from these classes: every method that takes a filesystem path
(`analyze_file`, `scan_file`, `check_lockfile_presence`, `BackupRestoreVerifier`), `run_pip_check`,
Bandit, and `check_runtime_imports` (it imports modules the analysed source names).

### Group B — code execution (`tools/open_exec.py`): off by default, accepted risk

`generate_and_run_tests` and `analyze_test_quality` run pytest, coverage and a boolean-mutation
pass against code the caller supplies, **in a child process of the gateway**. The owner chose to
register these with a per-call confirmation rather than defer them; this ADR records that as an
accepted risk (GAP-066), not as a safe design. The fences:

- Off unless `AURA_OPEN_EXEC_TOOLS_ENABLED=true`.
- `engineer_agent` capability, `side_effecting` at `CONFIDENTIAL`, so `ToolSpec` forces a
  confirmation on every call. Not `workspace_local`.
- Tagged `untrusted_content`: pytest output echoes the code it ran, so the turn is tainted.
- **Inline text only.** No parameter is a path; the child runs in a fresh temp directory.
- **Scrubbed environment** (`tools/open/_process.py`): only `PATH`, `TEMP`, locale and similar are
  passed, so the code under test cannot read the gateway's provider keys or JWT secret. Third-party
  pytest plugins do not auto-load.
- At most two children at once; per-run timeouts; output capped so the JSON stays valid.

What this does **not** give: isolation. The child shares the host's filesystem, network and CPU.
The proper home for this group is the `SandboxRunner` port, and it moves there when a Docker-backed
runner is wired for these tools (exit condition of GAP-066).

### Group C — desktop operations (`tools/desktop_ops.py`): needs a paired desktop

`docker_build`, `docker_compose` and `profile_python` run on the **user's own machine** through
`aura_hostd`. The tool builds a validated argv — a tag, service or path that could become an option
is refused — and hands it to the same `SandboxRunner` that backs `run_command`, so the daemon's
command policy and approval queue apply. They are `engineer_agent`-gated, confirmed and tainting,
and deliberately not `workspace_local`: `docker` reaches outside the workspace. A user without a
paired desktop gets a plain refusal; the gateway never runs `docker` (it has none, and its socket
proxy forbids `exec`).

### Superseded — not registered

| Class | Replaced by |
|---|---|
| `KnowledgeGraphManager` | `knowledge/graph_pipeline.py`, `domain/graph.py`, `adapters/postgres_graph.py` |
| `ModelRegistryAndEvaluationManager` | `mlops/model_registry.py` (approval-gated promotion, ADR-008) |
| `WorkflowDependencyValidator` | `domain/planning.py` (`Plan._assert_acyclic`, `ready_steps`) |
| `AgentToolTestHarness` | `evaluation/harness.py`, `ToolRegistry.execute` |
| `DataLineageProvenanceTracker` | `domain/graph.py` / `domain/ausm.py` provenance |
| `GpuResourceScheduler` | `observability/metrics.py` GPU gauges, `adapters/ml/device.py` |

Each file carries a note saying so, and `tests/architecture/test_superseded_open_tools_unused.py`
fails if anything under `aura_core` imports one.

### Deferred — needs new `aura_hostd` RPCs

`DevServerManager` (long-running processes, a global PID table, `shell=True`) and `BrowserSubagent`
(Playwright, no SSRF policy) cannot be made safe in the gateway and need `dev_server.*` and
`browser.run` RPCs in the Dart daemon. Not built here; they need their own change and Host Daemon
CI. `PerformanceProfiler`'s `eval`/`exec` of strings is removed; `profile_python` replaces it.

### Who sees what

Tool visibility is a persona grant (`agents/registry.py`). `coding_agent` gets the code, dependency
and data analysis tools; `research_agent` gets `synthesize_research` and `validate_data_records`;
`engineer` gets all of Group A plus Groups B and C; the orchestrator already sees whatever the
caller's capabilities allow. `decision_agent` and the personal-data personas get none. The names
are literals in `agents/registry.py` (it does not import `tools`), and
`tests/unit/test_analysis_tools.py` pins them to the tools the builders register.

## Consequences

- A plain user gains seventeen read-only analysis tools in production. They widen what a persona
  can *see*, not what it can *do*. The cost is planner prompt size, measured when this landed: the
  17 tools add about 5.8K characters (~1.5K tokens) to the tool list `PlanningEngine` renders for
  the orchestrator and the engineer, on a baseline of about 4.6K characters. Specialised personas
  see only their grant. `tests/unit/test_analysis_tools.py::TestPlannerPromptBudget` pins a 7,000
  character ceiling, so adding to the group is a decision. The effect on plan quality and latency
  on the production model is not measured.
- Railway auto-deploys every `main` push with no CI gate, so the safe default matters: only
  Group A and the (inert without a paired desktop) Group C are on by default; Group B is off.
- Group B is real code execution in the gateway when enabled. The mitigations above lower the
  blast radius; they do not remove it.
- The fixes to the classes change a few contracts (`QualityGateAggregator` check arguments default
  to "not run", drift reports `drift_detected: True` with `status: "invalid_input"`); nothing else
  in the repo called them.

## Alternatives considered

- **Register all 29 as-is.** Rejected: `eval`/`exec`, `shell=True`, global process table, path
  reads on the gateway, and a model registry that bypasses the approval gate.
- **Defer Group B until a sandbox runner exists.** The sound option; overruled by the owner in
  favour of an opt-in, flag-gated, confirmed registration. Recorded as GAP-066 with the migration as
  its exit condition.
- **One tool per method.** Rejected: ~80 specs in the planner prompt.

## Addendum — triage of the 25 modules added 2026-11-19 (2026-11-21, KAN-30…35)

On 2026-11-19 a commit added 25 more classes under `tools/open/`, and a later merge
(`a0024eae`/`fee9dc96`) both left conflict markers in the directory (fixed in `9ba1dbd5`: all of
`aura_core` had stopped importing) and silently reverted two of the fixes above: licence matching
went back to exact SPDX ids (so `GPL-3.0+`, `BUSL-1.1`, `""` and `NOASSERTION` passed, and OR/AND
were ignored) and the drift monitor went back to checking NaN only (so `inf` passed and a NaN inside
a sample was never looked at). Both are restored (KAN-30), with the methods added since kept.

**One list, checked.** `tools/open/_classification.py` now records every module's status —
`wrapped`, `superseded`, `deferred` or `not_exposed` — and
`tests/architecture/test_open_tools_classification.py` fails when a module is not in it, when the
builders import a module that is not `wrapped`, when anything else under `aura_core` imports one
that is not, when a non-wrapped file lacks its status header, when a module does not import, or
when a file carries a merge-conflict marker. `test_superseded_open_tools_unused.py` reads its list
from the same place. "Not exposed" therefore means *imported by nothing, so registered as no tool,
so granted to no persona* — a checked claim, not a comment. No module was deleted; retiring one is
a status plus a header.

**Decisions.** One module is wrapped. The bar was: pure text-in/JSON-out *and* something the model
cannot do reliably by reading the text itself, because each tool costs planner prompt on every
turn (a separate ticket is shrinking that prompt).

| Module | Decision | Why |
|---|---|---|
| `structured_data_parser` | **wrapped** → `validate_structured_text` | Whether JSON/YAML/TOML/XML parses, and the exact line and column when it does not, is deterministic work a model gets wrong. The result summarises the parsed value (root type, top-level keys) instead of echoing it: a YAML alias bomb is small as references and exponential once serialised. YAML is `safe_load`; expat refuses XML entity-expansion bombs and fetches no external entities. Both pinned by tests. YAML errors now report their line. |
| `background_task_manager` | not exposed (KAN-34) | `Popen` in the gateway with `os.environ.copy()` — the child inherits provider keys and the JWT secret — and a process table with no owner scoping. Long-running processes on the user's machine are `aura_hostd`'s terminal sessions. |
| `push_notification_dispatcher` | not exposed (KAN-34) | POSTs to any caller-named URL with `urllib` and no SSRF guard (contrast `fetch_url`'s `_assert_public_url`); without a URL it reports `delivered` when nothing was sent. |
| `binary_file_inspector` | not exposed (KAN-34) | Reads paths on the gateway. |
| `notebook_reader` | not exposed (KAN-34) | Treats every argument as a path first and reads it on the gateway. |
| `file_type_detector_router` | not exposed (KAN-34) | Reads paths and walks directories on the gateway. |
| `stale_context_detector` | not exposed (KAN-34) | Reads and hashes paths on the gateway. The useful version (has a file on the user's machine changed since the agent read it) would be an `aura_hostd` RPC; none is planned. |
| `remote_session_controller` | not exposed (KAN-34) | A process-local dict of session states connected to nothing: `pause` reports `paused` while the real run continues; no owner scoping. |
| `archive_extractor` | not exposed | Reads and writes paths on the gateway. |
| `project_conventions_loader` | not exposed | Reads and walks workspace paths on the gateway; the Engineer reads convention files with `read_file`. |
| `skills_rules_workflows_loader` | not exposed | Reads workspace paths on the gateway; the rest is frontmatter regex the model replaces by reading. |
| `repo_semantic_indexer` | not exposed | Walks directories on the gateway; duplicates the knowledge engine's indexing and retrieval. |
| `ui_visual_verifier` | not exposed | Reads any existing path on the gateway; image bytes do not travel well as tool JSON; the "perceptual" hash is over raw compressed bytes. |
| `verification_evidence_collector` | not exposed | A digest over caller-supplied evidence proves nothing about whether commands ran; a missing exit code counts as 0; reads paths. |
| `acceptance_criteria_verifier` | not exposed | Marks criteria `ACCEPTED` on substring overlap (an empty evidence key matches every criterion): a rubber stamp. `evaluate_quality_gate` is the fail-closed gate. |
| `scope_and_diff_guard` | not exposed | Overlaps `scan_code_security` and `analyze_change_impact`, and `lstrip("./")` strips characters, so a root `.env` becomes `env` and the default `.env*` pattern misses it. Fix before any exposure. |
| `multi_language_ast_analyzer` | not exposed | Regex line matching for JS/TS/Dart/Go/Rust is imprecise and backtracks quadratically on a long line (40K characters of `a(` took 1.3 s; the 200K cap would be ~30 s, uncancellable under `asyncio.to_thread`). The Python half duplicates `analyze_python_ast`. A candidate after a per-line cap and a better extractor. |
| `source_content_explainer` | not exposed | Duplicates `analyze_python_ast` plus reading the model does itself. |
| `architectural_consistency_checker` | not exposed | Pure, but needs a module→imports map the caller assembles first, and its layers are generic; AURA's own layering is enforced by `tests/architecture`. Not worth the prompt today. |
| `ambiguity_and_clarification_detector` | not exposed | Word-list scoring of vague terms; the model judges ambiguity better. |
| `artifact_feedback_ingestor` | not exposed | Regex plan edits the model makes itself. |
| `artifact_generator` | not exposed | Markdown templates the model writes itself; `save_artifact_to_disk` writes any path. |
| `automated_quiz_generator` | not exposed | Template output (every answer is "A"). |
| `mobile_artifact_summarizer` | not exposed | Presentation formatting that belongs in `aura_client`. |
| `project_status_qa_tool` | not exposed | Keyword search over an execution history no AURA engine supplies. |

None is deferred: no module among the 25 is something AURA plans to build as an `aura_hostd` RPC
now. Two older modules stay deferred (`dev_server_manager`, `browser_subagent`), and the two older
unwrapped ones not covered above (`performance_profiler`, `backup_restore_verifier`) are now
recorded as not exposed, with headers.

**Consequences.** Group A is eighteen tools. `validate_structured_text` is granted with the code
analysis set (`coding_agent`, `engineer`; the orchestrator sees it by capability as before). The
group's planner-prompt cost went from 5,837 to 6,077 characters; the 7,000 ceiling in
`TestPlannerPromptBudget` is unchanged. Like the rest of this ADR: built and tested, not measured on
the production model.

## Not proven

Built and unit/architecture tested. No live evidence: Group C needs a paired desktop running a
real daemon, Group B has not been run with the flag on in a deployed environment, and the planner
prompt cost on the production model is unmeasured. GAP-066 stays `built_not_proven` until a report
under `docs/reports/` shows those three.
