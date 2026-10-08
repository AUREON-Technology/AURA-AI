"""Opt-in proof that Loop B's ``local`` trainer changes model weights (GAP-017).

Drives the real pipeline end to end -- feedback -> curation -> FINETUNE approval -> job engine ->
``LocalLoraTrainer`` -> model registry -- against a real (tiny) causal LM, then checks the saved
adapter independently of anything the trainer reported:

* every ``lora_B`` tensor starts at exactly zero (PEFT's initialisation, checked on a fresh
  adapter built here) and at least one is non-zero in the saved adapter;
* base-model logits and base+saved-adapter logits differ on the same input, while a fresh,
  untrained adapter leaves them identical -- i.e. the saved file changes model behaviour;
* the registered artefact digest matches a re-hash of the files on disk;
* loss on the curated dataset went down.

Not part of the default run (it needs ``requirements-train.txt`` and a model download):

    pip install -r requirements.lock -r requirements-dev.txt -r requirements-train.txt
    pytest tests/training -m training -s

``AURA_TRAIN_TEST_BASE_MODEL`` overrides the base (default ``hf-internal-testing/tiny-random-gpt2``,
~1 MB, CPU-friendly). The test skips -- never passes -- when PEFT is missing or the model cannot
be fetched.

What this does NOT prove: anything about the production base model, answer quality, or serving.
A tiny random model learning a handful of rows shows the mechanism is real, nothing more.
"""

from __future__ import annotations

import hashlib
import importlib.util
import json
import os
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
)
from aura_core.mlops import InMemoryModelStore, ModelRegistry, ModelStage
from aura_core.mlops.experiments import ExperimentTracker, InMemoryExperimentStore

pytestmark = pytest.mark.training

BASE = os.environ.get("AURA_TRAIN_TEST_BASE_MODEL", "hf-internal-testing/tiny-random-gpt2")

_ROWS = [
    ("alice", "What is the annual leave policy?", "Annual leave is 21 working days per year."),
    ("bob", "How many sick days do I get?", "Sick leave is 10 days per calendar year."),
    ("carol", "When is payroll processed?", "Payroll runs on the 25th of every month."),
    ("dave", "Who approves travel?", "Travel is approved by your line manager in the portal."),
]


@pytest.fixture(scope="module")
def base_model_dir(tmp_path_factory) -> str:
    if importlib.util.find_spec("peft") is None:
        pytest.skip("training dependencies not installed (pip install -r requirements-train.txt)")
    from huggingface_hub import snapshot_download

    try:
        return snapshot_download(BASE, cache_dir=str(tmp_path_factory.mktemp("hf")))
    except Exception as exc:  # network egress is not guaranteed
        pytest.skip(f"cannot download {BASE}: {type(exc).__name__}: {exc}")


def _digest(path: Path) -> str:
    sha = hashlib.sha256()
    for file in sorted(p for p in path.rglob("*") if p.is_file()):
        sha.update(file.relative_to(path).as_posix().encode("utf-8") + b"\0")
        sha.update(file.read_bytes())
    return sha.hexdigest()


