# Incomplete tools: simulated and live end-to-end proof

Four suites under `aura_backend/tests/e2e/` exercise the 55 incomplete-tool rows through the real
`ToolRegistry.execute` (Engineer persona) or, for classes that are not agent tools, their public API.

| File | Needs | What |
|---|---|---|
| `test_incomplete_tools_coding_simulated.py` | nothing | filesystem, run_command, git, workspaces, GitHub, review policy, remote approval |
| `test_incomplete_tools_analysis_simulated.py` | nothing | review, scan, memory, drift, release, SQL, logs, transcription, AST |
| `test_incomplete_tools_classes_simulated.py` | nothing | class-only tools, relay/desktop, streaming, registry, graph, eval tools |
| `test_incomplete_tools_live_env.py` | real data fixtures; live parts need `AURA_VIRTUAL_ENV=1` | real OSV advisories, real Alembic set, real Redis, PostgreSQL, Chromium, ffmpeg |

Each test docstring says what is simulated. Stand-ins: `InMemorySandboxRunner` (real subprocesses, no isolation),
`FakeDockerClient` (no daemon), `LocalRemoteGit` (only `remote_url` replaced; real git), `InMemoryGitHubClient`,
`ScriptedPlanner`, a scripted `aura_hostd` behind the real host-agent runner and relay, `simulate_public_hosts`
(DNS and TCP only; the real SSRF check runs), a scripted Playwright module and a scripted whisper model.

## Virtual environment

`scripts/virtual_env_up.sh` starts a real Redis and PostgreSQL under `/tmp/aura_virtual_env`, installs Playwright and
Chromium for the test interpreter outside the repo and writes `AURA_VENV_*` variables to `/tmp/aura_virtual_env/env`.
`scripts/virtual_env_down.sh` removes it. Nothing touches `requirements*.lock`.

```
scripts/virtual_env_up.sh
set -a; . /tmp/aura_virtual_env/env; set +a
AURA_VIRTUAL_ENV=1 pytest tests/e2e/test_incomplete_tools_live_env.py
scripts/virtual_env_down.sh
```

Real data fixture: `tests/fixtures/osv/real_osv_advisories.json` (OSV records from api.osv.dev, 2026-11-23).

## Bugs found and fixed

* git_status / git_diff / git_commit / git_checkout_branch failed after `clone_repository` in a durable workspace
  (they ran at the workspace root, not the checkout). `aura_core/tools/coding.py`.
* `inspect_sql` returned zero tables for the repo's real Alembic migrations: it walked `downgrade()` as well as
  `upgrade()` and ignored raw `op.execute` DDL. `aura_core/tools/open/sql_schema_ext.py`.
* `InMemorySandboxRunner` / `DockerSandboxRunner.create_task` rejected the `origin` argument the tool layer passes.

## Recorded design limits (strict xfail)

* No live output or per-tool progress inside a running step (row 65).
* No speaker diarization (row 79); pyannote and its gated model are not available here.
* Workspace code files are not chunked or retrievable by section unless ingested (row 82).

Not testable here: the Dart daemon's command policy and worktree merge (rows 11, 58): no Dart SDK.
