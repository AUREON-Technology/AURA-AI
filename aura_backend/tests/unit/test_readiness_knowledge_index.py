"""Readiness reports whether the published knowledge index can answer queries (KM-01)."""

from __future__ import annotations

from aura_core.domain import IndexIdentity


class TestReadinessKnowledgeIndex:
    async def test_readiness_reports_a_matching_index(self, container) -> None:
        await container.index_manager.ensure_ready()
        health = await container.health()
        index = health["knowledge_index"]
        assert index["matches_embedder"] is True
        assert index["published"] == index["required"]
        # The existing field keeps its shape: older tooling reads it as a string.
        assert health["index"] == index["published"]

    async def test_readiness_reports_an_index_built_by_another_embedder(self, container) -> None:
        embedder = container.embedder
        foreign = IndexIdentity("aura_knowledge", 1, "legacy-embedder", embedder.dimension)
        await container.vectors.set_alias(container.settings.knowledge.index_alias, foreign)
        health = await container.health()
        index = health["knowledge_index"]
        assert index["matches_embedder"] is False
        assert index["published"] == foreign.physical_name
        assert health["index"] == foreign.physical_name

    async def test_a_mismatch_is_informational_and_does_not_gate_readiness(self, container) -> None:
        """Chat still works without knowledge. Failing readiness would take the whole gateway out
        of the load balancer for a problem that only an admin rebuild can fix."""
        baseline = await container.health()
        embedder = container.embedder
        foreign = IndexIdentity("aura_knowledge", 1, "legacy-embedder", embedder.dimension)
        await container.vectors.set_alias(container.settings.knowledge.index_alias, foreign)
        mismatched = await container.health()
        assert mismatched["healthy"] is baseline["healthy"]

    async def test_the_mismatch_gauge_follows_readiness(self, container) -> None:
        # The test container's metrics are no-ops, so record what readiness sets on the gauge.
        class _Gauge:
            values: list[float] = []

            def set(self, value: float) -> None:
                self.values.append(value)

        gauge = _Gauge()
        gauge.values = []
        container.metrics.knowledge_index_mismatch = gauge
        await container.index_manager.ensure_ready()
        await container.health()
        embedder = container.embedder
        foreign = IndexIdentity("aura_knowledge", 1, "legacy-embedder", embedder.dimension)
        await container.vectors.set_alias(container.settings.knowledge.index_alias, foreign)
        await container.health()
        assert gauge.values == [0, 1]
