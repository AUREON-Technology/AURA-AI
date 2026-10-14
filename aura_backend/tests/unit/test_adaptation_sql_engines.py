"""AP10: MySQL and SQL Server discovery, behind the same port as PostgreSQL.

What the default suite can prove without a server, a network or a model:

* **Goldens.** ``golden/catalog_mysql.json`` / ``catalog_mssql.json`` are the *raw* catalog rows a
  live capture of the HR pilot returned (MySQL 8.0, SQL Server 2022, each loaded from its own
  dialect's ``schema.sql`` and seeded with ``rows.json``). The pure mapping over them renders
  exactly the committed ``discovery_mysql.json`` / ``discovery_mssql.json``.
* **Parity.** The same system written for three engines is the same twin: the same entities,
  attributes, keys, states and roles, and an identical capability plan and agent set.
* **The mapping's edges**: type names, CHECK text (MySQL's escaping and charset introducers, SQL
  Server's OR chains and LIKE brackets), grants, privilege findings, and the TLS host check.
* **Discovery only.** A blueprint compiles over either engine; every read is dropped with a reason,
  because neither dialect has templated reads yet.

``tests/load/test_adaptation_discovery_engines.py`` proves the captures against live servers.
"""

from __future__ import annotations

import datetime
import importlib.util
import ipaddress
import json
from pathlib import Path
from typing import Any

import pytest

from aura_core.adaptation.compiler import compile_blueprint
from aura_core.adaptation.normalize import _check_values
from aura_core.adaptation.planner import CapabilityRegistry, plan_capabilities
from aura_core.adaptation.twin import build_twin
from aura_core.adapters.connectors import mssql, mysql
from aura_core.adapters.connectors.postgres_catalog import artifacts_from_catalog
from aura_core.domain.ausm import ElementKind
from aura_core.domain.blueprint import ConnectorRef
from aura_core.domain.connectors import ConnectorDescriptor, ConnectorKind, Credential, HealthStatus, IdentityMode
from aura_core.domain.discovery import DiscoveryBudget
from aura_core.domain.errors import DependencyUnavailableError, ToolExecutionError
from aura_core.domain.untrusted import UntrustedText

BACKEND = Path(__file__).resolve().parents[2]
GOLDEN = BACKEND / "tests" / "fixtures" / "pilots" / "hr" / "golden"


