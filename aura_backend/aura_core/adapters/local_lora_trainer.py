"""Self-hosted LoRA trainer (ADR-008 Loop B): real PEFT fine-tuning of a causal language model.

What this adapter does, and nothing more:

1. loads the base causal LM named by ``FineTuneJobSpec.base_model`` with ``transformers``
   (a Hugging Face repo id, optionally pinned by ``base_model_revision``, or a local checkpoint
   directory) and records the revision it actually loaded;
2. wraps it with a PEFT LoRA adapter (rank/alpha/dropout/target modules from the spec);
3. trains the adapter on the curated JSONL (``chatml``, ``alpaca`` or ``openai`` rows), with a
   causal-LM loss on the assistant/completion tokens only;
4. measures the adapter's weight delta (L2 norm of trainable-tensor changes) and the mean loss
   on the dataset before and after training;
5. saves the adapter (``adapter_model.safetensors`` + ``adapter_config.json``), hashes those
   files, and writes a ``training_manifest.json`` beside them.

What it does **not** do: merge the adapter into the base weights, convert anything to GGUF,
create an Ollama model, serve the result, or evaluate it against the production model. The
artefact it returns is a PEFT adapter directory, which the kernel's model assignment path
deliberately refuses to send to a provider (``AIKernel._servable_model_tag``). Getting from a
CANDIDATE adapter to served traffic is a separate, not-yet-built step (GAP-017).

Failure is loud. Any error — missing training dependencies, an unloadable base model, an empty
dataset — raises, and ``FineTuneJobEngine`` records the job ``FAILED`` with no registry entry.
A run that completes but leaves the adapter weights unchanged returns an explicit
``TrainResult.not_trained`` rather than an artefact. There is no fallback artefact of any kind:
a previous version of this module turned every exception into a ``modelfile://`` "success"
(GAP-018).

Dependencies: ``torch`` and ``transformers`` are in the gateway lock (sentence-transformers needs
them); ``peft`` is **not**, and lives only in ``requirements-train.txt``, installed by the
``training`` target of the backend Dockerfile that ``worker-training`` builds. All three are
imported inside methods, never at module scope.
"""

from __future__ import annotations

import ast
import asyncio
import hashlib
import json
import math
import os
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Sequence

from aura_core.domain.errors import ConfigurationError, ValidationError
from aura_core.observability.logging import get_logger
from aura_core.ports.mlops import FineTuneJobSpec, TrainResult

__all__ = ["LocalLoraTrainer", "TRAINING_REQUIREMENTS_FILE"]

logger = get_logger("adapters.local_lora")

#: Where the optional training dependencies are pinned (relative to ``aura_backend/``).
TRAINING_REQUIREMENTS_FILE = "requirements-train.txt"

_HEAVY_MODULES = ("torch", "transformers", "peft")


def _module_imports_torch_at_top_level(source: str) -> bool:
    """Architecture helper: refuse module-scope torch/transformers/peft imports."""
    tree = ast.parse(source)

    def heavy(name: str) -> bool:
        return any(name == m or name.startswith(m + ".") for m in _HEAVY_MODULES)

    for node in tree.body:
        if isinstance(node, ast.Import) and any(heavy(a.name) for a in node.names):
            return True
        if isinstance(node, ast.ImportFrom) and node.module and heavy(node.module):
            return True
    return False


@dataclass(frozen=True, slots=True)
class _Example:
    input_ids: list[int]
    labels: list[int]


