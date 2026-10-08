"""AP7 against a real Postgres: the active pointer, compare-and-set transitions, and live reads.

Opt-in (``-m load``); skips unless ``AURA_TEST_DATABASE_URL`` names a superuser DSN with password
auth (see ``test_adaptation_postgres.py``).

What the unit tests (in-memory store, fake connector) cannot show and this does:

* migration ``0012`` and ``PostgresBlueprintStore.activate`` move the pointer and both record
  states in one transaction -- under concurrent activations from separate connections exactly one
  version ends up active and the pointer names it;
* ``transition`` is a real compare-and-set: of two racing writers expecting the same state, one
  is refused;
* every generated SQL tool of the HR pilot, materialised exactly as a deploy does, runs through
  the real ``ToolRegistry`` + ``ToolPolicy`` and ``PostgresConnector.read`` against the loaded
  pilot, and returns a result that passes its output-schema check.
"""

from __future__ import annotations

import asyncio
import dataclasses
import importlib.util
import os
import subprocess
import sys
import uuid
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

import pytest

from aura_core.adaptation.runtime import BlueprintToolMaterializer
from aura_core.adapters.connectors.postgres import PostgresConnectorFactory
from aura_core.adapters.postgres_adaptation import PostgresBlueprintStore
from aura_core.domain.blueprint import BlueprintState
from aura_core.domain.compilation import BlueprintRecord
from aura_core.domain.connectors import ConnectorDescriptor, ConnectorKind, Credential, IdentityMode
from aura_core.domain.errors import ConflictError
from aura_core.domain.identifiers import ToolCallId, UserId
from aura_core.domain.messages import ToolCall
from aura_core.domain.templates import TemplateKind
from aura_core.ports.runtime import ToolContext
from aura_core.tools.policy import ToolPolicy
from aura_core.tools.registry import ToolRegistry

pytestmark = pytest.mark.load

_URL = os.environ.get("AURA_TEST_DATABASE_URL", "")
_BACKEND = Path(__file__).resolve().parents[2]


def _module(name: str, file: str):
    spec = importlib.util.spec_from_file_location(name, _BACKEND / "scripts" / file)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


DISCOVERY = _module("pilot_discovery_goldens", "pilot_discovery_goldens.py")
TWINS = _module("pilot_twin_goldens", "pilot_twin_goldens.py")
HR = TWINS.compile_pilot("hr")


def _record(version: int, state: BlueprintState) -> BlueprintRecord:
    bp = HR.blueprint
    bp = dataclasses.replace(bp, project=dataclasses.replace(bp.project, id="p0", version=version))
    live = state in (BlueprintState.ACTIVE, BlueprintState.SUPERSEDED)
    return BlueprintRecord(
        project_id="p0", version=version, state=state, digest=bp.digest,
        created_at="2026-10-04T00:00:00+00:00", created_by="sa@example.com",
        plan_digest="sha256:" + "1" * 64, registry_digest="sha256:" + "2" * 64,
        blueprint=bp, suites=HR.suites, dropped=HR.dropped,
        gate="ungated" if state is not BlueprintState.PROPOSED else None,
        approval_id="appr-1" if state not in (BlueprintState.PROPOSED, BlueprintState.EVALUATED) else None,
        activated_at="2026-10-04T00:00:00+00:00" if live else None,
    )


@pytest.fixture
async def migrated_url():
    if not _URL:
        pytest.skip("AURA_TEST_DATABASE_URL is not set")
    import asyncpg

    name = f"ap7_{uuid.uuid4().hex[:10]}"
    admin = await asyncpg.connect(_URL)
    await admin.execute(f'CREATE DATABASE "{name}"')
    url = urlunsplit(urlsplit(_URL)._replace(path=f"/{name}"))
    try:
        sqlalchemy_url = url.replace("postgresql://", "postgresql+asyncpg://", 1)
        env = {**os.environ, "DATABASE_URL": sqlalchemy_url, "AURA_DATABASE_URL": sqlalchemy_url}
        result = subprocess.run([sys.executable, "-m", "alembic", "upgrade", "head"], cwd=_BACKEND, env=env,
                                capture_output=True, text=True, timeout=300)
        assert result.returncode == 0, result.stderr[-3000:]
        yield url
    finally:
        await admin.execute(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)')
        await admin.close()


async def _states(url: str) -> dict[int, str]:
    import asyncpg

    conn = await asyncpg.connect(url)
    try:
        rows = await conn.fetch("SELECT version, state FROM aura_adaptation_blueprints ORDER BY version")
        return {r["version"]: r["state"] for r in rows}
    finally:
        await conn.close()


