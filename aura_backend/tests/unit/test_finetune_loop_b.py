"""Loop B fine-tune service: curation, approval, job engine, registry (ADR-008)."""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from aura_core.domain.errors import (
    ApprovalRequiredError,
    ConflictError,
    NotFoundError,
    ValidationError,
)
from aura_core.domain.identifiers import UserId
from aura_core.learning import (
    ApprovalKind,
    ApprovalService,
    DatasetCurator,
    FeedbackRecord,
    FineTuneJobEngine,
    FineTuneJobStatus,
    FineTuneService,
    InMemoryApprovalStore,
    InMemoryDatasetStore,
    InMemoryFeedbackStore,
    InMemoryJobStore,
    ScriptedTrainer,
)
from aura_core.mlops import InMemoryModelStore, ModelRegistry, ModelStage
from aura_core.mlops.experiments import ExperimentTracker, InMemoryExperimentStore
from aura_core.ports.mlops import FineTuneJobSpec, TrainResult


class _WeightsChangedPortDouble:
    """A port-level stand-in for a trainer that *did* update weights.

    The default ``pytest`` run has no torch/PEFT stack, so the promotion-gate tests below need a
    trainer that reports ``weights_changed=True`` with a named artefact. It is a test double of the
    ``FineTuneTrainer`` port, not a claim that anything was trained; the real weight-delta proof is
    the opt-in ``tests/training`` suite against ``LocalLoraTrainer``.
    """

    async def train(self, job: FineTuneJobSpec) -> TrainResult:
        return TrainResult(
            artefact_uri=f"/var/lib/aura/finetune/{job.job_id}/adapter",
            provider_kind="peft_lora",
            metrics={"train_loss": 0.42, "steps": 3.0},
            notes="port double",
            weights_changed=True,
            artefact_digest="0" * 64,
        )


def _record(
    *,
    owner: str,
    ident: str,
    rating: int = 5,
    correction: str | None = "Annual leave is 21 days.",
    prompt: str = "What is the leave policy?",
) -> FeedbackRecord:
    return FeedbackRecord(
        id=ident,
        prompt=prompt,
        response="I am not sure.",
        rating=rating,
        correction=correction,
        user_ref=owner,
        created_at=datetime.now(timezone.utc),
        metadata={"turn_id": f"t-{ident}"},
    )


async def _seed_two_users(feedback) -> None:
    await feedback.put(
        _record(owner="alice", ident="1", correction="Annual leave is 21 days."),
        owner_id=UserId("alice"),
    )
    await feedback.put(
        _record(
            owner="bob",
            ident="2",
            prompt="How many sick days do I get?",
            correction="Sick leave is 10 days per calendar year.",
        ),
        owner_id=UserId("bob"),
    )


@pytest.fixture
def loop_b():
    feedback = InMemoryFeedbackStore()
    datasets = InMemoryDatasetStore()
    approvals = ApprovalService(store=InMemoryApprovalStore())
    models = ModelRegistry(store=InMemoryModelStore(), approvals=approvals)
    experiments = ExperimentTracker(store=InMemoryExperimentStore())
    service = FineTuneService(
        feedback=feedback,
        datasets=datasets,
        approvals=approvals,
        curator=DatasetCurator(min_examples=2, min_distinct_users=2),
        base_model="echo-1",
    )
    jobs = FineTuneJobEngine(
        approvals=approvals,
        datasets=datasets,
        models=models,
        trainer=_WeightsChangedPortDouble(),
        jobs=InMemoryJobStore(),
        experiments=experiments,
        model_id="aura-ft",
    )
    return feedback, service, approvals, models, jobs


class TestFeedbackCurationList:
    async def test_list_for_curation_is_cross_tenant(self) -> None:
        store = InMemoryFeedbackStore()
        await store.put(_record(owner="alice", ident="a"), owner_id=UserId("alice"))
        await store.put(_record(owner="bob", ident="b"), owner_id=UserId("bob"))
        rows = await store.list_for_curation()
        assert {r.id for r in rows} == {"a", "b"}


