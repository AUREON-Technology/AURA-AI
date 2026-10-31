# Developer Guide

How to work in `aura_core`: the layering rules, the extension points, and the conventions that
the test suite enforces.

---

## Setup

```bash
cd aura_backend
python -m venv .venv && source .venv/bin/activate
pip install --require-hashes --extra-index-url https://download.pytorch.org/whl/cpu -r requirements.lock
pip install -r requirements-dev.txt   # a second install: the lock is hash-pinned, this is not
pytest                      # 1010 tests, ~7 seconds
```

The suite needs **no Docker, no network, no API key, and no model**. That is a design property,
not a convenience: `aura_core.domain` and `aura_core.ports` import only the standard library,
and the in-memory adapters are real implementations of every storage port. If a change makes
the suite require infrastructure, the change is wrong.

---

## Layering

```
domain/  ports/          stdlib only — entities, value objects, Protocols
engines                  domain + ports only — prompts, context, memory, knowledge,
                         tools, safety, planning, reasoning, execution, llm
adapters/  api/          vendor SDKs, lazily imported
container.py             the only place concrete meets abstract
```

The arrow never reverses. Fitness functions in `tests/architecture/` enforce this and fail
the build:

| Test | Rule |
| --- | --- |
| `test_core_layers_import_only_stdlib` | Nothing outside the stdlib in `domain/` or `ports/` (ports may use `domain`), at any scope |
| `test_engines_do_not_import_adapters` | No engine (every discovered package that is not core, `adapters`/`api`, `config`/`observability` or `container`) imports `adapters`, `api` or `container` at any scope, or any third-party module at import time |
| `test_vendor_sdks_are_imported_lazily` | No vendor SDK (`VENDOR_MODULES`) imported at module scope anywhere in `aura_core` |
| `test_only_the_composition_root_imports_the_container` | Nothing in `aura_core` imports `container` |
| `test_no_import_time_network_connections` | No client constructed at import (TD-01) |
| `test_ports_are_async` | Every port I/O method is a coroutine |

A layering rule that lives only in a document decays within weeks.

---

## Adding a provider

Most vendors need **no new adapter**: if the endpoint speaks OpenAI-compatible
`/v1/chat/completions`, point `OpenAICompatibleProvider` at it via configuration. vLLM, Ollama,
TGI, llama.cpp, Together, Groq, and OpenRouter all qualify.

For a genuinely different wire format:

1. Subclass `BaseProvider` in `aura_core/llm/providers/`. Implement `complete`, `stream`, and
   `health`. Inherit client lifecycle and error translation.
2. Declare capabilities honestly. The router filters on them, and a wrong `context_window`
   produces truncation surprises rather than a clean rejection.
3. **Do not yield before the upstream connection is established.** The port contract requires
   connection errors to surface *before* the first chunk, because after that point failover
   would duplicate output.
4. Add a branch to `registry.build_provider`, importing your module **inside** the branch so a
   deployment that does not use it never imports it.
5. Add a test class in `tests/unit/test_providers_and_platform.py` using
   `httpx.MockTransport`. Translation is where an adapter is actually wrong, and that is fully
   testable offline.

## Adding a tool

```python
from aura_core.tools import param, tool
from aura_core.domain.tools import ToolCategory, ToolSensitivity

@tool(
    "create_task",
    # The model selects on this text. Say when to use it and what it returns.
    "Create a task in the user's task list. Returns the created task's id.",
    param("title", "string", "Short imperative description", max_length=200),
    param("due_date", "string", "ISO 8601 date, or omit for no due date", required=False),
    category=ToolCategory.PRODUCTIVITY,
    sensitivity=ToolSensitivity.PERSONAL,
    side_effecting=True,        # changes state outside AURA
    required_capability="tasks.write",
    timeout_s=10.0,
)
async def create_task(title: str, context, due_date: str | None = None) -> str:
    ...
```

What the framework does for you, in order: resolution, JSON-schema argument validation (unknown
keys rejected, narrow coercion), policy evaluation (capability grant, taint, confirmation),
timeout, concurrency limit, result truncation, metrics, and an audit record for every attempt
including refusals.

What you must get right:

- **`side_effecting`** — a tool that changes the world cannot run in a turn whose context was
  quarantined without explicit confirmation. This is what breaks the SEC-02 attack chain.