async def test_activation_moves_pointer_and_states_together(migrated_url) -> None:
    store = PostgresBlueprintStore(migrated_url)
    for version in (1, 2):
        await store.append(_record(version, BlueprintState.APPROVED))
    first = await store.activate("p0", 1, by="sa@example.com", at="2026-10-04T10:00:00+00:00")
    assert (first.version, first.previous_version) == (1, None)
    second = await store.activate("p0", 2, by="sa@example.com", at="2026-10-04T11:00:00+00:00")
    assert (second.version, second.previous_version) == (2, 1)
    assert await _states(migrated_url) == {1: "superseded", 2: "active"}
    back = await store.activate("p0", 1, by="sa@example.com", at="2026-10-04T12:00:00+00:00")  # rollback
    assert (back.version, back.previous_version) == (1, 2)
    assert await _states(migrated_url) == {1: "active", 2: "superseded"}
    assert (await store.active("p0")) == back
    loaded = await store.get("p0", 1)
    assert loaded.state is BlueprintState.ACTIVE and loaded.activated_by == "sa@example.com"
    with pytest.raises(ConflictError, match="only an approved or superseded"):
        await store.activate("p0", 1, by="sa@example.com", at="2026-10-04T13:00:00+00:00")


async def test_concurrent_activations_leave_exactly_one_active(migrated_url) -> None:
    stores = [PostgresBlueprintStore(migrated_url) for _ in range(4)]
    for version in (1, 2, 3, 4):
        await stores[0].append(_record(version, BlueprintState.APPROVED))
    results = await asyncio.gather(
        *(store.activate("p0", v, by=f"op{v}", at=f"2026-10-04T1{v}:00:00+00:00")
          for store, v in zip(stores, (1, 2, 3, 4))),
        return_exceptions=True,
    )
    assert not [r for r in results if isinstance(r, BaseException) and not isinstance(r, ConflictError)]
    states = await _states(migrated_url)
    active = [v for v, s in states.items() if s == "active"]
    pointer = await stores[0].active("p0")
    assert len(active) == 1 and pointer.version == active[0]
    assert all(s in ("active", "superseded") for s in states.values())


async def test_transition_is_compare_and_set(migrated_url) -> None:
    store_a, store_b = PostgresBlueprintStore(migrated_url), PostgresBlueprintStore(migrated_url)
    proposed = _record(1, BlueprintState.PROPOSED)
    await store_a.append(proposed)
    evaluated = dataclasses.replace(proposed, state=BlueprintState.EVALUATED, gate="ungated")
    outcomes = await asyncio.gather(
        store_a.transition(evaluated, expected=BlueprintState.PROPOSED),
        store_b.transition(evaluated, expected=BlueprintState.PROPOSED),
        return_exceptions=True,
    )
    assert sorted(type(o).__name__ for o in outcomes) == ["ConflictError", "NoneType"]
    assert (await store_a.get("p0", 1)).state is BlueprintState.EVALUATED


async def test_generated_tools_read_a_real_pilot(migrated_url) -> None:
    del migrated_url  # this test needs only the pilot database
    dsn = await DISCOVERY.load_pilot(_URL, "hr")
    try:
        parts, target = urlsplit(_URL), urlsplit(dsn)
        descriptor = ConnectorDescriptor(
            id="hr-db", kind=ConnectorKind.POSTGRES, identity_mode=IdentityMode.SERVICE_ACCOUNT, display_name="HR",
            endpoint=urlunsplit(target._replace(netloc=f"{target.hostname}:{target.port}")),
            options={"user": parts.username, "sslmode": "disable"}, timeout_s=30.0,
        )
        factory = PostgresConnectorFactory()

        async def open_connector(connector_id: str):
            assert connector_id == "hr-db"
            return await factory.open(descriptor, Credential(parts.password))

        tools = ToolRegistry(policy=ToolPolicy())
        materializer = BlueprintToolMaterializer(tools=tools, open_connector=open_connector, grace_s=0)
        bp = dataclasses.replace(HR.blueprint, project=dataclasses.replace(HR.blueprint.project, version=1))
        materializer.register(bp)
        templates = {t.id: t for t in bp.templates}
        ran = 0
        for tool in bp.tools:
            if templates[tool.template].kind is not TemplateKind.SQL:
                continue
            arguments = {p.name: (1 if p.type == "integer" else "a") for p in tool.parameters if p.required}
            context = ToolContext(user_id=UserId("u1"), correlation_id="c1", deadline_s=30.0,
                                  capabilities=("project_data",))
            result, report = await tools.execute(
                ToolCall(ToolCallId(f"c{ran}"), f"bp1_{tool.name}", arguments), context,
                capabilities=("project_data",),
            )
            assert result.ok, (tool.name, result.error_kind, result.content[:300])
            assert context.tainted
            ran += 1
        assert ran == sum(1 for t in bp.templates if t.kind is TemplateKind.SQL)
    finally:
        await DISCOVERY.drop_database(_URL, dsn)
