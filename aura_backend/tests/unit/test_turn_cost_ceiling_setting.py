"""F30: the per-turn cost ceiling is configurable (``AURA_LLM_MAX_COST_PER_TURN_USD``) and off by default."""

from __future__ import annotations

import pytest

from aura_core.config.settings import LLMSettings
from aura_core.domain.errors import ConfigurationError

_VAR = "AURA_LLM_MAX_COST_PER_TURN_USD"


def test_the_cost_ceiling_is_off_by_default(monkeypatch) -> None:
    monkeypatch.delenv(_VAR, raising=False)
    assert LLMSettings.from_env().max_cost_per_turn_usd == 0.0


def test_the_cost_ceiling_reads_its_variable(monkeypatch) -> None:
    monkeypatch.setenv(_VAR, "0.25")
    assert LLMSettings.from_env().max_cost_per_turn_usd == 0.25


def test_a_negative_ceiling_is_refused(monkeypatch) -> None:
    monkeypatch.setenv(_VAR, "-1")
    with pytest.raises(ConfigurationError):
        LLMSettings.from_env()
