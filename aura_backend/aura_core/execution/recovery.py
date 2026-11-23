"""``RecoveryPolicy``: what to do about one failure, decided from its ``error_kind`` (ADR-037).

Until now the execution engine had one answer to every failed read-only step: run it again, up to
``max_attempts``. That is right for a timeout and wrong for nearly everything else -- a step whose
arguments failed validation fails identically the second time, a policy denial is a decision rather
than a fault, and a result a guard withheld must never be fetched again. Each wasted retry on a
slow provider is a tool call and, on the way to a replan, seconds; each blind retry of a refusal is
a request that should not have been made twice.

This is a small **pure** table, deliberately: no I/O, no clock, no engine imports. The engine asks
it per failed step, the agent loop asks it per failed pass, and a test can enumerate it.

========================  =========================  =================================
class                     example ``error_kind``     decision
========================  =========================  =================================
transient                 timeout, rate_limited,     retry the *same* step with backoff
                          dependency_unavailable     (read-only only), up to
                                                     ``max_attempts``; then replan
validation                validation_error,          never retry the same call; replan,
                          unresolved_argument        carrying the validator's message
denied                    policy_denied,             never retry, never replan around
                          confirmation_required      it; surface it to the user
security                  output_withheld,           stop; no retry, no replan
                          safety_violation
unknown                   anything else              no blind retry; replan once, and if
                                                     the same kind comes back, escalate
========================  =========================  =================================

A side-effecting step is never retried in place whatever its class (it may have acted before it
failed); for such a step "retry with backoff" becomes "replan". The engine's ``max_attempts``
stays the ceiling on in-place retries -- this policy can only say *no* to a retry the engine would
otherwise have made, or *wait* before it.
"""

from __future__ import annotations

import enum
import re
from dataclasses import dataclass

__all__ = [
    "FailureClass",
    "RecoveryAction",
    "RecoveryDecision",
    "RecoveryPolicy",
    "classify_failure",
]


class FailureClass(str, enum.Enum):
    TRANSIENT = "transient"
    VALIDATION = "validation"
    DENIED = "denied"
    SECURITY = "security"
    UNKNOWN = "unknown"


class RecoveryAction(str, enum.Enum):
    RETRY = "retry"          # the same step again, after ``backoff_s``
    REPLAN = "replan"        # a revised plan, with the failure as input
    SURFACE = "surface"      # tell the user; neither retry nor replan around it
    STOP = "stop"            # end the run; nothing further may be attempted
    ESCALATE = "escalate"    # already replanned once for this kind; hand it to the user


_TRANSIENT = frozenset({
    "timeout", "tool_timeout", "rate_limited", "dependency_unavailable", "overloaded",
    "user_concurrency_limit", "provider_error", "all_providers_failed", "deadline_exceeded",
    "connectionerror", "connectionreseterror", "connectionrefusederror", "timeouterror",
    "asynciotimeouterror", "brokenpipeerror",
})
_VALIDATION = frozenset({
    "validation_error", "unresolved_argument", "unresolved_reference", "invalid_output",
    "output_invalid", "tool_not_found", "not_found", "result_too_large", "conflict",
})
_DENIED = frozenset({
    "policy_denied", "confirmation_required", "needs_confirmation", "approval_required",
    "authorization_error", "authentication_error", "consent_required", "confirmation_invalid",
    "user_quota_exhausted",
})
_SECURITY = frozenset({"output_withheld", "safety_violation", "data_integrity_error"})

#: Words in a failure's *message* that mean "try again later". Only consulted when the kind
#: itself says nothing (``tool_execution_error`` wraps everything a tool raises), so a transport
#: error a tool surfaced as a generic failure is still treated as the transient fault it is.
_TRANSIENT_TEXT = re.compile(
    r"timed?[ -]?out|time-?out|rate[ -]?limit|too many requests|\b429\b|\b50[234]\b|"
    r"temporar(?:y|ily)|try again|connection (?:reset|refused|aborted|error)|"
    r"service unavailable|unavailable|overloaded|econnreset|broken pipe",
    re.I,
)


