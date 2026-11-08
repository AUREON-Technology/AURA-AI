"""How demanding a user's message is, scored without a model call (ADR-033).

This decides which *pool* an interactive turn asks the router for. A message that scores at or
above the operator's threshold asks for the ``reasoning`` pool -- where ADR-033 puts the Claude
provider -- and everything else keeps the ``general-fast`` pool it always had. The router then
does what it always does: pool members first, generalists as fallback, and every capability,
data-classification, circuit and quota check unchanged. So a complex turn whose context is
``sensitive`` still never reaches a third party, and a Claude outage degrades complex turns to
the local model rather than failing them.

Deliberately a heuristic, not a classifier model:

* it runs before planning, on every turn, so it must cost microseconds -- a CPU-served model
  call here would add seconds to every message to save seconds on a few;
* it is deterministic, so a routing decision can be explained ("long, multi-part, asks for a
  proof") and reproduced in a test;
* it fails cheap in both directions: a missed complex message is answered locally, as it was
  before ADR-033; a false positive costs one hosted call.

The score is a weighted sum of independent signals, clamped to ``[0, 1]``. Each signal is
bounded, so no single feature (a pasted log, a long greeting) can carry a message over the
threshold on its own unless it is overwhelming. Arabic keywords are included because the client
is used in Arabic (``languages`` routing already knows ``ar``).

Stdlib only: this is engine code, and it never sees the conversation history or any retrieved
content -- only the user's own message.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

__all__ = ["ComplexityAssessment", "assess_complexity"]


#: Words that ask for reasoning rather than recall. Matched as whole words, case-insensitive.
_REASONING_TERMS = (
    "analyze", "analyse", "analysis", "compare", "contrast", "evaluate", "trade-off",
    "tradeoff", "trade-offs", "tradeoffs", "prove", "proof", "derive", "derivation",
    "optimize", "optimise", "architecture", "architect", "design", "refactor", "debug",
    "diagnose", "root cause", "step by step", "step-by-step", "reason", "justify",
    "algorithm", "complexity", "strategy", "plan", "roadmap", "critique", "pros and cons",
    "implications", "why does", "why is", "how would you", "what if",
    # Arabic: analyse, compare, design, explain why, step by step, prove, plan, strategy, evaluate
    "حلل", "تحليل", "قارن", "مقارنة", "صمم", "تصميم", "اشرح لماذا", "خطوة بخطوة", "برهن",
    "اثبت", "خطة", "استراتيجية", "قيّم", "تقييم",
)

#: Requests for formal work -- a proof or a derivation is hard however short the message is.
_FORMAL_TERMS = (
    "prove", "proof", "derive", "derivation", "theorem", "lemma", "formally", "برهن", "اثبت",
)

#: Words that state constraints the answer has to satisfy simultaneously.
_CONSTRAINT_TERMS = (
    "must", "must not", "without", "at least", "at most", "ensure", "constraint",
    "constraints", "requirement", "requirements", "edge case", "edge cases", "given that",
    "assuming", "while keeping", "يجب", "بدون", "على الأقل", "شرط",
)

_TERM_RE_CACHE: dict[tuple[str, ...], re.Pattern[str]] = {}


def _terms_re(terms: tuple[str, ...]) -> re.Pattern[str]:
    pattern = _TERM_RE_CACHE.get(terms)
    if pattern is None:
        alternatives = "|".join(re.escape(t) for t in sorted(terms, key=len, reverse=True))
        # \b does not fire between Arabic letters and spaces reliably in every engine, so the
        # boundary is spelled out: start/end of text or a non-word character on each side.
        pattern = re.compile(rf"(?:^|(?<=\W))(?:{alternatives})(?=\W|$)", re.IGNORECASE)
        _TERM_RE_CACHE[terms] = pattern
    return pattern


_CODE_FENCE_RE = re.compile(r"```")
_INLINE_CODE_RE = re.compile(r"`[^`\n]+`")
_MATH_RE = re.compile(
    r"(\$[^$\n]+\$|\\(?:frac|sum|int|prod|lim|sqrt)\b|[=<>≤≥]\s*\d|\w\^\w|∑|∫|√)"
)
_LIST_ITEM_RE = re.compile(r"^\s*(?:\d+[.)]|[-*•])\s+\S", re.MULTILINE)
_QUESTION_RE = re.compile(r"[?؟]")


def _distinct(matches: list[str]) -> int:
    return len({m.casefold() for m in matches})


@dataclass(frozen=True, slots=True)
class ComplexityAssessment:
    """A score in ``[0, 1]`` and the signals that produced it (names only, never content)."""

    score: float
    signals: tuple[str, ...] = field(default_factory=tuple)

    def is_complex(self, threshold: float) -> bool:
        return self.score >= threshold


def assess_complexity(message: str) -> ComplexityAssessment:
    """Score how demanding ``message`` is to answer well. Pure, deterministic, no I/O."""
    text = (message or "").strip()
    if not text:
        return ComplexityAssessment(0.0)

    score = 0.0
    signals: list[str] = []

    def add(name: str, weight: float) -> None:
        nonlocal score
        if weight > 0:
            score += weight
            signals.append(name)

    words = len(text.split())
    # Length: a long message is usually a long task, but length alone is capped below the
    # default threshold so a pasted wall of text still needs another signal.
    if words >= 400:
        add("length", 0.35)
    elif words >= 150:
        add("length", 0.25)
    elif words >= 60:
        add("length", 0.1)

    # Distinct terms, so repeating one word does not count as several asks.
    reasoning_hits = _distinct(_terms_re(_REASONING_TERMS).findall(text))
    add("reasoning_terms", min(0.5, 0.2 * reasoning_hits))

    if _terms_re(_FORMAL_TERMS).search(text):
        add("formal", 0.2)

    constraint_hits = _distinct(_terms_re(_CONSTRAINT_TERMS).findall(text))
    add("constraints", min(0.2, 0.07 * constraint_hits))

    list_items = len(_LIST_ITEM_RE.findall(text))
    if list_items >= 3:
        add("multi_part", 0.2)

    questions = len(_QUESTION_RE.findall(text))
    if questions >= 3:
        add("many_questions", 0.15)
    elif questions == 2:
        add("many_questions", 0.07)

    if _CODE_FENCE_RE.search(text):
        add("code_block", 0.2)
    elif len(_INLINE_CODE_RE.findall(text)) >= 2:
        add("inline_code", 0.1)

    if _MATH_RE.search(text):
        add("math", 0.15)

    return ComplexityAssessment(round(min(1.0, score), 3), tuple(signals))