class LocalLoraTrainer:
    """Train a LoRA adapter on a causal LM with transformers + PEFT (see module docstring)."""

    def __init__(
        self,
        *,
        allow_cpu: bool = False,
        cpu_base_allowlist: tuple[str, ...] = (),
        work_dir: str | Path = "/tmp/aura-finetune",
    ) -> None:
        self._allow_cpu = allow_cpu
        self._cpu_bases = frozenset(cpu_base_allowlist)
        self._work_dir = Path(work_dir)

    async def train(self, job: FineTuneJobSpec) -> TrainResult:
        self._validate_base_model(job.base_model)
        rows = _parse_dataset(job.dataset_jsonl)
        stack = _import_training_stack()
        self._assert_hardware(stack["torch"], job.base_model)
        out_dir = self._work_dir / job.job_id
        return await asyncio.to_thread(self._train_blocking, stack, job, rows, out_dir)

    # -- validation ----------------------------------------------------------------------

    @staticmethod
    def _validate_base_model(base_model: str) -> None:
        base = (base_model or "").strip()
        if not base:
            raise ValidationError("fine-tune job has no base_model")
        if os.path.isabs(base) or base.startswith((".", "~")):
            path = Path(base).expanduser()
            if not path.is_dir():
                raise ValidationError(
                    "base model directory does not exist",
                    details={"base_model": base},
                )
            return
        if ":" in base:
            # qwen2.5:3b-instruct-q4_K_M and friends are quantised GGUF blobs inside Ollama.
            # There are no trainable weights to load from a tag.
            raise ValidationError(
                "base_model looks like an Ollama tag; Ollama models cannot be fine-tuned. "
                "Launch the job with the Hugging Face repo id (or a local transformers "
                "checkpoint directory) of the same base model",
                details={"base_model": base},
            )

    def _assert_hardware(self, torch: Any, base_model: str) -> None:
        if bool(torch.cuda.is_available()):
            return
        if not self._allow_cpu:
            raise ConfigurationError(
                "LocalLoraTrainer refuses CPU training unless LEARNING_FINETUNE_ALLOW_CPU=true",
                details={"base_model": base_model},
            )
        allowed = any(
            base_model == item or base_model.startswith(item.rstrip("*"))
            for item in self._cpu_bases
        )
        if not allowed:
            raise ValidationError(
                "base model is not on the CPU fine-tune allowlist",
                details={"base_model": base_model, "allowlist": sorted(self._cpu_bases)},
            )

    # -- training ------------------------------------------------------------------------

    def _train_blocking(
        self,
        stack: dict[str, Any],
        job: FineTuneJobSpec,
        rows: Sequence[list[dict[str, str]]],
        out_dir: Path,
    ) -> TrainResult:
        torch = stack["torch"]
        transformers = stack["transformers"]
        peft = stack["peft"]
        started = time.perf_counter()

        torch.manual_seed(job.seed)
        revision = job.base_model_revision.strip() or None
        base = str(Path(job.base_model).expanduser()) if os.path.isabs(job.base_model) \
            else job.base_model

        tokenizer = transformers.AutoTokenizer.from_pretrained(base, revision=revision)
        if tokenizer.pad_token_id is None:
            if tokenizer.eos_token is None:
                raise ValidationError("base model tokenizer has neither a pad nor an eos token")
            tokenizer.pad_token = tokenizer.eos_token
        model = transformers.AutoModelForCausalLM.from_pretrained(
            base, revision=revision, dtype=torch.float32
        )
        resolved_revision = str(getattr(model.config, "_commit_hash", "") or revision or "")

        examples = [
            ex for ex in (_encode(tokenizer, msgs, job.max_seq_len) for msgs in rows)
            if ex is not None
        ]
        if not examples:
            raise ValidationError(
                "no dataset row has trainable completion tokens within max_seq_len",
                details={"rows": len(rows), "max_seq_len": job.max_seq_len},
            )

        lora = peft.LoraConfig(
            task_type=peft.TaskType.CAUSAL_LM,
            r=job.lora_rank,
            lora_alpha=job.lora_alpha,
            lora_dropout=job.lora_dropout,
            target_modules=list(job.lora_target_modules) or None,
        )
        model = peft.get_peft_model(model, lora)
        device = "cuda" if torch.cuda.is_available() else "cpu"
        model.to(device)

        trainable = [(n, p) for n, p in model.named_parameters() if p.requires_grad]
        if not trainable:
            raise ValidationError("LoRA configuration produced no trainable parameters")
        before = {n: p.detach().clone() for n, p in trainable}
        trainable_params = sum(p.numel() for _, p in trainable)
        total_params = sum(p.numel() for p in model.parameters())

        pad_id = int(tokenizer.pad_token_id)
        loss_before = _mean_loss(torch, model, examples, job.batch_size, pad_id, device)

        optimiser = torch.optim.AdamW([p for _, p in trainable], lr=job.learning_rate)
        generator = torch.Generator().manual_seed(job.seed)
        losses: list[float] = []
        steps = 0
        epochs_completed = 0
        model.train()
        for _epoch in range(job.epochs):
            order = torch.randperm(len(examples), generator=generator).tolist()
            for start in range(0, len(order), job.batch_size):
                batch = _collate(torch, [examples[i] for i in order[start:start + job.batch_size]],
                                 pad_id, device)
                output = model(**batch)
                output.loss.backward()
                optimiser.step()
                optimiser.zero_grad(set_to_none=True)
                losses.append(float(output.loss.detach().cpu()))
                steps += 1
                if job.max_steps and steps >= job.max_steps:
                    break
            epochs_completed += 1
            if job.max_steps and steps >= job.max_steps:
                break
        model.eval()
        loss_after = _mean_loss(torch, model, examples, job.batch_size, pad_id, device)

        with torch.no_grad():
            delta_sq = sum(
                float((p.detach() - before[n]).pow(2).sum().cpu()) for n, p in trainable
            )
        weight_delta = math.sqrt(delta_sq)

        metrics = {
            "examples": float(len(rows)),
            "trainable_examples": float(len(examples)),
            "steps": float(steps),
            "epochs_completed": float(epochs_completed),
            "train_loss_first": losses[0] if losses else float("nan"),
            "train_loss_last": losses[-1] if losses else float("nan"),
            "dataset_loss_before": loss_before,
            "dataset_loss_after": loss_after,
            "lora_weight_delta_l2": weight_delta,
            "trainable_params": float(trainable_params),
            "total_params": float(total_params),
            "duration_s": time.perf_counter() - started,
        }
        if not all(math.isfinite(v) for k, v in metrics.items() if k.startswith(("train_", "dataset_"))):
            raise ValidationError("training diverged: a loss is not finite", details=metrics)
        if steps == 0 or weight_delta == 0.0:
            return TrainResult.not_trained(
                f"LoRA training ran {steps} optimiser step(s) but the adapter weights did not "
                "change; nothing was saved",
                metrics=metrics,
            )

        adapter_dir = out_dir / "adapter"
        adapter_dir.mkdir(parents=True, exist_ok=True)
        model.save_pretrained(str(adapter_dir), safe_serialization=True)
        digest = _digest_directory(adapter_dir)
        manifest = {
            "job_id": job.job_id,
            "output_tag": job.output_tag,
            "base_model": job.base_model,
            "base_model_revision": resolved_revision,
            "dataset_digest": job.dataset_digest,
            "adapter_sha256": digest,
            "hyperparameters": {
                "epochs": job.epochs,
                "batch_size": job.batch_size,
                "learning_rate": job.learning_rate,
                "max_steps": job.max_steps,
                "lora_rank": job.lora_rank,
                "lora_alpha": job.lora_alpha,
                "lora_dropout": job.lora_dropout,
                "lora_target_modules": sorted(_target_modules(model)),
                "max_seq_len": job.max_seq_len,
                "seed": job.seed,
            },
            "metrics": metrics,
            "versions": {
                "torch": getattr(torch, "__version__", ""),
                "transformers": getattr(transformers, "__version__", ""),
                "peft": getattr(peft, "__version__", ""),
            },
            "device": device,
        }
        (out_dir / "training_manifest.json").write_text(
            json.dumps(manifest, indent=2, sort_keys=True, default=str), encoding="utf-8"
        )
        logger.info(
            "LoRA adapter trained",
            extra={"job": job.job_id, "adapter": str(adapter_dir), "sha256": digest,
                   "steps": steps, "delta_l2": weight_delta},
        )
        return TrainResult(
            artefact_uri=str(adapter_dir),
            provider_kind="peft_lora",
            metrics=metrics,
            notes=(
                f"PEFT LoRA adapter trained on {job.base_model} for {steps} step(s); "
                "adapter only -- not merged, not converted, not servable by Ollama as-is"
            ),
            weights_changed=True,
            artefact_digest=digest,
            base_model_revision=resolved_revision,
        )


