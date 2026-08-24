"""``DomainEvent.as_dict()`` must flatten identifier fields to their bare string value.

Regression coverage for a real bug found while building Phase E1's outbox: ``as_dict()`` used to
call ``dataclasses.asdict()``, which recursively converts every nested dataclass -- including
each identifier field, itself a frozen dataclass -- into a plain ``{"value": ...}`` dict *before*
the method's own flattening check ever saw it. The check was never wrong; it just never ran on
anything it was meant to run on. Round-tripping an event through ``as_dict()`` is exactly what a
durable outbox needs to do, so this is exercised for real by ``aura_core/triggers/specs.py``'s
decoders, not only here -- this file pins the underlying contract in isolation.
"""

from __future__ import annotations

from datetime import datetime, timezone

from aura_core.domain.events import DocumentIngested, TurnFailed
from aura_core.domain.identifiers import CorrelationId, DocumentId, TurnId, UserId


class TestDomainEventAsDict:
    def test_identifier_fields_flatten_to_their_bare_value(self) -> None:
        event = DocumentIngested(
            document_id=DocumentId("doc1"), version=2, chunks=5, source_type="upload",
            owner_id=UserId("u1"), quarantined=False,
        )
        payload = event.as_dict()
        assert payload["document_id"] == "doc1"
        assert payload["owner_id"] == "u1"
        assert not isinstance(payload["document_id"], dict)
        assert not isinstance(payload["owner_id"], dict)

    def test_datetime_fields_become_iso_strings(self) -> None:
        event = DocumentIngested(
            occurred_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
            document_id=DocumentId("doc1"), owner_id=UserId("u1"),
        )
        assert event.as_dict()["occurred_at"] == "2026-01-01T00:00:00+00:00"

    def test_none_identifier_stays_none(self) -> None:
        event = DocumentIngested(document_id=None, owner_id=None)
        payload = event.as_dict()
        assert payload["document_id"] is None
        assert payload["owner_id"] is None

    def test_correlation_id_flattens_too(self) -> None:
        event = TurnFailed(
            correlation_id=CorrelationId("c1"), turn_id=TurnId("t1"),
            error_kind="internal_error", stage="planning", retryable=True,
        )
        payload = event.as_dict()
        assert payload["correlation_id"] == "c1"
        assert payload["turn_id"] == "t1"

    def test_event_name_is_included(self) -> None:
        event = DocumentIngested(document_id=DocumentId("doc1"), owner_id=UserId("u1"))
        assert event.as_dict()["event"] == "DocumentIngested"

    def test_plain_scalar_and_bool_fields_pass_through_unchanged(self) -> None:
        event = DocumentIngested(
            document_id=DocumentId("doc1"), owner_id=UserId("u1"),
            version=3, chunks=7, source_type="drive", quarantined=True,
        )
        payload = event.as_dict()
        assert payload["version"] == 3
        assert payload["chunks"] == 7
        assert payload["source_type"] == "drive"
        assert payload["quarantined"] is True
