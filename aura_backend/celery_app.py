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
