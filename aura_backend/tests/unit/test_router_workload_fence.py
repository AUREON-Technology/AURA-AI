"""A provider can be fenced to named workloads (module readiness WP6a, gap G-26, SEC-11).

D-02 (owner decision, 2026-11-17) puts Engineer turns on a Claude slot raised to the
``sensitive`` ceiling. A ceiling alone is not enough: an *unhinted* generation carries no pool
(``DEFAULT_WORKLOAD``), so pool tiering never excludes a pooled provider for it, and an
unclassified generation counts as SENSITIVE. Without a fence, every background caller that
forgot its hint would be eligible to send user data to Anthropic.

``AURA_PROVIDER_<NAME>_WORKLOADS`` names the workload classes a provider may serve. Empty means
all, which is how every provider configured before this behaves. A non-empty set excludes the
provider -- reason ``workload_not_allowed`` -- for any other workload, including the default one
an unhinted request gets.
"""

from __future__ import annotations

import logging

import pytest

from aura_core.config import load_settings
from aura_core.config.settings import ProviderConfig
from aura_core.domain import CompletionRequest, Conversation, Message, ProviderCapabilities
from aura_core.domain.errors import ConfigurationError
from aura_core.domain.identifiers import UserId
from aura_core.domain.llm import (
    DataClassification,
    ModelPool,
    RoutingPolicy,
    SamplingParams,
    WorkloadClass,
    WorkloadHint,
)
from aura_core.llm import ProviderRouter, capabilities_from_config
from aura_core.llm.providers.echo import EchoProvider
from aura_core.llm.sensitivity import data_scope
from aura_core.llm.workload import hint_for, workload_scope

SENSITIVE = DataClassification.SENSITIVE


def _local() -> EchoProvider:
    return EchoProvider(
        name="local",
        capabilities=ProviderCapabilities(
            model_id="qwen2.5:3b", context_window=8192, max_output_tokens=512,
            supports_tools=True, quality_tier=1, is_local=True,
        ),
    )


def _engineer_claude(workloads: frozenset[WorkloadClass] = frozenset({WorkloadClass.ENGINEER})):
    """What WP6 configures on Railway: Claude, coder pool, sensitive ceiling, Engineer only."""
    return EchoProvider(
        name="claude1",
        capabilities=ProviderCapabilities(
            model_id="claude-opus-5-5", context_window=200_000, max_output_tokens=32_000,
            supports_tools=True, quality_tier=3, pools=(ModelPool.CODER,),
            third_party=True, max_data_classification=SENSITIVE, workloads=workloads,
        ),
    )


def _request() -> CompletionRequest:
    return CompletionRequest(
        conversation=Conversation.of([Message.user("question")]),
        sampling=SamplingParams(temperature=0.0, max_output_tokens=64),
    )


@pytest.mark.parametrize("policy", list(RoutingPolicy))
class TestTheFence:
    def test_an_unhinted_sensitive_generation_never_reaches_a_fenced_provider(self, policy) -> None:
        router = ProviderRouter([_local(), _engineer_claude()], default_policy=policy)
        with data_scope(SENSITIVE, "test"):
            decision = router.plan(_request())
        assert decision.candidates == ("local",)
        assert decision.excluded["claude1"] == "workload_not_allowed"

    def test_an_interactive_turn_is_fenced_out(self, policy) -> None:
        router = ProviderRouter([_local(), _engineer_claude()], default_policy=policy)
        with workload_scope(hint_for(WorkloadClass.INTERACTIVE, user_key="u1")):
            decision = router.plan(_request())
        assert decision.candidates == ("local",)
        assert decision.excluded["claude1"] == "workload_not_allowed"

    def test_background_work_is_fenced_out(self, policy) -> None:
        router = ProviderRouter([_engineer_claude()], default_policy=policy)
        with workload_scope(hint_for(WorkloadClass.BACKGROUND_INSIGHT)), data_scope(
            SENSITIVE, "test"
        ):
            decision = router.plan(_request())
        assert decision.candidates == ()
        assert decision.excluded["claude1"] == "workload_not_allowed"

    def test_an_engineer_turn_is_served_by_it(self, policy) -> None:
        router = ProviderRouter([_local(), _engineer_claude()], default_policy=policy)
        with workload_scope(hint_for(WorkloadClass.ENGINEER, user_key="u1")), data_scope(
            SENSITIVE, "engineer_workspace"
        ):
            decision = router.plan(_request())
        assert "claude1" in decision.candidates

    def test_an_unfenced_provider_routes_exactly_as_before(self, policy) -> None:
        unfenced = _engineer_claude(workloads=frozenset())
        router = ProviderRouter([unfenced], default_policy=policy)
        with workload_scope(WorkloadHint()):
            decision = router.plan(_request())
        assert decision.candidates == ("claude1",)


