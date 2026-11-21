"""A separate per-turn cap for engine generations (KAN-107).

``max_generations_per_turn`` bounds the kernel's own generations (the answer, a repair).
Planner, replan, reasoning and summary generations were charged their cost but never counted
(``generation=False``), so a turn's generation count had no ceiling: a tree-of-thoughts search
or a long replan loop could make as many generations as its own knobs allowed. They now count
against ``max_engine_generations_per_turn`` (default 32), checked and counted when each one
starts, and are labelled by purpose on the ``aura_llm_generation_*`` series.
"""

from __future__ import annotations

import dataclasses

import pytest

from aura_core.config.settings import AuraSettings, LLMSettings
from aura_core.domain import UserId
from aura_core.domain.errors import BudgetExceededError
from aura_core.domain.llm import CompletionRequest
from aura_core.domain.messages import Conversation, Message
from aura_core.domain.reasoning import ReasoningStrategy
from aura_core.kernel import TurnRequest
from aura_core.llm import ProviderRouter
from aura_core.llm.providers.echo import EchoProvider, ScriptedTurn
from aura_core.llm.router import TurnBudget, generation_purpose, turn_budget_scope
from aura_core.observability.metrics import PROMETHEUS_AVAILABLE, Metrics
from aura_core.prompts import build_default_registry
from aura_core.reasoning import ReasoningEngine
from aura_core.reasoning.engine import ReasoningRequest

EXPANSIONS = (
    "step one is to review the open backlog carefully\n"
    "step two is to prioritise the items by their age\n"
    "step three is to escalate the oldest unresolved item"
)


class _Thinker(EchoProvider):
    """Every generation proposes three substantive next steps, so the search never stops early."""

    def _next_turn(self, request: CompletionRequest) -> ScriptedTurn:
        return ScriptedTurn(text=EXPANSIONS)


def _reasoner(router: ProviderRouter, **kw) -> ReasoningEngine:
    return ReasoningEngine(router=router, prompts=build_default_registry(), **kw)


def _question() -> ReasoningRequest:
    question = "How should the team plan the next quarter of support work, and why?"
    return ReasoningRequest(
        question=question,
        conversation=Conversation.of([Message.user(question)]),
        strategy=ReasoningStrategy.TREE_OF_THOUGHTS,
    )


class TestTreeOfThoughtsStaysWithinTheCap:
    async def test_the_default_search_fits_the_default_cap(self) -> None:
        provider = _Thinker(name="t")
        router = ProviderRouter([provider])
        cap = LLMSettings().max_engine_generations_per_turn
        budget = TurnBudget(max_engine_generations=cap)
        with turn_budget_scope(budget), generation_purpose("reasoning"):
            result = await _reasoner(router).reason(_question())
        assert result.text
        assert budget.engine_generations_used == provider.call_count <= cap
        # breadth 3 x depth 3: 3 expansions + 9 evaluations, the answer, the verification.
        assert provider.call_count == 14
        assert budget.usage.by_purpose["reasoning"].generations == 14
        assert budget.generations_used == 0  # none of it touched the kernel's own count

    @pytest.mark.parametrize("cap", [1, 3, 5, 9])
    async def test_a_tight_cap_bounds_the_whole_search(self, cap: int) -> None:
        provider = _Thinker(name="t")
        router = ProviderRouter([provider])
        budget = TurnBudget(max_engine_generations=cap)
        with turn_budget_scope(budget):
            try:
                await _reasoner(router).reason(_question())
            except BudgetExceededError as exc:
                assert exc.details["scope"] == "engine" and exc.details["limit"] == cap
        assert provider.call_count <= cap
        assert budget.engine_generations_used <= cap

    async def test_concurrent_samples_cannot_overshoot(self) -> None:
        """Self-consistency gathers its samples at once; counting at start keeps them in bounds."""
        provider = _Thinker(name="t")
        router = ProviderRouter([provider])
        budget = TurnBudget(max_engine_generations=2)
        request = ReasoningRequest(
            question="what is 17 * 23?",
            conversation=Conversation.of([Message.user("what is 17 * 23?")]),
            strategy=ReasoningStrategy.SELF_CONSISTENCY,
        )
        with turn_budget_scope(budget):
            # The refused samples come back as exceptions (gather(return_exceptions=True)) and
            # the vote runs over the two that were admitted; the verifier is refused too and
            # reports inconclusive. Nothing past the cap reaches the provider.
            result = await _reasoner(router, self_consistency_samples=5).reason(request)
        assert result.text
        assert provider.call_count == budget.engine_generations_used == 2

    async def test_the_kernels_own_generations_are_not_starved(self) -> None:
        """An exhausted engine cap leaves the answer's own budget intact."""
        provider = _Thinker(name="t")
        router = ProviderRouter([provider])
        budget = TurnBudget(max_generations=1, max_engine_generations=1)
        request = CompletionRequest(conversation=Conversation.of([Message.user("hi")]))
        with turn_budget_scope(budget):
            await router.complete(request)  # engine generation: the cap's one
            with pytest.raises(BudgetExceededError):
                await router.complete(request)
            await router.complete(request, budget=budget)  # the answer still runs
        assert (budget.generations_used, budget.engine_generations_used) == (1, 1)