class TestFineTuneService:
    async def test_recommend_persists_dataset(self, loop_b) -> None:
        feedback, service, *_ = loop_b
        await _seed_two_users(feedback)
        recommendation, stored = await service.recommend()
        assert stored is not None
        assert recommendation.dataset_digest == stored.digest
        assert recommendation.estimated_examples == 2

    async def test_request_approval_binds_digest(self, loop_b) -> None:
        feedback, service, approvals, *_ = loop_b
        await _seed_two_users(feedback)
        approval, recommendation = await service.request_approval()
        assert approval.kind is ApprovalKind.FINETUNE
        assert approval.artefact_digest == recommendation.dataset_digest
        loaded = await approvals._store.get(approval.id)
        assert loaded is not None

    async def test_recommend_with_no_examples_has_no_digest(self, loop_b) -> None:
        _, service, *_ = loop_b
        recommendation, stored = await service.recommend()
        assert stored is None
        assert recommendation.dataset_digest == ""

    async def test_request_approval_with_no_examples_is_a_validation_error(self, loop_b) -> None:
        _, service, *_ = loop_b
        with pytest.raises(ValidationError):
            await service.request_approval()


class TestFineTuneJobEngine:
    async def test_cannot_launch_without_approval(self, loop_b) -> None:
        feedback, service, approvals, models, jobs = loop_b
        await _seed_two_users(feedback)
        approval, _ = await service.request_approval()
        # Still pending — require() must fail.
        with pytest.raises(ApprovalRequiredError):
            await jobs.enqueue(approval_id=approval.id)

    async def test_cannot_launch_with_swapped_digest(self, loop_b) -> None:
        feedback, service, approvals, models, jobs = loop_b
        await _seed_two_users(feedback)
        approval, recommendation = await service.request_approval()
        await approvals.decide(approval.id, approved=True, by="admin@example.com")
        with pytest.raises(ConflictError):
            await jobs.enqueue(approval_id=approval.id, dataset_digest="not-the-digest")

    async def test_trained_result_registers_candidate(self, loop_b) -> None:
        feedback, service, approvals, models, jobs = loop_b
        await _seed_two_users(feedback)
        approval, recommendation = await service.request_approval()
        await approvals.decide(approval.id, approved=True, by="admin@example.com")
        job = await jobs.enqueue(approval_id=approval.id)
        assert job.status is FineTuneJobStatus.QUEUED
        done = await jobs.run(job.id)
        assert done.status is FineTuneJobStatus.SUCCEEDED
        assert done.artefact_uri
        versions = await models.list_versions(model_id="aura-ft")
        assert len(versions) == 1
        assert versions[0].stage is ModelStage.CANDIDATE
        assert versions[0].dataset_digest == recommendation.dataset_digest
        assert "artefact_sha256=" + "0" * 64 in versions[0].notes

    async def test_scripted_trainer_registers_nothing(self, loop_b) -> None:
        """The gateway default ends NOT_TRAINED; see test_finetune_honesty for the full set."""
        feedback, service, approvals, models, _ = loop_b
        await _seed_two_users(feedback)
        approval, _ = await service.request_approval()
        await approvals.decide(approval.id, approved=True, by="admin@example.com")
        engine = FineTuneJobEngine(
            approvals=approvals,
            datasets=service._datasets,
            models=models,
            trainer=ScriptedTrainer(),
        )
        job = await engine.enqueue(approval_id=approval.id)
        done = await engine.run(job.id)
        assert done.status is FineTuneJobStatus.NOT_TRAINED
        assert list(await models.list_versions(model_id="aura-ft")) == []

    async def test_bad_hyperparameter_fails_the_job_at_the_boundary(self, loop_b) -> None:
        feedback, service, approvals, models, _ = loop_b
        await _seed_two_users(feedback)
        approval, _ = await service.request_approval()
        await approvals.decide(approval.id, approved=True, by="admin@example.com")
        engine = FineTuneJobEngine(
            approvals=approvals,
            datasets=service._datasets,
            models=models,
            trainer=_WeightsChangedPortDouble(),
            default_parameters={"method": "lora", "epochs": "three"},
        )
        job = await engine.enqueue(approval_id=approval.id)
        done = await engine.run(job.id)
        assert done.status is FineTuneJobStatus.FAILED
        assert "epochs" in (done.error or "")
        assert list(await models.list_versions(model_id="aura-ft")) == []


