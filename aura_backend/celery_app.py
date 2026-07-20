import os
from celery import Celery

redis_host = os.getenv("REDIS_HOST", "localhost")

celery_app = Celery(
    "aura_tasks",
    broker=f"redis://{redis_host}:6379/0",
    backend=f"redis://{redis_host}:6379/0"
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
