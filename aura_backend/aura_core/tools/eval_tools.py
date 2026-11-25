"""Evaluation scoring as agent tools (ADR-053): pure, bounded, read-only.

The scoring functions the evaluation harness uses (retrieval recall/precision/MRR/nDCG, token-F1, regression
tolerances) and word error rate existed only inside the harness and CI scripts, so an agent evaluating a change
had to re-implement them from memory. These four tools expose them, over **numbers and text the caller supplies**:
no I/O, no model call, no state.

What they are not: ``score_answer`` is *lexical* (token overlap), so it cannot tell a correct paraphrase from a
wrong answer and its ``grounding_overlap`` is not the groundedness guard's verdict. They never run a suite, call
an LLM judge, or touch a provider (``LLMJudge`` and ``AgentEvaluator`` stay out of the tool surface: they cost
money and drive the live kernel).
"""

from __future__ import annotations

import json
import math
import re
from typing import Any

from ..domain.errors import ToolExecutionError
from ..domain.tools import ToolCategory, ToolSensitivity
from ..evaluation.harness import _mrr, _ndcg, _precision_at_k, _recall_at_k, _token_f1
from ..ports.runtime import Tool
from .base import param, tool

__all__ = ["build_eval_tools", "EVAL_TOOL_NAMES", "word_error_rate"]

EVAL_TOOL_NAMES: frozenset[str] = frozenset(
    {"score_retrieval", "score_answer", "compute_wer", "compare_to_baseline"})
_MAX_IDS = 2_000
_MAX_TEXT = 100_000
_COMMON = dict(category=ToolCategory.DATA, sensitivity=ToolSensitivity.PUBLIC, timeout_s=10.0)
_WORD = re.compile(r"[^\W_]+", re.UNICODE)  # letters and digits of any script (Arabic included)


def _words(text: str) -> list[str]:
    return _WORD.findall(text.casefold())


def word_error_rate(reference: str, hypothesis: str) -> dict[str, Any]:
    """WER = (substitutions + deletions + insertions) / reference words, by edit distance over words."""
    ref, hyp = _words(reference), _words(hypothesis)
    if not ref:
        return {"wer": None, "reference_words": 0, "hypothesis_words": len(hyp),
                "problem": "the reference has no words, so a rate is undefined"}
    previous = list(range(len(hyp) + 1))
    for i, r in enumerate(ref, 1):
        current = [i] + [0] * len(hyp)
        for j, h in enumerate(hyp, 1):
            current[j] = min(previous[j] + 1, current[j - 1] + 1, previous[j - 1] + (r != h))
        previous = current
    errors = previous[-1]
    return {"wer": round(errors / len(ref), 4), "errors": errors, "reference_words": len(ref),
            "hypothesis_words": len(hyp)}


def _ids(name: str, tool_name: str, value: list | None) -> list[str]:
    items = [str(v) for v in (value or [])]
    if len(items) > _MAX_IDS:
        raise ToolExecutionError(f"'{name}' has {len(items)} items; the limit is {_MAX_IDS}", tool=tool_name)
    return items


def _text(name: str, tool_name: str, value: str | None) -> str:
    if value is not None and len(value) > _MAX_TEXT:
        raise ToolExecutionError(f"'{name}' is longer than {_MAX_TEXT:,} characters", tool=tool_name)
    return value or ""


def _number_map(tool_name: str, name: str, value: Any) -> dict[str, float]:
    if not isinstance(value, dict) or not value or len(value) > 200:
        raise ToolExecutionError(f"'{name}' must be an object of 1-200 metric: number entries", tool=tool_name)
    out: dict[str, float] = {}
    for key, number in value.items():
        if isinstance(number, bool) or not isinstance(number, (int, float)) or not math.isfinite(float(number)):
            raise ToolExecutionError(f"'{name}.{key}' must be a finite number", tool=tool_name)
        out[str(key)[:80]] = float(number)
    return out


@tool(
    "score_retrieval",
    "Score a ranked retrieval result against the known-relevant ids: recall@k, precision@k, MRR and nDCG "
    "(the harness's own formulas). Pass document ids in rank order.",
    param("retrieved", "array", "Retrieved ids in rank order.", item_type="string"),
    param("relevant", "array", "The ids that are relevant.", item_type="string"),
    param("k", "integer", "Score only the first k (default: all).", required=False, minimum=1, maximum=_MAX_IDS),
    **_COMMON,
)
async def score_retrieval(retrieved: list, relevant: list, k: int | None = None) -> str:
    name = "score_retrieval"
    ranked, truth = _ids("retrieved", name, retrieved), set(_ids("relevant", name, relevant))
    if not truth:
        raise ToolExecutionError("'relevant' is empty: recall is undefined without a ground truth", tool=name)
    top = ranked[:k] if k else ranked
    return json.dumps({
        "k": len(top), "retrieved": len(ranked), "relevant": len(truth),
        "recall_at_k": round(_recall_at_k(top, truth), 4), "precision_at_k": round(_precision_at_k(top, truth), 4),
        "mrr": round(_mrr(top, truth), 4), "ndcg": round(_ndcg(top, truth), 4),
        "found": sorted(set(top) & truth), "missed": sorted(truth - set(top))[:50]})


