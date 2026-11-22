"""The nightly knowledge refresh and its gates (ADR-008, Loop A).

The property under test throughout: **nothing from a source reaches the live corpus unless that
source passes the content gates, and the index is rebuilt and gated only when the embedding model
changes.** A source is assessed first (nothing written), gated as a whole, and only then applied;
a failing source is held back on its own while the others still apply. A different embedding model
is a different physical index, so that rebuild really is separate from the one serving traffic and
a failed gate leaves production exactly as it was.
"""

from __future__ import annotations

import pytest

from aura_core.domain import DocumentId, IndexIdentity, KnowledgeSource, SourceType, TrustTier, UserId
from aura_core.knowledge import IndexManager
from aura_core.learning import (
    FetchedDocument,
    InMemorySourceRegistry,
    KnowledgeRefreshPipeline,
    StaticSourceFetcher,
)
from aura_core.learning.knowledge_refresh import (
    _DocumentBudget,
    _hamming,
    _is_near_duplicate,
    _simhash,
)


def _source(source_id: str, *, owner: UserId | None = None) -> KnowledgeSource:
    return KnowledgeSource(
        id=source_id, type=SourceType.INTERNAL, uri=f"internal://{source_id}",
        owner_id=owner, trust_tier=TrustTier.VERIFIED,
    )


@pytest.fixture
def refresh(knowledge, index_manager, user):
    registry = InMemorySourceRegistry([_source("policies", owner=user)])
    fetcher = StaticSourceFetcher()
    pipeline = KnowledgeRefreshPipeline(
        registry=registry, fetcher=fetcher, knowledge=knowledge,
        index_manager=index_manager, max_documents=100,
    )
    return pipeline, fetcher, registry


