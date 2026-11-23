"""Stuck detection: notice that the agent loop is spinning, and say so (ADR-037).

The loop's only stop conditions used to be hard bounds -- a replan limit, a deadline, budgets. A
plan that failed the same way every time therefore ran to the limit: one identical failing plan was
executed nine times with eight replans before anything stopped it. Each of those passes costs a
planner generation and the tool calls, and the user gets the same failure nine times over instead
of one clear explanation.

:class:`StuckDetector` watches what each pass *did* and reports a :class:`StuckVerdict` when the
loop is demonstrably going nowhere, by any of three rules (each configurable, ``0`` disables):

``identical_calls``
    The same ``(tool, arguments)`` was dispatched ``N`` times and produced the same outcome each
    time -- the same failure, or the same output. Re-reading a file that has not changed, or
    re-running a command that still fails identically, is not progress. (A rerun whose *output
    changed* -- the tests now fail differently -- resets the count: that is movement.)
``repeated_error``
    The same ``error_kind`` (or the same failing-test signature) in ``K`` consecutive passes.
``no_progress``
    ``M`` consecutive passes in which no step newly succeeded.

It only ever stops a loop that is *already* about to replan; it cannot cut a run that is making
progress, and it never acts on a pass that succeeded. Pure and stdlib-only; the thresholds are data
(:class:`StuckConfig`) so they can come from settings.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from typing import Any, Iterable, Mapping

__all__ = ["StuckConfig", "StuckVerdict", "StuckDetector", "CallRecord", "escalation_message"]


@dataclass(frozen=True, slots=True)
class StuckConfig:
    enabled: bool = True
    identical_calls: int = 3
    same_error: int = 3
    no_progress: int = 4

    @classmethod
    def disabled(cls) -> "StuckConfig":
        return cls(enabled=False)


@dataclass(frozen=True, slots=True)
class CallRecord:
    """One tool dispatch, reduced to what repetition is judged on."""

    tool: str
    args_digest: str
    ok: bool
    error_kind: str | None
    result_digest: str
    description: str = ""


@dataclass(frozen=True, slots=True)
class StuckVerdict:
    rule: str                      # identical_calls | repeated_error | no_progress
    count: int
    detail: str
    tool: str | None = None
    error_kind: str | None = None
    iterations: int = 0

    def as_dict(self) -> dict[str, Any]:
        return {"rule": self.rule, "count": self.count, "detail": self.detail,
                "tool": self.tool, "error_kind": self.error_kind, "iterations": self.iterations}


def digest(value: Any) -> str:
    """A short, stable digest of arguments or text (never reversible, never logged raw)."""
    try:
        raw = json.dumps(value, sort_keys=True, default=str) if not isinstance(value, str) else value
    except (TypeError, ValueError):
        raw = repr(value)
    return hashlib.sha256(raw.encode("utf-8", "replace")).hexdigest()[:16]


def _pass_progress(records: Iterable[CallRecord]) -> bool:
    return any(r.ok for r in records)


@dataclass
class StuckDetector:
    """Feed it one pass at a time with :meth:`record_pass`; it answers with a verdict or ``None``."""

    config: StuckConfig = field(default_factory=StuckConfig)
    _repeat: dict[tuple[str, str], tuple[tuple[bool, str | None, str], int]] = field(
        default_factory=dict)
    _error_run: tuple[str, int] | None = None
    _idle_passes: int = 0
    _passes: int = 0

    def record_pass(
        self,
        calls: Iterable[CallRecord],
        *,
        new_success: bool | None = None,
        failure_signals: Iterable[str] = (),
    ) -> StuckVerdict | None:
        """Account for one execution pass.

        ``new_success`` says whether any step newly succeeded (defaults to "any recorded call
        was ok"). ``failure_signals`` are extra per-pass failure identities -- a failing-test
        signature -- counted by the ``repeated_error`` rule alongside the calls' own error kinds.
        """
        records = list(calls)
        self._passes += 1
        cfg = self.config
        if not cfg.enabled:
            return None

        verdict: StuckVerdict | None = None

        # identical_calls -------------------------------------------------------------------
        for rec in records:
            key = (rec.tool, rec.args_digest)
            fingerprint = (rec.ok, rec.error_kind, rec.result_digest)
            last, count = self._repeat.get(key, (None, 0))
            count = count + 1 if last == fingerprint else 1
            self._repeat[key] = (fingerprint, count)
            if (
                verdict is None and cfg.identical_calls > 0 and count >= cfg.identical_calls
            ):
                what = "failed the same way" if not rec.ok else "returned the same result"
                verdict = StuckVerdict(
                    "identical_calls", count,
                    f"`{rec.tool}` was called {count} times with identical arguments and "
                    f"{what} each time"
                    + (f" ({rec.error_kind})" if rec.error_kind and not rec.ok else "")
                    + (f": {rec.description}" if rec.description else ""),
                    tool=rec.tool, error_kind=rec.error_kind if not rec.ok else None,
                    iterations=self._passes,
                )

        # repeated_error --------------------------------------------------------------------
        kinds = {r.error_kind for r in records if not r.ok and r.error_kind}
        kinds.update(s for s in failure_signals if s)
        if self._error_run is not None and self._error_run[0] in kinds:
            self._error_run = (self._error_run[0], self._error_run[1] + 1)
        elif kinds:
            self._error_run = (sorted(kinds)[0], 1)
        else:
            self._error_run = None
        if (
            verdict is None and cfg.same_error > 0 and self._error_run is not None
            and self._error_run[1] >= cfg.same_error
        ):
            kind, count = self._error_run
            verdict = StuckVerdict(
                "repeated_error", count,
                f"the same failure ({kind}) came back in {count} consecutive attempts",
                error_kind=kind, iterations=self._passes,
            )

        # no_progress -----------------------------------------------------------------------
        progressed = _pass_progress(records) if new_success is None else new_success
        self._idle_passes = 0 if progressed else self._idle_passes + 1
        if verdict is None and cfg.no_progress > 0 and self._idle_passes >= cfg.no_progress:
            verdict = StuckVerdict(
                "no_progress", self._idle_passes,
                f"{self._idle_passes} consecutive attempts made no new step succeed",
                iterations=self._passes,
            )
        return verdict


_WHAT_TO_TRY = {
    "identical_calls": (
        "Change what is being asked: give the tool different arguments, use a different tool, or "
        "fix whatever the failing output points at before running it again."
    ),
    "repeated_error": (
        "The cause is probably outside the plan (a missing file, a permission, a service that is "
        "down). Check that, or give me the detail I am missing."
    ),
    "no_progress": (
        "Break the task into a smaller first step, or tell me which part to do first."
    ),
}


def escalation_message(verdict: StuckVerdict) -> str:
    """What the user is told when the loop gives up: what was repeated, and what to try."""
    return (
        f"I stopped because I was going in circles: {verdict.detail}. "
        f"Repeating it again would not change the result. "
        f"{_WHAT_TO_TRY.get(verdict.rule, 'Tell me how you would like to proceed.')}"
    )
