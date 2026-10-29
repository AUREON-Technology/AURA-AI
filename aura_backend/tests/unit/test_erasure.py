"""Admin erasure reaches every aura_core store it claims to.

``POST /api/v3/admin/users/{subject}/forget`` used to soft-delete six v2 tables and answer
``forgotten: true`` while the user's memories and their vectors, knowledge documents (whose
tombstones keep chunk text), agent-written personal-OS records and server-side transcripts all
stayed. These drive ``_erase_core`` through the real in-memory container.
"""

from __future__ import annotations

from datetime import datetime, timezone

from api.admin_platform import _erase_core
from aura_core.domain.identifiers import PersonalOsRecordId, SessionId, UserId
from aura_core.domain.personal_os import PersonalOsRecord, RecordKind

ALICE = UserId("alice-sub")
BOB = UserId("bob-sub")


async def _seed(container, owner: UserId) -> None:
    await container.memory.remember(owner_id=owner, content=f"{owner.value} likes tea.", subject="drink")
    doc = await container.knowledge.ingest(
        content=f"# Notes\n\n{owner.value}'s private notes about the Cairo office.", title="notes",
        owner_id=owner,
    )
    # A document deleted earlier: its tombstone still holds chunk text.
    old = await container.knowledge.ingest(
        content=f"# Old\n\n{owner.value}'s deleted draft text.", title="old", owner_id=owner,
    )
    await container.knowledge.delete_document(old.document.id, owner_id=owner)
    now = datetime.now(timezone.utc)
    await container.personal_os_store.put(PersonalOsRecord(
        id=PersonalOsRecordId.new(), owner_id=owner, kind=RecordKind.TASK,
        fields={"title": "call the bank"}, created_at=now, updated_at=now,
    ))
    await container.episodic.append(
        SessionId("c1"), owner, user_message="hello", assistant_message="hi",
    )
    assert doc is not None


async def _state(container, owner: UserId) -> dict:
    documents = container.documents
    return {
        "memories": len(await container.memory._store.list_for_owner(owner, include_superseded=True)),
        "document_rows": sum(
            1 for d in documents._documents.values()
            if d.owner_id is not None and d.owner_id.value == owner.value
        ),
        "personal_os": len(await container.personal_os_store.list_for_owner(owner)),
        "transcript": len((await container.episodic.history(SessionId("c1"), owner))[0].messages),
    }


async def test_every_store_is_emptied_for_the_user_and_only_the_user(container) -> None:
    await _seed(container, ALICE)
    await _seed(container, BOB)
    await container.knowledge.ingest(content="# Global\n\nCompany holidays.", title="global")

    counts, failed = await _erase_core(container, ALICE.value, {"c1"})

    assert failed == []
    assert counts["memories"] == 1
    assert counts["documents"] == 2, "the earlier-deleted document's rows must go too"
    assert counts["personal_os_records"] == 1
    assert await _state(container, ALICE) == {
        "memories": 0, "document_rows": 0, "personal_os": 0, "transcript": 0,
    }
    bob = await _state(container, BOB)
    assert bob["memories"] == 1 and bob["document_rows"] == 2 and bob["personal_os"] == 1
    assert bob["transcript"] > 0
    assert any(d.owner_id is None for d in container.documents._documents.values())


async def test_erased_knowledge_is_not_retrievable(container) -> None:
    await _seed(container, ALICE)

    await _erase_core(container, ALICE.value, set())

    result = await container.knowledge.retrieve("Cairo office notes", owner_id=ALICE)
    assert not result.chunks and not result.below_threshold


async def test_a_failing_store_is_reported_not_swallowed(container, monkeypatch) -> None:
    async def broken(owner_id):
        raise RuntimeError("store down")

    monkeypatch.setattr(container.memory, "forget_owner", broken)

    counts, failed = await _erase_core(container, ALICE.value, set())

    assert failed == ["memories"]
    assert "documents" in counts