class TestRefreshRun:
    async def test_documents_are_ingested_without_a_rebuild_when_the_embedder_is_unchanged(
        self, refresh, knowledge, index_manager, user
    ) -> None:
        """The live index already matches the embedder, so applying a source embeds its documents
        straight into it. There is nothing to promote; the old design rebuilt the whole corpus."""
        pipeline, fetcher, _ = refresh
        await index_manager.ensure_ready()
        fetcher.set(
            "policies",
            [
                FetchedDocument(
                    title="Leave Policy",
                    content="# Leave\nAnnual leave is 21 days per year for permanent staff.",
                    document_id=DocumentId("leave"),
                )
            ],
        )

        report = await pipeline.run()
        assert report.documents_ingested == 1
        assert report.index_rebuild == "not_needed"
        assert not report.promoted
        assert report.generation.value
        found = await knowledge.retrieve("annual leave days per year", owner_id=user)
        assert found and found.chunks[0].chunk.document_id == DocumentId("leave")

    async def test_unchanged_content_is_skipped_on_the_second_run(self, refresh, index_manager) -> None:
        """Without idempotency the nightly job re-versions every document every night."""
        pipeline, fetcher, _ = refresh
        await index_manager.ensure_ready()
        document = FetchedDocument(
            title="Leave Policy",
            content="# Leave\nAnnual leave is 21 days per year for permanent staff.",
            document_id=DocumentId("leave"),
        )
        fetcher.set("policies", [document])

        await pipeline.run()
        second = await pipeline.run()
        assert second.documents_ingested == 0
        assert second.documents_skipped == 1

    async def test_near_duplicates_within_a_run_are_dropped(self, refresh, index_manager) -> None:
        pipeline, fetcher, _ = refresh
        await index_manager.ensure_ready()
        base = "# Leave\nAnnual leave is 21 days per year for all permanent members of staff."
        fetcher.set(
            "policies",
            [
                FetchedDocument(title="A", content=base, document_id=DocumentId("a")),
                FetchedDocument(
                    title="B", content=base + " Updated 2026.", document_id=DocumentId("b")
                ),
            ],
        )
        report = await pipeline.run()
        assert report.sources[0].skipped_duplicate >= 1

    async def test_poisoned_sources_are_quarantined_not_ingested(
        self, refresh, index_manager, poisoned_document
    ) -> None:
        """The same guards a user upload passes through. One ingestion path means the scheduled
        job cannot drift from interactive behaviour."""
        pipeline, fetcher, _ = refresh
        await index_manager.ensure_ready()
        fetcher.set(
            "policies",
            [FetchedDocument(title="Vendor", content=poisoned_document, document_id=DocumentId("v"))],
        )
        report = await pipeline.run()
        assert report.quarantined >= 1
        assert report.documents_ingested == 0

    async def test_oversized_documents_are_rejected(self, knowledge, index_manager, user) -> None:
        registry = InMemorySourceRegistry([
            KnowledgeSource(
                id="s", type=SourceType.INTERNAL, uri="internal://s",
                owner_id=user, max_bytes_per_document=100,
            )
        ])
        fetcher = StaticSourceFetcher({
            "s": [FetchedDocument(title="Huge", content="x" * 5000, document_id=DocumentId("h"))]
        })
        pipeline = KnowledgeRefreshPipeline(
            registry=registry, fetcher=fetcher, knowledge=knowledge, index_manager=index_manager
        )
        await index_manager.ensure_ready()

        report = await pipeline.run()
        assert report.failures == 1
        assert report.documents_ingested == 0

    async def test_markup_heavy_responses_are_rejected(self, knowledge, index_manager, user) -> None:
        """Catches a source that started returning an HTML error page."""
        registry = InMemorySourceRegistry([_source("s", owner=user)])
        fetcher = StaticSourceFetcher({
            "s": [FetchedDocument(
                title="Error", content="<div><span><p>" * 200, document_id=DocumentId("e")
            )]
        })
        pipeline = KnowledgeRefreshPipeline(
            registry=registry, fetcher=fetcher, knowledge=knowledge, index_manager=index_manager
        )
        await index_manager.ensure_ready()
        assert (await pipeline.run()).failures == 1

    async def test_a_failing_source_does_not_abort_the_run(self, knowledge, index_manager, user) -> None:
        class BrokenFetcher:
            async def fetch(self, source):
                if source.id == "broken":
                    raise RuntimeError("upstream is down")
                return [FetchedDocument(
                    title="Good", content="# Leave\nAnnual leave is 21 days per year.",
                    document_id=DocumentId("good"),
                )]

        registry = InMemorySourceRegistry([_source("broken", owner=user), _source("good", owner=user)])
        pipeline = KnowledgeRefreshPipeline(
            registry=registry, fetcher=BrokenFetcher(), knowledge=knowledge,
            index_manager=index_manager,
        )
        await index_manager.ensure_ready()

        report = await pipeline.run()
        outcomes = {s.source_id: s for s in report.sources}
        assert outcomes["broken"].error is not None
        assert outcomes["good"].ingested == 1

    async def test_dry_run_never_promotes(self, refresh, index_manager) -> None:
        pipeline, fetcher, _ = refresh
        await index_manager.ensure_ready()
        fetcher.set("policies", [FetchedDocument(
            title="A", content="# Leave\nAnnual leave is 21 days.", document_id=DocumentId("a")
        )])
        assert not (await pipeline.run(dry_run=True)).promoted

    async def test_no_sources_is_a_clean_no_op(self, knowledge, index_manager) -> None:
        pipeline = KnowledgeRefreshPipeline(
            registry=InMemorySourceRegistry(), fetcher=StaticSourceFetcher(),
            knowledge=knowledge, index_manager=index_manager,
        )
        report = await pipeline.run()
        assert report.documents_ingested == 0 and not report.promoted

    async def test_the_report_is_serialisable(self, refresh, index_manager) -> None:
        """Reports are the operator's record of what the job did overnight."""
        import json

        pipeline, fetcher, _ = refresh
        await index_manager.ensure_ready()
        fetcher.set("policies", [FetchedDocument(
            title="A", content="# Leave\nAnnual leave is 21 days.", document_id=DocumentId("a")
        )])
        payload = json.loads(json.dumps((await pipeline.run()).as_dict()))
        assert payload["promoted"] is False
        assert payload["index_rebuild"] == "not_needed"
        assert payload["held_sources"] == {}
        assert payload["totals"]["held"] == 0