class TestEngineerReplanHeadroom:
    async def test_a_plan_and_eight_replans_plus_a_full_search_fit_the_default(self) -> None:
        provider = _Thinker(name="t")
        router = ProviderRouter([provider])
        budget = TurnBudget(max_engine_generations=LLMSettings().max_engine_generations_per_turn)
        request = CompletionRequest(conversation=Conversation.of([Message.user("plan")]))
        with turn_budget_scope(budget):
            with generation_purpose("planner"):
                for _ in range(1 + 8):
                    await router.complete(request)
            with generation_purpose("reasoning"):
                await _reasoner(router).reason(_question())
            with generation_purpose("summary"):
                await router.complete(request)
        assert budget.engine_generations_used == 9 + 14 + 1
        assert budget.usage.by_purpose["planner"].generations == 9


class TestKernelTurn:
    async def test_a_tree_of_thoughts_turn_stays_within_the_total_cap(
        self, container, monkeypatch
    ) -> None:
        await container.startup()
        provider = container.router.get("echo")
        provider.reset()
        monkeypatch.setattr(provider, "_next_turn", lambda request: ScriptedTurn(text=EXPANSIONS))
        kernel = container.kernel
        llm = dataclasses.replace(container.settings.llm, max_engine_generations_per_turn=6)
        monkeypatch.setattr(
            kernel._deps, "settings", dataclasses.replace(container.settings, llm=llm)
        )
        result = await kernel.run(TurnRequest(
            message="How should the team plan the next quarter of support work, and why?",
            user_id=UserId("u-tot"),
            strategy=ReasoningStrategy.TREE_OF_THOUGHTS,
            plan=False,
        ))
        # The search ran out of engine generations and the kernel answered directly instead of
        # failing the turn: at most the engine cap plus the kernel's own generations.
        assert result.text
        total = llm.max_engine_generations_per_turn + llm.max_generations_per_turn
        assert provider.call_count <= total
        assert result.telemetry.engine_generations <= llm.max_engine_generations_per_turn
        assert result.telemetry.generations == provider.call_count

    def test_the_kernel_reads_the_setting(self, container) -> None:
        budget = container.kernel._new_turn_budget()
        assert budget.max_engine_generations == container.settings.llm.max_engine_generations_per_turn


class TestSetting:
    def test_default_and_environment(self, monkeypatch) -> None:
        assert LLMSettings().max_engine_generations_per_turn == 32
        monkeypatch.setenv("AURA_LLM_MAX_ENGINE_GENERATIONS_PER_TURN", "12")
        assert AuraSettings.from_env().llm.max_engine_generations_per_turn == 12
        monkeypatch.setenv("AURA_LLM_MAX_ENGINE_GENERATIONS_PER_TURN", "0")
        assert AuraSettings.from_env().llm.max_engine_generations_per_turn == 0
        assert TurnBudget(max_engine_generations=0).max_engine_generations == 0


@pytest.mark.skipif(not PROMETHEUS_AVAILABLE, reason="prometheus_client not installed")
class TestPurposeLabel:
    async def test_generation_series_carry_the_purpose(self) -> None:
        metrics = Metrics(enabled=True)
        router = ProviderRouter([EchoProvider(name="e", model="m")], metrics=metrics)
        budget = TurnBudget()
        request = CompletionRequest(conversation=Conversation.of([Message.user("hello")]))
        with turn_budget_scope(budget):
            with generation_purpose("planner"):
                await router.complete(request)
            await router.complete(request, budget=budget)
        sample = metrics.registry.get_sample_value
        labels = {"provider": "e", "model": "m", "pool": "default", "outcome": "success"}
        assert sample("aura_llm_generation_total_seconds_count", {**labels, "purpose": "planner"}) == 1
        assert sample("aura_llm_generation_total_seconds_count", {**labels, "purpose": "answer"}) == 1
        assert sample(
            "aura_llm_generation_tokens_total",
            {"provider": "e", "model": "m", "pool": "default", "direction": "input",
             "purpose": "answer"},
        ) > 0
