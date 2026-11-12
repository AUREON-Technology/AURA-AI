"""The VPS stack's Postgres connection budget is computed, not assumed (audit FSA-073).

Every gateway worker and every Celery prefork child builds its own container, so each holds one
asyncpg pool and (where the legacy tree runs) one SQLAlchemy pool. ``postgresql.conf``'s
``max_connections`` was a hand-written guess ("4 gateway workers x 15 pooled + workers +
headroom") that no test compared with what the compose files actually start. This computes the
worst case from the compose defaults and the settings' per-process figures and requires headroom
for alembic, the backup job and an operator's psql.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

yaml = pytest.importorskip("yaml")

from aura_core.config.settings import InfrastructureSettings  # noqa: E402

_ROOT = Path(__file__).resolve().parents[3]
_DEPLOY = _ROOT / "deploy"
_COMPOSE = {"prod": _DEPLOY / "docker-compose.prod.yml", "dev": _DEPLOY / "docker-compose.dev.yml"}
_GATEWAY = {"prod": "gateway", "dev": "dev-gateway"}
_DEFAULT = re.compile(r"^\$\{[A-Z0-9_]+:-(.*)\}$")
#: Connections outside any pool that must still fit: alembic at deploy, the backup job, psql.
HEADROOM = 8


def _default(value) -> str:
    match = _DEFAULT.match(str(value))
    return match.group(1) if match else str(value)


def _env_int(env: dict, key: str, fallback: int) -> int:
    return int(_default(env[key])) if key in env else fallback


def _processes(command: str) -> int:
    """uvicorn ``--workers N`` or celery ``-c N``."""
    match = re.search(r"--workers\s+(\S+)|-c\s+(\d+)", command)
    assert match, command
    return int(_default(match.group(1) or match.group(2)))


def _per_process(env: dict) -> int:
    defaults = InfrastructureSettings()
    return (
        _env_int(env, "AURA_POSTGRES_POOL_MAX_SIZE", defaults.postgres_pool_max_size)
        + _env_int(env, "AURA_LEGACY_DB_POOL_SIZE", defaults.legacy_db_pool_size)
        + _env_int(env, "AURA_LEGACY_DB_MAX_OVERFLOW", defaults.legacy_db_max_overflow)
    )


def _budget(name: str) -> tuple[int, dict[str, int]]:
    data = yaml.safe_load(_COMPOSE[name].read_text(encoding="utf-8"))
    services = data["services"]
    breakdown: dict[str, int] = {}
    for svc_name, svc in services.items():
        command = svc.get("command") or ""
        command = command if isinstance(command, str) else " ".join(command)
        if "uvicorn" not in command and "celery -A tasks worker" not in command:
            continue
        env = svc.get("environment") or {}
        breakdown[svc_name] = _processes(command) * _per_process(env)
    return sum(breakdown.values()), breakdown


def _max_connections() -> int:
    conf = (_DEPLOY / "postgres" / "postgresql.conf").read_text(encoding="utf-8")
    match = re.search(r"^max_connections\s*=\s*(\d+)", conf, re.M)
    assert match, "postgresql.conf sets no max_connections"
    return int(match.group(1))


@pytest.mark.parametrize("name", sorted(_COMPOSE))
def test_the_stack_s_worst_case_fits_under_max_connections_with_headroom(name: str) -> None:
    total, breakdown = _budget(name)
    limit = _max_connections()
    assert total + HEADROOM <= limit, (
        f"{name}: every pool full would open {total} connections ({breakdown}) against "
        f"max_connections={limit}; {HEADROOM} must stay free for alembic, backups and psql"
    )


@pytest.mark.parametrize("name", sorted(_COMPOSE))
def test_every_worker_sets_its_pool_bounds_explicitly(name: str) -> None:
    """A worker left at the gateway's defaults is the budget's silent overrun."""
    data = yaml.safe_load(_COMPOSE[name].read_text(encoding="utf-8"))
    for svc_name, svc in data["services"].items():
        command = svc.get("command") or ""
        command = command if isinstance(command, str) else " ".join(command)
        if "celery -A tasks worker" not in command:
            continue
        env = svc.get("environment") or {}
        for key in ("AURA_POSTGRES_POOL_MAX_SIZE", "AURA_LEGACY_DB_POOL_SIZE", "AURA_LEGACY_DB_MAX_OVERFLOW"):
            assert key in env, f"{name}/{svc_name} does not set {key}"


def test_both_stacks_run_the_same_per_process_figures() -> None:
    prod = yaml.safe_load(_COMPOSE["prod"].read_text(encoding="utf-8"))["services"]
    dev = yaml.safe_load(_COMPOSE["dev"].read_text(encoding="utf-8"))["services"]
    pairs = [("gateway", "dev-gateway"), ("worker-realtime", "worker-realtime"),
             ("worker-ingestion", "worker-ingestion"), ("worker-training", "worker-training")]
    for p, d in pairs:
        assert _per_process(prod[p].get("environment") or {}) == _per_process(dev[d].get("environment") or {}), (p, d)