LEAVE = "# Leave\n" + " ".join(
    f"Annual leave rule {i} says permanent staff are entitled to days off each year." for i in range(40)
)
PARKING = "# Parking\n" + " ".join(
    f"Parking rule {i} lets visitors use the north car park after registering at reception." for i in range(20)
)


def _doc(document_id: str, title: str, content: str) -> FetchedDocument:
    return FetchedDocument(title=title, content=content, document_id=DocumentId(document_id))


class _Recorder:
    def __init__(self) -> None:
        self.calls: list = []

    def set(self, value) -> None:  # noqa: ANN001
        self.calls.append(value)

    observe = set

    def inc(self, amount: float = 1) -> None:
        self.calls.append(amount)


class _FakeMetrics:
    """Records what the pipeline reports; the suite's real metrics object is a disabled no-op."""

    def __init__(self) -> None:
        self.refresh_duration = _Recorder()
        self.refresh_sources_held = _Recorder()
        self.refresh_last_success_timestamp = _Recorder()
        self.knowledge_age_seconds = _Recorder()
        self.gate_labels: list[str] = []
        outer = self

        class _Counter:
            def labels(self, *, gate: str) -> _Recorder:
                outer.gate_labels.append(gate)
                return _Recorder()

        self.refresh_gate_failures_total = _Counter()


class TestContentGates:
    """A source is judged as a whole, before anything it fetched is written."""

    async def test_a_source_that_shrinks_sharply_is_held_and_the_others_still_apply(
        self, knowledge, index_manager, documents, user
    ) -> None:
        registry = InMemorySourceRegistry([_source("policies", owner=user), _source("parking", owner=user)])
        fetcher = StaticSourceFetcher({
            "policies": [_doc("leave", "Leave", LEAVE)],
            "parking": [_doc("parking", "Parking", PARKING)],
        })
        pipeline = KnowledgeRefreshPipeline(
            registry=registry, fetcher=fetcher, knowledge=knowledge, index_manager=index_manager
        )
        await index_manager.ensure_ready()
        first = await pipeline.run()
        assert first.documents_ingested == 2 and not first.held_sources
        stamp = (await registry.get("policies")).last_success_at

        # The leave source starts returning a short error page; the parking source gets a real update.
        fetcher.set("policies", [_doc("leave", "Leave", "Service unavailable, try again later.")])
        fetcher.set("parking", [_doc("parking", "Parking", PARKING + " Bicycles are welcome too.")])
        second = await pipeline.run()

        outcomes = {s.source_id: s for s in second.sources}
        assert outcomes["policies"].held and outcomes["policies"].ingested == 0
        assert outcomes["policies"].held[0].startswith("shrink")
        assert outcomes["parking"].ingested == 1 and not outcomes["parking"].held
        live = await documents.get(DocumentId("leave"))
        assert live.version == 1 and "Annual leave rule" in live.content, "the held source changed the live corpus"
        assert (await documents.get(DocumentId("parking"))).version == 2
        assert (await registry.get("policies")).last_success_at == stamp, "a held source was marked refreshed"

    async def test_a_poisoned_source_is_held_and_nothing_from_it_is_written(
        self, refresh, documents, index_manager, poisoned_document
    ) -> None:
        pipeline, fetcher, registry = refresh
        await index_manager.ensure_ready()
        fetcher.set("policies", [_doc("v", "Vendor", poisoned_document)])

        report = await pipeline.run()

        assert set(report.held_sources) == {"policies"}
        assert report.held_sources["policies"][0].startswith("quarantine")
        assert report.documents_ingested == 0 and report.quarantined >= 1
        assert await documents.get(DocumentId("v")) is None
        assert (await registry.get("policies")).last_success_at is None
        # Source-level reasons are prefixed with the source; index-phase failures are separate.
        assert report.gate_failures == ()
        assert report.all_gate_failures[0].startswith("source:policies: quarantine")

    async def test_a_held_source_is_retried_on_the_next_run(self, refresh, documents, index_manager) -> None:
        pipeline, fetcher, _ = refresh
        await index_manager.ensure_ready()
        fetcher.set("policies", [_doc("leave", "Leave", LEAVE)])
        await pipeline.run()
        fetcher.set("policies", [_doc("leave", "Leave", "Service unavailable.")])
        assert (await pipeline.run()).held_sources

        fetcher.set("policies", [_doc("leave", "Leave", LEAVE + " One more rule about carry-over days.")])
        recovered = await pipeline.run()

        assert not recovered.held_sources and recovered.documents_ingested == 1
        assert (await documents.get(DocumentId("leave"))).version == 2


