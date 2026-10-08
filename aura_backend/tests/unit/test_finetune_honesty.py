"""GAP-018: a training attempt that did not change weights must never read as success.

Before this was fixed, ``LocalLoraTrainer`` caught every exception from its own training path and
returned a ``modelfile://`` artefact ("training dependencies unavailable"), and
``FineTuneJobEngine.run`` recorded that as ``SUCCEEDED`` and registered a CANDIDATE model version
that could then walk the promotion ladder -- a model version for weights that never existed.

These tests pin the invariant from both sides: the engine refuses to count a non-training result as
success, and the trainer no longer manufactures one.
"""

from __future__ import annotations

import importlib.util
from datetime import datetime, timezone
from pathlib import Path

import pytest

from aura_core.domain.identifiers import UserId
from aura_core.learning import (
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
from aura_core.mlops import InMemoryModelStore, ModelRegistry
from aura_core.mlops.experiments import ExperimentTracker, InMemoryExperimentStore
from aura_core.ports.mlops import FineTuneJobSpec, TrainResult


class _ModelfileOnlyTrainer:
    """Reproduces the pre-fix LocalLoraTrainer fallback verbatim: no weights, a Modelfile URI."""

    async def train(self, job: FineTuneJobSpec) -> TrainResult:
        return TrainResult(
            artefact_uri=f"modelfile://{job.output_tag}",
            provider_kind="ollama",
            metrics={"examples": 2.0, "peft": 0.0},
            notes="modelfile-only (training dependencies unavailable)",
        )


class _RaisingTrainer:
    async def train(self, job: FineTuneJobSpec) -> TrainResult:
        raise RuntimeError("CUDA out of memory")


def _record(owner: str, ident: str, prompt: str, correction: str) -> FeedbackRecord:
    return FeedbackRecord(
        id=ident,
        prompt=prompt,
        response="I am not sure.",
        rating=5,
        correction=correction,
        user_ref=owner,
        created_at=datetime.now(timezone.utc),
        metadata={"turn_id": f"t-{ident}"},
    )


async def _approved_job(trainer, *, base_model: str = "echo-1", allow_cpu: bool = False):
    feedback = InMemoryFeedbackStore()
    datasets = InMemoryDatasetStore()
    approvals = ApprovalService(store=InMemoryApprovalStore())
    models = ModelRegistry(store=InMemoryModelStore(), approvals=approvals)
    experiment_store = InMemoryExperimentStore()
    experiments = ExperimentTracker(store=experiment_store)
    service = FineTuneService(
        feedback=feedback,
        datasets=datasets,
        approvals=approvals,
        curator=DatasetCurator(min_examples=2, min_distinct_users=2),
        base_model=base_model,
    )
    engine = FineTuneJobEngine(
        approvals=approvals,
        datasets=datasets,
        models=models,
        trainer=trainer,
        jobs=InMemoryJobStore(),
        experiments=experiments,
        model_id="aura-ft",
        allow_cpu=allow_cpu,
    )
    await feedback.put(
        _record("alice", "1", "What is the leave policy?", "Annual leave is 21 days."),
        owner_id=UserId("alice"),
    )
    await feedback.put(
        _record("bob", "2", "How many sick days?", "Sick leave is 10 days per year."),
        owner_id=UserId("bob"),
    )
    approval, _ = await service.request_approval()
    await approvals.decide(approval.id, approved=True, by="admin@example.com")
    job = await engine.enqueue(approval_id=approval.id)
    return engine, models, experiment_store, job


class TestEngineNeverCountsANonTrainingResultAsSuccess:
    async def test_modelfile_fallback_is_not_succeeded_and_registers_nothing(self) -> None:
        engine, models, _, job = await _approved_job(_ModelfileOnlyTrainer())
        done = await engine.run(job.id)

        assert done.status is not FineTuneJobStatus.SUCCEEDED
        assert done.status is FineTuneJobStatus.NOT_TRAINED
        assert done.error and "training dependencies unavailable" in done.error
        assert done.artefact_uri == ""
        # Nothing to promote: no version exists for weights that were never produced.
        assert list(await models.list_versions(model_id="aura-ft")) == []

    async def test_default_scripted_trainer_is_an_explicit_non_training_outcome(self) -> None:
        """The gateway default must not register a model version it never trained."""
        engine, models, _, job = await _approved_job(ScriptedTrainer())
        done = await engine.run(job.id)

        assert done.status is FineTuneJobStatus.NOT_TRAINED
        assert done.error and "no weights" in done.error
        assert list(await models.list_versions(model_id="aura-ft")) == []

    async def test_non_training_outcome_closes_the_experiment_run_as_failed(self) -> None:
        engine, _, experiments, job = await _approved_job(_ModelfileOnlyTrainer())
        done = await engine.run(job.id)
        assert done.run_id
        run = await experiments.get_run(done.run_id)
        assert str(run.status) == "failed"

    async def test_trainer_exception_fails_the_job_and_registers_nothing(self) -> None:
        engine, models, _, job = await _approved_job(_RaisingTrainer())
        done = await engine.run(job.id)

        assert done.status is FineTuneJobStatus.FAILED
        assert "CUDA out of memory" in (done.error or "")
        assert list(await models.list_versions(model_id="aura-ft")) == []

    async def test_claimed_weights_without_an_artefact_digest_fail(self) -> None:
        """A trainer cannot assert weights changed without naming what it produced."""

        class _Unverifiable:
            async def train(self, job: FineTuneJobSpec) -> TrainResult:
                return TrainResult(
                    artefact_uri="/tmp/somewhere", provider_kind="peft_lora", weights_changed=True
                )

        engine, models, _, job = await _approved_job(_Unverifiable())
        done = await engine.run(job.id)
        assert done.status is FineTuneJobStatus.FAILED
        assert list(await models.list_versions(model_id="aura-ft")) == []


class TestLocalLoraTrainerNoLongerFabricatesAnArtefact:
    """The pre-fix trainer turned any training error into a ``modelfile://`` success."""

    async def test_unloadable_base_model_fails_the_job(self, tmp_path: Path) -> None:
        from aura_core.adapters.local_lora_trainer import LocalLoraTrainer

        missing = str(tmp_path / "no-such-base-model")
        trainer = LocalLoraTrainer(
            allow_cpu=True, cpu_base_allowlist=(missing,), work_dir=tmp_path / "work"
        )
        engine, models, _, job = await _approved_job(trainer, base_model=missing, allow_cpu=True)
        done = await engine.run(job.id)

        assert done.status is FineTuneJobStatus.FAILED
        assert "modelfile://" not in done.artefact_uri
        assert list(await models.list_versions(model_id="aura-ft")) == []

    async def test_ollama_tag_base_model_is_refused(self, tmp_path: Path) -> None:
        from aura_core.adapters.local_lora_trainer import LocalLoraTrainer

        tag = "qwen2.5:3b-instruct-q4_K_M"
        trainer = LocalLoraTrainer(
            allow_cpu=True, cpu_base_allowlist=("qwen2.5:*",), work_dir=tmp_path / "work"
        )
        engine, models, _, job = await _approved_job(trainer, base_model=tag, allow_cpu=True)
        done = await engine.run(job.id)

        assert done.status is FineTuneJobStatus.FAILED
        assert "Ollama" in (done.error or "")
        assert list(await models.list_versions(model_id="aura-ft")) == []

    @pytest.mark.skipif(
        importlib.util.find_spec("peft") is not None,
        reason="training dependencies are installed here; this pins the gateway-image behaviour",
    )
    async def test_missing_training_dependencies_fail_the_job(self, tmp_path: Path) -> None:
        from aura_core.adapters.local_lora_trainer import LocalLoraTrainer

        base = tmp_path / "base-model"
        base.mkdir()
        trainer = LocalLoraTrainer(
            allow_cpu=True, cpu_base_allowlist=(str(base),), work_dir=tmp_path / "work"
        )
        engine, models, _, job = await _approved_job(trainer, base_model=str(base), allow_cpu=True)
        done = await engine.run(job.id)

        assert done.status is FineTuneJobStatus.FAILED
        assert "requirements-train.txt" in (done.error or "")
        assert list(await models.list_versions(model_id="aura-ft")) == []
