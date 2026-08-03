"""Reasoning strategies: selection, execution, bounding, and verification.

The expensive strategies must stay bounded and must degrade rather than fail. Tree-of-thoughts
without a hard node budget costs ``breadth^depth`` generations, which is how it quietly becomes
the most expensive thing in a system; and a verifier that is unavailable must report
INCONCLUSIVE, because treating "we could not check" as "it passed" inflates confidence exactly
when it should fall.
"""

from __future__ import annotations

import json

import pytest

from aura_core.domain.llm import CompletionResponse, FinishReason, TokenUsage
from aura_core.domain.messages import Conversation, Message
from aura_core.domain.reasoning import ReasoningStrategy, VerificationOutcome
from aura_core.prompts import build_default_registry
from aura_core.reasoning import ReasoningEngine
from aura_core.reasoning.engine import (
    ReasoningRequest,
    _normalise_answer,
    _parse_json_object,
)


class ScriptedRouter:
    """Returns queued bodies in order, repeating the last one once exhausted."""

    def __init__(self, *bodies: str | Exception) -> None:
        self._bodies = list(bodies) or [""]
        self.calls = 0

    async def complete(self, request):  # noqa: ANN001
        index = min(self.calls, len(self._bodies) - 1)
        self.calls += 1
        body = self._bodies[index]
        if isinstance(body, BaseException):
            raise body
        return CompletionResponse(
            text=body, provider="scripted", model="scripted-1",
            finish_reason=FinishReason.STOP,
            usage=TokenUsage(prompt_tokens=10, completion_tokens=5),
        )


def _engine(*bodies, **kwargs) -> ReasoningEngine:
    return ReasoningEngine(
        router=ScriptedRouter(*bodies), prompts=build_default_registry(), **kwargs
    )


def _request(question: str = "What is the leave policy?", **overrides) -> ReasoningRequest:
    base = dict(question=question, conversation=Conversation.of([Message.user(question)]))
    base.update(overrides)
    return ReasoningRequest(**base)


class TestStrategySelection:
    def test_an_explicit_strategy_wins(self) -> None:
        engine = _engine()
        assert engine.select_strategy(
            _request(strategy=ReasoningStrategy.TREE_OF_THOUGHTS)
        ) is ReasoningStrategy.TREE_OF_THOUGHTS

    def test_auto_selection_can_be_disabled(self) -> None:
        engine = _engine(auto_select=False, default_strategy=ReasoningStrategy.CHAIN_OF_THOUGHT)
        assert engine.select_strategy(
            _request("Compare the two policies and tell me which is better")
        ) is ReasoningStrategy.CHAIN_OF_THOUGHT

    def test_a_well_grounded_short_factual_question_stays_direct(self) -> None:
        """The work was done by retrieval; deliberating adds cost without accuracy."""
        assert _engine().select_strategy(
            _request("What is the annual leave entitlement?", retrieval_strength=0.9)
        ) is ReasoningStrategy.DIRECT

    def test_weak_retrieval_does_not_force_direct(self) -> None:
        assert _engine().select_strategy(
            _request("How much is 12 months of claims at $250 each?", retrieval_strength=0.1)
        ) is ReasoningStrategy.SELF_CONSISTENCY

    def test_arithmetic_selects_self_consistency(self) -> None:
        """Independent samples rarely make the same arithmetic slip."""
        assert _engine().select_strategy(
            _request("Calculate the total of 12 monthly claims at 250 each")
        ) is ReasoningStrategy.SELF_CONSISTENCY

    def test_an_open_ended_design_question_selects_tree_of_thoughts(self) -> None:
        assert _engine().select_strategy(
            _request("Design a strategy for reducing our reimbursement backlog over two quarters")
        ) is ReasoningStrategy.TREE_OF_THOUGHTS

    def test_a_comparison_selects_chain_of_thought(self) -> None:
        assert _engine().select_strategy(
            _request("Compare the annual leave policy with the sick leave policy in detail")
        ) is ReasoningStrategy.CHAIN_OF_THOUGHT

    def test_a_very_long_question_selects_chain_of_thought(self) -> None:
        assert _engine().select_strategy(_request("x " * 150)) is ReasoningStrategy.CHAIN_OF_THOUGHT

    def test_an_unremarkable_question_uses_the_default(self) -> None:
        assert _engine().select_strategy(_request("Tell me about the office")) is (
            ReasoningStrategy.DIRECT
        )


