"""The nightly knowledge refresh and its promotion gates (ADR-008, Loop A).

The property under test throughout: **a failed run leaves production exactly as it was**. Every
generation is built into a new index and promoted only if every gate passes, so a bad source, a
degraded embedding backend, or a collapsed corpus cannot damage the live index.
"""

from __future__ import annotations

import pytest

from aura_core.domain import DocumentId, KnowledgeSource, SourceType, TrustTier, UserId
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
    async def test_documents_are_ingested_and_the_index_promoted(self, refresh, index_manager, user) -> None:
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
        assert report.promoted
        assert report.generation.value

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
        assert json.loads(json.dumps((await pipeline.run()).as_dict()))["promoted"] is True


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
        request = await approvals.request(
            kind=ApprovalKind.MODEL_PROMOTION, summary="promote 2.0.0", artefact_digest="d"
        )
        await approvals.decide(request.id, approved=True, by="reviewer@example.com")
        await registry.transition("m", "2.0.0", to=ModelStage.CANARY, canary_percent=10)
        await registry.transition(
            "m", "2.0.0", to=ModelStage.PRODUCTION, approval_id=request.id
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
