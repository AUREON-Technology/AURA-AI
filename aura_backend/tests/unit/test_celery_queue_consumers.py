"""Every Celery task lands on a queue some worker consumes (audit FSA-043, Phase 1 regression).

Workers start with `-Q <queues>`, so a task whose declared queue -- or Celery's default `celery`
queue, for a task that declares none -- is on no worker's list is accepted by the broker and never
runs. `POST /api/v3/learning/build_dataset` returned a task id that nobody would ever execute, and
nothing in the suite could see it. This reads the task declarations and both compose files, and
refuses any gap that is not written down with a reason.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

yaml = pytest.importorskip("yaml")

BACKEND = Path(__file__).resolve().parents[2]
DEPLOY = BACKEND.parent / "deploy"
COMPOSE_FILES = (DEPLOY / "docker-compose.prod.yml", DEPLOY / "docker-compose.dev.yml")

#: Tasks allowed to sit on an unconsumed queue, each with the reason and the finding that owns it.
DELIBERATELY_UNCONSUMED: dict[str, str] = {
    "tasks.build_lora_dataset_task": (
        "the legacy learning path that enqueues it is slated for removal or a 410 (audit FSA-043, "
        "Phase 3); until then the task must not start running un-curated datasets past ADR-008"
    ),
}

_TASK = re.compile(r'@celery_app\.task\(\s*name="([^"]+)"(?P<rest>[^)]*)\)')
_QUEUE = re.compile(r'queue="([^"]+)"')


def declared_tasks() -> dict[str, str]:
    """`{task name: queue}`, `celery` for a task that names none."""
    text = (BACKEND / "tasks.py").read_text(encoding="utf-8")
    tasks: dict[str, str] = {}
    for match in _TASK.finditer(text):
        queue = _QUEUE.search(match.group("rest"))
        tasks[match.group(1)] = queue.group(1) if queue else "celery"
    assert tasks, "no Celery tasks found in tasks.py"
    return tasks


def consumed_queues(compose: Path) -> set[str]:
    services = yaml.safe_load(compose.read_text(encoding="utf-8"))["services"]
    queues: set[str] = set()
    for service in services.values():
        parts = str(service.get("command") or "").split()
        if "-Q" in parts:
            queues |= set(parts[parts.index("-Q") + 1].split(","))
    assert queues, f"{compose.name} starts no Celery worker with -Q"
    return queues


@pytest.mark.parametrize("compose", COMPOSE_FILES, ids=lambda p: p.name)
def test_every_task_queue_has_a_worker_or_a_written_reason(compose: Path) -> None:
    consumed = consumed_queues(compose)
    orphans = {
        task: queue for task, queue in declared_tasks().items()
        if queue not in consumed and task not in DELIBERATELY_UNCONSUMED
    }
    assert orphans == {}, (
        f"{compose.name}: these tasks are enqueued on a queue no worker consumes, so they never "
        f"run: {orphans}. Give them a consumed queue or list them with a reason."
    )


def test_the_exemptions_are_real_tasks_with_reasons() -> None:
    tasks = declared_tasks()
    for task, reason in DELIBERATELY_UNCONSUMED.items():
        assert task in tasks, f"exemption names a task that does not exist: {task}"
        assert reason.strip(), f"{task} is exempt without a reason"


def test_the_exempt_task_is_still_unconsumed_so_the_exemption_is_live() -> None:
    """An exemption for a task that has since gained a consumer is a stale line to remove."""
    tasks = declared_tasks()
    for compose in COMPOSE_FILES:
        consumed = consumed_queues(compose)
        for task in DELIBERATELY_UNCONSUMED:
            assert tasks[task] not in consumed, f"{task} is consumed in {compose.name}; drop the exemption"
