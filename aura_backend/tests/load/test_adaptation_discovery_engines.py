"""AP10 against real MySQL 8 and SQL Server: live captures equal the recorded catalogs.

Opt-in (``-m load``). Each engine skips unless its admin URL is set:

* ``AURA_TEST_MYSQL_URL`` -- ``mysql://admin:pw@host:3306`` with rights to create databases,
  accounts and roles (e.g. ``docker run -e MYSQL_ROOT_PASSWORD=pw -p 3306:3306 mysql:8.0``).
* ``AURA_TEST_MSSQL_URL`` -- ``mssql://sa:pw@host:1433`` (e.g. ``docker run -e ACCEPT_EULA=Y
  -e MSSQL_SA_PASSWORD=... -p 1433:1433 mcr.microsoft.com/mssql/server:2022-latest``).

Each test loads the HR pilot into a fresh ``aura_pilot_hr`` database through the same loader the
golden recorder uses, and drops it (and its accounts) afterwards -- so point these at a throwaway
server. What the default suite cannot show and this does: the catalog queries still produce
exactly the committed raw capture; the connector, through its own connect path, discovers the
committed golden; a read-only account is healthy and not write-capable, while one granted a
write path is refused at activation; and an account that cannot see other principals' grants gets
an issue, not a failed run.
"""

from __future__ import annotations

import importlib.util
import json
import os
import uuid
from pathlib import Path
from typing import Any

import pytest

from aura_core.domain.connectors import ConnectorDescriptor, ConnectorKind, Credential, HealthStatus, IdentityMode
from aura_core.domain.discovery import DiscoveryBudget
from aura_core.domain.serialization import to_data

pytestmark = pytest.mark.load

_BACKEND = Path(__file__).resolve().parents[2]
_GOLDEN = _BACKEND / "tests" / "fixtures" / "pilots" / "hr" / "golden"
_URLS = {"mysql": os.environ.get("AURA_TEST_MYSQL_URL", ""), "mssql": os.environ.get("AURA_TEST_MSSQL_URL", "")}


