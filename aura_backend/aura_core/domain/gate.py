"""Evaluation gate results: what AP8's gate measured on one blueprint version (ADR-024).

A :class:`GateReport` is stored on the :class:`~aura_core.domain.compilation.BlueprintRecord` it
evaluated and bound to that record's digest, so the approval request can cite it and a reviewer
sees exactly which cases ran, which were skipped and why.

Three outcomes per case, deliberately: ``passed``, ``failed`` and ``not_run``. A case that could
not run (no reachable replica for its connector, an identifier the replica could not supply, a
model-dependent case with no model) is never counted as a pass. Each suite therefore has two
numbers -- its pass rate over the cases that ran, and its *coverage* (ran / total) -- and a gating
suite is satisfied only when both clear their floors.
"""

from __future__ import annotations

import enum
import re
from dataclasses import dataclass
from typing import Any

from .errors import ValidationError

__all__ = ["GateMode", "CaseOutcome", "GateCaseResult", "GateSuiteResult", "GateReport", "suite_result"]

_SLUG = re.compile(r"^[a-z][a-z0-9_]{0,47}$")
_CASE_ID = re.compile(r"^[a-z][a-z0-9_.-]{0,159}$")
_CODE = re.compile(r"^[a-z][a-z0-9_]*(:[a-z0-9_.]+)?$")
_SHA256 = re.compile(r"^sha256:[0-9a-f]{64}$")
MAX_DETAIL = 300
MAX_CASES = 2000


class GateMode(str, enum.Enum):
    #: A failing gating suite blocks the version from leaving ``proposed``.
    ENFORCE = "enforce"
    #: The gate runs and is recorded, but a failure does not block (the roadmap's rollback lever).
    ADVISORY = "advisory"

    def __str__(self) -> str:
        return self.value


class CaseOutcome(str, enum.Enum):
    PASSED = "passed"
    FAILED = "failed"
    NOT_RUN = "not_run"

    def __str__(self) -> str:
        return self.value


@dataclass(frozen=True, slots=True)
class GateCaseResult:
    """One case's outcome. ``reason`` is a code (``ok``, ``row_mismatch``, ``replica_unavailable``,
    ...); ``detail`` is gate-written text, never a vendor error message or connected-system data."""

    id: str
    suite: str
    outcome: CaseOutcome
    reason: str
    detail: str = ""

    def __post_init__(self) -> None:
        if not _CASE_ID.match(self.id):
            raise ValidationError("gate case ids are dotted slugs", details={"id": self.id})
        if not _SLUG.match(self.suite):
            raise ValidationError("suite names are slugs", details={"suite": self.suite})
        if not _CODE.match(self.reason):
            raise ValidationError("gate reasons are codes", details={"reason": self.reason})
        if len(self.detail) > MAX_DETAIL:
            raise ValidationError(f"gate detail is at most {MAX_DETAIL} characters")


@dataclass(frozen=True, slots=True)
class GateSuiteResult:
    name: str
    gating: bool
    threshold: float
    min_coverage: float
    total: int
    ran: int
    passed: int
    ok: bool

    def __post_init__(self) -> None:
        if not _SLUG.match(self.name):
            raise ValidationError("suite names are slugs", details={"name": self.name})
        if not (0.0 <= self.threshold <= 1.0 and 0.0 <= self.min_coverage <= 1.0):
            raise ValidationError("gate floors are rates in [0, 1]", details={"suite": self.name})
        if not 0 <= self.passed <= self.ran <= self.total:
            raise ValidationError("gate counts must satisfy passed <= ran <= total", details={"suite": self.name})
        if self.ok != _clears(self.total, self.ran, self.passed, self.threshold, self.min_coverage):
            raise ValidationError("a suite's ok flag must follow from its counts", details={"suite": self.name})

    @property
    def pass_rate(self) -> float:
        return round(self.passed / self.ran, 4) if self.ran else 0.0

    @property
    def coverage(self) -> float:
        return round(self.ran / self.total, 4) if self.total else 1.0


def _clears(total: int, ran: int, passed: int, threshold: float, min_coverage: float) -> bool:
    if total == 0:
        return True  # nothing to gate on: the compiler generated no case of this kind
    if ran == 0:
        return False
    return ran / total >= min_coverage and passed / ran >= threshold


def suite_result(name: str, cases: list[GateCaseResult], *, gating: bool, threshold: float,
                 min_coverage: float) -> GateSuiteResult:
    ran = [c for c in cases if c.outcome is not CaseOutcome.NOT_RUN]
    passed = sum(1 for c in ran if c.outcome is CaseOutcome.PASSED)
    return GateSuiteResult(
        name=name, gating=gating, threshold=threshold, min_coverage=min_coverage, total=len(cases),
        ran=len(ran), passed=passed, ok=_clears(len(cases), len(ran), passed, threshold, min_coverage),
    )


@dataclass(frozen=True, slots=True)
class GateReport:
    """One gate run over one blueprint version. ``passed`` is whether every gating suite cleared
    its floors; in :attr:`GateMode.ADVISORY` a version may advance with ``passed=False``."""

    project_id: str
    version: int
    digest: str
    mode: GateMode
    passed: bool
    ran_at: str
    ran_by: str
    duration_ms: float
    suites: tuple[GateSuiteResult, ...]
    cases: tuple[GateCaseResult, ...] = ()

    def __post_init__(self) -> None:
        if not _SHA256.match(self.digest):
            raise ValidationError("a gate report binds a sha256 digest")
        if self.version < 1:
            raise ValidationError("versions start at 1")
        names = [s.name for s in self.suites]
        if len(set(names)) != len(names):
            raise ValidationError("duplicate suites in a gate report")
        if self.passed != all(s.ok for s in self.suites if s.gating):
            raise ValidationError("a report passes exactly when every gating suite is ok")
        if len(self.cases) > MAX_CASES:
            raise ValidationError(f"a gate report holds at most {MAX_CASES} cases")
        if not self.ran_by or len(self.ran_by) > 256:
            raise ValidationError("ran_by must be 1-256 characters")

    def blocking(self) -> tuple[str, ...]:
        """Gating suites that did not clear their floors."""
        return tuple(s.name for s in self.suites if s.gating and not s.ok)

    def summary(self) -> dict[str, Any]:
        return {
            "version": self.version, "digest": self.digest, "mode": self.mode.value, "passed": self.passed,
            "ran_at": self.ran_at, "ran_by": self.ran_by, "duration_ms": round(self.duration_ms, 1),
            "blocking": list(self.blocking()),
            "suites": {
                s.name: {"gating": s.gating, "pass_rate": s.pass_rate, "threshold": s.threshold,
                         "coverage": s.coverage, "min_coverage": s.min_coverage, "total": s.total,
                         "ran": s.ran, "passed": s.passed, "ok": s.ok}
                for s in self.suites
            },
        }