async def test_local_lora_job_changes_adapter_weights(base_model_dir: str, tmp_path: Path) -> None:
    import torch
    from peft import LoraConfig, PeftModel, TaskType, get_peft_model, get_peft_model_state_dict
    from safetensors.torch import load_file
    from transformers import AutoModelForCausalLM, AutoTokenizer

    from aura_core.adapters.local_lora_trainer import LocalLoraTrainer

    feedback = InMemoryFeedbackStore()
    datasets = InMemoryDatasetStore()
    approvals = ApprovalService(store=InMemoryApprovalStore())
    models = ModelRegistry(store=InMemoryModelStore(), approvals=approvals)
    experiments = ExperimentTracker(store=InMemoryExperimentStore())
    service = FineTuneService(
        feedback=feedback, datasets=datasets, approvals=approvals,
        curator=DatasetCurator(min_examples=2, min_distinct_users=2), base_model=base_model_dir,
    )
    for i, (owner, prompt, correction) in enumerate(_ROWS):
        await feedback.put(
            FeedbackRecord(
                id=str(i), prompt=prompt, response="I am not sure.", rating=5,
                correction=correction, user_ref=owner, created_at=datetime.now(timezone.utc),
                metadata={"turn_id": f"t-{i}"},
            ),
            owner_id=UserId(owner),
        )

    engine = FineTuneJobEngine(
        approvals=approvals, datasets=datasets, models=models,
        trainer=LocalLoraTrainer(
            allow_cpu=True, cpu_base_allowlist=(base_model_dir,), work_dir=tmp_path / "work"
        ),
        jobs=InMemoryJobStore(), experiments=experiments, model_id="aura-ft", allow_cpu=True,
        default_parameters={
            "method": "lora", "rank": 8, "alpha": 16, "dropout": 0.0, "lr": 5e-3,
            "epochs": 8, "batch_size": 2, "seed": 0, "max_seq_len": 128,
        },
    )
    approval, recommendation = await service.request_approval()
    await approvals.decide(approval.id, approved=True, by="admin@example.com")
    job = await engine.enqueue(approval_id=approval.id)
    done = await engine.run(job.id)

    assert done.status is FineTuneJobStatus.SUCCEEDED, done.error
    adapter_dir = Path(done.artefact_uri)
    assert (adapter_dir / "adapter_model.safetensors").is_file()
    assert (adapter_dir / "adapter_config.json").is_file()

    manifest = json.loads((adapter_dir.parent / "training_manifest.json").read_text())
    assert manifest["adapter_sha256"] == _digest(adapter_dir)
    assert manifest["dataset_digest"] == recommendation.dataset_digest

    (version,) = await models.list_versions(model_id="aura-ft")
    assert version.stage is ModelStage.CANDIDATE
    assert version.artefact_uri == str(adapter_dir)
    assert f"artefact_sha256={_digest(adapter_dir)}" in version.notes
    assert done.metrics["steps"] > 0
    assert done.metrics["lora_weight_delta_l2"] > 0
    assert done.metrics["dataset_loss_after"] < done.metrics["dataset_loss_before"]

    # -- independent checks on the saved file, not on anything the trainer reported ---------
    tokenizer = AutoTokenizer.from_pretrained(base_model_dir)
    inputs = tokenizer("What is the annual leave policy?", return_tensors="pt")

    base = AutoModelForCausalLM.from_pretrained(base_model_dir, dtype=torch.float32).eval()
    with torch.no_grad():
        base_logits = base(**inputs).logits

    fresh = get_peft_model(
        AutoModelForCausalLM.from_pretrained(base_model_dir, dtype=torch.float32),
        LoraConfig(task_type=TaskType.CAUSAL_LM, r=8, lora_alpha=16, lora_dropout=0.0),
    ).eval()
    before = get_peft_model_state_dict(fresh)
    after = load_file(str(adapter_dir / "adapter_model.safetensors"))
    b_keys = sorted(k for k in after if "lora_B" in k)
    assert b_keys and set(b_keys) <= set(before)
    assert all(torch.count_nonzero(before[k]) == 0 for k in b_keys)
    changed = [k for k in b_keys if torch.count_nonzero(after[k]) > 0]
    assert changed, "every lora_B tensor is still zero: the adapter learned nothing"
    with torch.no_grad():
        fresh_logits = fresh(**inputs).logits
    assert torch.equal(fresh_logits, base_logits)

    trained = PeftModel.from_pretrained(
        AutoModelForCausalLM.from_pretrained(base_model_dir, dtype=torch.float32), str(adapter_dir)
    ).eval()
    with torch.no_grad():
        trained_logits = trained(**inputs).logits
    max_abs = float((trained_logits - base_logits).abs().max())
    assert max_abs > 0.0

    print(json.dumps({
        "base_model": BASE,
        "base_model_revision": manifest["base_model_revision"],
        "dataset_digest": manifest["dataset_digest"],
        "adapter_sha256": manifest["adapter_sha256"],
        "lora_B_tensors_changed": f"{len(changed)}/{len(b_keys)}",
        "logit_max_abs_diff_vs_base": max_abs,
        "metrics": manifest["metrics"],
        "versions": manifest["versions"],
    }, indent=2, sort_keys=True))
