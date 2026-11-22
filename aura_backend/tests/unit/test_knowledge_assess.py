"""``KnowledgeEngine.assess``: what an ingest would do, without doing it (Loop A, ADR-008).

The nightly refresh used to ingest first and ask questions afterwards, so a source that started
returning an error page, or a poisoned one, had already changed the live corpus by the time any gate
ran. ``assess`` runs the read-only half of ingestion (content-hash idempotency, the owner check,
versioning, chunking and the context guard) and reports the outcome, so a gate can decide before
anything is written.

The properties under test: it writes nothing, and what it reports is what ``ingest`` then does.
"""

from __future__ import annotations

import pytest

from aura_core.domain import DocumentId
from aura_core.domain.errors import ConflictError, ValidationError


async def _nothing_was_written(knowledge, documents, vectors, index_manager, document_id, owner) -> None:
    assert await documents.get(document_id) is None
    assert knowledge.lexical_size == 0
    active = await index_manager.active_identity()
    assert active is None or await vectors.count(active) == 0


class TestAssessWritesNothing:
    async def test_a_new_document_leaves_no_trace(
        self, knowledge, documents, vectors, index_manager, user, handbook
    ) -> None:
        document_id = DocumentId("handbook")
        assessment = await knowledge.assess(
            content=handbook, title="Handbook", document_id=document_id, owner_id=user
        )

        assert assessment.chunks > 0
        assert assessment.would_version == 1
        assert not assessment.unchanged and not assessment.fully_quarantined
        await _nothing_was_written(knowledge, documents, vectors, index_manager, document_id, user)

    async def test_a_new_version_does_not_supersede_the_old_one(
        self, knowledge, documents, user, handbook
    ) -> None:
        document_id = DocumentId("handbook")
        first = await knowledge.ingest(
            content=handbook, title="Handbook", document_id=document_id, owner_id=user
        )

        assessment = await knowledge.assess(
            content=handbook + "\n\nA new paragraph about parking.", title="Handbook",
            document_id=document_id, owner_id=user,
        )

        assert assessment.would_version == 2
        assert (await documents.get(document_id)).version == first.document.version == 1


class TestAssessMatchesIngest:
    async def test_the_chunk_count_and_version_are_what_ingest_then_produces(
        self, knowledge, user, handbook
    ) -> None:
        document_id = DocumentId("handbook")
        assessment = await knowledge.assess(
            content=handbook, title="Handbook", document_id=document_id, owner_id=user
        )
        result = await knowledge.ingest(
            content=handbook, title="Handbook", document_id=document_id, owner_id=user
        )

        assert assessment.chunks == result.chunks
        assert assessment.would_version == result.document.version
        assert assessment.new_bytes == len(handbook.strip().encode("utf-8"))

    async def test_unchanged_content_is_reported_unchanged(self, knowledge, user, handbook) -> None:
        document_id = DocumentId("handbook")
        await knowledge.ingest(
            content=handbook, title="Handbook", document_id=document_id, owner_id=user
        )

        assessment = await knowledge.assess(
            content=handbook, title="Handbook", document_id=document_id, owner_id=user
        )

        assert assessment.unchanged
        assert assessment.would_version is None
        assert assessment.chunks == 0

    async def test_an_update_reports_the_size_of_what_it_replaces(
        self, knowledge, user, handbook
    ) -> None:
        document_id = DocumentId("handbook")
        await knowledge.ingest(
            content=handbook, title="Handbook", document_id=document_id, owner_id=user
        )
        shorter = handbook[: len(handbook) // 3]

        assessment = await knowledge.assess(
            content=shorter, title="Handbook", document_id=document_id, owner_id=user
        )

        assert assessment.previous_bytes == len(handbook.strip().encode("utf-8"))
        assert assessment.new_bytes == len(shorter.strip().encode("utf-8"))

    async def test_a_new_document_has_no_previous_size(self, knowledge, user, handbook) -> None:
        assessment = await knowledge.assess(
            content=handbook, title="Handbook", document_id=DocumentId("new"), owner_id=user
        )
        assert assessment.previous_bytes is None

    async def test_a_fully_quarantined_document_is_reported_as_ingest_would(
        self, knowledge, user, poisoned_document
    ) -> None:
        document_id = DocumentId("vendor")
        assessment = await knowledge.assess(
            content=poisoned_document, title="Vendor", document_id=document_id, owner_id=user
        )
        result = await knowledge.ingest(
            content=poisoned_document, title="Vendor", document_id=document_id, owner_id=user
        )

        assert assessment.fully_quarantined
        assert result.skipped_reason == "fully_quarantined"
        assert assessment.quarantined_chunks == result.quarantined >= 1
        assert assessment.chunks == 0

    async def test_a_partly_quarantined_document_reports_the_same_split(
        self, knowledge, user, handbook, poisoned_document
    ) -> None:
        mixed = handbook + "\n\n" + poisoned_document
        document_id = DocumentId("mixed")
        assessment = await knowledge.assess(
            content=mixed, title="Mixed", document_id=document_id, owner_id=user
        )
        result = await knowledge.ingest(
            content=mixed, title="Mixed", document_id=document_id, owner_id=user
        )

        assert assessment.quarantined_chunks == result.quarantined
        assert assessment.chunks == result.chunks


class TestAssessRefusesWhatIngestRefuses:
    async def test_someone_elses_document_id_is_a_conflict(self, knowledge, user, handbook) -> None:
        from aura_core.domain import UserId

        document_id = DocumentId("handbook")
        await knowledge.ingest(
            content=handbook, title="Handbook", document_id=document_id, owner_id=user
        )
        with pytest.raises(ConflictError):
            await knowledge.assess(
                content="Different content entirely.", title="Handbook",
                document_id=document_id, owner_id=UserId("someone-else"),
            )

    async def test_empty_content_is_rejected(self, knowledge, user) -> None:
        with pytest.raises(ValidationError):
            await knowledge.assess(content="   ", title="Empty", owner_id=user)

    async def test_oversized_content_is_rejected(self, knowledge, user) -> None:
        knowledge._max_bytes = 50
        with pytest.raises(ValidationError):
            await knowledge.assess(content="x" * 500, title="Huge", owner_id=user)
