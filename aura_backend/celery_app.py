import os
from collections.abc import Mapping

from celery import Celery
from celery.schedules import crontab

from infrastructure.secret_hydration import hydrate_secrets_from_files


def _redis_password(environ: Mapping[str, str]) -> str | None:
    """Docker mounts the Redis password as a secret file (AURA_REDIS_PASSWORD_FILE); the
    running app hydrates it into AURA_REDIS_URL at startup (aura_service.py), but the celery
    CLI (`celery -A celery_app worker`) imports this module directly and never goes through
    that hydration, so it has to read the file itself."""
    path = environ.get("AURA_REDIS_PASSWORD_FILE")
    if path and os.path.exists(path):
        with open(path, encoding="utf-8") as handle:
            return handle.read().strip()
    return environ.get("AURA_REDIS_PASSWORD")


def broker_url(environ: Mapping[str, str]) -> str:
    """The Redis URL for the Celery broker and result backend.

    Resolved in the same order as ``infrastructure.redis_client`` and the v3 container: a full
    URL first (``AURA_REDIS_URL``, then the legacy ``REDIS_URL``), which is all Railway's managed
    Redis provides, and only then the discrete ``REDIS_HOST``/password pair the compose files set.
    Building it from ``REDIS_HOST`` alone sent every Railway ``.delay()`` to ``localhost:6379``.
    On the VPS nothing changes: ``hydrate_secrets_from_files`` turns the password file and
    ``REDIS_HOST`` into the same ``redis://:<password>@redis:6379/0`` this used to build.
    """
    url = environ.get("AURA_REDIS_URL") or environ.get("REDIS_URL")
    if url:
        return url
    host = environ.get("REDIS_HOST", "localhost")
    password = _redis_password(environ)
    auth = f":{password}@" if password else ""
    return f"redis://{auth}{host}:6379/0"


def _truthy(raw: str | None, default: bool = True) -> bool:
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def _crontab_from_cron(expr: str) -> crontab:
    parts = expr.split()
    if len(parts) != 5:
        parts = ["0", "2", "*", "*", "*"]
    return crontab(
        minute=parts[0],
        hour=parts[1],
        day_of_month=parts[2],
        month_of_year=parts[3],
        day_of_week=parts[4],
    )


# Beat is scheduler-only; workers execute. Hydrate Postgres/Qdrant/JWT so a
# worker that imported this module can build the v3 container.
hydrate_secrets_from_files()

_redis_url = broker_url(os.environ)

celery_app = Celery(
    "aura_tasks",
    broker=_redis_url,
    backend=_redis_url,
)

def _seconds(name: str, default: int) -> int:
    raw = os.environ.get(name)
    if raw is None or not raw.strip():
        return default
    try:
        value = int(raw)
    except ValueError:
        return default
    return value if value > 0 else default


# -- Runtime robustness (audit F-25) --------------------------------------------------------
# Before this, a task had no time limit at all, was acknowledged the moment a worker *received*
# it, and every pool process prefetched four. A hung provider call held a worker slot forever,
# and a deploy (SIGKILL after the grace period) or an OOM silently dropped whatever was running
# plus everything prefetched behind it.
#
# Time limits. The soft limit raises SoftTimeLimitExceeded inside the task; every task in
# tasks.py catches Exception and returns an error dict, so a soft limit ends as a logged,
# acknowledged failure (and tasks._run_async cancels the coroutine it abandons). The hard limit
# (SIGKILL of the pool child) is the backstop for a task stuck where the signal cannot reach it.
# The default is sized for the realtime queue: the outbox drain can dispatch a trigger that makes
# a provider call, and one call may take the deployed 180 s request timeout (CLAUDE.md), so ten
# minutes is three of those with room to spare.
TASK_SOFT_TIME_LIMIT_S = _seconds("AURA_CELERY_TASK_SOFT_TIME_LIMIT_S", 600)
TASK_TIME_LIMIT_S = max(
    _seconds("AURA_CELERY_TASK_TIME_LIMIT_S", 660), TASK_SOFT_TIME_LIMIT_S + 30
)

