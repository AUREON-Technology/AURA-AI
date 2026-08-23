"""Phase A4: load test against the real data tier.

Every other suite in this repository runs against the in-memory adapters (see
``tests/conftest.py``'s own docstring on why) or, for benchmarks, an echo provider. Both are
honest about what they are not measuring: `docs/AURA_UPGRADE_ROADMAP.md` names the concurrency
figures those numbers are hidden behind (478 turns/s) as an echo-provider artifact, not a real
measurement. This suite is the one place that is not true: it boots the same `Container` the
production compose file boots, against real Postgres, Qdrant, Redis, and Ollama, and measures
what actually happens when a model sits in the request path.

It is opt-in, not run by `pytest tests` (see the `load` marker's exclusion in ``pytest.ini``,
matching how ``bench_*.py`` is excluded), because the infrastructure it needs is not available on
a standard CI runner and a real model's wall-clock time is not something to gate a shared runner
on. Point it at a real stack and run explicitly:

    docker compose -f deploy/docker-compose.prod.yml up -d postgres redis qdrant ollama
    docker exec <ollama container> ollama pull qwen2.5:3b-instruct-q4_K_M
    alembic upgrade head   # against AURA_DATABASE_URL

    export AURA_DATABASE_URL=postgresql+asyncpg://aura:<password>@localhost:5432/aura
    export AURA_REDIS_URL=redis://:<password>@localhost:6379/0
    export AURA_QDRANT_URL=http://localhost:6333
    export AURA_QDRANT_API_KEY=<key>
    export AURA_PROVIDER_LOCAL_BASE_URL=http://localhost:11434/v1
    export AURA_PROVIDER_LOCAL_MODEL=qwen2.5:3b-instruct-q4_K_M
    pytest tests/load -m load -s

Every test skips cleanly, rather than failing, when the required environment variables are not
set -- a contributor running the default suite without a data tier running should see nothing
from this directory. See ``docs/reports/phase-a4-load-test.md`` for the numbers from the one real
run this suite has had, and why they are the honest, unflattering answer rather than a tuned one.
"""

from __future__ import annotations

import asyncio
import os
import statistics
import time

import pytest

from aura_core.config import load_settings
from aura_core.container import Container
from aura_core.domain import DocumentId, UserId
from aura_core.kernel import TurnRequest

pytestmark = pytest.mark.load

# What a real run needs pointed at real infrastructure. AURA_PROVIDER_LOCAL_BASE_URL is the one
# that matters most: without it, Container falls back to the echo provider, which is exactly the
# non-measurement this suite exists to not produce.
_REQUIRED_ENV = (
    "AURA_DATABASE_URL",
    "AURA_QDRANT_URL",
    "AURA_REDIS_URL",
    "AURA_PROVIDER_LOCAL_BASE_URL",
)


def _missing_env() -> list[str]:
    return [key for key in _REQUIRED_ENV if not os.environ.get(key)]


def _report(name: str, samples: list[float], *, unit: str = "ms") -> None:
    samples = sorted(samples)
    p50 = statistics.median(samples)
    p95 = samples[int(len(samples) * 0.95)] if len(samples) > 1 else samples[0]
    print(
        f"  {name:<32} n={len(samples):<4} "
        f"p50={p50:9.1f}{unit}  p95={p95:9.1f}{unit}  max={samples[-1]:9.1f}{unit}"
    )


@pytest.fixture
async def real_container():
    """A `Container` wired to whatever real backends the environment names.

    Deliberately not the `container` fixture from `tests/conftest.py`: that one is pinned to the
    in-memory substrate on purpose (its own docstring explains why), and overloading it with
    env-var-dependent behaviour would make every other suite's fixture quietly environment-
    sensitive. This is its own fixture so the dependency is explicit and confined to this
    directory.
    """
    missing = _missing_env()
    if missing:
        pytest.skip(f"real data tier not configured, missing: {', '.join(missing)}")

    settings = load_settings(refresh=True, validate=False)
    container = Container(settings)
    await container.startup()
    try:
        yield container
    finally:
        await container.shutdown()


@pytest.fixture
async def loaded_container(real_container):
    """`real_container` with one document ingested, for tests that need something to retrieve."""
    from tests.conftest import HANDBOOK

    user = UserId("load-test-user")
    await real_container.knowledge.ingest(
        content=HANDBOOK, title="Handbook",
        document_id=DocumentId("load-test-handbook"), owner_id=user,
    )
    return real_container, user


class TestSingleTurnLatency:
    """Exit criterion: a real p95 with a model in the path (not an echo provider)."""

    async def test_p95_latency_real_model(self, loaded_container) -> None:
        container, user = loaded_container

        totals: list[float] = []
        overheads: list[float] = []
        for i in range(20):
            started = time.perf_counter()
            result = await container.kernel.run(
                TurnRequest(
                    message=f"Question {i}: how many days of annual leave do I get?",
                    user_id=user,
                )
            )
            totals.append((time.perf_counter() - started) * 1000)
            overheads.append(result.telemetry.timings.overhead)

        print(f"\nSingle-turn latency, real data tier, model={os.environ.get('AURA_PROVIDER_LOCAL_MODEL', 'unset')}")
        _report("total (model + pipeline)", totals)
        _report("pipeline overhead only", overheads)

        # A generous ceiling, not a tuned target: this catches a hang or a dead provider, not a
        # regression. The real number belongs in the report, not a hardcoded assertion that would
        # need updating every time inference hardware changes.
        assert sorted(totals)[int(len(totals) * 0.95)] < 120_000, "p95 exceeded 120s -- likely hung"


class TestConcurrencyCeiling:
    """Exit criterion: a documented concurrency ceiling naming OLLAMA_NUM_PARALLEL as the
    binding constraint, measured rather than assumed."""

    async def test_concurrency_levels(self, loaded_container) -> None:
        container, user = loaded_container

        print(
            f"\nConcurrency, real data tier "
            f"(OLLAMA_NUM_PARALLEL={os.environ.get('OLLAMA_NUM_PARALLEL', 'unset (server default)')}, "
            f"AURA_PROVIDER_LOCAL_MAX_CONCURRENCY={os.environ.get('AURA_PROVIDER_LOCAL_MAX_CONCURRENCY', 'unset (settings default)')})"
        )
        for concurrency in (1, 2, 4, 8):
            started = time.perf_counter()
            results = await asyncio.gather(*(
                container.kernel.run(
                    TurnRequest(message=f"Concurrent question {i} about leave?", user_id=user)
                )
                for i in range(concurrency)
            ))
            elapsed = time.perf_counter() - started
            per_turn = [r.telemetry.total_ms for r in results]
            print(
                f"  concurrency={concurrency:<3} wall={elapsed * 1000:9.1f}ms  "
                f"throughput={concurrency / elapsed:6.2f} turns/s  "
                f"per-turn p50={statistics.median(per_turn):9.1f}ms max={max(per_turn):9.1f}ms"
            )