- **`required_capability`** — fails closed. No grant, no dispatch.
- **Validated arguments are still attacker-influenced.** Their *shape* is guaranteed; their
  *values* are not.
- **Handlers must be async.** A synchronous handler blocks the loop for every concurrent
  request; `FunctionTool` rejects one at construction.

## Adding a guard

```python
from aura_core.safety.guards import Guard
from aura_core.domain.safety import GuardAction, GuardStage, GuardVerdict

class MyGuard(Guard):
    name = "my_guard"
    stages = (GuardStage.OUTPUT,)

    def _evaluate(self, text, *, stage, context) -> GuardVerdict:
        ...
```

A guard is pure: it takes text, returns a verdict, never mutates, never decides whether the
turn proceeds, never raises. Aggregation and enforcement belong to the pipeline, which applies
the strictest verdict.

Choose the action deliberately. Exfiltration guards (secrets, prompt leakage) **fail closed** —
a blocked answer is recoverable, a leaked credential is not. Quality guards (groundedness)
**fail open** with a metric — treating a noisy proxy as an enforcement signal produces exactly
the confidently-wrong behaviour it is meant to measure.

## Adding a prompt

```python
PromptTemplate(
    id="task.my_task",
    version="1.0.0",
    description="What this prompt is for.",
    variables=variables(("input", "What the caller supplies"), ("optional", "…", False)),
    body="""Do the thing with {{input}}.
{{#optional}}
Also consider {{optional}}.
{{/optional}}""",
)
```

Undeclared variables raise at construction, so a typo fails at import rather than emitting a
literal `{{ usre_name }}` into a production prompt. Bump the version on any body change: the
checksum makes an un-bumped edit detectable, and telemetry records which version ran.

---

## Conventions

**Errors are typed.** Every failure is an `AuraError` subclass with a stable `kind` slug (used
as a metric label), a `retryable` flag, and structured `details`. Never `log-and-return-empty`:
the caller cannot distinguish "no results" from "the store is down", which is TD-09 and the
reason 47 sites in the legacy code hide failures.

**Degradations are counted.** If code falls back, retries, quarantines, or skips, increment a
metric. A system that degrades silently cannot be operated.

**Telemetry carries no content.** Prompts, documents, memories, and outputs never enter
metrics, logs, or the telemetry record. Lengths, hashes, counts, and scores do. The logging
filter enforces this for `extra=` fields, but do not rely on it — do not log the content.

**Entities are immutable.** Domain objects are frozen dataclasses; transitions return new
instances. The execution engine runs steps concurrently, and immutability is why that needs no
locking.

**I/O is injected.** Nothing constructs its own collaborators. If a class needs a store, a
clock, or a provider, it takes one.

---

## Testing

Tests assert **behaviour**, not call choreography. Nothing internal is mocked — the in-memory
adapters are the substrate, so a test exercises the same engine code production runs.

```python
async def test_out_of_corpus_query_returns_nothing(knowledge, user, handbook):
    await knowledge.ingest(content=handbook, title="Handbook",
                           document_id=DocumentId("handbook"), owner_id=user)
    result = await knowledge.retrieve("the melting point of tungsten", owner_id=user)
    assert not result.chunks
    assert result.is_empty_but_searched
```

Useful fixtures (`tests/conftest.py`): `container` (fully wired), `knowledge`, `memory`,
`tools`, `router`, `safety`, `echo` (deterministic provider), `clock` (`FrozenClock`, so decay
and circuit-breaker timing are testable without sleeping), `handbook`, `poisoned_document`.

`EchoProvider` echoes its input, so a test can assert exactly what the model was shown — which
catches context-assembly regressions a canned response would hide. `ScriptedTurn` queues
specific responses, tool calls, delays, or errors.

Six real defects were found by writing these tests: confidence exceeding its valid range under
clock skew, SimHash being unusable on short documents, `EchoProvider` ignoring its declared
pricing, self-consistency scoring agreement by a different rule than the vote it reported,
`TimeSeriesSpec` silently sizing a typo'd backbone as a transformer, and a benchmark suite that
was never collected because `python_files` did not match its filename. That is what the suite is
for.