class TestDryRun:
    async def test_a_dry_run_writes_nothing(
        self, refresh, knowledge, documents, vectors, index_manager
    ) -> None:
        pipeline, fetcher, registry = refresh
        await index_manager.ensure_ready()
        fetcher.set("policies", [_doc("a", "A", "# Leave\nAnnual leave is 21 days per year.")])

        report = await pipeline.run(dry_run=True)

        assert report.documents_ingested == 0
        assert report.sources[0].would_apply == 1
        assert await documents.get(DocumentId("a")) is None
        assert knowledge.lexical_size == 0
        assert await vectors.count(await index_manager.active_identity()) == 0
        assert (await registry.get("policies")).last_success_at is None

    async def test_a_dry_run_still_reports_what_the_gate_would_hold(
        self, refresh, index_manager, poisoned_document
    ) -> None:
        pipeline, fetcher, _ = refresh
        await index_manager.ensure_ready()
        fetcher.set("policies", [_doc("v", "Vendor", poisoned_document)])
        report = await pipeline.run(dry_run=True)
        assert set(report.held_sources) == {"policies"}


class TestIndexPhase:
    """The index is rebuilt and gated only when the live one is not the one the embedder builds."""

    async def _foreign_alias(self, vectors, embedder) -> IndexIdentity:
        foreign = IndexIdentity("test_knowledge", 1, "legacy-embedder", embedder.dimension)
        await vectors.set_alias("test_knowledge", foreign)
        return foreign

    async def test_a_changed_embedder_builds_a_separate_index_and_promotes(
        self, refresh, knowledge, vectors, embedder, index_manager, user
    ) -> None:
        pipeline, fetcher, _ = refresh
        document = _doc("leave", "Leave", LEAVE)
        await knowledge.ingest(
            content=LEAVE, title="Leave", document_id=document.document_id, owner_id=user
        )
        foreign = await self._foreign_alias(vectors, embedder)
        fetcher.set("policies", [document])  # unchanged: nothing to apply, only the index to fix

        report = await pipeline.run()

        assert report.index_rebuild == "promoted" and report.promoted
        assert report.previous_index == foreign.physical_name
        assert (await index_manager.status())["matches_embedder"] is True
        found = await knowledge.retrieve("annual leave entitled days off", owner_id=user)
        assert found and found.chunks[0].chunk.document_id == document.document_id

    async def test_a_schema_version_bump_rebuilds_too(
        self, knowledge, vectors, documents, embedder, user
    ) -> None:
        document = _doc("leave", "Leave", LEAVE)
        await knowledge.ingest(
            content=LEAVE, title="Leave", document_id=document.document_id, owner_id=user
        )
        bumped = IndexManager(
            vectors=vectors, documents=documents, embedder=embedder,
            alias="test_knowledge", schema_version=2,
        )
        registry = InMemorySourceRegistry([_source("policies", owner=user)])
        pipeline = KnowledgeRefreshPipeline(
            registry=registry, fetcher=StaticSourceFetcher({"policies": [document]}),
            knowledge=knowledge, index_manager=bumped,
        )

        report = await pipeline.run()

        assert report.index_rebuild == "promoted"
        assert (await bumped.status())["up_to_date"] is True

    async def test_a_failed_rebuild_leaves_the_published_index_serving(
        self, refresh, knowledge, vectors, embedder, index_manager, user, monkeypatch
    ) -> None:
        pipeline, fetcher, _ = refresh
        document = _doc("leave", "Leave", LEAVE)
        await knowledge.ingest(
            content=LEAVE, title="Leave", document_id=document.document_id, owner_id=user
        )
        foreign = await self._foreign_alias(vectors, embedder)
        fetcher.set("policies", [document])

        async def broken(*_args, **_kwargs):
            raise RuntimeError("embedding backend is down")

        monkeypatch.setattr(embedder, "embed", broken)
        report = await pipeline.run()

        assert report.index_rebuild == "held" and not report.promoted
        assert report.gate_failures
        assert (await index_manager.active_identity()).physical_name == foreign.physical_name
        assert not report.succeeded

    async def test_nothing_published_means_nothing_to_rebuild(self, refresh, index_manager) -> None:
        pipeline, fetcher, _ = refresh
        fetcher.set("policies", [])
        report = await pipeline.run()
        assert report.index_rebuild == "not_needed"
        assert await index_manager.active_identity() is None


