"""Shared scaffolding for the agent-loop tests (ADR-037): drive a *real* engineer turn.

Nothing under test is mocked. The kernel, planner, execution engine, tool registry, policy and
agent loop are the container's own; the sandbox runs real commands in a temp directory
(``InMemorySandboxRunner``). The only stand-in is the model: :func:`script_model` makes the
container's echo provider answer planning prompts from a list of plans and everything else with a
fixed final answer, so a test decides exactly what "the model" planned and said.
"""

from __future__ import annotations

import json
import shlex
import sys
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Mapping, Sequence

from aura_core.config import load_settings
from aura_core.container import Container
from aura_core.domain.engineering import ENGINEER_CONSENT_NOTICE_VERSION
from aura_core.domain.identifiers import UserId
from aura_core.kernel import TurnRequest

ENGINEER = UserId("loop-engineer")
CAPS = ("engineer_agent",)
PLAN_MARKER = "Break this goal into the smallest set of concrete steps"
CRITIC_MARKER = "You are reviewing an AI agent's"

#: A module with a bug and the test that exposes it; real pytest runs them for real.
BUGGY = "def add(a, b):\n    return a - b\n"
FIXED = "def add(a, b):\n    return a + b\n"
TEST_MODULE = (
    "from calc import add\n\n\n"
    "def test_add():\n    assert add(2, 3) == 5\n\n\n"
    "def test_add_zero():\n    assert add(0, 0) == 0\n"
)
# `-B`: a fix that keeps the file's size and lands within the same second would otherwise be
# shadowed by the stale .pyc the first run wrote (mtime + size are what Python keys it on).
PYTEST = f"{shlex.quote(sys.executable)} -B -m pytest -q -p no:cacheprovider"


def tool_step(
    step_id: str, tool: str, description: str, arguments: Mapping[str, Any],
    depends_on: Sequence[str] = (),
) -> dict[str, Any]:
    return {"id": step_id, "kind": "tool", "tool": tool, "description": description,
            "arguments": dict(arguments), "depends_on": list(depends_on)}


def write_step(step_id: str, path: str, content: str, depends_on: Sequence[str] = ()) -> dict[str, Any]:
    return tool_step(step_id, "write_file", f"Write {path}", {"path": path, "content": content},
                     depends_on)


def pytest_step(step_id: str, depends_on: Sequence[str] = ()) -> dict[str, Any]:
    return tool_step(step_id, "run_command", "Run the tests", {"command": PYTEST, "timeout_s": 60},
                     depends_on)


@dataclass
class ModelScript:
    """What the stand-in model was asked, and how often (so a test can assert on it)."""

    plans: Sequence[Sequence[Mapping[str, Any]]]
    final_text: str = "Done."
    critic_json: str | None = None
    plan_prompts: list[str] = field(default_factory=list)
    critic_prompts: list[str] = field(default_factory=list)
    final_prompts: list[str] = field(default_factory=list)

    @property
    def planning_calls(self) -> int:
        return len(self.plan_prompts)


def script_model(echo: Any, plans: Sequence[Sequence[Mapping[str, Any]]], *,
                 final_text: str = "Done.", critic_json: str | None = None) -> ModelScript:
    """Make ``echo`` plan from ``plans`` (the last one repeats) and otherwise say ``final_text``."""
    from aura_core.llm.providers.echo import ScriptedTurn

    script = ModelScript(plans=plans, final_text=final_text, critic_json=critic_json)

    def _next_turn(request: Any) -> ScriptedTurn:
        last = request.conversation.last_user_message
        prompt = last.content if last else ""
        if PLAN_MARKER in prompt:
            index = min(script.planning_calls, len(script.plans) - 1)
            script.plan_prompts.append(prompt)
            return ScriptedTurn(text=json.dumps({"steps": list(script.plans[index])}))
        if CRITIC_MARKER in prompt:
            script.critic_prompts.append(prompt)
            return ScriptedTurn(text=script.critic_json or '{"findings": []}')
        script.final_prompts.append(prompt)
        return ScriptedTurn(text=script.final_text)

    echo._next_turn = _next_turn
    return script


def make_container(monkeypatch: Any, **env: str) -> Container:
    """A container on the in-memory stack with ``AURA_<KEY>=value`` set for each ``env`` item."""
    for key, value in env.items():
        monkeypatch.setenv(f"AURA_{key}", str(value))
    return Container(load_settings(refresh=True, validate=False))


async def ready_engineer(container: Container) -> Any:
    """Start the container, record the Engineer's consent, return its scripted echo provider."""
    await container.startup()
    # The default container has no sandbox (no Docker, no paired desktop). The fake runs real
    # commands in a temp directory, which is what these turns need; it is the substrate, not a mock.
    from aura_core.adapters.coding_fakes import InMemorySandboxRunner

    container.sandbox_runner._fallback = InMemorySandboxRunner()
    await container.engineer_consent.accept(
        ENGINEER, ENGINEER_CONSENT_NOTICE_VERSION, at=datetime(2026, 11, 17, tzinfo=timezone.utc)
    )
    echo = container.router.get("echo")
    # The scripted provider stands in for a model. Its default 8,192-token window is smaller than the
    # Engineer's own tool catalogue plus a planning prompt (the catalogue grows with every tool added),
    # so a plan would be refused as `context_window_too_small` before any loop behaviour is exercised.
    # A real planning model has a far larger window; the tests that care about the *prompt budget*
    # set AURA_LLM_MAX_PROMPT_TOKENS themselves.
    import dataclasses

    echo._capabilities = dataclasses.replace(echo.capabilities, context_window=32_768)
    return echo


def engineer_turn(message: str = "fix the failing test in calc.py", **extra: Any) -> TurnRequest:
    return TurnRequest(message=message, user_id=ENGINEER, agent="engineer",
                       capabilities=CAPS, plan=True, **extra)


async def collect_stream(container: Container, request: TurnRequest) -> list[Mapping[str, Any]]:
    return [frame async for frame in container.kernel.stream(request)]


def frames_of(frames: Sequence[Mapping[str, Any]], kind: str) -> list[Mapping[str, Any]]:
    return [f for f in frames if f.get("type") == kind]
