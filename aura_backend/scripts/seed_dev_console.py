#!/usr/bin/env python3
"""Dev-only Loop B console seed (ADR-008).

Wipes Loop B tables (feedback, approvals, jobs, models, experiments) while keeping
knowledge sources and documents, inserts curated feedback, then runs the full pipeline:
recommend → FINETUNE approval → LocalLora job (via Celery worker-training) →
CANDIDATE → SHADOW → CANARY → pending MODEL_PROMOTION approval.

Refuses production/staging ``AURA_ENVIRONMENT``. Intended to run inside the dev gateway
container via ``deploy/scripts/seed-dev.sh``.
"""

from __future__ import annotations

import asyncio
import os
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

# Loop B tables only — knowledge / documents / memories are untouched.
_WIPE_TABLES = (
    "aura_experiment_runs",
    "aura_experiments",
    "aura_finetune_jobs",
    "aura_model_versions",
    "aura_approvals",
    "aura_feedback",
)

_SEED_OPERATOR = "seed@dev.local"

_USERS = tuple(f"dev-user-{index}" for index in range(1, 6))

_SCENARIOS: tuple[tuple[str, str, str], ...] = (
    (
        "What is the annual leave policy?",
        "I am not sure about the leave policy.",
        "Full-time employees receive 21 calendar days of paid annual leave per year.",
    ),
    (
        "How many sick days do I get?",
        "Sick leave varies by role.",
        "Employees accrue 10 paid sick days per calendar year with manager notification.",
    ),
    (
        "When is the benefits enrollment window?",
        "Enrollment dates depend on your start date.",
        "Open enrollment runs November 1–15; new hires enroll within 30 days of start.",
    ),
    (
        "Can I work remotely from another country?",
        "Remote work rules are flexible.",
        "International remote work requires HR and Legal approval at least 30 days in advance.",
    ),
    (
        "How do I submit an expense report?",
        "Use the finance portal.",
        "Upload receipts in the expense portal within 14 days; approvals route to your manager.",
    ),
    (
        "What is the parental leave policy?",
        "Parental leave depends on location.",
        "Primary caregivers receive 12 weeks paid leave; secondary caregivers receive 4 weeks.",
    ),
    (
        "How is performance reviewed?",
        "Reviews happen periodically.",
        "Annual reviews occur each March with mid-year check-ins in September.",
    ),
    (
        "What training budget do engineers get?",
        "Training budget varies.",
        "Engineering staff receive $1,500 per year for approved conferences and courses.",
    ),
    (
        "How do I reset my SSO password?",
        "Contact IT support.",
        "Use the self-service portal; locked accounts require a ticket to IT within 4 business hours.",
    ),
    (
        "What is the code of conduct escalation path?",
        "Speak with your manager first.",
        "Report concerns to HR or the ethics hotline; retaliation is prohibited.",
    ),
)


def _assert_dev_only() -> None:
    environment = (os.environ.get("AURA_ENVIRONMENT") or "").strip().lower()
    if environment in {"production", "staging"}:
        raise SystemExit(
            f"refusing to seed: AURA_ENVIRONMENT={environment!r} "
            "(only development/local are allowed)"
        )
    project = (os.environ.get("COMPOSE_PROJECT_NAME") or "").strip()
    if project == "aura":
        raise SystemExit("refusing to seed: COMPOSE_PROJECT_NAME=aura (production project)")


def _feedback_rows() -> list[tuple[str, str, str, str, str, datetime]]:
    """~20 rows across five pseudonymous users."""
    rows: list[tuple[str, str, str, str, str, datetime]] = []
    base_time = datetime.now(timezone.utc) - timedelta(days=3)
    scenario_index = 0
    for user_index, user_ref in enumerate(_USERS):
        for item in range(4):
            prompt, response, correction = _SCENARIOS[scenario_index % len(_SCENARIOS)]
            scenario_index += 1
            ident = f"seed-{user_index + 1}-{item + 1}"
            created = base_time + timedelta(hours=user_index * 4 + item)
            rows.append((ident, user_ref, prompt, response, correction, created))
    return rows


