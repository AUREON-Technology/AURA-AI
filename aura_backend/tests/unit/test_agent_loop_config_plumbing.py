"""The agent-loop settings (ADR-037) reach every place an operator looks: README, .env.example, and
BOTH compose files at parity, with compose defaults equal to the settings defaults."""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from aura_core.config import load_settings

REPO = Path(__file__).resolve().parents[3]
NAMES = {
    "AURA_LOOP_STUCK_DETECTION_ENABLED": "loop_stuck_detection_enabled",
    "AURA_LOOP_STUCK_IDENTICAL_CALLS": "loop_stuck_identical_calls",
    "AURA_LOOP_STUCK_SAME_ERROR": "loop_stuck_same_error",
    "AURA_LOOP_STUCK_NO_PROGRESS": "loop_stuck_no_progress",
    "AURA_LOOP_RECOVERY_POLICY_ENABLED": "loop_recovery_policy_enabled",
    "AURA_LOOP_RECOVERY_BACKOFF_S": "loop_recovery_backoff_s",
    "AURA_LOOP_TEST_SIGNALS_ENABLED": "loop_test_signals_enabled",
    "AURA_CRITIC_ENABLED": "critic_enabled",
    "AURA_CRITIC_LLM_ENABLED": "critic_llm_enabled",
    "AURA_ENGINEER_TOKEN_BUDGET": "engineer_token_budget",
    "AURA_CONTEXT_PRESSURE_THRESHOLD": "context_pressure_threshold",
}


def _default(value) -> str:
    return str(value).lower() if isinstance(value, bool) else str(value)


@pytest.fixture(scope="module")
def reasoning():
    return load_settings(refresh=True, validate=False).reasoning


@pytest.mark.parametrize("compose", ["docker-compose.prod.yml", "docker-compose.dev.yml"])
def test_compose_passes_each_variable_with_the_settings_default(compose, reasoning) -> None:
    text = (REPO / "deploy" / compose).read_text(encoding="utf-8")
    for name, attr in NAMES.items():
        match = re.search(rf"^\s+{name}: \$\{{{name}:-([^}}]*)\}}\s*$", text, re.M)
        assert match, f"{compose} does not pass {name} to the gateway"
        assert match.group(1) == _default(getattr(reasoning, attr)), f"{compose}: {name} default drifted"


def test_documented_in_env_example_and_readme() -> None:
    env = (REPO / "deploy" / ".env.example").read_text(encoding="utf-8")
    readme = (REPO / "aura_backend" / "README.md").read_text(encoding="utf-8")
    for name in NAMES:
        assert name in env, f".env.example lacks {name}"
        assert name in readme or name.rsplit("_", 1)[0] in readme, f"README lacks {name}"


def test_agent_recovery_and_test_signals_are_enabled_by_default(reasoning) -> None:
    assert reasoning.loop_recovery_policy_enabled is True
    assert reasoning.loop_test_signals_enabled is True
    assert reasoning.critic_llm_enabled is False