class TestDirectStrategy:
    async def test_it_costs_exactly_one_generation(self) -> None:
        engine = _engine("21 days.", verification_enabled=True)
        result = await engine.reason(_request(strategy=ReasoningStrategy.DIRECT))

        assert result.text == "21 days."
        assert result.generations == 1
        # Verification is skipped: spending a generation on the turn that already chose the
        # cheapest strategy would double the cost of the least uncertain answers.
        assert result.trace.verification is None

    async def test_confidence_is_assessed_even_without_verification(self) -> None:
        engine = _engine("21 days.")
        result = await engine.reason(_request(retrieval_strength=0.9))
        assert result.confidence is not None
        assert 0.0 <= result.confidence.score <= 1.0


class TestChainOfThought:
    async def test_the_reasoning_instruction_is_appended_as_a_system_message(self) -> None:
        """Reasoning that leaks into the answer is a presentation defect: the user asked a
        question, not for a transcript."""
        captured: list = []

        class Capturing(ScriptedRouter):
            async def complete(self, request):  # noqa: ANN001
                captured.append(request.conversation)
                return await super().complete(request)

        engine = ReasoningEngine(
            router=Capturing("The answer.", json.dumps({"verdict": "passed"})),
            prompts=build_default_registry(),
        )
        await engine.reason(_request(strategy=ReasoningStrategy.CHAIN_OF_THOUGHT))

        system_texts = [m.content for m in captured[0] if m.role.value == "system"]
        assert any("Present only" in t for t in system_texts)

    async def test_it_is_verified(self) -> None:
        engine = _engine("The answer.", json.dumps({"verdict": "passed", "checks": []}))
        result = await engine.reason(_request(strategy=ReasoningStrategy.CHAIN_OF_THOUGHT))

        assert result.trace.verification.outcome is VerificationOutcome.PASSED
        assert result.generations == 2


class TestSelfConsistency:
    async def test_the_majority_answer_wins(self) -> None:
        engine = _engine(verification_enabled=False, self_consistency_samples=3)
        engine._router = ScriptedRouter("The total is 3000.", "The total is 3000.",
                                        "The total is 42.")
        result = await engine.reason(_request(strategy=ReasoningStrategy.SELF_CONSISTENCY))

        assert "3000" in result.text
        assert result.generations == 3
        assert len(result.trace.candidates) == 3

    async def test_numerically_equal_answers_agree_despite_different_prose(self) -> None:
        """Comparing full text would score these as disagreement, which is the wrong signal."""
        engine = _engine(verification_enabled=False, self_consistency_samples=3)
        engine._router = ScriptedRouter(
            "The total comes to 3000.",
            "After adding it up: 3000",
            "I calculate 3000 exactly.",
        )
        result = await engine.reason(_request(strategy=ReasoningStrategy.SELF_CONSISTENCY))
        assert result.trace.agreement == pytest.approx(1.0)

    async def test_samples_are_drawn_at_a_non_zero_temperature(self) -> None:
        """At temperature 0 the samples are identical and the vote is meaningless."""
        temperatures: list[float] = []

        class Capturing(ScriptedRouter):
            async def complete(self, request):  # noqa: ANN001
                temperatures.append(request.sampling.temperature)
                return await super().complete(request)

        engine = ReasoningEngine(
            router=Capturing("42"), prompts=build_default_registry(),
            verification_enabled=False, self_consistency_samples=3,
        )
        await engine.reason(_request(strategy=ReasoningStrategy.SELF_CONSISTENCY))
        assert all(t > 0.0 for t in temperatures)

    async def test_partial_sample_failure_still_produces_an_answer(self) -> None:
        engine = _engine(verification_enabled=False, self_consistency_samples=3)
        engine._router = ScriptedRouter("42", RuntimeError("provider blew up"), "42")
        result = await engine.reason(_request(strategy=ReasoningStrategy.SELF_CONSISTENCY))

        assert result.text == "42"
        assert result.generations == 2

    async def test_total_sample_failure_falls_back_to_one_attempt(self) -> None:
        """The turn should still have a chance to produce an answer."""

        class FailThenSucceed:
            def __init__(self) -> None:
                self.calls = 0

            async def complete(self, request):  # noqa: ANN001
                self.calls += 1
                if self.calls <= 3:
                    raise RuntimeError("sampling failed")
                return CompletionResponse(
                    text="fallback answer", provider="p", model="m",
                    finish_reason=FinishReason.STOP, usage=TokenUsage(),
                )

        engine = ReasoningEngine(
            router=FailThenSucceed(), prompts=build_default_registry(),
            verification_enabled=False, self_consistency_samples=3,
        )
        result = await engine.reason(_request(strategy=ReasoningStrategy.SELF_CONSISTENCY))
        assert result.text == "fallback answer"