def classify_failure(error_kind: str | None, message: str | None = None) -> FailureClass:
    """The recovery class of one failure. ``error_kind`` may be a structured kind, an exception
    class name, or (``ExecutionEngine``'s fallback) the first characters of the tool's own
    message -- all three are handled; none raises."""
    kind = (error_kind or "").strip().lower()
    if kind in _SECURITY:
        return FailureClass.SECURITY
    if kind in _DENIED:
        return FailureClass.DENIED
    if kind in _VALIDATION:
        return FailureClass.VALIDATION
    if kind in _TRANSIENT:
        return FailureClass.TRANSIENT
    probe = f"{error_kind or ''} {message or ''}"
    if _TRANSIENT_TEXT.search(probe):
        return FailureClass.TRANSIENT
    if re.search(r"invalid arguments|validation|unresolved", probe, re.I):
        return FailureClass.VALIDATION
    return FailureClass.UNKNOWN


@dataclass(frozen=True, slots=True)
class RecoveryDecision:
    action: RecoveryAction
    failure_class: FailureClass
    reason: str
    #: Seconds to wait before an in-place retry (``RETRY`` only).
    backoff_s: float = 0.0

    @property
    def retry(self) -> bool:
        return self.action is RecoveryAction.RETRY


class RecoveryPolicy:
    """Pure decision table; hold no state between calls."""

    def __init__(self, *, backoff_base_s: float = 0.5, backoff_cap_s: float = 8.0) -> None:
        self._base = max(0.0, float(backoff_base_s))
        self._cap = max(self._base, float(backoff_cap_s))

    def backoff_for(self, attempt: int) -> float:
        """Exponential backoff after ``attempt`` tries (1 -> base, 2 -> 2*base, ...), capped."""
        return min(self._cap, self._base * (2 ** max(0, attempt - 1)))

    def decide(
        self,
        error_kind: str | None,
        *,
        message: str | None = None,
        attempt: int = 1,
        max_attempts: int = 1,
        side_effecting: bool = False,
        prior_replans_for_kind: int = 0,
    ) -> RecoveryDecision:
        """What to do about a step that just failed.

        ``attempt`` is how many times it has been dispatched (this one included);
        ``max_attempts`` is the engine's ceiling. ``prior_replans_for_kind`` is how many revised
        plans this run has already spent on a failure of this same kind (the loop tracks it).
        """
        fclass = classify_failure(error_kind, message)
        if fclass is FailureClass.SECURITY:
            return RecoveryDecision(
                RecoveryAction.STOP, fclass,
                "a safety guard withheld this result; it is not retried and not planned around",
            )
        if fclass is FailureClass.DENIED:
            return RecoveryDecision(
                RecoveryAction.SURFACE, fclass,
                "policy or confirmation refused this call; that is a decision, not a fault, so it "
                "is neither retried nor worked around",
            )
        if fclass is FailureClass.VALIDATION:
            return RecoveryDecision(
                RecoveryAction.REPLAN, fclass,
                "the call itself is invalid and would fail identically; replan with the "
                "validator's message",
            )
        if fclass is FailureClass.TRANSIENT:
            if side_effecting:
                return RecoveryDecision(
                    RecoveryAction.REPLAN, fclass,
                    "transient failure of a side-effecting step: it may already have acted, so "
                    "it is re-planned rather than repeated",
                )
            if attempt < max_attempts:
                return RecoveryDecision(
                    RecoveryAction.RETRY, fclass,
                    "transient failure; retry the same step after a backoff",
                    backoff_s=self.backoff_for(attempt),
                )
            return RecoveryDecision(
                RecoveryAction.REPLAN, fclass,
                "transient failure persisted through every in-place attempt; replan",
            )
        if prior_replans_for_kind >= 1:
            return RecoveryDecision(
                RecoveryAction.ESCALATE, fclass,
                "an unrecognised failure came back after a replan; escalating to the user "
                "instead of trying a third time",
            )
        return RecoveryDecision(
            RecoveryAction.REPLAN, fclass,
            "unrecognised failure; replan once, and escalate if it recurs",
        )