def _goldens():
    spec = importlib.util.spec_from_file_location("pilot_goldens", BACKEND / "scripts" / "pilot_discovery_goldens.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


GOLDENS = _goldens()


def _raw(engine: str) -> dict[str, Any]:
    return json.loads((GOLDEN / f"catalog_{engine}.json").read_text("utf-8"))


def _snapshot(engine: str) -> dict[str, Any]:
    if engine == "postgres":
        return json.loads((GOLDEN / "catalog.json").read_text("utf-8"))
    return {"mysql": mysql.snapshot_from_mysql, "mssql": mssql.snapshot_from_mssql}[engine](_raw(engine))


def _report(engine: str):
    return artifacts_from_catalog("hr-db", _snapshot(engine), DiscoveryBudget(), extractor=engine)


def _twin(engine: str):
    return build_twin("p0", 1, [_report(engine)]).twin


def _local(locator: str) -> str:
    """``column:aura_pilot_hr.employees.status`` -> ``employees.status``: the schema differs by
    engine (``public``, the MySQL database, ``dbo``); everything after it must not."""
    _kind, _, rest = locator.partition(":")
    return rest.split(".", 1)[1] if "." in rest else rest


ENGINES = ("mysql", "mssql")


# ------------------------------------------------------------------------------- goldens


@pytest.mark.parametrize("engine", ENGINES)
def test_committed_golden_matches_the_mapping(engine: str) -> None:
    rendered = {"mysql": GOLDENS.render_mysql, "mssql": GOLDENS.render_mssql}[engine]("hr")
    assert rendered == (GOLDEN / f"discovery_{engine}.json").read_text("utf-8")


@pytest.mark.parametrize("engine", ENGINES)
def test_the_recorded_capture_is_complete_and_clean(engine: str) -> None:
    raw = _raw(engine)
    assert raw["issues"] == [] and raw["truncated"] is False
    assert raw["own_grantees"] == [GOLDENS.DISCOVERY_ACCOUNT]
    # Seeded row values never reach the report; schema-declared values (CHECK, ENUM) do.
    rendered = (GOLDEN / f"discovery_{engine}.json").read_text("utf-8")
    assert "@hr.example" not in rendered and "Employee 1" not in rendered


# ------------------------------------------------------------------------------- parity


class TestParityWithPostgres:
    """The HR system written for MySQL and for SQL Server is the twin PostgreSQL's is."""

    @pytest.mark.parametrize("engine", ENGINES)
    def test_the_catalogs_agree(self, engine: str) -> None:
        pg, other = _snapshot("postgres"), _snapshot(engine)
        assert [(r["name"], r["kind"]) for r in other["relations"]] == [(r["name"], r["kind"]) for r in pg["relations"]]
        assert [(c["table"], c["name"], c["position"]) for c in other["columns"]] == \
               [(c["table"], c["name"], c["position"]) for c in pg["columns"]]
        assert {(g["grantee"], g["table"], g["privilege"]) for g in other["grants"]} == \
               {(g["grantee"], g["table"], g["privilege"]) for g in pg["grants"]}
        keys = {(c["table"], c["type"], tuple(c["columns"]), c["ref_table"], tuple(c["ref_columns"]))
                for c in pg["constraints"] if c["type"] in "puf"}
        assert {(c["table"], c["type"], tuple(c["columns"]), c["ref_table"], tuple(c["ref_columns"]))
                for c in other["constraints"] if c["type"] in "puf"} == keys
        profiled = {(p["table"], p["column"], p["sampled_rows"], p["non_null"], p["distinct"]) for p in pg["profiles"]}
        assert {(p["table"], p["column"], p["sampled_rows"], p["non_null"], p["distinct"])
                for p in other["profiles"]} == profiled

    @pytest.mark.parametrize("engine", ENGINES)
    def test_the_twins_agree(self, engine: str) -> None:
        pg, other = _twin("postgres"), _twin(engine)
        for kind in (ElementKind.ENTITY, ElementKind.ATTRIBUTE, ElementKind.STATE, ElementKind.ROLE,
                     ElementKind.RELATION, ElementKind.PERMISSION, ElementKind.METRIC):
            assert sorted(_local(e.locator) for e in other.of_kind(kind)) == \
                   sorted(_local(e.locator) for e in pg.of_kind(kind)), kind
        states = {_local(e.locator) for e in other.of_kind(ElementKind.STATE)}
        assert {"employees.status=on_leave", "leave_requests.leave_type=parental"} <= states

    @pytest.mark.parametrize("engine", ENGINES)
    def test_the_plans_agree(self, engine: str) -> None:
        registry = CapabilityRegistry.from_directory()
        pg, other = plan_capabilities(_twin("postgres"), registry), plan_capabilities(_twin(engine), registry)

        def selected(plan):
            return sorted((s.capability, _local(s.target)) for s in plan.selected)

        assert selected(other) == selected(pg) and len(selected(pg)) > 15
        assert sorted(a.id for a in other.agents) == sorted(a.id for a in pg.agents)

    @pytest.mark.parametrize("engine", ENGINES)
    def test_discovered_text_stays_untrusted(self, engine: str) -> None:
        schema = _snapshot(engine)["relations"][0]["schema"]
        salary = _report(engine).get(f"column:{schema}.employees.salary")
        assert isinstance(salary.description, UntrustedText)
        assert "Ignore all previous instructions" in salary.description.raw()
        assert "Ignore" not in str(salary.description)


# ------------------------------------------------------------------------------- MySQL mapping


class TestMySqlMapping:
    @pytest.mark.parametrize("column_type, canonical", [
        ("int", "integer"), ("int unsigned", "integer"), ("bigint", "bigint"), ("tinyint(1)", "boolean"),
        ("tinyint(4)", "smallint"), ("decimal(10,2)", "numeric(10,2)"), ("varchar(254)", "character varying(254)"),
        ("char(3)", "character(3)"), ("longtext", "text"), ("blob", "bytea"), ("datetime", "timestamp without time zone"),
        ("timestamp", "timestamp with time zone"), ("date", "date"), ("json", "json"), ("double", "double precision"),
    ])
    def test_types_take_the_twins_canonical_names(self, column_type: str, canonical: str) -> None:
        assert mysql.canonical_type(column_type) == canonical

    def test_enum_labels_keep_order_and_quotes(self) -> None:
        assert mysql.enum_labels("enum('annual','it''s','sick')") == ["annual", "it's", "sick"]

    @pytest.mark.parametrize("clause, definition", [
        # information_schema escapes once more: every quote arrives as \'.
        (r"(`status` in (_utf8mb4\'active\',_utf8mb4\'on_leave\',_utf8mb4\'terminated\'))",
         "CHECK ((status in ('active','on_leave','terminated')))"),
        (r"(`x` in (_utf8mb4\'it\\\'s\',_latin1\'a _latin1\\\'b\'))", "CHECK ((x in ('it''s','a _latin1''b')))"),
        (r"regexp_like(`employee_code`,_utf8mb4\'^E[0-9]{4}$\')", "CHECK (regexp_like(employee_code,'^E[0-9]{4}$'))"),
        ("(`ends_on` >= `starts_on`)", "CHECK ((ends_on >= starts_on))"),
    ])
    def test_check_clauses_read_as_postgresql_shaped_text(self, clause: str, definition: str) -> None:
        assert mysql.normalize_check(clause) == definition

    def test_a_value_that_looks_like_an_introducer_is_kept(self) -> None:
        definition = mysql.normalize_check(r"(`status` in (_utf8mb4\'on_leave\',_utf8mb4\'x_y\'))")
        assert _check_values(definition) == ("on_leave", "x_y")

    def _rows(self, **extra: Any) -> dict[str, Any]:
        rows = {
            "database": "app", "own_grantees": ["aura_discovery"], "own_select_schema": True,
            "relations": [{"schema": "app", "name": "orders", "table_type": "BASE TABLE", "comment": ""},
                          {"schema": "app", "name": "open_orders", "table_type": "VIEW", "comment": "VIEW"}],
            "columns": [{"schema": "app", "table": "orders", "name": "id", "position": 1, "data_type": "int",
                         "column_type": "int", "is_nullable": "NO", "column_default": None,
                         "extra": "auto_increment", "comment": ""},
                        {"schema": "app", "table": "orders", "name": "state", "position": 2, "data_type": "enum",
                         "column_type": "enum('new','paid')", "is_nullable": "NO", "column_default": "new",
                         "extra": "", "comment": "Order state"},
                        {"schema": "app", "table": "open_orders", "name": "state", "position": 1,
                         "data_type": "enum", "column_type": "enum('new','paid')", "is_nullable": "NO",
                         "column_default": "new", "extra": "", "comment": ""}],
            "keys": [], "checks": [],
            "schema_grants": [{"user": "reporting", "host": "%", "select_priv": "Y", "insert_priv": "N"},
                              {"user": "aura_discovery", "host": "%", "select_priv": "Y"}],
            "table_grants": [{"user": "clerk", "host": "10.0.0.%", "table_name": "orders",
                              "table_priv": "Select,Insert"},
                             {"user": "clerk", "host": "10.0.0.%", "table_name": "dropped", "table_priv": "Select"}],
        }
        rows.update(extra)
        return rows

    def test_grants_expand_schema_level_and_exclude_the_connectors_own_account(self) -> None:
        snapshot = mysql.snapshot_from_mysql(self._rows())
        assert {(g["grantee"], g["table"], g["privilege"]) for g in snapshot["grants"]} == {
            ("reporting", "orders", "SELECT"), ("reporting", "open_orders", "SELECT"),
            ("clerk@10.0.0.%", "orders", "SELECT"), ("clerk@10.0.0.%", "orders", "INSERT"),
        }

    def test_columns_enums_and_views(self) -> None:
        snapshot = mysql.snapshot_from_mysql(self._rows())
        ident, state, view_state = snapshot["columns"]
        assert ident["has_default"] is False  # AUTO_INCREMENT is identity, not a default
        assert state["is_enum"] and state["data_type"] == "orders_state" and state["has_default"] is True
        assert view_state["has_default"] is False  # a view's column does not default
        assert snapshot["enums"] == [{"schema": "app", "name": "open_orders_state", "labels": ["new", "paid"]},
                                     {"schema": "app", "name": "orders_state", "labels": ["new", "paid"]}]
        view = next(r for r in snapshot["relations"] if r["kind"] == "v")
        assert view["comment"] is None  # MySQL's literal "VIEW", not a description

    def test_readability_follows_the_accounts_own_select(self) -> None:
        rows = self._rows(own_select_schema=False, own_select_tables=["orders"])
        assert {r["name"]: r["readable"] for r in mysql.snapshot_from_mysql(rows)["relations"]} == {
            "orders": True, "open_orders": False}

    @pytest.mark.parametrize("lines, findings", [
        (["GRANT USAGE ON *.* TO `aura`@`%`", "GRANT SELECT ON `hr`.* TO `aura`@`%`",
          "GRANT SELECT ON `mysql`.`db` TO `aura`@`%`"], ()),
        (["GRANT SELECT (`id`, `name`) ON `hr`.`t` TO `aura`@`%`", "GRANT SHOW VIEW ON `hr`.* TO `aura`@`%`"], ()),
        (["GRANT SELECT, INSERT ON `hr`.`leave_requests` TO `w`@`%`"], ("table_write",)),
        (["GRANT `hr_viewer`@`%` TO `w`@`%`"], ("granted_roles",)),
        (["GRANT SELECT ON `hr`.* TO `w`@`%` WITH GRANT OPTION"], ("grant_option",)),
        (["GRANT CREATE, DROP ON `hr`.* TO `w`@`%`"], ("ddl",)),
        (["GRANT ALL PRIVILEGES ON *.* TO `root`@`localhost` WITH GRANT OPTION",
          "GRANT PROXY ON ``@`` TO `root`@`localhost` WITH GRANT OPTION"], ("admin", "grant_option", "table_write")),
        (["GRANT RELOAD, PROCESS ON *.* TO `w`@`%`"], ("admin",)),
    ])
    def test_privilege_findings(self, lines: list[str], findings: tuple[str, ...]) -> None:
        assert mysql.privilege_findings(lines) == findings


# ------------------------------------------------------------------------------- SQL Server mapping


class TestSqlServerMapping:
    @pytest.mark.parametrize("args, canonical", [
        (("int", 4, 10, 0), "integer"), (("bit", 1, 1, 0), "boolean"), (("tinyint", 1, 3, 0), "smallint"),
        (("decimal", 9, 10, 2), "numeric(10,2)"), (("nvarchar", 508, 0, 0), "character varying(254)"),
        (("varchar", 32, 0, 0), "character varying(32)"), (("nvarchar", -1, 0, 0), "text"),
        (("nchar", 6, 0, 0), "character(3)"), (("datetime2", 8, 27, 7), "timestamp without time zone"),
        (("datetimeoffset", 10, 34, 7), "timestamp with time zone"), (("uniqueidentifier", 16, 0, 0), "uuid"),
        (("varbinary", -1, 0, 0), "bytea"), (("money", 8, 19, 4), "numeric(19,4)"),
    ])
    def test_types_take_the_twins_canonical_names(self, args: tuple, canonical: str) -> None:
        assert mssql.canonical_type(*args) == canonical

    @pytest.mark.parametrize("stored, definition", [
        # How SQL Server stores status IN ('active', 'on_leave', 'terminated'): reversed OR chain.
        ("([status]='terminated' OR [status]='on_leave' OR [status]='active')",
         "CHECK (status IN ('active', 'on_leave', 'terminated'))"),
        ("([we]]ird]=N'it''s' OR [we]]ird]='[x]')", "CHECK (\"we]ird\" IN ('[x]', 'it''s'))"),
        # A LIKE pattern's brackets are not identifiers.
        ("([employee_code] like 'E[0-9][0-9][0-9][0-9]')", "CHECK ((employee_code like 'E[0-9][0-9][0-9][0-9]'))"),
        ("([ends_on]>=[starts_on])", "CHECK ((ends_on>=starts_on))"),
    ])
    def test_check_definitions_read_as_postgresql_shaped_text(self, stored: str, definition: str) -> None:
        assert mssql.normalize_check(stored) == definition

    @pytest.mark.parametrize("stored", [
        "([a]='x' OR [b]='y')",                   # two columns: not one closed set
        "([s]='a' OR [s]='b' AND [t]=(1))",       # boolean structure under the OR
        "([s]='a')",                              # a single equality is not a value list
    ])
    def test_anything_but_a_one_column_or_chain_stays_boolean_structure(self, stored: str) -> None:
        assert " IN (" not in mssql.normalize_check(stored)

    def test_the_value_set_reaches_the_normaliser(self) -> None:
        definition = mssql.normalize_check("([leave_type]='parental' OR [leave_type]='unpaid' OR "
                                           "[leave_type]='sick' OR [leave_type]='annual')")
        assert _check_values(definition) == ("annual", "sick", "unpaid", "parental")

    def _rows(self, **extra: Any) -> dict[str, Any]:
        column = {"base_type": "int", "type_name": "int", "is_user_defined": False, "max_length": 4,
                  "precision": 10, "scale": 0, "is_nullable": False, "is_computed": False, "comment": None}
        rows = {
            "own_grantees": ["aura_discovery"],
            "relations": [{"schema": "sales", "name": "orders", "type": "U", "comment": "Orders.", "readable": 1},
                          {"schema": "sales", "name": "open_orders", "type": "V", "comment": None, "readable": 1},
                          {"schema": "ops", "name": "jobs", "type": "U", "comment": None, "readable": 0}],
            "columns": [{**column, "schema": "sales", "table": "orders", "name": "id", "position": 1,
                         "is_identity": True, "has_default": 0},
                        {**column, "schema": "sales", "table": "orders", "name": "qty", "position": 2,
                         "is_identity": False, "has_default": 1},
                        {**column, "schema": "sales", "table": "open_orders", "name": "id", "position": 1,
                         "is_identity": True, "has_default": 0}],
            "keys": [], "foreign_keys": [],
            "checks": [{"schema": "sales", "table": "orders", "name": "orders_qty_check", "definition": None}],
            "grants": [{"grantee": "clerk", "class": 1, "privilege": "INSERT", "schema": "sales", "table": "orders",
                        "grant_schema": None},
                       {"grantee": "analyst", "class": 3, "privilege": "SELECT", "schema": None, "table": None,
                        "grant_schema": "sales"},
                       {"grantee": "auditor", "class": 0, "privilege": "SELECT", "schema": None, "table": None,
                        "grant_schema": None},
                       {"grantee": "aura_discovery", "class": 0, "privilege": "SELECT", "schema": None,
                        "table": None, "grant_schema": None}],
        }
        rows.update(extra)
        return rows

    def test_grants_expand_by_scope_and_exclude_the_connectors_own_user(self) -> None:
        grants = {(g["grantee"], g["schema"], g["table"], g["privilege"])
                  for g in mssql.snapshot_from_mssql(self._rows())["grants"]}
        assert grants == {
            ("clerk", "sales", "orders", "INSERT"),
            ("analyst", "sales", "orders", "SELECT"), ("analyst", "sales", "open_orders", "SELECT"),
            ("auditor", "sales", "orders", "SELECT"), ("auditor", "sales", "open_orders", "SELECT"),
            ("auditor", "ops", "jobs", "SELECT"),
        }

    def test_identity_defaults_readability_and_hidden_checks(self) -> None:
        snapshot = mssql.snapshot_from_mssql(self._rows())
        assert [c["has_default"] for c in snapshot["columns"]] == [False, True, False]
        assert {r["name"]: r["readable"] for r in snapshot["relations"]} == {
            "orders": True, "open_orders": True, "jobs": False}
        check = next(c for c in snapshot["constraints"] if c["type"] == "c")
        assert check["definition"] is None and check["columns"] == []  # needs VIEW DEFINITION
        assert snapshot["enums"] == []

    @pytest.mark.parametrize("rows, findings", [
        ([("server", "CONNECT SQL"), ("server", "VIEW ANY DATABASE"), ("database", "CONNECT"),
          ("database", "SELECT"), ("database", "VIEW DEFINITION"), ("database", "SHOWPLAN")], ()),
        ([("object", "INSERT"), ("object", "UPDATE")], ("table_write",)),
        ([("database", "DELETE")], ("table_write",)),
        ([("object", "EXECUTE")], ("procedure_execute",)),
        ([("schema", "ALTER"), ("database", "CREATE TABLE")], ("ddl",)),
        ([("grant_option", "SELECT")], ("grant_option",)),
        ([("server", "CONTROL SERVER")], ("admin",)),
        ([("database", "IMPERSONATE")], ("admin",)),
    ])
    def test_privilege_findings(self, rows: list[tuple[str, str]], findings: tuple[str, ...]) -> None:
        assert mssql.privilege_findings([{"scope": s, "permission": p} for s, p in rows]) == findings


# ------------------------------------------------------------------------------- TLS host check


def _certificate(*, cn: str | None, dns: tuple[str, ...] = (), ips: tuple[str, ...] = ()):
    from cryptography import x509
    from cryptography.hazmat.primitives import hashes
    from cryptography.hazmat.primitives.asymmetric import ec
    from cryptography.x509.oid import NameOID

    key = ec.generate_private_key(ec.SECP256R1())
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, cn)] if cn else [])
    now = datetime.datetime(2026, 1, 1, tzinfo=datetime.timezone.utc)
    builder = (x509.CertificateBuilder().subject_name(name).issuer_name(name).public_key(key.public_key())
               .serial_number(1).not_valid_before(now).not_valid_after(now + datetime.timedelta(days=1)))
    names = [x509.DNSName(d) for d in dns] + [x509.IPAddress(ipaddress.ip_address(i)) for i in ips]
    if names:
        builder = builder.add_extension(x509.SubjectAlternativeName(names), critical=False)
    return builder.sign(key, hashes.SHA256())