Coverage is 90% of `aura_core`, measured with `pytest --cov=aura_core`. The uncovered remainder
is mostly defensive branches (`pragma: no cover`) and the torch-dependent `ml/nn.build`, which a
deployment that never trains does not install.

---

## Where things live

| Question | File |
| --- | --- |
| How is a turn orchestrated? | `kernel/kernel.py` |
| Which provider serves a request? | `llm/router.py` |
| What does the model actually see? | `context/engine.py` |
| How is injection stopped? | `safety/guards.py`, `safety/pipeline.py`, `tools/policy.py` |
| How is retrieval ranked? | `knowledge/retrieval.py`, `knowledge/engine.py` |
| Why does an index have that name? | `knowledge/index_manager.py`, ADR-007 |
| What is recorded about a turn? | `domain/telemetry.py`, `observability/metrics.py` |
| Where is everything wired? | `container.py` |
| How do persistent engineer workspaces work? | ADR-013, ADR-014, `adapters/docker_sandbox.py`, `domain/engineering.py`, `adapters/postgres_workspaces.py` |
| How does the desktop Engineer agent reach the user's machine? | ADR-011, `adapters/host_agent.py`, `aura_hostd/` |

## Persistent engineer workspaces

Server-side Engineer tools use durable per-(user, repository) Docker workspaces when
`AURA_SANDBOX_ENABLED=true` and the compose `sandbox` profile is up (ADR-013). Workspaces are
stopped between turns so peak RAM tracks `max_running`, not stored count; git runs host-side so
tokens never enter the container; confirmation gates the workspace boundary (ADR-014), not every
in-workspace write. Off by default — see [Deployment Guide → Engineer workspaces](deployment-guide.md#engineer-workspaces-opt-in).
The Flutter `EngineerWorkspacesPanel` is mounted on the Engineer history page; Phase J in
`docs/AURA_UPGRADE_ROADMAP.md` stays **built, not proven** until the VPS live checks pass.

## Desktop Engineer agent (`aura_hostd`)

The Engineer persona's sandbox tools route through `CompositeSandboxRunner`: a connected
`aura_hostd` session wins; otherwise the server Docker workspace (if configured). Grant the
capability with `AURA_ENGINEER_AGENT_ROLES`. Design: [ADR-011](../architecture/011-desktop-engineer-agent.md).

### Local run

```bash
# Terminal 1 — backend (v3 gateway with engineer roles granted)
export AURA_ENGINEER_AGENT_ROLES=Developer,Admin,SuperAdmin   # example
# …start your usual local stack / uvicorn…

# Terminal 2 — host daemon
cd aura_hostd && dart pub get
dart run bin/aura_hostd.dart auth pair <CODE> --backend-url http://localhost:8000
dart run bin/aura_hostd.dart grant /path/to/a/project
dart run bin/aura_hostd.dart run          # WS + loopback control plane (default :8765)

# Terminal 3 — Flutter desktop
cd aura_client && flutter run -d windows  # or linux / macos
# Settings → Desktop Engineer (/desktop-engineer): pairing code, roots, YOLO, daemon status
```

Desktop login uses `POST /api/v3/auth/desktop/token` (no Play Integrity). Pairing codes come from
the Desktop Engineer screen; the daemon exchanges them via `aura_hostd auth pair <code>`.
Containment and the hard command denylist are enforced only on the daemon — never trusted from
the backend.

### `surface=` (tool descriptions)

`build_sandbox_tools(..., surface="auto"|"host"|"sandbox")` keeps tool *names* identical and
swaps only the prose so the model is not told "scratch directory" when a host is paired
(`surface="auto"` is the production default and describes both paths honestly). Locked by
`tests/unit/test_engineer_tools.py`.

### Consent / YOLO

Untrusted roots: small read-only allowlist. `aura_hostd trust <path>` / control-plane trust:
anything not on the hard denylist. `aura_hostd yolo on` (or the panel toggle): allowlist off for
every granted root; containment and denylist stay. Interactive per-call Flutter approve/deny and
ToolPolicy→`server_confirmed` RPC wiring are still deferred.

### Still deferred

Real PTY, live SSE→xterm in the panel, host file-tree/diff review, daemon auto-spawn, packaged
installers. See ADR-011's Units 5–9 table.
