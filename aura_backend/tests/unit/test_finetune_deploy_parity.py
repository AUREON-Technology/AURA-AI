"""Loop B deployment wiring (GAP-017/018), pinned in both compose files at once.

What the job engine records is only as honest as *which* trainer runs a job. These pin that:

* the gateway never trains -- its trainer is fixed to ``scripted`` (records NOT_TRAINED);
* only ``worker-training`` runs ``local`` (real PEFT LoRA), and only it is built with the
  training dependencies;
* the fine-tune tasks go to a ``training`` queue that nothing but ``worker-training`` consumes.
  They used to share ``learning`` with ``worker-ingestion``, whose default trainer would "run"
  a queued job without training it.

The dev and prod stacks share a host and are meant to stay at parity (CLAUDE.md), so every
check runs against both files.
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

_REPO_ROOT = Path(__file__).resolve().parents[3]
_COMPOSE_FILES = (
    _REPO_ROOT / "deploy" / "docker-compose.prod.yml",
    _REPO_ROOT / "deploy" / "docker-compose.dev.yml",
)
_GATEWAYS = {"docker-compose.prod.yml": "gateway", "docker-compose.dev.yml": "dev-gateway"}


def _services(compose: Path) -> dict:
    return yaml.safe_load(compose.read_text(encoding="utf-8"))["services"]


def _queues(command: object) -> set[str]:
    parts = str(command or "").split()
    if "-Q" not in parts:
        return set()
    return set(parts[parts.index("-Q") + 1].split(","))


@pytest.mark.parametrize("compose", _COMPOSE_FILES, ids=lambda p: p.name)
class TestFinetuneWiring:
    def test_gateway_trainer_is_pinned_to_scripted(self, compose: Path) -> None:
        env = _services(compose)[_GATEWAYS[compose.name]]["environment"]
        assert env.get("AURA_FINETUNE_TRAINER") == "scripted"

    def test_only_worker_training_runs_the_real_trainer(self, compose: Path) -> None:
        services = _services(compose)
        local = {
            name for name, svc in services.items()
            if isinstance(svc.get("environment"), dict)
            and svc["environment"].get("AURA_FINETUNE_TRAINER") == "local"
        }
        assert local == {"worker-training"}

    def test_worker_training_is_the_only_image_with_training_deps(self, compose: Path) -> None:
        services = _services(compose)
        with_deps = {
            name for name, svc in services.items()
            if isinstance(svc.get("build"), dict)
            and (svc["build"].get("args") or {}).get("AURA_TRAINING_DEPS") == "true"
        }
        assert with_deps == {"worker-training"}

    def test_only_worker_training_consumes_the_training_queue(self, compose: Path) -> None:
        services = _services(compose)
        consumers = {n for n, svc in services.items() if "training" in _queues(svc.get("command"))}
        assert consumers == {"worker-training"}
        assert _queues(services["worker-training"]["command"]) == {"training"}


def test_finetune_tasks_route_to_the_training_queue() -> None:
    import tasks
    from celery_app import celery_app

    assert tasks.finetune_job_task.queue == "training"
    assert tasks.drain_finetune_queue_task.queue == "training"
    drain = celery_app.conf.beat_schedule["drain-finetune-queue"]
    assert drain["options"]["queue"] == "training"
    # Beat keeps enqueuing whether or not the `training` profile is up; stale drains must expire.
    assert 0 < drain["options"]["expires"] <= drain["schedule"]