async def _wipe_loop_b(pool: object, dataset_dir: Path) -> None:
    conn_cm = await pool.acquire()  # type: ignore[attr-defined]
    async with conn_cm as conn:
        for table in _WIPE_TABLES:
            await conn.execute(f"TRUNCATE TABLE {table} RESTART IDENTITY CASCADE")
    if dataset_dir.is_dir():
        for path in dataset_dir.iterdir():
            if path.is_file():
                path.unlink()


async def _seed_feedback(container: object) -> int:
    from aura_core.domain.identifiers import UserId
    from aura_core.learning.curation import FeedbackRecord

    rows = _feedback_rows()
    for ident, user_ref, prompt, response, correction, created in rows:
        record = FeedbackRecord(
            id=ident,
            prompt=prompt,
            response=response,
            rating=5,
            correction=correction,
            user_ref=user_ref,
            model="qwen2.5:3b-instruct-q4_K_M",
            agent="default",
            created_at=created,
            metadata={"turn_id": f"seed-turn-{ident}", "seed": True},
        )
        await container.feedback.put(record, owner_id=UserId(user_ref))  # type: ignore[attr-defined]
    return len(rows)


async def _wait_for_job(container: object, job_id: str, *, timeout_s: float = 900.0) -> object:
    from aura_core.learning.finetune import FineTuneJobStatus

    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        job = await container.finetune_jobs.get(job_id)  # type: ignore[attr-defined]
        if job.status is FineTuneJobStatus.SUCCEEDED:
            return job
        if job.status is FineTuneJobStatus.FAILED:
            raise RuntimeError(f"fine-tune job {job_id} failed: {job.error}")
        if job.status is FineTuneJobStatus.CANCELLED:
            raise RuntimeError(f"fine-tune job {job_id} was cancelled")
        await asyncio.sleep(3.0)
    raise TimeoutError(f"fine-tune job {job_id} did not finish within {timeout_s:.0f}s")


async def _run_pipeline(container: object) -> dict[str, str]:
    from aura_core.learning.approval import ApprovalKind
    from aura_core.mlops.model_registry import ModelStage

    approval, recommendation = await container.finetune.request_approval()  # type: ignore[attr-defined]
    await container.approvals.decide(  # type: ignore[attr-defined]
        approval.id, approved=True, by=_SEED_OPERATOR, note="dev seed"
    )

    job = await container.finetune_jobs.enqueue(approval_id=approval.id)  # type: ignore[attr-defined]

    from tasks import finetune_job_task

    finetune_job_task.delay(job.id)
    job = await _wait_for_job(container, job.id)

    model_id = job.model_id
    version = job.version
    await container.models.transition(model_id, version, to=ModelStage.SHADOW, actor=_SEED_OPERATOR)  # type: ignore[attr-defined]
    await container.models.transition(  # type: ignore[attr-defined]
        model_id, version, to=ModelStage.CANARY, canary_percent=10, actor=_SEED_OPERATOR
    )

    current = await container.models.get(model_id, version)  # type: ignore[attr-defined]
    promo = await container.approvals.request(  # type: ignore[attr-defined]
        kind=ApprovalKind.MODEL_PROMOTION,
        summary=f"Promote {current.key} to production (dev seed)",
        artefact_digest=current.key,
        payload={
            "model_id": model_id,
            "version": version,
            "artefact_uri": current.artefact_uri,
            "dataset_digest": current.dataset_digest,
            "seed": True,
        },
    )

    return {
        "feedback_rows": str(recommendation.estimated_examples),
        "dataset_digest": recommendation.dataset_digest,
        "finetune_approval": approval.id,
        "job_id": job.id,
        "model_key": current.key,
        "promotion_approval": promo.id,
    }


async def _main() -> int:
    _assert_dev_only()

    from aura_core.config.settings import load_settings
    from aura_core.container import build_container

    settings = load_settings(refresh=True)
    container = build_container(settings)

    pool = container.postgres_pool()
    dataset_dir = Path(settings.learning.dataset_dir)
    await _wipe_loop_b(pool, dataset_dir)

    inserted = await _seed_feedback(container)
    summary = await _run_pipeline(container)

    print("dev console seed complete")
    print(f"  feedback inserted: {inserted}")
    for key, value in summary.items():
        print(f"  {key}: {value}")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(asyncio.run(_main()))
    except KeyboardInterrupt:
        raise SystemExit(130) from None
