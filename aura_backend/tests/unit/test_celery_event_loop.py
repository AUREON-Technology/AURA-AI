"""Celery tasks run on one event loop per worker process, never ``asyncio.run``.

The worker container -- and the asyncpg pool and SQLAlchemy engine inside it -- is cached for the
life of the process (``tasks._ensure_worker_container``) and binds to the loop of the first task
that used it. ``asyncio.run`` gave each task a new loop, so every task after the first in a
process failed against a pool attached to a closed loop. The real-Postgres reproduction is
``test_celery_loop_integration.py``; this pins the source so a new task cannot reintroduce it.
"""

from __future__ import annotations

import inspect

import tasks


def test_no_task_creates_its_own_event_loop() -> None:
    source = inspect.getsource(tasks).replace("``asyncio.run``", "")
    assert "asyncio.run(" not in source


def test_the_worker_loop_is_reused_across_calls() -> None:
    async def current_loop():
        import asyncio

        return asyncio.get_running_loop()

    assert tasks._run_async(current_loop()) is tasks._run_async(current_loop())
