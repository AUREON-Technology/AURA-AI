"""The content gate a refreshed source passes **before** any of its documents is written (ADR-008).

``PromotionGate`` judges an index that has already been built. By then the nightly refresh had
ingested every document straight into the live corpus, so a source that started returning an error
page, or a poisoned one, had changed production before any gate ran. This gate runs first, on what
:meth:`~aura_core.knowledge.engine.KnowledgeEngine.assess` says each document *would* do, and holds
a failing source back as a whole. Sources that pass are applied; a held one is retried next run.

Two checks, each for one failure mode:

``max_quarantine_rate``  the share of a source's chunks the context guard would drop. A few is the
                         guard doing its job; most of a source is a poisoned (or compromised) one.
``max_shrink``           how far the source's *updated* documents shrink in total. A source that
                         started returning a short error page shrinks sharply.

Both are measured across the whole source, not per document, so one odd document does not hold back
a thousand good ones, while the same oddity in a source of three does.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Sequence

from .index_manager import GateResult

if TYPE_CHECKING:  # pragma: no cover - typing only; the engine imports nothing from here
    from .engine import IngestAssessment

__all__ = ["RefreshContentGate"]


@dataclass(frozen=True, slots=True)
class RefreshContentGate:
    max_quarantine_rate: float = 0.10
    max_shrink: float = 0.40

    def evaluate(self, assessments: Sequence["IngestAssessment"]) -> GateResult:
        failures: list[str] = []
        measurements: dict[str, float] = {}

        # Every chunk the guard looked at: those that would be indexed and those it would drop. A
        # fully quarantined document contributes only to the second count, which is what makes a
        # source made of poisoned documents fail rather than divide by zero.
        quarantined = sum(a.quarantined_chunks for a in assessments)
        total = quarantined + sum(a.chunks for a in assessments)
        if total:
            rate = quarantined / total
            measurements["quarantine_rate"] = round(rate, 4)
            if rate > self.max_quarantine_rate:
                failures.append(
                    f"quarantine rate {rate:.1%} exceeds {self.max_quarantine_rate:.1%}"
                )

        # Only documents that would replace an existing version: a new document has nothing to
        # shrink from, an unchanged one is not written, and a fully quarantined one writes nothing
        # (the live version stays; the quarantine rate judges it).
        updated = [
            a for a in assessments
            if a.previous_bytes is not None and not a.unchanged and not a.fully_quarantined
        ]
        previous_bytes = sum(a.previous_bytes or 0 for a in updated)
        if previous_bytes > 0:
            shrink = 1.0 - sum(a.new_bytes for a in updated) / previous_bytes
            measurements["shrink"] = round(shrink, 4)
            if shrink > self.max_shrink:
                failures.append(
                    f"shrink {shrink:.1%} across updated documents exceeds {self.max_shrink:.1%}"
                )

        return GateResult(passed=not failures, failures=tuple(failures), measurements=measurements)
