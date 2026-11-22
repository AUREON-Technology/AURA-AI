"""The per-source content gate that runs before a refresh writes anything (ADR-008, Loop A).

It reads what ``KnowledgeEngine.assess`` says each document would do and decides whether a source's
documents may be applied. Two failure modes it exists for: a source that started returning an
error page (its documents shrink sharply) and a poisoned one (too many chunks quarantined).
"""

from __future__ import annotations

from aura_core.domain import DocumentId
from aura_core.knowledge import RefreshContentGate
from aura_core.knowledge.engine import IngestAssessment


def _doc(
    *, chunks: int = 10, quarantined: int = 0, new: int = 1000, previous: int | None = None,
    unchanged: bool = False, fully_quarantined: bool = False,
) -> IngestAssessment:
    return IngestAssessment(
        document_id=DocumentId("d"), would_version=None if unchanged or fully_quarantined else 1,
        unchanged=unchanged, fully_quarantined=fully_quarantined, chunks=chunks,
        quarantined_chunks=quarantined, new_bytes=new, previous_bytes=previous,
    )


class TestQuarantineRate:
    def test_a_source_with_a_few_quarantined_chunks_passes(self) -> None:
        result = RefreshContentGate().evaluate([_doc(chunks=95, quarantined=5)])
        assert result.passed
        assert result.measurements["quarantine_rate"] == 0.05

    def test_the_boundary_is_inclusive(self) -> None:
        assert RefreshContentGate(max_quarantine_rate=0.10).evaluate(
            [_doc(chunks=90, quarantined=10)]
        ).passed

    def test_a_mostly_quarantined_source_is_held(self) -> None:
        result = RefreshContentGate().evaluate([_doc(chunks=70, quarantined=30)])
        assert not result.passed
        assert result.failures[0].startswith("quarantine")

    def test_the_rate_is_taken_across_the_whole_source(self) -> None:
        """One poisoned document among many clean ones is within tolerance; the same poison in a
        small source is not."""
        clean = [_doc(chunks=20) for _ in range(9)]
        assert RefreshContentGate().evaluate(clean + [_doc(chunks=10, quarantined=10)]).passed
        small = [_doc(chunks=10), _doc(chunks=0, quarantined=10, fully_quarantined=True)]
        assert not RefreshContentGate().evaluate(small).passed

    def test_a_fully_quarantined_document_counts_against_the_source(self) -> None:
        result = RefreshContentGate().evaluate(
            [_doc(chunks=0, quarantined=8, fully_quarantined=True), _doc(chunks=2)]
        )
        assert not result.passed

    def test_a_source_with_no_chunks_has_no_rate(self) -> None:
        result = RefreshContentGate().evaluate([_doc(chunks=0, unchanged=True)])
        assert result.passed
        assert "quarantine_rate" not in result.measurements


class TestShrink:
    def test_documents_that_stay_about_the_same_size_pass(self) -> None:
        assert RefreshContentGate().evaluate([_doc(new=950, previous=1000)]).passed

    def test_growing_documents_pass(self) -> None:
        assert RefreshContentGate().evaluate([_doc(new=3000, previous=1000)]).passed

    def test_a_source_whose_documents_collapse_is_held(self) -> None:
        """The classic: the source started returning a short error page."""
        result = RefreshContentGate().evaluate([_doc(new=200, previous=1000)])
        assert not result.passed
        assert result.failures[0].startswith("shrink")
        assert result.measurements["shrink"] == 0.8

    def test_the_boundary_is_inclusive(self) -> None:
        assert RefreshContentGate(max_shrink=0.40).evaluate([_doc(new=600, previous=1000)]).passed

    def test_shrink_is_summed_across_updated_documents(self) -> None:
        """One document shrinking a lot is fine if the others grew; the source as a whole decides."""
        mixed = [_doc(new=100, previous=1000), _doc(new=2000, previous=1000)]
        assert RefreshContentGate().evaluate(mixed).passed

    def test_new_and_unchanged_documents_do_not_count(self) -> None:
        documents = [
            _doc(new=50, previous=None),                    # new: nothing to shrink from
            _doc(new=50, previous=None, unchanged=True),
            _doc(new=900, previous=1000),
        ]
        assert RefreshContentGate().evaluate(documents).passed

    def test_a_fully_quarantined_update_is_not_measured_as_shrink(self) -> None:
        """It would write nothing, so the live document stays as it is; the quarantine rate is what
        judges it."""
        result = RefreshContentGate(max_quarantine_rate=1.0).evaluate(
            [_doc(chunks=0, quarantined=5, new=100, previous=1000, fully_quarantined=True)]
        )
        assert result.passed

    def test_a_source_with_nothing_to_compare_passes(self) -> None:
        assert RefreshContentGate().evaluate([]).passed


class TestBothChecks:
    def test_every_failing_check_is_reported(self) -> None:
        result = RefreshContentGate().evaluate(
            [_doc(chunks=10, quarantined=10, new=100, previous=1000)]
        )
        assert not result.passed
        assert {f.split()[0] for f in result.failures} == {"quarantine", "shrink"}
