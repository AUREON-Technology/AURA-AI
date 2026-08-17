import os
from celery import Celery


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


redis_host = os.getenv("REDIS_HOST", "localhost")
_redis_password_value = _redis_password()
_redis_auth = f":{_redis_password_value}@" if _redis_password_value else ""
_redis_url = f"redis://{_redis_auth}{redis_host}:6379/0"

celery_app = Celery(
    "aura_tasks",
    broker=_redis_url,
    backend=_redis_url
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
# celery_beat service.
celery_app.conf.beat_schedule = {
    "generate-insights-hourly": {
        "task": "tasks.generate_insights_for_all_users_task",
        "schedule": 3600.0,
    },
}
