# ADR-045 — Workspace-reading tools: repo index, stale-context ledger, conventions, skills, notebooks, binaries, backups, run status

**Status:** Proposed (built, not proven) · **Date:** 2026-11-23 · **Related:** ADR-006 (trust channels,
confirmation), ADR-014 (`workspace_local`), ADR-036 (open analysis tools), ADR-042 (file readers under the
`read_file` root), ADR-011 (host daemon) · **Inventory rows:** 17, 18, 19, 22, 25, 29, 30, 61, 70, 77, 83, 96, 101

## Context

Thirteen inventory rows were `Open Source` or PARTIAL for the same reason: the class existed in
`aura_core/tools/open/`, or a wrapper existed in `tools/analysis.py`, but no agent could point it at
the user's files.

| Row | State before | What was missing |
|---|---|---|
| 25 repo_semantic_indexer | class only | not a tool |
| 29 stale_context_detector | class only | nothing recorded what the agent had read; `write_file`/`edit_file` never compared |
| 30 project_conventions_loader | class only | AGENTS.md / CLAUDE.md never reached the agent |
| 61 skills_rules_workflows_loader | class only | not a tool; named a skill by its file stem; ignored `.claude/` |
| 70 project_status_qa_tool | class only | needed records passed in; nothing read the recorded runs |
| 77 notebook_reader | class only | `read_file` showed raw `.ipynb` JSON |
| 83 binary_file_inspector | class only | no hash/type/metadata tool |
| 96 backup_restore_verifier | class only | not a tool; no workspace coverage |
| 19 `check_requirements` | pasted text only | no lockfile, consistency, environment, import checks |
| 22 `analyze_change_impact` | pasted text only | could not scan for importers; test names were guesses |
| 101 `scan_dependency_licenses` | licences supplied by the caller | no lockfile or installed-metadata reading |
| 18 `verify_python_symbols` | undefined names + arity of pasted text | imports and imported members never checked |
| 17 `scan_code_security` | pasted text only | no file scan, no dependency vulnerability check |

## Decision

A new module, `tools/open_workspace.py` (with `tools/workspace_deps.py` for dependency files), adds **nine
tools** and **extends five**. Nothing here changes an existing behaviour.

### Registration

* The eight file tools register only when `AURA_TOOLS_FILES_ROOT` is set (the `read_file` root; one path
  rule, `tools/confinement.py::resolve_within`: resolve, follow symlinks, compare with the root). No new
  setting. `project_status` registers when a run store exists (it reads no file).
* The five extended analysis tools keep their names and every parameter; the new ones are optional.
  `build_analysis_tools(workspace=...)` swaps in the superset only when a root is configured, and a call
  without the new arguments runs the **original handler** (`tests/unit/test_open_workspace_tools.py` compares
  the two outputs on the same inputs).
* **Grants.** The nine new tools go to the Engineer only. They return workspace content, so each is tagged
  `untrusted_content` (ADR-006) and `coding_agent` -- which must hold no such tool -- gets none. The five
  extended tools stay untagged because `coding_agent` holds them; instead each refuses its workspace
  arguments to a persona that does not hold `search_repository` (the marker for "may read the workspace").
* Every walk uses `os.walk(followlinks=False)` and skips a file link whose target leaves the root.
  Every output path is relative to the root. Inputs are capped (size, file count, time).

### The tools

| Tool | Row | What it does | What it cannot do |
|---|---|---|---|
| `index_repository`, `search_repository` | 25 | In-memory index (files, 50-line chunks, symbols: Python via `ast`; JS/TS/Dart/Go/Rust/Java/Kotlin via regexes), rebuilt when a file's mtime/size changes; ranked search with matching lines | No embeddings and nothing written to the knowledge index. The name says "semantic"; it is filename + keyword + symbol matching |
| `check_file_staleness` | 29 | `record` a content hash, `check` later: `fresh`, `touched_only`, `changed`, `deleted`, `refused`; **`no_baseline` is never `fresh`** | Baselines live in process memory; a restart forgets them |
| `load_project_conventions` | 30 | AGENTS.md / CLAUDE.md / AURA.md / RULES.md / .cursorrules / copilot instructions / CONTRIBUTING / README / .editorconfig / linter config from the folder up to the root; must-do and forbidden directives; `check_file` against the editorconfig rules that match it | The text is the project's, not the user's: the result says it never overrides the user |
| `load_project_skills` | 61 | list / match / read / workflow over `.aura/` and `.claude/` (and top-level `skills/ rules/ workflows/`); real names from frontmatter, `SKILL.md` named by its folder | Nothing activates a skill on its own; the model asks |
| `read_notebook` | 77 | Ordered cells, outputs (text, errors, image mime+size), kernel/language, `as_script` | Image data is not returned; nothing is executed |
| `inspect_binary_file` | 83 | Type, size, hashes, entropy, optional strings, header metadata (ELF/PE/PNG/GIF/JPEG/SQLite/WAV/gzip/zip-family/tar/PDF) | Format table is magic-byte based |
| `verify_backup` | 96 | Checksum (value or `.sha256` file), archive integrity and unsafe members, `.sql` structure, restore drill into a temp dir that is removed, `compare_to` a live folder | **Verify only.** Writes nothing but that temp dir; never opens a database: a `.sql` dump is checked statically, not replayed (the class would replay it into in-memory SQLite; the tool does not call that path). Restoring into PostgreSQL is not done |
| `project_status` | 70 | Answers from this user's recorded `EngineerRun`s and `AgentRun`s (owner-scoped in the store); empty history is "nothing recorded", never "no errors" | Only what the stores recorded |