class TestTreeOfThoughts:
    async def test_the_node_budget_bounds_the_search(self) -> None:
        """Without a hard node budget the cost is breadth^depth."""
        engine = _engine(
            "step one is to review the backlog\nstep two is to prioritise by age\n"
            "step three is to escalate the oldest",
            verification_enabled=False, tot_breadth=3, tot_depth=5, tot_max_nodes=4,
        )
        result = await engine.reason(_request(strategy=ReasoningStrategy.TREE_OF_THOUGHTS))
        assert len(result.trace.nodes) <= 4

    async def test_an_empty_expansion_stops_the_search_early(self) -> None:
        engine = _engine("", verification_enabled=False, tot_depth=3)
        result = await engine.reason(_request(strategy=ReasoningStrategy.TREE_OF_THOUGHTS))
        assert result.trace.strategy is ReasoningStrategy.TREE_OF_THOUGHTS

    async def test_an_expansion_failure_still_produces_an_answer(self) -> None:
        """Expansion and path evaluation are optional refinements; only the final generation
        is load-bearing."""
        engine = _engine(
            RuntimeError("expansion failed"), "The answer despite a failed search.",
            verification_enabled=False,
        )
        result = await engine.reason(_request(strategy=ReasoningStrategy.TREE_OF_THOUGHTS))
        assert result.text == "The answer despite a failed search."

    async def test_numbered_expansion_lines_are_cleaned(self) -> None:
        engine = _engine(
            "1. review the reimbursement backlog thoroughly\n"
            "2. prioritise the oldest outstanding claims\n"
            "- short",
            verification_enabled=False, tot_breadth=3, tot_depth=1, tot_max_nodes=8,
        )
        result = await engine.reason(_request(strategy=ReasoningStrategy.TREE_OF_THOUGHTS))
        contents = [n.content for n in result.trace.nodes]
        assert not any(c.startswith(("1.", "2.", "-")) for c in contents)
        # Lines under the length floor are dropped as non-substantive.
        assert "short" not in contents