class TestSuccessIsNotPromotion:
    """With no nightly promotion, the freshness signal must not depend on one."""

    def _pipeline(self, knowledge, index_manager, registry, fetcher, metrics) -> KnowledgeRefreshPipeline:
        return KnowledgeRefreshPipeline(
            registry=registry, fetcher=fetcher, knowledge=knowledge,
            index_manager=index_manager, metrics=metrics,
        )

    async def test_a_clean_run_without_a_promotion_marks_the_corpus_fresh(
        self, knowledge, index_manager, user
    ) -> None:
        metrics = _FakeMetrics()
        registry = InMemorySourceRegistry([_source("policies", owner=user)])
        fetcher = StaticSourceFetcher({"policies": [_doc("leave", "Leave", LEAVE)]})
        await index_manager.ensure_ready()

        report = await self._pipeline(knowledge, index_manager, registry, fetcher, metrics).run()

        assert not report.promoted and report.succeeded
        assert len(metrics.refresh_last_success_timestamp.calls) == 1
        assert metrics.knowledge_age_seconds.calls == [0]
        assert metrics.refresh_sources_held.calls == [0]

    async def test_a_held_source_raises_its_own_gauge_but_does_not_fail_the_run(
        self, knowledge, index_manager, user, poisoned_document
    ) -> None:
        metrics = _FakeMetrics()
        registry = InMemorySourceRegistry([_source("policies", owner=user)])
        fetcher = StaticSourceFetcher({"policies": [_doc("v", "Vendor", poisoned_document)]})
        await index_manager.ensure_ready()

        report = await self._pipeline(knowledge, index_manager, registry, fetcher, metrics).run()

        assert report.succeeded
        assert metrics.refresh_sources_held.calls == [1]
        assert len(metrics.refresh_last_success_timestamp.calls) == 1
        # The label is the check, never the operator-chosen source id.
        assert metrics.gate_labels == ["quarantine"]

    async def test_a_source_that_cannot_be_fetched_is_not_a_success(
        self, knowledge, index_manager, user
    ) -> None:
        class Down:
            async def fetch(self, source):  # noqa: ANN001
                raise RuntimeError("upstream is down")

        metrics = _FakeMetrics()
        registry = InMemorySourceRegistry([_source("policies", owner=user)])
        await index_manager.ensure_ready()

        report = await self._pipeline(knowledge, index_manager, registry, Down(), metrics).run()

        assert not report.succeeded
        assert metrics.refresh_last_success_timestamp.calls == []

    async def test_a_dry_run_says_nothing_about_freshness(self, knowledge, index_manager, user) -> None:
        metrics = _FakeMetrics()
        registry = InMemorySourceRegistry([_source("policies", owner=user)])
        fetcher = StaticSourceFetcher({"policies": [_doc("leave", "Leave", LEAVE)]})
        await index_manager.ensure_ready()

        await self._pipeline(knowledge, index_manager, registry, fetcher, metrics).run(dry_run=True)

        assert metrics.refresh_last_success_timestamp.calls == []

    async def test_a_source_whose_documents_all_fail_is_not_marked_refreshed(
        self, knowledge, index_manager, user
    ) -> None:
        registry = InMemorySourceRegistry([_source("policies", owner=user)])
        fetcher = StaticSourceFetcher({"policies": [_doc("e", "Error", "<div><span><p>" * 200)]})
        await index_manager.ensure_ready()

        report = await self._pipeline(knowledge, index_manager, registry, fetcher, None).run()

        assert report.failures == 1 and report.documents_ingested == 0
        assert (await registry.get("policies")).last_success_at is None

    async def test_a_source_with_nothing_to_apply_is_still_alive(
        self, knowledge, index_manager, user
    ) -> None:
        registry = InMemorySourceRegistry([_source("policies", owner=user)])
        fetcher = StaticSourceFetcher({"policies": []})
        await index_manager.ensure_ready()

        await self._pipeline(knowledge, index_manager, registry, fetcher, None).run()

        assert (await registry.get("policies")).last_success_at is not None