# -- helpers (pure; the heavy modules are passed in, never imported here) -----------------


def _import_training_stack() -> dict[str, Any]:
    missing: list[str] = []
    stack: dict[str, Any] = {}
    for name in _HEAVY_MODULES:
        try:
            stack[name] = __import__(name)
        except ImportError:
            missing.append(name)
    if missing:
        raise ConfigurationError(
            f"LoRA training dependencies are not installed ({', '.join(missing)}). "
            f"They are deliberately not in the gateway image: install "
            f"aura_backend/{TRAINING_REQUIREMENTS_FILE}, i.e. run the job on the "
            "worker-training service (Dockerfile target 'training')",
            details={"missing": missing},
        )
    return stack


def _parse_dataset(jsonl: str) -> list[list[dict[str, str]]]:
    """Normalise curated JSONL rows to message lists ending in the target completion."""
    rows: list[list[dict[str, str]]] = []
    for number, line in enumerate(jsonl.splitlines(), start=1):
        if not line.strip():
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError as exc:
            raise ValidationError(f"dataset line {number} is not JSON") from exc
        messages = _row_messages(row)
        if messages is None:
            raise ValidationError(
                f"dataset line {number} is not a chatml/alpaca/openai training row",
                details={"keys": sorted(row) if isinstance(row, dict) else type(row).__name__},
            )
        rows.append(messages)
    if not rows:
        raise ValidationError("fine-tune dataset is empty")
    return rows


