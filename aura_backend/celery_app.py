import os
from celery import Celery
from celery.schedules import crontab

from infrastructure.secret_hydration import hydrate_secrets_from_files


def _redis_password() -> str | None:
    """Docker mounts the Redis password as a secret file (AURA_REDIS_PASSWORD_FILE); the
    running app hydrates it into AURA_REDIS_URL at startup (aura_service.py), but the celery
    CLI (`celery -A celery_app worker`) imports this module directly and never goes through
    that hydration, so it has to read the file itself."""
    path = os.environ.get("AURA_REDIS_PASSWORD_FILE")
    if path and os.path.exists(path):
        with open(path, encoding="utf-8") as handle:
            return handle.read().strip()
    return os.environ.get("AURA_REDIS_PASSWORD")


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

redis_host = os.getenv("REDIS_HOST", "localhost")
_redis_password_value = _redis_password()
_redis_auth = f":{_redis_password_value}@" if _redis_password_value else ""
_redis_url = f"redis://{_redis_auth}{redis_host}:6379/0"

celery_app = Celery(
    "aura_tasks",
    broker=_redis_url,
    backend=_redis_url,
)

celery_app.conf.update(
    task_serializer="json",
    accept_content=["json"],
    result_serializer="json",
    timezone="UTC",
    enable_utc=True,
    task_track_started=True,
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
    # Phase E2: once a day, well before a typical morning check-in.
    "daily-briefing": {
        "task": "tasks.generate_daily_briefings_task",
        "schedule": crontab(minute=0, hour=6),
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