class TestNearDuplicateDetection:
    def test_simhash_is_stable_for_identical_text(self) -> None:
        text = "Annual leave is twenty one days per year for permanent staff members."
        assert _simhash(text) == _simhash(text)

    def test_long_documents_use_simhash_distance(self) -> None:
        """SimHash is reliable at length, where an O(1) comparison is the only affordable one."""
        base = "Annual leave is twenty one days per year for permanent staff members. " * 40
        edited = base + "Reviewed in 2026."
        assert len(base.split()) > 200
        assert _hamming(_simhash(base), _simhash(edited)) <= 6

    def test_short_documents_use_exact_jaccard(self) -> None:
        """SimHash destabilises on short text -- appending one sentence to a 13-word document
        flips ~10 of 64 bits -- so short documents take the exact path instead."""
        budget = _DocumentBudget(100)
        base = "Annual leave is twenty one days per year for permanent staff members here."
        assert not _is_near_duplicate(base, budget)
        assert _is_near_duplicate(base + " Reviewed in 2026.", budget)

    def test_unrelated_short_text_is_not_a_duplicate(self) -> None:
        budget = _DocumentBudget(100)
        assert not _is_near_duplicate(
            "Annual leave is twenty one days per year for permanent staff.", budget
        )
        assert not _is_near_duplicate(
            "The quarterly revenue forecast was revised downward by finance.", budget
        )


class TestSourceSecurity:
    def test_http_sources_must_use_https(self) -> None:
        from aura_core.domain.errors import AuthorizationError
        from aura_core.learning.sources import HttpSourceFetcher

        with pytest.raises(AuthorizationError):
            HttpSourceFetcher._assert_fetchable("http://example.com/policy")

    @pytest.mark.parametrize(
        "uri",
        [
            "https://169.254.169.254/latest/meta-data/",  # cloud instance metadata
            "https://127.0.0.1/admin",
            "https://10.0.0.5/internal",
            "https://localhost/secret",
        ],
    )
    def test_internal_addresses_are_refused(self, uri: str) -> None:
        """A scheduled job with the deployment's network position must not be an SSRF
        primitive; an SSRF here is harder to notice than one a user triggers."""
        from aura_core.domain.errors import AuthorizationError
        from aura_core.learning.sources import HttpSourceFetcher

        with pytest.raises(AuthorizationError):
            HttpSourceFetcher._assert_fetchable(uri)

    def test_public_https_is_allowed(self) -> None:
        from aura_core.learning.sources import HttpSourceFetcher

        HttpSourceFetcher._assert_fetchable("https://docs.example.com/handbook")