class TestSqlServerHostCheck:
    @pytest.mark.parametrize("cert, host, ok", [
        (dict(cn="db.example.com"), "db.example.com", True),
        (dict(cn="db.example.com"), "DB.Example.com.", True),
        (dict(cn="other.example.com", dns=("db.example.com",)), "db.example.com", True),
        # With a SAN present the CN is not consulted (RFC 6125).
        (dict(cn="db.example.com", dns=("other.example.com",)), "db.example.com", False),
        (dict(cn=None, dns=("*.database.windows.net",)), "srv.database.windows.net", True),
        (dict(cn=None, dns=("*.database.windows.net",)), "a.srv.database.windows.net", False),
        (dict(cn=None, dns=("*.com",)), "example.com", False),
        (dict(cn=None, dns=("db*.example.com",)), "db1.example.com", False),
        (dict(cn=None, ips=("10.0.0.5",)), "10.0.0.5", True),
        (dict(cn=None, dns=("10.0.0.5",)), "10.0.0.5", False),
        (dict(cn="10.0.0.5"), "10.0.0.5", False),
    ])
    def test_host_matching(self, cert: dict, host: str, ok: bool) -> None:
        assert mssql.host_matches(_certificate(**cert), host) is ok

    def test_the_replacement_fails_closed(self) -> None:
        class Broken:
            def to_cryptography(self):
                raise ValueError("garbled")

        assert mssql._validate_host(Broken(), b"db.example.com") is False

    def test_it_replaces_python_tds_own_check(self) -> None:
        pytest.importorskip("pytds")
        import pytds.tls

        original = pytds.tls.validate_host
        try:
            mssql._install_host_check()
            assert pytds.tls.validate_host is mssql._validate_host
        finally:
            pytds.tls.validate_host = original