# Per-family overrides. soft < hard always, and the hard limit of an acks_late task must stay
# below the broker's visibility timeout (below), or Redis redelivers a task that is still running.
_INGESTION_LIMITS = {"soft_time_limit": 1500, "time_limit": 1800}  # CPU embedding of a big PDF
_LEARNING_LIMITS = {"soft_time_limit": 3300, "time_limit": 3600}  # nightly refresh/consolidate
# Loop B LoRA training on a CPU host takes hours: a runaway backstop, not a budget.
_TRAINING_HARD_S = _seconds("AURA_CELERY_TRAINING_TIME_LIMIT_S", 6 * 3600)
_TRAINING_LIMITS = {
    "soft_time_limit": max(_TRAINING_HARD_S - 600, 60),
    "time_limit": _TRAINING_HARD_S,
}

# Late acks: a message is acknowledged only after its task returns, so a worker killed mid-task
# (deploy, OOM, host reboot) leaves it unacked and Redis redelivers it -- after
# `visibility_timeout` when the whole container died, or at once (task_reject_on_worker_lost)
# when only the pool child died. That is only safe for a task that tolerates running twice.
# Each task left on the default below does, for its own reason:
#   ingest_document / ingest_pdf  Qdrant upsert keyed by doc_id: a rerun overwrites the same points
#   build_lora_dataset            atomic rewrite of one file
#   insights / daily briefings    each run replaces that user's rows for the period
#   knowledge_refresh             content-hash dedup in the refresh pipeline (ADR-008)
#   memory_consolidate            merges memories; a rerun finds them already merged
#   drain_event_outbox            the outbox is at-least-once by design (Phase E1); a rerun
#                                 re-claims entries, it does not replay ones marked done
#   reap / purge                  pause-if-idle, delete-if-expired: convergent
# Opted out:
#   finetune_job / drain_finetune_queue  a redelivery would retrain for hours. The job store keeps
#                                 the job, and relaunching it is an operator's decision.
# reject_on_worker_lost is switched back off for the ingestion-queue tasks: their pool child most
# likely dies to the container memory limit on one oversized input, and requeueing that message
# would kill the next child the same way, forever (Redis has no redelivery cap).
_NO_LATE_ACK = {"acks_late": False, "reject_on_worker_lost": False}
_NO_REQUEUE_ON_CRASH = {"reject_on_worker_lost": False}

TASK_ANNOTATIONS: dict[str, dict] = {
    "tasks.ingest_document_task": {**_INGESTION_LIMITS, **_NO_REQUEUE_ON_CRASH},
    "tasks.ingest_pdf_task": {**_INGESTION_LIMITS, **_NO_REQUEUE_ON_CRASH},
    "tasks.build_lora_dataset_task": {**_INGESTION_LIMITS, **_NO_REQUEUE_ON_CRASH},
    "tasks.knowledge_refresh_task": dict(_LEARNING_LIMITS),
    "tasks.memory_consolidate_task": dict(_LEARNING_LIMITS),
    "tasks.finetune_job_task": {**_TRAINING_LIMITS, **_NO_LATE_ACK},
    "tasks.drain_finetune_queue_task": {**_TRAINING_LIMITS, **_NO_LATE_ACK},
}

# Must exceed the longest hard limit of any acks_late task, or Redis hands a still-running task
# to a second worker. It is also how long a message orphaned by a killed container waits before
# it is redelivered, so it is not set higher than that requires.
BROKER_VISIBILITY_TIMEOUT_S = 7200

celery_app.conf.update(
    task_serializer="json",
    accept_content=["json"],
    result_serializer="json",
    timezone="UTC",
    enable_utc=True,
    task_track_started=True,
    task_soft_time_limit=TASK_SOFT_TIME_LIMIT_S,
    task_time_limit=TASK_TIME_LIMIT_S,
    task_acks_late=True,
    task_reject_on_worker_lost=True,
    task_annotations=TASK_ANNOTATIONS,
    # One message per pool process. With late acks a prefetched message sits unacked behind a
    # busy process while an idle one could have run it, and a long ingestion job would hold
    # three more hostage behind it.
    worker_prefetch_multiplier=1,
    broker_transport_options={"visibility_timeout": BROKER_VISIBILITY_TIMEOUT_S},
    broker_connection_retry_on_startup=True,
)

