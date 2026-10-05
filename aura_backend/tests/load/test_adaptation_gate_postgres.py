"""AP8 against a real Postgres: the replica is faithful, and the gate decides the same live.

Opt-in (``-m load``); skips unless ``AURA_TEST_DATABASE_URL`` names a superuser DSN with password
auth (see ``test_adaptation_postgres.py``).

What the default suite (in-memory replica over ``golden/rows.json``) cannot show and this does:

* ``rows.json`` is what the seeded pilot database actually holds;
* **every read the gate sends** -- each generated tool's template with the arguments the gate
  chose, and each oracle read it composed from the twin -- returns the same rows from
  ``InMemoryReadConnector`` as from ``PostgresConnector.read`` against the loaded pilot, for good
  and seeded-bad blueprints alike. So a verdict reached on the replica is the verdict the real
  database would give;
* the gate itself, pointed at the live pilot through ``PostgresConnector``, passes the compiled
  blueprints and blocks the wrong join and the over-broad grant.
"""

from __future__ import annotations

import dataclasses
import importlib.util
import json
import os
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

import pytest

from aura_core.adaptation.gate import BlueprintGate
from aura_core.adapters.connectors.memory import InMemoryReadConnector
from aura_core.adapters.connectors.postgres import PostgresConnectorFactory
from aura_core.domain.blueprint import BlueprintState
from aura_core.domain.compilation import BlueprintRecord
from aura_core.domain.connectors import ConnectorDescriptor, ConnectorKind, Credential, IdentityMode
from aura_core.domain.errors import AuraError, NotFoundError
from aura_core.domain.templates import FilterOp, SqlFilter
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


def _record(compiled, blueprint=None) -> BlueprintRecord:
    bp = blueprint or compiled.blueprint
    return BlueprintRecord(
        project_id="p0", version=1, state=BlueprintState.PROPOSED, digest=bp.digest,
        created_at="2026-10-05T00:00:00+00:00", created_by="sa@example.com",
        plan_digest="sha256:" + "1" * 64, registry_digest="sha256:" + "2" * 64,
        blueprint=bp, suites=compiled.suites, dropped=compiled.dropped,
    )


def _variants(pilot: str):
    compiled = TWINS.compile_pilot(pilot)
    yield "good", compiled.blueprint
    if pilot == "hr":
        bp = compiled.blueprint
        template = next(t for t in bp.templates if t.id == "list_employees_by_department_id")
        wrong = dataclasses.replace(template, sql=dataclasses.replace(
            template.sql, filters=(SqlFilter("manager_id", FilterOp.EQ, "department_id"),)))
        yield "wrong_join", dataclasses.replace(bp, templates=tuple(wrong if t.id == template.id else t for t in bp.templates))
        tool = next(t for t in bp.tools if t.name == "list_assets")
        agents = tuple(dataclasses.replace(a, tools=a.tools + ("list_assets",), capabilities=a.capabilities + (tool.capability,))
                       if a.id == "role_hr_manager" else a for a in bp.agents)
        yield "over_broad", dataclasses.replace(bp, agents=agents)


@pytest.fixture(params=["hr", "ecommerce"])
async def live_pilot(request):
    if not _URL:
        pytest.skip("AURA_TEST_DATABASE_URL is not set")
    pilot = request.param
    dsn = await DISCOVERY.load_pilot(_URL, pilot)
    parts, target = urlsplit(_URL), urlsplit(dsn)
    descriptor = ConnectorDescriptor(
        id=f"{pilot}-db", kind=ConnectorKind.POSTGRES, identity_mode=IdentityMode.SERVICE_ACCOUNT,
        display_name=pilot, endpoint=urlunsplit(target._replace(netloc=f"{target.hostname}:{target.port}")),
        options={"user": parts.username, "sslmode": "disable"}, timeout_s=30.0,
    )
    factory = PostgresConnectorFactory()

    async def open_live(connector_id: str):
        if connector_id != descriptor.id:
            raise NotFoundError("no replica for this connector")
        return await factory.open(descriptor, Credential(parts.password))

    try:
        yield pilot, dsn, open_live
    finally:
        await DISCOVERY.drop_database(_URL, dsn)