# ------------------------------------------------------------------------------- connectors


def _descriptor(kind: ConnectorKind, endpoint: str, **options: Any) -> ConnectorDescriptor:
    return ConnectorDescriptor(id="hr-db", kind=kind, identity_mode=IdentityMode.SERVICE_ACCOUNT,
                               display_name="HR", endpoint=endpoint, options=options)


class TestConnectorPreflight:
    """Everything refused before a socket is opened."""

    @pytest.mark.parametrize("kind, connector, endpoint, options, reason", [
        (ConnectorKind.MYSQL, mysql.MySqlConnector, "postgres://h/db", {"user": "u"}, "invalid_endpoint"),
        (ConnectorKind.MYSQL, mysql.MySqlConnector, "mysql://h:3306/", {"user": "u"}, "invalid_endpoint"),
        (ConnectorKind.MYSQL, mysql.MySqlConnector, "mysql://h/db", {}, "invalid_options"),
        (ConnectorKind.MYSQL, mysql.MySqlConnector, "mysql://h/db", {"user": "u", "tls": "prefer"}, "invalid_options"),
        (ConnectorKind.SQL_SERVER, mssql.SqlServerConnector, "mysql://h/db", {"user": "u"}, "invalid_endpoint"),
        (ConnectorKind.SQL_SERVER, mssql.SqlServerConnector, "mssql://h/a/b", {"user": "u"}, "invalid_endpoint"),
        (ConnectorKind.SQL_SERVER, mssql.SqlServerConnector, "mssql://h/db", {"user": "u", "tls": "require"},
         "invalid_options"),
        (ConnectorKind.SQL_SERVER, mssql.SqlServerConnector, "sqlserver://h/db", {"user": "u", "ca_file": 3},
         "invalid_options"),
    ])
    async def test_misconfiguration_is_unhealthy_with_a_reason(self, kind, connector, endpoint, options, reason) -> None:
        conn = connector(_descriptor(kind, endpoint, **options), Credential("pw"))
        health = await conn.health()
        assert health.status is HealthStatus.UNHEALTHY and health.reason == reason
        with pytest.raises(DependencyUnavailableError):
            await conn.discover(DiscoveryBudget())

    @pytest.mark.parametrize("kind, connector, endpoint", [
        (ConnectorKind.MYSQL, mysql.MySqlConnector, "mysql://h/db"),
        (ConnectorKind.SQL_SERVER, mssql.SqlServerConnector, "mssql://h/db"),
    ])
    async def test_reads_are_not_offered_yet(self, kind, connector, endpoint) -> None:
        from aura_core.domain.templates import ReadTemplate, SqlColumn, SqlSelect, TemplateKind

        template = ReadTemplate("t", "hr-db", TemplateKind.SQL, sql=SqlSelect(
            schema="hr", table="employees", columns=(SqlColumn("id", "id"),)))
        with pytest.raises(ToolExecutionError):
            await connector(_descriptor(kind, endpoint, user="u"), Credential("pw")).read(template, {})


