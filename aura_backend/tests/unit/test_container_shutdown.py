"""R2.7: ``Container.shutdown()`` releases what the container connected, and nothing else.

The F-25 drain (uvicorn ``--timeout-graceful-shutdown``, ``stop_grace_period``) lets in-flight
turns finish, but the process then exited with the asyncpg pool, the Redis client, the Qdrant
client and every provider's httpx client still open. Postgres saw each of those as an abrupt
disconnect, and a Celery child recycled by ``--max-tasks-per-child`` left its connections to time
out server-side. These pin the shutdown contract:

* every client the container *did* construct is closed;
* none it did not construct is built just to be closed (that would be a network call on the
  way out, against a dependency that may be the reason the process is stopping);
* one close failing or hanging does not stop the others, and the whole thing is bounded so it
  fits inside the lifespan margin left after uvicorn's drain;
* a second call is a no-op;
* the gateway lifespan and the Celery worker process both actually call it.

Everything here runs on the in-memory substrate plus real adapters pointed at a closed port;
no test opens a connection.
"""

from __future__ import annotations

import asyncio
import logging

import pytest

import aura_core.container as container_module
from aura_core.adapters.docker_sandbox import DockerSandboxRunner
from aura_core.adapters.postgres_store import PostgresPool
from aura_core.config import load_settings
from aura_core.container import Container

#: Real backends, all pointed at the discard port. Building the container must not connect, and
#: neither may shutting it down.
_REAL_BACKENDS = {
    "AURA_CACHE_BACKEND": "redis",
    "AURA_REDIS_URL": "redis://127.0.0.1:9/0",
    "AURA_VECTOR_BACKEND": "qdrant",
    "AURA_QDRANT_URL": "http://127.0.0.1:9",
    "AURA_DOCUMENT_BACKEND": "postgres",
    "AURA_DATABASE_URL": "postgresql://aura:aura@127.0.0.1:9/aura",
    "AURA_LLM_PROVIDERS": "local",
    "AURA_PROVIDER_LOCAL_KIND": "openai_compatible",
    "AURA_PROVIDER_LOCAL_BASE_URL": "http://127.0.0.1:9/v1",
    "AURA_PROVIDER_LOCAL_MODEL": "test-model",
}


def _container(monkeypatch, **env) -> Container:
    for key, value in env.items():
        monkeypatch.setenv(key, value)
    return Container(load_settings(refresh=True, validate=False))


class _Closeable:
    """An adapter whose close is observable: counts calls, and can fail or hang on demand."""

    def __init__(self, *, error: Exception | None = None, hang: bool = False) -> None:
        self.calls = 0
        self.loops: list[asyncio.AbstractEventLoop] = []
        self._error = error
        self._hang = hang

    async def close(self) -> None:
        self.calls += 1
        self.loops.append(asyncio.get_running_loop())
        if self._hang:
            await asyncio.Event().wait()
        if self._error is not None:
            raise self._error


class _FakeAsyncpgPool:
    """Stands in for the ``asyncpg.Pool`` a real first query would have created."""

    def __init__(self, *, hang: bool = False) -> None:
        self.closed = False
        self.terminated = False
        self._hang = hang

    async def close(self) -> None:
        if self._hang:
            await asyncio.Event().wait()
        self.closed = True

    def terminate(self) -> None:
        self.terminated = True


class _FakeDockerClient:
    def __init__(self) -> None:
        self.closed = False

    def close(self) -> None:
        self.closed = True


# ------------------------------------------------------------------- what gets closed


async def test_shutdown_closes_every_client_the_container_connected(monkeypatch) -> None:
    container = _container(monkeypatch, **_REAL_BACKENDS)

    # Bring each client into existence the way a first real request would -- none of these
    # constructors touches the network -- and stand in for the asyncpg pool, whose creation does.
    container.cache_backend._get_client()
    container.vectors._get_client()
    provider = container.providers[0]
    http = await provider._get_client()
    asyncpg_pool = _FakeAsyncpgPool()
    container._pg_pool._pool = asyncpg_pool
    runner = DockerSandboxRunner(image="aura-sandbox:test")
    docker_client = _FakeDockerClient()
    runner._client = docker_client
    container.docker_sandbox_runner = runner

    await container.shutdown()

    assert container.cache_backend._client is None, "the Redis client was left open"
    assert container.vectors._client is None, "the Qdrant client was left open"
    assert provider._client is None and http.is_closed, "the provider's httpx client was left open"
    assert asyncpg_pool.closed, "the Postgres pool was left open"
    assert docker_client.closed and runner._client is None, "the Docker client was left open"


async def test_shutdown_never_builds_a_client_just_to_close_it(monkeypatch) -> None:
    container = _container(monkeypatch, **_REAL_BACKENDS)

    built: list[str] = []

    def recorder(name):
        def build(*args, **kwargs):
            built.append(name)
            raise AssertionError(f"{name} was constructed during shutdown")

        return build

    import asyncpg
    import httpx
    import qdrant_client
    import redis.asyncio

    monkeypatch.setattr(asyncpg, "create_pool", recorder("asyncpg pool"))
    monkeypatch.setattr(redis.asyncio, "from_url", recorder("redis client"))
    monkeypatch.setattr(qdrant_client, "AsyncQdrantClient", recorder("qdrant client"))
    monkeypatch.setattr(httpx, "AsyncClient", recorder("httpx client"))

    await container.shutdown()

    assert built == []