class TestConfiguration:
    def test_workloads_are_read_from_the_environment(self, monkeypatch) -> None:
        monkeypatch.setenv("AURA_PROVIDER_CLAUDE1_KIND", "echo")
        monkeypatch.setenv("AURA_PROVIDER_CLAUDE1_WORKLOADS", "Engineer")
        config = ProviderConfig.from_env("claude1")
        assert config.workloads == ("engineer",)
        assert capabilities_from_config(config).workloads == frozenset({WorkloadClass.ENGINEER})

    def test_undeclared_means_every_workload(self, monkeypatch) -> None:
        monkeypatch.setenv("AURA_PROVIDER_CLAUDE1_KIND", "echo")
        monkeypatch.delenv("AURA_PROVIDER_CLAUDE1_WORKLOADS", raising=False)
        config = ProviderConfig.from_env("claude1")
        assert config.workloads == ()
        assert capabilities_from_config(config).workloads == frozenset()

    def test_an_unknown_workload_fails_at_boot(self, monkeypatch) -> None:
        monkeypatch.setenv("AURA_PROVIDER_CLAUDE1_KIND", "echo")
        monkeypatch.setenv("AURA_PROVIDER_CLAUDE1_WORKLOADS", "engineer,enginer")
        with pytest.raises(ConfigurationError, match="enginer"):
            ProviderConfig.from_env("claude1")

    def test_a_sensitive_claude_slot_without_a_fence_warns_at_boot(
        self, monkeypatch, caplog
    ) -> None:
        self._claude_env(monkeypatch, workloads=None)
        with caplog.at_level(logging.WARNING):
            ProviderConfig.from_env("claude1")
        assert any("WORKLOADS" in r.getMessage() for r in caplog.records)

    def test_a_fenced_sensitive_claude_slot_does_not_warn_about_the_fence(
        self, monkeypatch, caplog
    ) -> None:
        self._claude_env(monkeypatch, workloads="engineer")
        with caplog.at_level(logging.WARNING):
            ProviderConfig.from_env("claude1")
        assert not any("WORKLOADS" in r.getMessage() for r in caplog.records)

    def test_the_registry_carries_the_fence_onto_the_claude_provider(self, monkeypatch) -> None:
        from aura_core.llm import build_providers

        self._claude_env(monkeypatch, workloads="engineer")
        monkeypatch.setenv("AURA_LLM_PROVIDERS", "claude1")
        [provider] = build_providers(load_settings(refresh=True, validate=False).llm)
        assert provider.capabilities.workloads == frozenset({WorkloadClass.ENGINEER})
        assert provider.capabilities.max_data_classification is SENSITIVE

    @staticmethod
    def _claude_env(monkeypatch, *, workloads: str | None) -> None:
        monkeypatch.setenv("AURA_ALLOW_CLAUDE_INFERENCE", "true")
        monkeypatch.setenv("AURA_PROVIDER_CLAUDE1_KIND", "claude")
        monkeypatch.setenv("AURA_PROVIDER_CLAUDE1_MODEL", "claude-opus-5-5")
        monkeypatch.setenv("AURA_PROVIDER_CLAUDE1_API_KEY_ENV", "AURA_TEST_CLAUDE_KEY")
        monkeypatch.setenv("AURA_PROVIDER_CLAUDE1_POOLS", "coder")
        monkeypatch.setenv("AURA_PROVIDER_CLAUDE1_MAX_DATA_CLASSIFICATION", "sensitive")
        if workloads is None:
            monkeypatch.delenv("AURA_PROVIDER_CLAUDE1_WORKLOADS", raising=False)
        else:
            monkeypatch.setenv("AURA_PROVIDER_CLAUDE1_WORKLOADS", workloads)


class TestEngineerTurnsAreSensitiveFromTheStart:
    """The pin `04` G-01 said was missing: the reason D-02 needs a sensitive-ceiling slot."""

    async def test_an_engineer_turn_starts_sensitive(self, container) -> None:
        from aura_core.kernel import TurnRequest

        kernel = container.kernel
        level, reason = kernel._baseline_classification(TurnRequest(
            message="fix the bug", user_id=UserId("eng"), agent="engineer",
            capabilities=("engineer_agent",),
        ))
        assert (level, reason) == (SENSITIVE, "engineer_workspace")

    async def test_without_the_capability_it_is_an_ordinary_turn(self, container) -> None:
        from aura_core.kernel import TurnRequest

        level, _ = container.kernel._baseline_classification(TurnRequest(
            message="fix the bug", user_id=UserId("eng"), agent="engineer",
        ))
        assert level is DataClassification.INTERNAL