### The stale-context ledger (row 29)

`FileReadLedger` keeps `(user, space, path) -> sha256, size, how`, bounded (1000 entries/user, 500 users).
Two spaces, because they are two file systems: `files` (the server's workspace root; fed by `read_file`,
`read_notebook`, `inspect_binary_file`, `load_project_*` and `check_file_staleness record`) and `sandbox`
(the Engineer's own sandbox or paired desktop; fed by `write_file`/`edit_file`).

`write_file` and `edit_file` **warn, never block**: if the file now differs from what the agent last read or
wrote there, the result gets a `[stale-context warning]` line (`write_file` first re-reads the file through
the runner, only when a baseline exists). This needs **nothing from the host-daemon protocol**: both tools
already read through `SandboxRunner`. What it cannot do: a file the agent only read with `run_command cat`
has no baseline, and `write_files` clears the baselines it touches rather than guess content. The daemon's
own refusal after approval (`task_handler.dart`) is unchanged and still the real guard.

### Dependency tools read the project's files, offline (rows 19, 101, 17)

`workspace_deps.py` parses `requirements*.txt/.lock`, `pyproject.toml` (PEP 621 and Poetry), `Pipfile`,
`poetry.lock`, `uv.lock`, `Pipfile.lock`, `package.json`, `package-lock.json` (v1, v2/v3), `yarn.lock`,
`pnpm-lock.yaml`.

* `check_requirements(paths=...)`: lockfile presence; manifest-versus-lock agreement (declared but not
  locked, locked version outside the declared range); a **`pip check`-style test of the project's own
  virtualenv** from `*.dist-info/METADATA` on disk (no process; markers are not evaluated, only counted);
  declared-but-unimported packages and imports nobody declares. The gateway's own environment is
  deliberately not inspected (`run_pip_check` checks the wrong interpreter for a user project).
* `scan_dependency_licenses(paths=..., project_license=...)`: licences from the lockfile field, the
  workspace's `node_modules` and `site-packages` metadata, same-name-and-version metadata in the gateway's
  environment, then a small built-in table (marked *inferred*), else `UNKNOWN`; every violation says where
  its licence came from. Adds `check_license_compatibility` and supply-chain facts (integrity hashes, git/URL
  dependencies). **Outdated packages are not checked**: that needs a registry.
* `scan_code_security(paths=..., advisories_path=...)`: scans workspace code, config and `.env`-style files
  (secret values are never returned; lockfiles and minified files are skipped). **Dependency
  vulnerabilities:** neither this repository nor the locked environment bundles an advisory database, and
  the tool makes no network call, so by default it reports `dependency_vulnerabilities.checked: false` and
  why. With an OSV-format advisory file placed in the workspace and named in `advisories_path`, it matches
  locked versions against it. A package with no entry is not shown to be safe, and the result says so.
  ADR-046 adds a server-side copy of the public OSV advisories (off by default, downloaded by a scheduled
  job): with it the scan matches against that copy and reports its age; if it was never downloaded the
  result says the packages were **not** checked.
* A small PEP 440 implementation lives in `workspace_deps.py` because `packaging` is not a direct dependency;
  `tests/unit/test_open_workspace_tools.py` pins it against `packaging` over 495 version/specifier pairs.

### `analyze_change_impact(repo_dir=...)` (row 22)

Scans the folder for importers (Python via `ast`, relative JS/TS imports), follows them up to four hops
(`affected_by_distance`), and lists the **existing** tests that import or are named after the change; naming
guesses are reported separately with whether they exist. It says what it does not do: it runs nothing, so it
cannot detect a regression.

### `verify_python_symbols(paths=...)` (row 18)

Resolves each import without importing anything: standard-library *source*, workspace modules, a virtualenv
inside the workspace, then declared-but-not-installed (reported `unresolved_third_party`, never "present").
Checks that `from m import x` names something `m` defines (an `import *` standard-library module that the
gateway already has loaded is checked by attribute; otherwise `unverified`) and derives expected arities from
the real `def` of imported workspace functions. The standard library is the gateway's Python, not the
project's, and the result says so.

## Consequences

* The Engineer's tool list grows by nine descriptions when a files root is set. On the CPU-hosted model the
  planner prompt is the budget; the tools can be trimmed with `AURA_TOOLS_DENYLIST`.
* The ledger adds one extra runner read to `write_file` only for a file that has a baseline.
* Nothing is written to the knowledge store, no table is added, no route is added, no setting is added.

## Not proven

Built and tested against real files through `ToolRegistry`/`Container.tools.execute` on a Linux host, with
in-memory run stores and the in-memory sandbox runner. Not run: a paired desktop daemon (the warning path
uses the same `SandboxRunner` calls, but it has not been exercised against `aura_hostd`), a Postgres-backed
run store for `project_status`, a restore into a real database (by design), Windows path handling, projects
with very large trees (the caps are tested only for being honoured on small inputs).
