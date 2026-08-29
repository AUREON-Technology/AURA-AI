"""CAG prefix: stability, isolation, budget, and RAG dedup (ADR-010)."""

from __future__ import annotations

import pytest

from aura_core.context import ContextEngine, ContextRequest
from aura_core.domain import Conversation, Message
from aura_core.domain.context import ContextSection
from aura_core.domain.errors import ValidationError
from aura_core.domain.identifiers import ChunkId, DocumentId, UserId
from aura_core.domain.knowledge import (
    Chunk,
    Document,
    RetrievalResult,
    RetrievedChunk,
    TrustTier,
)
from aura_core.domain.messages import TrustChannel
from aura_core.prompts import build_default_registry


async def _ingest_and_pin(
    container,
    *,
    title: str,
    content: str,
    owner_id: UserId | None,
    trust_tier: TrustTier = TrustTier.COMMUNITY,
    document_id: str | None = None,
) -> Document:
    result = await container.knowledge.ingest(
        title=title,
        content=content,
        owner_id=owner_id,
        trust_tier=trust_tier,
        document_id=DocumentId(document_id) if document_id else None,
    )
    assert result.document is not None
    doc = result.document
    pin_owner = None if owner_id is None else owner_id
    return await container.cag.set_pin(doc.id, owner_id=pin_owner, pinned=True)


@pytest.mark.asyncio
async def test_prefix_bytes_stable_across_assembles(container):
    user = UserId("user-stable")
    await _ingest_and_pin(
        container,
        title="Leave policy",
        content="Annual leave is twenty one days for permanent staff.",
        owner_id=user,
        document_id="leave-policy",
    )

    a = await container.cag.prefix_for(user)
    b = await container.cag.prefix_for(user)
    assert a is not None and b is not None
    assert a.identity.value == b.identity.value
    assert a.prefix_nonce == b.prefix_nonce
    assert a.text == b.text
    assert b.cache_hit is True

    engine = ContextEngine(max_prompt_tokens=400)
    system = (
        build_default_registry()
        .render(
            "system.orchestrator",
            {
                "fence_rules": "",
                "current_time": "2026-08-29T12:00+00:00",
                "capabilities": "calculator",
            },
        )
        .text
    )
    first = engine.assemble(
        ContextRequest(
            system_prompt=system,
            user_message="How much leave do I get?",
            cag_prefix=a,
            max_prompt_tokens=400,
        )
    )
    second = engine.assemble(
        ContextRequest(
            system_prompt=system,
            user_message="How much leave do I get?",
            cag_prefix=b,
            max_prompt_tokens=400,
        )
    )
    cag_first = first.bundle.render(include_sections=(ContextSection.CAG,))
    cag_second = second.bundle.render(include_sections=(ContextSection.CAG,))
    assert cag_first == cag_second
    assert a.prefix_nonce in cag_first
    assert first.bundle.fence_nonce != a.prefix_nonce
    assert first.bundle.fence_nonce not in cag_first


@pytest.mark.asyncio
async def test_unpin_changes_identity(container):
    user = UserId("user-unpin")
    doc = await _ingest_and_pin(
        container,
        title="Prefs",
        content="The user prefers dark mode in every app.",
        owner_id=user,
        document_id="prefs-doc",
    )
    before = await container.cag.prefix_for(user)
    assert before is not None

    await container.cag.set_pin(doc.id, owner_id=user, pinned=False)
    after = await container.cag.prefix_for(user)
    assert after is None


@pytest.mark.asyncio
async def test_tenant_isolation_user_pins(container):
    alice = UserId("alice")
    bob = UserId("bob")
    await _ingest_and_pin(
        container,
        title="Alice secret",
        content="Alice's private project code name is Orion.",
        owner_id=alice,
        document_id="alice-secret",
    )
    await _ingest_and_pin(
        container,
        title="Bob secret",
        content="Bob's private project code name is Neptune.",
        owner_id=bob,
        document_id="bob-secret",
    )

    a_prefix = await container.cag.prefix_for(alice)
    b_prefix = await container.cag.prefix_for(bob)
    assert a_prefix is not None and b_prefix is not None
    assert "Orion" in a_prefix.text
    assert "Orion" not in b_prefix.text
    assert "Neptune" in b_prefix.text
    assert "Neptune" not in a_prefix.text