class TestApprovalGate:
    async def test_production_promotion_requires_an_approval(self) -> None:
        from aura_core.domain.errors import ApprovalRequiredError
        from aura_core.learning import ApprovalService, InMemoryApprovalStore
        from aura_core.mlops import InMemoryModelStore, ModelRegistry, ModelStage, ModelVersion

        approvals = ApprovalService(store=InMemoryApprovalStore())
        registry = ModelRegistry(store=InMemoryModelStore(), approvals=approvals)
        await registry.register(
            ModelVersion(model_id="m", version="1.0.0", provider_kind="ollama", dataset_digest="d1")
        )
        await registry.transition("m", "1.0.0", to=ModelStage.CANARY, canary_percent=5)

        with pytest.raises(ApprovalRequiredError):
            await registry.transition("m", "1.0.0", to=ModelStage.PRODUCTION)

    async def test_an_approval_is_bound_to_its_artefact(self) -> None:
        """An approval granted for one dataset cannot be used to train on another."""
        from aura_core.domain.errors import ConflictError
        from aura_core.learning import ApprovalKind, ApprovalService, InMemoryApprovalStore

        approvals = ApprovalService(store=InMemoryApprovalStore())
        request = await approvals.request(
            kind=ApprovalKind.FINETUNE, summary="train v2", artefact_digest="digest-A"
        )
        await approvals.decide(request.id, approved=True, by="reviewer@example.com")

        await approvals.require(request.id, expected_digest="digest-A")
        with pytest.raises(ConflictError):
            await approvals.require(request.id, expected_digest="digest-B")

    async def test_an_approval_must_name_a_person(self) -> None:
        from aura_core.domain.errors import ValidationError
        from aura_core.learning import ApprovalKind, ApprovalService, InMemoryApprovalStore

        approvals = ApprovalService(store=InMemoryApprovalStore())
        request = await approvals.request(
            kind=ApprovalKind.FINETUNE, summary="x", artefact_digest="d"
        )
        with pytest.raises(ValidationError):
            await approvals.decide(request.id, approved=True, by="   ")

    async def test_expired_approvals_are_refused(self) -> None:
        from datetime import timedelta

        from aura_core.domain.errors import ConflictError
        from aura_core.learning import ApprovalKind, ApprovalService, InMemoryApprovalStore

        approvals = ApprovalService(store=InMemoryApprovalStore())
        request = await approvals.request(
            kind=ApprovalKind.FINETUNE, summary="x", artefact_digest="d",
            ttl=timedelta(seconds=-1),
        )
        with pytest.raises(ConflictError):
            await approvals.decide(request.id, approved=True, by="reviewer@example.com")

    async def test_rollback_never_requires_an_approval(self) -> None:
        """Reverting to a known-good state is safe by definition; an incident must not wait on
        a reviewer."""
        from aura_core.learning import ApprovalKind, ApprovalService, InMemoryApprovalStore
        from aura_core.mlops import InMemoryModelStore, ModelRegistry, ModelStage, ModelVersion

        approvals = ApprovalService(store=InMemoryApprovalStore())
        registry = ModelRegistry(store=InMemoryModelStore(), approvals=approvals)
        await registry.register(
            ModelVersion(model_id="m", version="1.0.0", provider_kind="ollama", dataset_digest="d")
        )
        await registry.register(
            ModelVersion(model_id="m", version="2.0.0", provider_kind="ollama", dataset_digest="d")
        )
        # 1.0.0 is the known-good state: it went live through the gate before 2.0.0 replaced it.
        # Rolling back to a version that never did is refused (INT-101,
        # tests/unit/test_model_rollback_gate.py).
        for version in ("1.0.0", "2.0.0"):
            request = await approvals.request(
                kind=ApprovalKind.MODEL_PROMOTION,
                summary=f"promote {version}",
                artefact_digest=f"m@{version}",
            )
            await approvals.decide(request.id, approved=True, by="reviewer@example.com")
            await registry.transition("m", version, to=ModelStage.CANARY, canary_percent=10)
            await registry.transition(
                "m", version, to=ModelStage.PRODUCTION, approval_id=request.id
            )

        restored = await registry.rollback("m", to_version="1.0.0", actor="oncall")
        assert restored.stage is ModelStage.PRODUCTION
        assert (await registry.production("m")).version == "1.0.0"

    async def test_ab_assignment_is_stable_per_subject(self) -> None:
        """A user must see the same variant on every request, or the comparison measures
        routing variance rather than a difference between models."""
        from aura_core.mlops import InMemoryModelStore, ModelRegistry, ModelStage, ModelVersion

        registry = ModelRegistry(store=InMemoryModelStore())
        await registry.register(
            ModelVersion(model_id="m", version="1.0.0", provider_kind="ollama",
                         stage=ModelStage.PRODUCTION)
        )
        await registry.register(
            ModelVersion(model_id="m", version="2.0.0", provider_kind="ollama",
                         stage=ModelStage.CANARY, canary_percent=50)
        )
        first = await registry.assign("m", subject="user-42")
        for _ in range(5):
            assert (await registry.assign("m", subject="user-42")).version == first.version