class TestVerification:
    async def test_a_passing_verdict_is_recorded_with_its_checks(self) -> None:
        body = json.dumps({
            "verdict": "passed",
            "checks": [
                {"criterion": "cites a source", "passed": True, "note": "cites handbook"},
                {"criterion": "answers the question", "passed": True, "note": ""},
            ],
        })
        engine = _engine("The answer.", body)
        result = await engine.reason(_request(strategy=ReasoningStrategy.CHAIN_OF_THOUGHT))

        verification = result.trace.verification
        assert verification.outcome is VerificationOutcome.PASSED
        assert len(verification.checks) == 2
        assert verification.pass_rate == pytest.approx(1.0)

    async def test_a_failing_verdict_lowers_confidence(self) -> None:
        failing = json.dumps({
            "verdict": "failed",
            "checks": [{"criterion": "grounded", "passed": False, "note": "no source"}],
        })
        passing = json.dumps({
            "verdict": "passed",
            "checks": [{"criterion": "grounded", "passed": True, "note": "cited"}],
        })

        low = await _engine("A.", failing).reason(
            _request(strategy=ReasoningStrategy.CHAIN_OF_THOUGHT, retrieval_strength=0.5)
        )
        high = await _engine("A.", passing).reason(
            _request(strategy=ReasoningStrategy.CHAIN_OF_THOUGHT, retrieval_strength=0.5)
        )
        assert low.confidence.score < high.confidence.score

    async def test_an_unavailable_verifier_is_inconclusive_not_a_pass(self) -> None:
        """Treating 'we could not check' as 'it passed' inflates confidence exactly when it
        should fall."""
        engine = _engine("The answer.", RuntimeError("verifier down"))
        result = await engine.reason(_request(strategy=ReasoningStrategy.CHAIN_OF_THOUGHT))

        assert result.trace.verification.outcome is VerificationOutcome.INCONCLUSIVE
        assert result.trace.verification.notes == "verification unavailable"

    async def test_a_malformed_verdict_is_inconclusive(self) -> None:
        engine = _engine("The answer.", "I think it looks fine to me!")
        result = await engine.reason(_request(strategy=ReasoningStrategy.CHAIN_OF_THOUGHT))
        assert result.trace.verification.outcome is VerificationOutcome.INCONCLUSIVE

    async def test_an_unknown_verdict_string_is_inconclusive(self) -> None:
        engine = _engine("The answer.", json.dumps({"verdict": "probably_ok"}))
        result = await engine.reason(_request(strategy=ReasoningStrategy.CHAIN_OF_THOUGHT))
        assert result.trace.verification.outcome is VerificationOutcome.INCONCLUSIVE

    async def test_non_mapping_checks_are_skipped(self) -> None:
        body = json.dumps({"verdict": "passed", "checks": ["not an object", {"passed": True}]})
        engine = _engine("The answer.", body)
        result = await engine.reason(_request(strategy=ReasoningStrategy.CHAIN_OF_THOUGHT))
        assert len(result.trace.verification.checks) == 1

    async def test_verification_can_be_disabled(self) -> None:
        engine = _engine("The answer.", verification_enabled=False)
        result = await engine.reason(_request(strategy=ReasoningStrategy.CHAIN_OF_THOUGHT))
        assert result.trace.verification is None
        assert result.generations == 1


class TestAnswerNormalisation:
    def test_a_trailing_number_is_the_comparison_key(self) -> None:
        assert _normalise_answer("The total is 3,000. So: 3000") == "3000"

    def test_a_negative_number_is_preserved(self) -> None:
        assert _normalise_answer("The delta is -42") == "-42"

    def test_prose_answers_are_case_and_whitespace_folded(self) -> None:
        assert _normalise_answer("  Annual   LEAVE  ") == "annual leave"

    def test_long_prose_is_truncated_for_comparison(self) -> None:
        assert len(_normalise_answer("word " * 200)) <= 200


class TestJsonExtraction:
    def test_a_fenced_object_is_unwrapped(self) -> None:
        assert _parse_json_object('```json\n{"score": 0.7}\n```') == {"score": 0.7}

    def test_an_object_in_prose_is_recovered(self) -> None:
        assert _parse_json_object('Verdict: {"verdict": "passed"} done')["verdict"] == "passed"

    def test_an_array_is_not_an_object(self) -> None:
        assert _parse_json_object("[1,2]") == {}

    def test_no_object_returns_empty(self) -> None:
        assert _parse_json_object("no json") == {}

    def test_an_unterminated_object_returns_empty(self) -> None:
        assert _parse_json_object('{"score":') == {}


class TestMetrics:
    async def test_strategy_confidence_and_verification_are_all_recorded(self, metrics) -> None:
        engine = ReasoningEngine(
            router=ScriptedRouter("The answer.", json.dumps({"verdict": "passed"})),
            prompts=build_default_registry(), metrics=metrics,
        )
        result = await engine.reason(_request(strategy=ReasoningStrategy.CHAIN_OF_THOUGHT))
        assert result.trace.confidence is not None