@pytest.mark.asyncio
async def test_global_handbook_visible_to_all_users(container):
    await _ingest_and_pin(
        container,
        title="Company handbook",
        content="All staff get a wellness stipend of five hundred dollars.",
        owner_id=None,
        trust_tier=TrustTier.INTERNAL,
        document_id="handbook-global",
    )
    for user in (UserId("alice-hb"), UserId("bob-hb")):
        prefix = await container.cag.prefix_for(user)
        assert prefix is not None
        assert "wellness stipend" in prefix.text.lower()
        assert any(d.is_global for d in prefix.docs)


@pytest.mark.asyncio
async def test_untrusted_global_rejected_from_handbook(container):
    result = await container.knowledge.ingest(
        title="Spam",
        content="Buy crypto now for guaranteed returns.",
        owner_id=None,
        trust_tier=TrustTier.UNTRUSTED,
        document_id=DocumentId("spam-global"),
    )
    assert result.document is not None
    with pytest.raises(ValidationError):
        await container.cag.set_pin(result.document.id, owner_id=None, pinned=True)


@pytest.mark.asyncio
async def test_assemble_stays_within_400_with_cag(container):
    user = UserId("budget-user")
    await _ingest_and_pin(
        container,
        title="Budget doc",
        content=("The reimbursement backlog ticket is AURA-4419. " * 20),
        owner_id=user,
        document_id="budget-doc",
    )
    prefix = await container.cag.prefix_for(user)
    assert prefix is not None
    assert prefix.tokens <= 80

    engine = ContextEngine(max_prompt_tokens=400)
    history = Conversation.of(
        [
            Message.user("older"),
            Message.assistant("older reply"),
            Message.user("What is the ticket?"),
            Message.assistant("Checking."),
        ]
    )
    assembled = engine.assemble(
        ContextRequest(
            system_prompt="You are AURA.",
            user_message="Remind me of the ticket id.",
            history=history,
            cag_prefix=prefix,
            max_prompt_tokens=400,
        )
    )
    assert assembled.prompt_tokens <= 400


@pytest.mark.asyncio
async def test_rag_chunk_from_pinned_doc_is_deduped(container):
    user = UserId("dedup-user")
    doc = await _ingest_and_pin(
        container,
        title="Pinned source",
        content="The office wifi password is aurora-secure.",
        owner_id=user,
        document_id="pinned-src",
    )
    prefix = await container.cag.prefix_for(user)
    assert prefix is not None

    chunk = Chunk(
        id=ChunkId(f"{doc.id.value}:v1:0"),
        document_id=doc.id,
        document_version=doc.version,
        index=0,
        content="The office wifi password is aurora-secure. Extra RAG-only sentence.",
    )
    retrieval = RetrievalResult(
        chunks=(RetrievedChunk(chunk=chunk, score=0.99),),
        query="wifi",
        candidates_considered=1,
        strategy="hybrid",
    )
    engine = ContextEngine(max_prompt_tokens=400)
    assembled = engine.assemble(
        ContextRequest(
            system_prompt="You are AURA.",
            user_message="What is the wifi password?",
            cag_prefix=prefix,
            retrieval=retrieval,
            max_prompt_tokens=400,
        )
    )
    knowledge = assembled.bundle.by_section(ContextSection.KNOWLEDGE)
    assert knowledge == ()
    cag_data = [
        i
        for i in assembled.bundle.by_section(ContextSection.CAG)
        if i.channel is TrustChannel.DATA
    ]
    assert any("aurora-secure" in i.content for i in cag_data)


@pytest.mark.asyncio
async def test_cag_pin_cap_enforced(container):
    user = UserId("cap-user")
    for i in range(3):
        await _ingest_and_pin(
            container,
            title=f"Doc {i}",
            content=f"Fact number {i} about the user.",
            owner_id=user,
            document_id=f"cap-doc-{i}",
        )
    result = await container.knowledge.ingest(
        title="Doc 3",
        content="One fact too many.",
        owner_id=user,
        document_id=DocumentId("cap-doc-3"),
    )
    assert result.document is not None
    with pytest.raises(ValidationError):
        await container.cag.set_pin(result.document.id, owner_id=user, pinned=True)
