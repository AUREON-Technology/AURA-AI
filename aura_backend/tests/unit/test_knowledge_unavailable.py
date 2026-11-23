"""WP-1 (knowledge readiness KB-001 / KB-002): an index nobody can read is visible and repairable.

When the published index was built by another embedding model, every retrieval returns
``strategy="unavailable"``. That used to leave no trace on the turn, in the metrics or in an upload's
response, and the nightly refresh returned early whenever no source was registered, so it could not
repair the very state it exists to repair. These tests pin the four fixes:

* the turn says it answered without the knowledge base (``knowledge_unavailable``);
* the retrieval counter records the attempt (``result="unavailable"``);
* an ingest response says whether the document can be found yet (``searchable``);
* a refresh with no enabled source still rebuilds a stale published index.
"""

from __future__ import annotations

import pytest

from aura_core.domain.identifiers import DocumentId, UserId
from aura_core.domain.knowledge import IndexIdentity
from aura_core.kernel import TurnRequest
from aura_core.knowledge.engine import KnowledgeEngine
from aura_core.learning.knowledge_refresh import KnowledgeRefreshPipeline
from aura_core.learning.sources import InMemorySourceRegistry
from aura_core.observability.metrics import PROMETHEUS_AVAILABLE, Metrics

needs_prometheus = pytest.mark.skipif(
    not PROMETHEUS_AVAILABLE, reason="prometheus_client is not installed"
)

ALIAS = "test_knowledge"


async def _foreign_index(vectors, embedder) -> IndexIdentity:
    """Publish an index built by a different embedding model: the KM-01 state."""
    foreign = IndexIdentity(ALIAS, 1, "legacy-embedder", embedder.dimension)
    await vectors.set_alias(ALIAS, foreign)
    return foreign


class TestRetrievalReportsUnavailable:
    async def test_a_foreign_index_is_unavailable_not_empty(
        self, knowledge, vectors, embedder, user
    ) -> None:
        await _foreign_index(vectors, embedder)
        result = await knowledge.retrieve("annual leave policy", owner_id=user)
        assert result.is_unavailable is True
        # The old signal stays false: nothing was searched, so nothing was "found empty".
        assert result.is_empty_but_searched is False
        assert result.chunks == ()

    async def test_a_matching_index_is_not_unavailable(self, knowledge, user) -> None:
        await knowledge.ingest(content="# Leave\nAnnual leave is 21 days.", title="Leave", owner_id=user)
        result = await knowledge.retrieve("annual leave", owner_id=user)
        assert result.is_unavailable is False

    @needs_prometheus
    async def test_the_attempt_is_counted(
        self, vectors, documents, embedder, index_manager, safety, graph, user
    ) -> None:
        metrics = Metrics(enabled=True)
        engine = KnowledgeEngine(
            documents=documents, vectors=vectors, embedder=embedder, index_manager=index_manager,
            safety=safety, graph=graph, metrics=metrics,
        )
        await _foreign_index(vectors, embedder)

        def counted() -> float:
            return metrics.registry.get_sample_value(
                "aura_retrieval_total", {"strategy": "hybrid", "result": "unavailable"}
            ) or 0.0

        before = counted()
        await engine.retrieve("annual leave policy", owner_id=user)
        assert counted() == before + 1


class TestTheTurnSaysSo:
    async def test_a_turn_without_the_knowledge_base_is_degraded_with_a_reason(
        self, container
    ) -> None:
        await container.startup()
        await _foreign_index_for(container)
        result = await container.kernel.run(
            TurnRequest(message="What does our leave policy say?", user_id=UserId("asker"))
        )
        assert result.telemetry.retrieval.unavailable is True
        assert result.degraded is True
        assert "knowledge_unavailable" in result.telemetry.degraded_reasons
        # It is not reported as "searched and found nothing".
        assert "empty_retrieval" not in result.telemetry.degraded_reasons

    async def test_a_healthy_index_adds_no_such_reason(self, container) -> None:
        await container.startup()
        result = await container.kernel.run(
            TurnRequest(message="What does our leave policy say?", user_id=UserId("asker"))
        )
        assert "knowledge_unavailable" not in result.telemetry.degraded_reasons