def _goldens():
    spec = importlib.util.spec_from_file_location("pilot_goldens", _BACKEND / "scripts" / "pilot_discovery_goldens.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


GOLDENS = _goldens()


@pytest.fixture(params=["mysql", "mssql"])
def engine(request):
    url = _URLS[request.param]
    if not url:
        pytest.skip(f"AURA_TEST_{request.param.upper()}_URL is not set")
    password = "Aa1-" + uuid.uuid4().hex
    load = {"mysql": GOLDENS.load_mysql_pilot, "mssql": GOLDENS.load_mssql_pilot}[request.param]
    account = load(url, "hr", password)
    try:
        yield request.param, url, account
    finally:
        {"mysql": GOLDENS.drop_mysql_pilot, "mssql": GOLDENS.drop_mssql_pilot}[request.param](url, "hr")


def _connector(engine: str, account: dict[str, Any], *, user: str | None = None, password: str | None = None):
    from aura_core.adapters.connectors.mssql import SqlServerConnector
    from aura_core.adapters.connectors.mysql import MySqlConnector

    scheme, kind, cls, tls = {
        "mysql": ("mysql", ConnectorKind.MYSQL, MySqlConnector, "disable"),
        "mssql": ("mssql", ConnectorKind.SQL_SERVER, SqlServerConnector, "disable"),
    }[engine]
    descriptor = ConnectorDescriptor(
        id="hr-db", kind=kind, identity_mode=IdentityMode.SERVICE_ACCOUNT, display_name="HR",
        endpoint=f"{scheme}://{account['host']}:{account['port']}/{account['database']}",
        options={"user": user or account["user"], "tls": tls}, timeout_s=30.0)
    return cls(descriptor, Credential(password or account["password"]))


def _admin(engine: str, url: str):
    if engine == "mysql":
        import pymysql

        return pymysql.connect(**GOLDENS._server(url, ("mysql",), 3306), autocommit=True)
    import pytds

    server = GOLDENS._server(url, ("mssql", "sqlserver"), 1433)
    return pytds.connect(server["host"], user=server["user"], password=server["password"], port=server["port"],
                         database=GOLDENS.ENGINE_DATABASE, autocommit=True)


def _create_account(engine: str, url: str, name: str, password: str, grants: list[str]) -> None:
    admin = _admin(engine, url)
    try:
        cur = admin.cursor()
        if engine == "mysql":
            cur.execute(f"DROP USER IF EXISTS '{name}'@'%'")
            cur.execute(f"CREATE USER '{name}'@'%%' IDENTIFIED BY %s", (password,))  # %% : bound
        else:
            cur.execute(f"IF SUSER_ID('{name}') IS NOT NULL DROP LOGIN [{name}]")
            cur.execute(f"CREATE LOGIN [{name}] WITH PASSWORD = '{password}', CHECK_POLICY = OFF")
            cur.execute(f"CREATE USER [{name}] FOR LOGIN [{name}]")
        for grant in grants:
            cur.execute(grant)
    finally:
        admin.close()


def _drop_account(engine: str, url: str, name: str) -> None:
    admin = _admin(engine, url)
    try:
        cur = admin.cursor()
        if engine == "mysql":
            cur.execute(f"DROP USER IF EXISTS '{name}'@'%'")
        else:
            cur.execute(f"IF SUSER_ID('{name}') IS NOT NULL DROP LOGIN [{name}]")
    finally:
        admin.close()


def test_a_live_capture_equals_the_recorded_catalog(engine) -> None:
    name, _url, account = engine
    capture = {"mysql": GOLDENS.capture_mysql_pilot, "mssql": GOLDENS.capture_mssql_pilot}[name]
    recorded = json.loads((_GOLDEN / f"catalog_{name}.json").read_text("utf-8"))
    assert json.loads(json.dumps(capture(account), default=str)) == recorded


async def test_the_connector_discovers_the_golden(engine) -> None:
    name, _url, account = engine
    report = await _connector(name, account).discover(DiscoveryBudget())
    rendered = json.dumps(to_data(report), indent=2, sort_keys=True, ensure_ascii=False) + "\n"
    assert rendered == (_GOLDEN / f"discovery_{name}.json").read_text("utf-8")


async def test_the_discovery_account_is_healthy_and_cannot_write(engine) -> None:
    name, _url, account = engine
    health = await _connector(name, account).health()
    assert health.status is HealthStatus.HEALTHY
    assert health.write_capable is False and health.findings == ()


async def test_a_write_path_is_a_finding(engine) -> None:
    name, url, account = engine
    user, password = "aura_writer", "Aa1-" + uuid.uuid4().hex
    grants = {
        "mysql": [f"GRANT SELECT, INSERT ON `{GOLDENS.ENGINE_DATABASE}`.leave_requests TO 'aura_writer'@'%'"],
        "mssql": ["GRANT SELECT, INSERT ON dbo.leave_requests TO [aura_writer]"],
    }[name]
    _create_account(name, url, user, password, grants)
    try:
        health = await _connector(name, account, user=user, password=password).health()
    finally:
        _drop_account(name, url, user)
    assert health.status is HealthStatus.HEALTHY
    assert health.write_capable is True and "table_write" in health.findings


async def test_hidden_grants_are_an_issue_not_a_failure(engine) -> None:
    name, url, account = engine
    user, password = "aura_narrow", "Aa1-" + uuid.uuid4().hex
    grants = {  # reads the data, but not the grant tables / other principals' metadata
        "mysql": [f"GRANT SELECT ON `{GOLDENS.ENGINE_DATABASE}`.* TO 'aura_narrow'@'%'"],
        "mssql": ["GRANT SELECT ON SCHEMA::dbo TO [aura_narrow]"],
    }[name]
    _create_account(name, url, user, password, grants)
    try:
        report = await _connector(name, account, user=user, password=password).discover(DiscoveryBudget())
    finally:
        _drop_account(name, url, user)
    codes = {issue.code for issue in report.issues}
    assert "grants_not_visible" in codes
    assert not any(a.locator.startswith("grant:") for a in report.artifacts)
    assert any(a.locator.startswith("table:") for a in report.artifacts)
    if name == "mssql":
        assert "check_definition_hidden" in codes


async def test_budgets_truncate_on_a_live_capture(engine) -> None:
    name, _url, account = engine
    report = await _connector(name, account).discover(DiscoveryBudget(max_objects=3, sample_rows=0))
    assert report.truncated is True
    assert any(issue.code == "budget_exhausted" for issue in report.issues)
