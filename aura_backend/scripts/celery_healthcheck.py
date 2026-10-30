"""Container healthcheck for the Celery services (audit F-25).

    python scripts/celery_healthcheck.py worker   # this container's worker answers a ping
    python scripts/celery_healthcheck.py beat     # broker reachable, schedule still being written

Exit 0 when healthy, 1 otherwise, with one line on stderr saying why.

Imports ``celery_app`` rather than ``tasks``: the healthcheck runs every minute, and ``tasks``
pulls in the legacy RAG service, which builds a Qdrant client at import time. ``celery_app``
reads the same broker URL (including the Redis password secret file) the worker itself uses.

**worker** pings only this container's own node (``celery@<hostname>``, Celery's default node
name, and the container hostname is unique per container). A broadcast ping would report healthy
as long as *any* worker on the broker answered. The ping is answered by the worker's main
process, not a pool child, so a worker that is busy running long tasks still answers -- the
check means "the consumer is alive and connected to the broker", which is what restarting the
container would fix, not "a slot is free".

**beat** has no control channel to ping. It is healthy when the broker accepts a connection
(the only thing beat needs) and its schedule file was rewritten recently: ``PersistentScheduler``
syncs its shelve every ~3 minutes while it ticks, and the 60 s outbox drain keeps it ticking. A
hung beat stops touching the file. The threshold is deliberately loose (15 minutes) because
Docker does not restart an unhealthy container on its own; a false alarm costs a confusing
``docker ps`` line, a missed one costs a scheduler silently stopped for hours.
"""

from __future__ import annotations

import glob
import os
import socket
import sys
import time

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(_HERE))

PING_TIMEOUT_S = float(os.environ.get("AURA_CELERY_HEALTH_TIMEOUT_S", "10"))
BEAT_SCHEDULE_GLOB = os.environ.get("AURA_CELERY_BEAT_SCHEDULE_GLOB", "/app/celerybeat-schedule*")
BEAT_MAX_STALENESS_S = float(os.environ.get("AURA_CELERY_BEAT_MAX_STALENESS_S", "900"))


def _fail(reason: str) -> int:
    sys.stderr.write(f"unhealthy: {reason}\n")
    return 1


def check_worker(app, hostname: str) -> int:
    node = f"celery@{hostname}"
    try:
        replies = app.control.ping(destination=[node], timeout=PING_TIMEOUT_S) or []
    except Exception as exc:  # broker unreachable, auth failure, ...
        return _fail(f"ping to {node} raised {type(exc).__name__}: {exc}")
    if not any(node in reply for reply in replies):
        return _fail(f"{node} did not answer a ping within {PING_TIMEOUT_S:g}s")
    return 0


def check_beat(app, schedule_glob: str, max_staleness_s: float, now: float | None = None) -> int:
    try:
        with app.connection_for_write() as conn:
            conn.ensure_connection(max_retries=1, timeout=PING_TIMEOUT_S)
    except Exception as exc:
        return _fail(f"broker unreachable: {type(exc).__name__}: {exc}")
    files = glob.glob(schedule_glob)
    if not files:
        # Beat creates the file on its first tick; before that, start_period covers it.
        return _fail(f"no beat schedule file matches {schedule_glob}")
    age = (time.time() if now is None else now) - max(os.path.getmtime(f) for f in files)
    if age > max_staleness_s:
        return _fail(f"beat schedule last written {age:.0f}s ago (> {max_staleness_s:.0f}s)")
    return 0


def main(argv: list[str]) -> int:
    if len(argv) != 2 or argv[1] not in {"worker", "beat"}:
        sys.stderr.write("usage: celery_healthcheck.py worker|beat\n")
        return 2
    from celery_app import celery_app

    if argv[1] == "worker":
        return check_worker(celery_app, socket.gethostname())
    return check_beat(celery_app, BEAT_SCHEDULE_GLOB, BEAT_MAX_STALENESS_S)


if __name__ == "__main__":
    sys.exit(main(sys.argv))