async def test_recorded_rows_are_what_the_pilot_holds(live_pilot) -> None:
    import asyncpg

    from aura_core.adapters.connectors.postgres import json_value
    from aura_core.domain.discovery import quote_identifier as q

    pilot, dsn, _ = live_pilot
    recorded = DISCOVERY.load_rows(pilot)
    conn = await asyncpg.connect(dsn)
    try:
        for (schema, table), rows in recorded.items():
            live = [{k: json_value(v) for k, v in r.items()} for r in await conn.fetch(f"SELECT * FROM {q(schema)}.{q(table)}")]
            key = lambda r: json.dumps(r, sort_keys=True)  # noqa: E731
            assert sorted(map(key, live)) == sorted(map(key, rows)), f"{schema}.{table}"
    finally:
        await conn.close()


async def test_every_gate_read_answers_the_same_on_the_replica_and_live(live_pilot, clock) -> None:
    pilot, _, open_live = live_pilot
    rows = DISCOVERY.load_rows(pilot)
    sent: list[tuple] = []

    class Recording(InMemoryReadConnector):
        async def read(self, template, arguments):
            try:
                result = await super().read(template, arguments)
            except AuraError as exc:
                sent.append((template, dict(arguments), ("error", exc.kind)))
                raise
            sent.append((template, dict(arguments), ("ok", result)))
            return result

    async def open_replica(connector_id: str):
        if connector_id != f"{pilot}-db":
            raise NotFoundError("no replica for this connector")
        return Recording(connector_id, rows, enum_orders=DISCOVERY.enum_orders(pilot))

    compiled, twin = TWINS.compile_pilot(pilot), TWINS.twin_for(pilot)
    for _, blueprint in _variants(pilot):
        gate = BlueprintGate(open_connector=open_replica, new_registry=lambda: ToolRegistry(policy=ToolPolicy()), clock=clock)
        await gate.evaluate(_record(compiled, blueprint), twin, by="sa@example.com")
    assert len(sent) > 50

    mismatches = []
    for template, arguments, (status, expected) in sent:
        connector = await open_live(template.connector)
        try:
            try:
                live = ("ok", await connector.read(template, arguments))
            except AuraError as exc:
                live = ("error", exc.kind)
        finally:
            await connector.aclose()
        if status == "ok" and live[0] == "ok" and not template.sql.order_by:
            same = sorted(map(json.dumps, expected)) == sorted(map(json.dumps, live[1]))
        else:
            same = (status, expected) == live
        if not same:
            mismatches.append((template.id, arguments))
    assert not mismatches, mismatches[:5]


async def test_the_gate_decides_the_same_against_the_live_pilot(live_pilot, clock) -> None:
    pilot, _, open_live = live_pilot
    compiled, twin = TWINS.compile_pilot(pilot), TWINS.twin_for(pilot)
    verdicts = {}
    for name, blueprint in _variants(pilot):
        gate = BlueprintGate(open_connector=open_live, new_registry=lambda: ToolRegistry(policy=ToolPolicy()), clock=clock)
        report = await gate.evaluate(_record(compiled, blueprint), twin, by="sa@example.com")
        verdicts[name] = report.blocking()
        if name == "good":
            contract = next(s for s in report.suites if s.name == "tool_contract")
            assert contract.ran == sum(1 for t in blueprint.templates if t.kind.value == "sql")
    expected = {"good": ()}
    if pilot == "hr":
        expected |= {"wrong_join": ("tool_contract",), "over_broad": ("permission_denied", "grant_scope", "injection_refusal")}
    assert verdicts == expected