class TestDiscoveryOnly:
    @pytest.mark.parametrize("engine, kind", [("mysql", ConnectorKind.MYSQL), ("mssql", ConnectorKind.SQL_SERVER)])
    def test_a_blueprint_compiles_and_every_read_is_dropped_with_a_reason(self, engine, kind) -> None:
        twin = _twin(engine)
        plan = plan_capabilities(twin, CapabilityRegistry.from_directory())
        compiled = compile_blueprint(twin, plan, version=1, connectors=[
            ConnectorRef("hr-db", kind, IdentityMode.SERVICE_ACCOUNT)])
        assert compiled.blueprint.templates == () and compiled.blueprint.tools == ()
        reasons = {d.reason for d in compiled.dropped if d.kind == "capability"}
        assert reasons == {"sql_dialect_unsupported"}
        assert {c.id for c in compiled.blueprint.integrations} == {"hr-db"}

    def test_the_container_offers_both_kinds(self, monkeypatch) -> None:
        from cryptography.fernet import Fernet

        from aura_core.config.settings import load_settings
        from aura_core.container import Container

        monkeypatch.setenv("AURA_ADAPTATION_ENABLED", "true")
        monkeypatch.setenv("AURA_SECRETS_ENCRYPTION_KEY", Fernet.generate_key().decode())
        registry = Container(load_settings(refresh=True, validate=False)).connectors
        assert {ConnectorKind.MYSQL, ConnectorKind.SQL_SERVER} <= registry.supported_kinds