class TestPromotionGate:
    async def test_production_requires_model_key_approval(self, loop_b) -> None:
        feedback, service, approvals, models, jobs = loop_b
        await _seed_two_users(feedback)
        train_approval, _ = await service.request_approval()
        await approvals.decide(train_approval.id, approved=True, by="admin@example.com")
        job = await jobs.enqueue(approval_id=train_approval.id)
        done = await jobs.run(job.id)
        version = (await models.list_versions(model_id="aura-ft"))[0]
        await models.transition("aura-ft", version.version, to=ModelStage.SHADOW)
        await models.transition("aura-ft", version.version, to=ModelStage.CANARY, canary_percent=10)
        # Reusing the train approval must fail — digest is dataset, not model key.
        with pytest.raises((ApprovalRequiredError, ConflictError)):
            await models.transition(
                "aura-ft",
                version.version,
                to=ModelStage.PRODUCTION,
                approval_id=train_approval.id,
            )
        promo = await approvals.request(
            kind=ApprovalKind.MODEL_PROMOTION,
            summary=f"Promote {version.key}",
            artefact_digest=version.key,
        )
        await approvals.decide(promo.id, approved=True, by="admin@example.com")
        promoted = await models.transition(
            "aura-ft",
            version.version,
            to=ModelStage.PRODUCTION,
            approval_id=promo.id,
        )
        assert promoted.stage is ModelStage.PRODUCTION
        assert done.status is FineTuneJobStatus.SUCCEEDED

    async def test_finetune_digest_approval_cannot_promote_to_production(self, loop_b) -> None:
        """A FINETUNE approval keyed to a dataset digest must not satisfy a PRODUCTION
        promotion, even when the model version shares that same dataset_digest."""
        _, _, approvals, models, _ = loop_b
        from aura_core.mlops.model_registry import ModelVersion

        digest = "shared-digest-xyz"
        version = ModelVersion(
            model_id="aura-ft",
            version="9.9.9",
            provider_kind="ollama",
            stage=ModelStage.CANARY,
            canary_percent=10,
            artefact_uri="aura-ft:9.9.9",
            dataset_digest=digest,
        )
        await models.register(version)

        finetune_approval = await approvals.request(
            kind=ApprovalKind.FINETUNE, summary="train run", artefact_digest=digest,
        )
        await approvals.decide(finetune_approval.id, approved=True, by="admin@example.com")

        with pytest.raises((ApprovalRequiredError, ConflictError)):
            await models.transition(
                "aura-ft", "9.9.9", to=ModelStage.PRODUCTION, approval_id=finetune_approval.id,
            )

    async def test_canary_blocked_on_coverage_drop(self, loop_b) -> None:
        _, _, approvals, models, _ = loop_b
        from aura_core.mlops.model_registry import ModelVersion

        version = ModelVersion(
            model_id="aura-ft",
            version="v-drop",
            provider_kind="ollama",
            stage=ModelStage.SHADOW,
            artefact_uri="aura-ft:v-drop",
            metrics={"coverage_drop": 0.2, "max_coverage_drop": 0.05},
        )
        await models.register(version)
        with pytest.raises(ConflictError):
            await models.transition("aura-ft", "v-drop", to=ModelStage.CANARY, canary_percent=10)


class TestAssignStable:
    async def test_assign_is_stable_per_subject(self, loop_b) -> None:
        _, _, approvals, models, _ = loop_b
        from aura_core.mlops.model_registry import ModelVersion

        await models.register(
            ModelVersion(
                model_id="aura-ft",
                version="prod",
                provider_kind="ollama",
                stage=ModelStage.PRODUCTION,
                artefact_uri="aura-ft:prod",
            )
        )
        await models.register(
            ModelVersion(
                model_id="aura-ft",
                version="canary",
                provider_kind="ollama",
                stage=ModelStage.CANARY,
                artefact_uri="aura-ft:canary",
                canary_percent=50,
            )
        )
        first = await models.assign("aura-ft", subject="user-42")
        second = await models.assign("aura-ft", subject="user-42")
        assert first is not None and second is not None
        assert first.version == second.version


class TestLocalLoraTrainerArchitecture:
    def test_local_lora_module_does_not_import_torch_at_top_level(self) -> None:
        from pathlib import Path

        from aura_core.adapters.local_lora_trainer import _module_imports_torch_at_top_level

        source = Path(__file__).resolve().parents[2] / "aura_core" / "adapters" / "local_lora_trainer.py"
        assert not _module_imports_torch_at_top_level(source.read_text(encoding="utf-8"))