def _row_messages(row: Any) -> list[dict[str, str]] | None:
    if not isinstance(row, dict):
        return None
    if isinstance(row.get("messages"), list):
        msgs = [
            {"role": str(m.get("role", "")), "content": str(m.get("content", ""))}
            for m in row["messages"] if isinstance(m, dict)
        ]
        if len(msgs) >= 2 and msgs[-1]["role"] == "assistant" and msgs[-1]["content"].strip():
            return msgs
        return None
    if "output" in row:
        user = "\n\n".join(s for s in (str(row.get("instruction", "")), str(row.get("input", "")))
                           if s.strip())
        return [{"role": "user", "content": user},
                {"role": "assistant", "content": str(row["output"])}]
    if "completion" in row:
        return [{"role": "user", "content": str(row.get("prompt", ""))},
                {"role": "assistant", "content": str(row["completion"])}]
    return None


def _render(tokenizer: Any, messages: list[dict[str, str]]) -> tuple[str, str]:
    """Split a conversation into (prompt text, completion text) for loss masking."""
    if getattr(tokenizer, "chat_template", None):
        prompt = tokenizer.apply_chat_template(
            messages[:-1], tokenize=False, add_generation_prompt=True
        )
        full = tokenizer.apply_chat_template(messages, tokenize=False)
        if isinstance(prompt, str) and isinstance(full, str) and full.startswith(prompt):
            return prompt, full[len(prompt):]
    prompt = "".join(f"<|{m['role']}|>\n{m['content']}\n" for m in messages[:-1])
    prompt += "<|assistant|>\n"
    return prompt, messages[-1]["content"] + (tokenizer.eos_token or "")


def _encode(tokenizer: Any, messages: list[dict[str, str]], max_len: int) -> _Example | None:
    prompt, completion = _render(tokenizer, messages)
    prompt_ids = list(tokenizer(prompt, add_special_tokens=False)["input_ids"])
    completion_ids = list(tokenizer(completion, add_special_tokens=False)["input_ids"])
    input_ids = (prompt_ids + completion_ids)[:max_len]
    labels = ([-100] * len(prompt_ids) + completion_ids)[:max_len]
    if all(label == -100 for label in labels):
        return None
    return _Example(input_ids=input_ids, labels=labels)


def _collate(torch: Any, batch: Sequence[_Example], pad_id: int, device: str) -> dict[str, Any]:
    width = max(len(ex.input_ids) for ex in batch)
    ids, labels, mask = [], [], []
    for ex in batch:
        pad = width - len(ex.input_ids)
        ids.append(ex.input_ids + [pad_id] * pad)
        labels.append(ex.labels + [-100] * pad)
        mask.append([1] * len(ex.input_ids) + [0] * pad)
    return {
        "input_ids": torch.tensor(ids, dtype=torch.long, device=device),
        "labels": torch.tensor(labels, dtype=torch.long, device=device),
        "attention_mask": torch.tensor(mask, dtype=torch.long, device=device),
    }


def _mean_loss(torch: Any, model: Any, examples: Sequence[_Example], batch_size: int,
               pad_id: int, device: str) -> float:
    total, count = 0.0, 0
    with torch.no_grad():
        for start in range(0, len(examples), batch_size):
            chunk = examples[start:start + batch_size]
            output = model(**_collate(torch, chunk, pad_id, device))
            total += float(output.loss.detach().cpu()) * len(chunk)
            count += len(chunk)
    return total / count if count else float("nan")


def _target_modules(model: Any) -> list[str]:
    config = getattr(model, "peft_config", {}).get("default")
    modules = getattr(config, "target_modules", None) or ()
    return [modules] if isinstance(modules, str) else [str(m) for m in modules]


def _digest_directory(path: Path) -> str:
    """sha256 over (relative name, bytes) of every file, in sorted order."""
    sha = hashlib.sha256()
    for file in sorted(p for p in path.rglob("*") if p.is_file()):
        sha.update(file.relative_to(path).as_posix().encode("utf-8") + b"\0")
        sha.update(file.read_bytes())
    return sha.hexdigest()
