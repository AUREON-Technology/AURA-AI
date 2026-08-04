"""Evaluation: golden suites, retrieval and answer metrics, regression gates.

One implementation serves CI regression gating, the nightly refresh's promotion gate, and
on-demand admin scoring.
"""

from .harness import (
    AnswerEvaluator,
    CaseResult,
    EvalCase,
    EvalSuite,
    LLMJudge,
    RegressionGate,
    RetrievalEvaluator,
    SuiteResult,
    build_suite,
    summarise,
)

__all__ = [
    "EvalCase",
    "EvalSuite",
    "CaseResult",
    "SuiteResult",
    "RetrievalEvaluator",
    "AnswerEvaluator",
    "RegressionGate",
    "LLMJudge",
    "build_suite",
    "summarise",
]