async def test_a_container_with_no_postgres_never_creates_a_pool(container) -> None:
    assert container._pg_pool is None

    await container.shutdown()

    assert container._pg_pool is None, "shutdown built a Postgres pool in order to close it"


# ------------------------------------------------------------------- failure tolerance


async def test_one_close_failing_does_not_stop_the_others(container, caplog) -> None:
    failing = _Closeable(error=RuntimeError("redis went away first"))
    healthy = _Closeable()
    container.cache_backend = failing
    container.docker_sandbox_runner = healthy

    with caplog.at_level(logging.WARNING):
        await container.shutdown()  # must not raise

    assert failing.calls == 1
    assert healthy.calls == 1
    assert any("cache" in record.getMessage() for record in caplog.records), (
        "a close that failed was not reported"
    )


async def test_a_hanging_close_is_bounded(container, monkeypatch) -> None:
    monkeypatch.setattr(container_module, "SHUTDOWN_CLOSE_TIMEOUT_S", 0.05)
    hanging = _Closeable(hang=True)
    healthy = _Closeable()
    container.vectors = hanging
    container.cache_backend = healthy

    await asyncio.wait_for(container.shutdown(), timeout=2.0)

    assert hanging.calls == 1
    assert healthy.calls == 1


def test_the_close_budget_fits_inside_the_lifespan_margin() -> None:
    """Closes run concurrently, so the whole shutdown is one per-close timeout, and that must end
    before the orchestrator's SIGKILL: the margin F-25 left after uvicorn's drain."""
    from tests.unit.test_deploy_robustness import LIFESPAN_MARGIN_S

    assert 0 < container_module.SHUTDOWN_CLOSE_TIMEOUT_S < LIFESPAN_MARGIN_S


async def test_the_postgres_pool_is_terminated_when_a_graceful_close_hangs() -> None:
    """asyncpg's ``Pool.close()`` waits for every acquired connection to come back. A connection
    stuck in a query past the drain would hold shutdown forever; terminating closes it anyway."""
    pool = PostgresPool("postgresql://aura:aura@127.0.0.1:9/aura")
    stuck = _FakeAsyncpgPool(hang=True)
    pool._pool = stuck

    await asyncio.wait_for(pool.close(timeout_s=0.05), timeout=2.0)

    assert stuck.terminated
    assert pool._pool is None


async def test_a_docker_runner_that_never_connected_closes_without_connecting() -> None:
    runner = DockerSandboxRunner(image="aura-sandbox:test")

    await runner.close()

    assert runner._client is None


# ------------------------------------------------------------------- idempotence


async def test_shutdown_is_idempotent(container) -> None:
    closeable = _Closeable()
    container.docker_sandbox_runner = closeable

    await container.shutdown()
    await container.shutdown()
    await asyncio.gather(container.shutdown(), container.shutdown())

    assert closeable.calls == 1


async def test_a_restarted_container_closes_again(container) -> None:
    """``startup()`` after ``shutdown()`` (a test client re-entering its lifespan, an evaluation
    run reusing a container) re-arms it, or the second run's clients would leak."""
    closeable = _Closeable()
    container.docker_sandbox_runner = closeable

    await container.shutdown()
    await container.startup()
    await container.shutdown()

    assert closeable.calls == 2


# ------------------------------------------------------------------- who calls it


def test_the_gateway_lifespan_shuts_the_container_down(container) -> None:
    from fastapi.testclient import TestClient

    from aura_core.api import build_app

    closeable = _Closeable()
    container.docker_sandbox_runner = closeable

    async def authenticate(request):  # pragma: no cover - no authenticated route is called
        raise AssertionError("not reached")

    app = build_app(container, authenticator=authenticate)
    with TestClient(app) as client:
        assert client.get("/health/live").status_code == 200
        assert closeable.calls == 0, "closed while still serving"

    assert closeable.calls == 1


async def _current_loop() -> asyncio.AbstractEventLoop:
    return asyncio.get_running_loop()


def test_a_celery_worker_process_closes_its_container_on_its_own_loop(container, monkeypatch) -> None:
    """f9d580a gave each worker process one event loop, and the container's pool is bound to it,
    so the close has to run there -- on any other loop asyncpg refuses to touch the pool."""
    from celery.signals import worker_process_shutdown

    import tasks

    closeable = _Closeable()
    container.docker_sandbox_runner = closeable
    monkeypatch.setattr(tasks, "_worker_container", container)
    monkeypatch.setattr(tasks, "_worker_started", True)
    worker_loop = tasks._run_async(_current_loop())

    worker_process_shutdown.send(sender=None, pid=0, exitcode=0)

    assert closeable.calls == 1
    assert closeable.loops == [worker_loop]
    assert tasks._worker_container is None


def test_a_worker_process_that_never_built_a_container_does_not_build_one(monkeypatch) -> None:
    from celery.signals import worker_process_shutdown

    import tasks

    monkeypatch.setattr(tasks, "_worker_container", None)
    monkeypatch.setattr(tasks, "_worker_started", False)

    worker_process_shutdown.send(sender=None, pid=0, exitcode=0)

    assert tasks._worker_container is None