async def _foreign_index_for(container) -> None:
    alias = container.index_manager._alias
    await container.vectors.set_alias(
        alias, IndexIdentity(alias, 1, "legacy-embedder", container.embedder.dimension)
    )


def _client(container, user: str = "uploader"):
    from fastapi.testclient import TestClient

    from aura_core.api import Principal, build_app

    async def authenticator(request):
        return Principal(user_id=UserId(user), email="u@example.com", role="User")

    return TestClient(build_app(container, authenticator=authenticator))


class TestIngestSaysWhetherItIsSearchable:
    async def test_a_document_is_searchable_when_the_index_matches(self, container) -> None:
        await container.startup()
        with _client(container) as client:
            response = client.post(
                "/api/v3/knowledge/documents",
                json={"title": "Leave", "content": "# Leave\nAnnual leave is 21 days."},
            )
        assert response.status_code == 200, response.text
        assert response.json()["searchable"] is True

    async def test_a_document_is_not_searchable_while_the_index_is_foreign(self, container) -> None:
        await container.startup()
        await _foreign_index_for(container)
        with _client(container) as client:
            response = client.post(
                "/api/v3/knowledge/documents",
                json={"title": "Leave", "content": "# Leave\nAnnual leave is 21 days."},
            )
        assert response.status_code == 200, response.text
        body = response.json()
        # Stored and acknowledged, but invisible to search until an admin rebuilds.
        assert body["created"] is True
        assert body["searchable"] is False


class TestRefreshWithNoSourcesStillRepairsTheIndex:
    """KM-01: the repair path must not depend on a source being registered."""

    async def _stale_corpus(self, knowledge, vectors, embedder, user) -> None:
        await knowledge.ingest(
            content="# Leave\nAnnual leave is 21 days per year.", title="Leave",
            document_id=DocumentId("leave"), owner_id=user,
        )
        await _foreign_index(vectors, embedder)

    async def test_a_stale_index_is_rebuilt_and_promoted(
        self, knowledge, vectors, embedder, index_manager, user
    ) -> None:
        await self._stale_corpus(knowledge, vectors, embedder, user)
        assert (await index_manager.status())["matches_embedder"] is False

        pipeline = KnowledgeRefreshPipeline(
            registry=InMemorySourceRegistry([]), fetcher=object(), knowledge=knowledge,
            index_manager=index_manager, max_documents=10,
        )
        report = await pipeline.run()

        assert report.index_rebuild == "promoted"
        assert report.promoted is True
        assert (await index_manager.status())["matches_embedder"] is True
        result = await knowledge.retrieve("annual leave", owner_id=user)
        assert result.is_unavailable is False

    async def test_a_dry_run_changes_nothing(
        self, knowledge, vectors, embedder, index_manager, user
    ) -> None:
        await self._stale_corpus(knowledge, vectors, embedder, user)
        pipeline = KnowledgeRefreshPipeline(
            registry=InMemorySourceRegistry([]), fetcher=object(), knowledge=knowledge,
            index_manager=index_manager, max_documents=10,
        )
        report = await pipeline.run(dry_run=True)
        assert report.promoted is False
        assert (await index_manager.status())["matches_embedder"] is False

    async def test_an_up_to_date_index_is_left_alone(
        self, knowledge, index_manager, user
    ) -> None:
        await knowledge.ingest(content="# Leave\nAnnual leave is 21 days.", title="Leave", owner_id=user)
        before = (await index_manager.status())["published"]
        pipeline = KnowledgeRefreshPipeline(
            registry=InMemorySourceRegistry([]), fetcher=object(), knowledge=knowledge,
            index_manager=index_manager, max_documents=10,
        )
        report = await pipeline.run()
        assert report.promoted is False
        assert report.index_rebuild == "not_needed"
        assert (await index_manager.status())["published"] == before
