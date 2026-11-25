"""ADR-053: evaluation scoring tools. Pure; checked against the harness's own functions and known values."""

from __future__ import annotations

import json

import pytest

from aura_core.domain.errors import ToolExecutionError
from aura_core.domain.identifiers import UserId
from aura_core.evaluation.harness import _mrr, _ndcg, _precision_at_k, _recall_at_k, _token_f1
from aura_core.ports.runtime import ToolContext
from aura_core.tools.eval_tools import EVAL_TOOL_NAMES, build_eval_tools, word_error_rate

CTX = ToolContext(user_id=UserId("u"), correlation_id="c", deadline_s=10.0)
TOOLS = {t.spec.name: t for t in build_eval_tools()}


async def call(name: str, **args) -> dict:
    return json.loads(await TOOLS[name].execute(args, CTX))


class TestRetrieval:
    async def test_it_gives_the_harnesss_own_numbers(self) -> None:
        retrieved, relevant = ["a", "x", "b", "y", "a"], ["a", "b", "c"]
        out = await call("score_retrieval", retrieved=retrieved, relevant=relevant)
        truth = set(relevant)
        assert out["recall_at_k"] == round(_recall_at_k(retrieved, truth), 4) == 0.6667
        assert out["precision_at_k"] == round(_precision_at_k(retrieved, truth), 4)
        assert out["mrr"] == round(_mrr(retrieved, truth), 4) == 1.0
        assert out["ndcg"] == round(_ndcg(retrieved, truth), 4)
        assert out["found"] == ["a", "b"] and out["missed"] == ["c"]

    async def test_k_limits_the_ranking_and_an_empty_truth_is_refused(self) -> None:
        assert (await call("score_retrieval", retrieved=["x", "a"], relevant=["a"], k=1))["recall_at_k"] == 0.0
        with pytest.raises(ToolExecutionError, match="undefined"):
            await call("score_retrieval", retrieved=["a"], relevant=[])

    async def test_size_is_bounded(self) -> None:
        with pytest.raises(ToolExecutionError, match="limit"):
            await call("score_retrieval", retrieved=[str(i) for i in range(2001)], relevant=["a"])


class TestAnswer:
    async def test_token_f1_terms_and_grounding(self) -> None:
        out = await call("score_answer", answer="Paris is the capital of France", reference="The capital of France is Paris",
                         required_terms=["Paris", "Lyon"], passages=["France's capital city is Paris, on the Seine."])
        assert out["token_f1"] == round(_token_f1("Paris is the capital of France", "The capital of France is Paris"), 4) == 1.0
        assert out["required_terms"] == {"present": ["Paris"], "missing": ["Lyon"], "coverage": 0.5}
        assert 0 < out["grounding_overlap"] <= 1 and "not prove" in out["grounding_note"]
        assert "lexical" in out["method"]

    async def test_an_answer_saying_things_the_passages_do_not_scores_low_grounding(self) -> None:
        grounded = await call("score_answer", answer="the server restarts nightly", passages=["the server restarts nightly at two"])
        invented = await call("score_answer", answer="unicorns manage billing quarterly", passages=["the server restarts nightly at two"])
        assert grounded["grounding_overlap"] > 0.9 > 0.2 > invented["grounding_overlap"]

    async def test_something_to_score_against_is_required(self) -> None:
        with pytest.raises(ToolExecutionError, match="at least one"):
            await call("score_answer", answer="x")


class TestWer:
    @pytest.mark.parametrize("ref,hyp,wer", [
        ("the cat sat", "the cat sat", 0.0), ("the cat sat", "the dog sat", 0.3333), ("the cat sat", "the cat", 0.3333),
        ("the cat sat", "the cat sat down", 0.3333), ("a b c d", "", 1.0), ("a b", "x y z", 1.5),
        ("The CAT, sat.", "the cat sat", 0.0),
    ])
    async def test_known_values(self, ref, hyp, wer) -> None:
        assert (await call("compute_wer", reference=ref, hypothesis=hyp))["wer"] == wer

    async def test_arabic_words_count_and_an_empty_reference_is_not_a_number(self) -> None:
        assert (await call("compute_wer", reference="السلام عليكم ورحمة الله", hypothesis="السلام عليكم ورحمه الله"))["wer"] == 0.25
        assert word_error_rate("", "x")["wer"] is None


class TestBaseline:
    async def test_a_drop_beyond_tolerance_fails_and_a_small_one_does_not(self) -> None:
        out = await call("compare_to_baseline", metrics={"recall": 0.80, "ndcg": 0.84}, baseline={"recall": 0.90, "ndcg": 0.85})
        assert out["passed"] is False and [r["metric"] for r in out["regressions"]] == ["recall"]
        assert (await call("compare_to_baseline", metrics={"recall": 0.80}, baseline={"recall": 0.90},
                           tolerances={"recall": 0.2}))["passed"] is True

    async def test_error_like_metrics_regress_when_they_rise(self) -> None:
        out = await call("compare_to_baseline", metrics={"wer": 0.20}, baseline={"wer": 0.10}, lower_is_better=["wer"])
        assert out["passed"] is False and out["regressions"][0]["worse_by"] == 0.1
        assert (await call("compare_to_baseline", metrics={"wer": 0.05}, baseline={"wer": 0.10}, lower_is_better=["wer"]))["passed"]

    async def test_nothing_comparable_is_not_a_pass(self) -> None:
        out = await call("compare_to_baseline", metrics={"a": 1.0}, baseline={"b": 1.0})
        assert out["passed"] is False and out["baseline_metrics_not_measured"] == ["b"] and out["problem"]

    @pytest.mark.parametrize("bad", [{}, {"a": "x"}, {"a": float("nan")}, {"a": True}, [1]])
    async def test_malformed_metrics_are_refused(self, bad) -> None:
        with pytest.raises(ToolExecutionError):
            await call("compare_to_baseline", metrics=bad, baseline={"a": 1.0})


def test_the_tools_are_pure_public_and_registered_and_granted() -> None:
    from aura_core.agents.registry import build_default_agent_registry
    from aura_core.tools.builtin import default_tools

    assert {t.spec.name for t in build_eval_tools()} == EVAL_TOOL_NAMES
    for t in build_eval_tools():
        assert t.spec.sensitivity.name == "PUBLIC" and not t.spec.side_effecting and not t.spec.required_capability
    assert EVAL_TOOL_NAMES <= {t.spec.name for t in default_tools(enable_open_pure_tools=True)}
    assert all(build_default_agent_registry().get("engineer").permits_tool(n) for n in EVAL_TOOL_NAMES)
