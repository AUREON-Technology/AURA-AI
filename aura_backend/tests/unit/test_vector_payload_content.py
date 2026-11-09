"""No readable content in vector payloads when the vector store is off-host (audit FSA-018 / CB-22).

Every chunk's text and every memory's ``subject`` were copied into the Qdrant payload, so on the
Railway design (Qdrant Cloud) a provider-side operator or an API-key leak exposed every user's
documents in the clear. The payload now carries content only when the vector store is on-host
(the in-memory store, a Docker service name, a private or ``.internal`` address); off-host the
chunk text is resolved from the owner-scoped document store on retrieval, which the engine
already did for lexical-only hits. ``AURA_KNOWLEDGE_VECTOR_PAYLOAD_CONTENT`` overrides the rule
either way, and /health/ready reports the decision.
"""

from __future__ import annotations

from dataclasses import replace

import pytest

from aura_core.adapters import InMemoryMemoryStore
from aura_core.config.settings import InfrastructureSettings, KnowledgeSettings, vector_payload_content_enabled
from aura_core.domain.identifiers import UserId
from aura_core.domain.memory import MemoryQuery
from aura_core.knowledge.engine import KnowledgeEngine
from aura_core.knowledge.index_manager import IndexManager
from aura_core.memory.engine import MemoryEngine


class TestTheRule:
    @pytest.mark.parametrize("explicit, backend, url, expected", [
        (True, "qdrant", "https://x.cloud.qdrant.io:6333", True),
        (False, "memory", "", False),
        (None, "memory", "", True),
        (None, "qdrant", "http://qdrant:6333", True),
        (None, "qdrant", "http://localhost:6333", True),
        (None, "qdrant", "http://10.0.0.5:6333", True),
        (None, "qdrant", "http://qdrant.railway.internal:6333", True),
        (None, "qdrant", "https://abc-123.us-east.aws.cloud.qdrant.io:6333", False),
        (None, "qdrant", "https://vectors.example.com", False),
    ])
    def test_content_stays_on_host_only(self, explicit, backend, url, expected) -> None:
        knowledge = replace(KnowledgeSettings(), vector_payload_content=explicit)
        infra = replace(InfrastructureSettings(), vector_backend=backend, qdrant_url=url)
        assert vector_payload_content_enabled(knowledge, infra) is expected

    def test_the_variable_is_tri_state(self, monkeypatch) -> None:
        monkeypatch.delenv("AURA_KNOWLEDGE_VECTOR_PAYLOAD_CONTENT", raising=False)
        assert KnowledgeSettings.from_env().vector_payload_content is None
        monkeypatch.setenv("AURA_KNOWLEDGE_VECTOR_PAYLOAD_CONTENT", "false")
        assert KnowledgeSettings.from_env().vector_payload_content is False


def _points(vectors, identity):
    return list(vectors._indexes[identity.physical_name].values())


class TestKnowledgePayloads:
    async def _engine(self, vectors, documents, embedder, safety, graph, metrics, *, payload_content: bool):
        index_manager = IndexManager(
            vectors=vectors, documents=documents, embedder=embedder, alias="payload-test",
            payload_content=payload_content,
        )
        return KnowledgeEngine(
            documents=documents, vectors=vectors, embedder=embedder, index_manager=index_manager,
            safety=safety, graph=graph, metrics=metrics, score_threshold=0.0,
        ), index_manager

    async def test_off_host_payloads_carry_no_text_and_retrieval_still_reads_it(
        self, vectors, documents, embedder, safety, graph, metrics
    ) -> None:
        engine, index_manager = await self._engine(
            vectors, documents, embedder, safety, graph, metrics, payload_content=False
        )
        owner = UserId("alice")
        await engine.ingest(title="notes", content="# Notes\n\nThe Cairo office opens at nine.", owner_id=owner)
        points = _points(vectors, index_manager.identity_for_current_embedder())
        assert points and all("content" not in p.payload for p in points)
        result = await engine.retrieve("when does the Cairo office open", owner_id=owner)
        assert result.chunks, "nothing retrieved"
        assert any("Cairo office opens at nine" in c.chunk.content for c in result.chunks)

    async def test_on_host_payloads_still_carry_the_text(
        self, vectors, documents, embedder, safety, graph, metrics
    ) -> None:
        engine, index_manager = await self._engine(
            vectors, documents, embedder, safety, graph, metrics, payload_content=True
        )
        await engine.ingest(title="notes", content="# Notes\n\nPlain on-host text.", owner_id=UserId("alice"))
        points = _points(vectors, index_manager.identity_for_current_embedder())
        assert points and all(p.payload.get("content") for p in points)


class TestMemoryPayloads:
    async def test_off_host_payloads_carry_no_subject_and_recall_still_works(self, vectors, embedder, metrics, events, clock) -> None:
        memory = MemoryEngine(
            store=InMemoryMemoryStore(), vectors=vectors, embedder=embedder, index_alias="pm",
            metrics=metrics, event_bus=events, clock=clock, payload_content=False,
        )
        owner = UserId("alice")
        record = await memory.remember(owner_id=owner, content="Alice drinks tea.", subject="drink")
        points = _points(vectors, memory._identity)
        assert points and all("subject" not in p.payload for p in points)
        hits = await memory.recall(MemoryQuery(owner_id=owner, text="Alice drinks tea"))
        assert [h.record.id for h in hits] == [record.id]
        assert hits[0].record.subject == "drink"


class TestTheContainerAppliesTheRule:
    async def test_the_override_reaches_both_engines_and_the_readiness_report(self, monkeypatch) -> None:
        from aura_core.config import load_settings
        from aura_core.container import Container

        monkeypatch.setenv("AURA_KNOWLEDGE_VECTOR_PAYLOAD_CONTENT", "false")
        container = Container(load_settings(refresh=True, validate=False))
        assert container.index_manager.payload_content is False
        assert container.memory.payload_content is False
        await container.startup()
        try:
            health = await container.health()
        finally:
            await container.shutdown()
        assert health["vectors"] == {"backend": "memory", "payload_content": False}