@tool(
    "score_answer",
    "Lexical answer scoring: token-F1 against a reference, coverage of required terms, and how much of the "
    "answer's wording appears in the supplied passages. Token overlap cannot judge a paraphrase or a wrong-but-"
    "similar answer: a screening aid, not a verdict.",
    param("answer", "string", "The answer to score.", max_length=_MAX_TEXT),
    param("reference", "string", "The expected answer.", required=False, max_length=_MAX_TEXT),
    param("required_terms", "array", "Terms the answer must contain.", required=False, item_type="string"),
    param("passages", "array", "Source passages the answer should rest on.", required=False, item_type="string"),
    **_COMMON,
)
async def score_answer(answer: str, reference: str | None = None, required_terms: list | None = None,
                       passages: list | None = None) -> str:
    name = "score_answer"
    text = _text("answer", name, answer)
    out: dict[str, Any] = {"answer_words": len(_words(text)), "method": "lexical (token overlap), not semantic"}
    if reference is not None:
        out["token_f1"] = round(_token_f1(text, _text("reference", name, reference)), 4)
    terms = _ids("required_terms", name, required_terms)
    if terms:
        lowered = text.casefold()
        present = [t for t in terms if t.casefold() in lowered]
        out["required_terms"] = {"present": present, "missing": [t for t in terms if t not in present],
                                 "coverage": round(len(present) / len(terms), 4)}
    sources = _ids("passages", name, passages)
    if sources:
        source_words = set(_words(" ".join(sources)))
        answer_words = [w for w in _words(text) if len(w) > 2]
        out["grounding_overlap"] = (round(sum(1 for w in answer_words if w in source_words) / len(answer_words), 4)
                                    if answer_words else None)
        out["grounding_note"] = ("share of the answer's words found in the passages; low means the answer says "
                                 "things the passages do not, high does not prove it is supported")
    if len(out) == 2:
        raise ToolExecutionError("give at least one of reference, required_terms, passages", tool=name)
    return json.dumps(out)


@tool(
    "compute_wer",
    "Word error rate of a transcript against a reference (edit distance over words; punctuation and case "
    "ignored; works for Arabic).",
    param("reference", "string", "The correct transcript.", max_length=_MAX_TEXT),
    param("hypothesis", "string", "The transcript to score.", max_length=_MAX_TEXT),
    **_COMMON,
)
async def compute_wer(reference: str, hypothesis: str) -> str:
    name = "compute_wer"
    return json.dumps(word_error_rate(_text("reference", name, reference), _text("hypothesis", name, hypothesis)))


@tool(
    "compare_to_baseline",
    "Check current evaluation metrics against a baseline with the regression gate's rule: a metric fails when "
    "it fell by more than its tolerance (default 0.05; pass tolerances to override). Higher is better; use "
    "lower_is_better for error-like metrics.",
    param("metrics", "object", "Current metric -> number."),
    param("baseline", "object", "Baseline metric -> number."),
    param("tolerances", "object", "Metric -> allowed drop.", required=False),
    param("lower_is_better", "array", "Metrics where a rise is the regression (error rate, WER, latency).",
          required=False, item_type="string"),
    **_COMMON,
)
async def compare_to_baseline(metrics: dict, baseline: dict, tolerances: dict | None = None,
                              lower_is_better: list | None = None) -> str:
    name = "compare_to_baseline"
    current, reference = _number_map(name, "metrics", metrics), _number_map(name, "baseline", baseline)
    allowed = _number_map(name, "tolerances", tolerances) if tolerances else {}
    inverted = set(_ids("lower_is_better", name, lower_is_better))
    checked, regressions, unmeasured = [], [], []
    for metric, previous in reference.items():
        if metric not in current:
            unmeasured.append(metric)
            continue
        tolerance = allowed.get(metric, 0.05)
        worse_by = (current[metric] - previous) if metric in inverted else (previous - current[metric])
        row = {"metric": metric, "baseline": previous, "current": current[metric],
               "worse_by": round(worse_by, 6), "tolerance": tolerance, "regressed": worse_by > tolerance}
        checked.append(row)
        if row["regressed"]:
            regressions.append(row)
    return json.dumps({
        "passed": not regressions and bool(checked), "regressions": regressions, "checked": checked,
        "baseline_metrics_not_measured": unmeasured,
        "problem": None if checked else "no metric in the baseline was present in the current metrics"})


def build_eval_tools() -> list[Tool]:
    return [score_retrieval, score_answer, compute_wer, compare_to_baseline]