# AURA Insights (Doc/V2_BLUEPRINT.md §8): periodic background refresh, on
# top of the on-demand POST /api/v2/insights/generate endpoint (which
# works without a beat scheduler at all). Requires a separate
# `celery -A celery_app beat` process — see docker-compose.yml's
# celery_beat service. Beat only enqueues; workers consume the queues.
_beat_schedule = {
    "generate-insights-hourly": {
        "task": "tasks.generate_insights_for_all_users_task",
        "schedule": 3600.0,
        "options": {"queue": "realtime"},
    },
    "memory-consolidate-daily": {
        "task": "tasks.memory_consolidate_task",
        "schedule": crontab(minute=30, hour=2),
        "options": {"queue": "learning"},
    },
    # Phase E1: drains frequently -- this interval *is* a durable trigger's delivery latency.
    "drain-event-outbox": {
        "task": "tasks.drain_event_outbox_task",
        "schedule": 60.0,
        "options": {"queue": "realtime"},
    },
    # Phase E2: each user's briefing at 06:00 *their* time (audit F-17). Hourly, on the hour,
    # because time zones are; the task itself picks who is due and never sends one twice a day.
    "daily-briefing": {
        "task": "tasks.generate_daily_briefings_task",
        "schedule": crontab(minute=0),
        "options": {"queue": "realtime"},
    },
    # Engineer-agent workspaces (ADR-013): pause what is idle, delete what has expired.
    # Every 30 minutes rather than hourly because the pause half returns memory on a host whose
    # RAM is the binding constraint, and a workspace abandoned mid-turn holds it until this runs.
    "reap-agent-workspaces": {
        "task": "tasks.reap_agent_workspaces_task",
        "schedule": 1800.0,
        "options": {"queue": "realtime"},
    },
    # Roadmap P1.4: bound the Idempotency-Key table. Expiry is enforced on every claim regardless;
    # this only deletes the dead rows, so hourly is plenty against a 24h default TTL.
    "purge-idempotency-keys": {
        "task": "tasks.purge_idempotency_keys_task",
        "schedule": 3600.0,
        "options": {"queue": "realtime"},
    },
    # Audit FSA-016: audit events and turn telemetry past AURA_AUDIT_RETENTION_DAYS /
    # AURA_TELEMETRY_RETENTION_DAYS. Daily, off the hour, after the 02:30 consolidation.
    "purge-audit-and-telemetry": {
        "task": "tasks.purge_audit_and_telemetry_task",
        "schedule": crontab(minute=20, hour=3),
        "options": {"queue": "realtime"},
    },
    # Loop B: pick up fine-tune jobs left QUEUED when launch used run_inline=false.
    # The `training` queue is consumed only by `worker-training` (compose profile `training`),
    # the one service built with requirements-train.txt and AURA_FINETUNE_TRAINER=local. It
    # used to be `learning`, which worker-ingestion also consumes -- with the default
    # (non-training) trainer, so a queued job could be "run" by a worker that cannot train.
    # `expires` stops drain messages piling up in Redis while the profile is not running;
    # jobs themselves stay QUEUED in the job store until a training worker exists.
    "drain-finetune-queue": {
        "task": "tasks.drain_finetune_queue_task",
        "schedule": 120.0,
        "options": {"queue": "training", "expires": 110},
    },
}

if _truthy(os.environ.get("AURA_LEARNING_REFRESH_ENABLED"), default=True):
    _beat_schedule["knowledge-refresh-nightly"] = {
        "task": "tasks.knowledge_refresh_task",
        "schedule": _crontab_from_cron(
            os.environ.get("AURA_LEARNING_REFRESH_CRON") or "0 2 * * *"
        ),
        "options": {"queue": "learning"},
    }

celery_app.conf.beat_schedule = _beat_schedule