class TestCuration:
    def test_credentials_are_rejected_not_redacted(self) -> None:
        """A training corpus is copied, shared, and retained; a redaction bug in one example is
        unrecoverable."""
        from aura_core.learning import DatasetCurator, FeedbackRecord

        curator = DatasetCurator()
        _, statistics = curator.curate([
            FeedbackRecord(
                id="1", prompt="Where is the deploy key?",
                response="Use ghp_" + "a" * 36 + " to authenticate.",
                rating=5, user_ref="u1",
            )
        ])
        assert statistics.rejected_pii == 1
        assert statistics.accepted == 0

    def test_human_corrections_bypass_the_rating_threshold(self) -> None:
        """A correction is a human saying "this is the right answer" -- stronger than a rating."""
        from aura_core.learning import DatasetCurator, FeedbackRecord

        examples, _ = DatasetCurator().curate([
            FeedbackRecord(
                id="1", prompt="What is the sick leave policy?",
                response="I am not sure about that.", rating=1,
                correction="Sick leave is uncapped; a certificate is needed after three days.",
                user_ref="u1",
            )
        ])
        assert len(examples) == 1
        assert examples[0].was_corrected
        assert examples[0].weight > 1.0

    def test_degenerate_responses_are_dropped(self) -> None:
        from aura_core.learning import DatasetCurator, FeedbackRecord

        _, statistics = DatasetCurator().curate([
            FeedbackRecord(id="1", prompt="What is the leave policy exactly?",
                           response="[Error: service unavailable]", rating=5, user_ref="u1")
        ])
        assert statistics.rejected_degenerate == 1

    def test_the_split_is_deterministic(self) -> None:
        """The same example lands in the same split across runs, so a validation score compares
        datasets rather than resampling luck."""
        from aura_core.learning import CuratedExample, DatasetCurator

        examples = [
            CuratedExample(prompt=f"q{i}", completion=f"a{i}", source_id=f"s{i}")
            for i in range(50)
        ]
        first = DatasetCurator.split(examples, validation_fraction=0.2)
        second = DatasetCurator.split(examples, validation_fraction=0.2)
        assert [e.source_id for e in first[1]] == [e.source_id for e in second[1]]

    def test_a_single_user_corpus_is_not_recommended(self) -> None:
        from aura_core.learning import DatasetCurator, FeedbackRecord

        curator = DatasetCurator(min_examples=1, min_distinct_users=3)
        examples, statistics = curator.curate([
            FeedbackRecord(id=str(i), prompt=f"Question number {i} about the policy?",
                           response=f"A detailed answer number {i} for the policy question.",
                           rating=5, user_ref="only-one-user")
            for i in range(10)
        ])
        recommendation = curator.recommend(examples, statistics)
        assert not recommendation.recommended
        assert any("distinct user" in reason for reason in recommendation.reasons)
