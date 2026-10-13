"""Files connector (AP10): CSV and JSON exports, read as tables.

A ``files`` connector's endpoint is a directory, relative to the operator-configured root
(``AURA_ADAPTATION_FILES_ROOT``). Each top-level ``*.csv`` or ``*.json`` file in it is one table in
the ``files`` schema, named after the file. With no root configured the kind is not offered at all,
so a Super Admin cannot point AURA at an arbitrary path on its own host.

Discovery builds the same *catalog snapshot* the PostgreSQL connector captures -- relations,
columns, keys, profiles -- and maps it with :func:`postgres_catalog.artifacts_from_catalog`, so the
normaliser, planner, compiler and gate treat a spreadsheet export exactly like a table. Reads run
the compiled SQL templates through the in-memory evaluator the gate's replica uses
(:class:`InMemoryReadConnector`), so a template means the same thing against a file as against
PostgreSQL. Nothing here renders or executes SQL text.

What is inferred, and how strictly:

* **Column types** from every value in the file: a column is ``boolean``, ``bigint``, ``numeric``,
  ``date`` or ``timestamp`` only when *every* non-empty value parses as one; anything mixed is
  ``text``. A number with a leading zero (``00123``) stays text -- it is a code, not a quantity.
* **Primary key**: a column named ``id`` or ``<table>_id`` whose values are all present and
  unique. **Foreign key**: a column ``<name>_id`` whose non-empty values all occur in the primary
  key of a table called ``<name>`` or ``<name>s``. Both are evidence-based: one duplicate or one
  dangling value and the key is not claimed.
* **Profiles** over the first ``sample_rows`` rows, with the PostgreSQL connector's pattern rules,
  so a column classifies identically from either source. Values only with
  ``DiscoveryBudget.sample_values``.

Files are never written: they are opened read-only, symlinks are refused, and every path is checked
to stay inside the root after resolution. ``health()`` therefore reports ``write_capable=False``.
"""

from __future__ import annotations

import csv
import io
import json
import os
import re
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping, Sequence

from ...domain.connectors import ConnectorDescriptor, ConnectorHealth, Credential, HealthStatus
from ...domain.discovery import DiscoveryBudget, DiscoveryReport
from ...domain.errors import DependencyUnavailableError, ToolExecutionError
from ...domain.templates import ReadTemplate
from ...observability.logging import get_logger
from .memory import InMemoryReadConnector
from .postgres_catalog import _CATEGORICAL_MAX_DISTINCT, _MAX_VALUE_CHARS, _PATTERNS, artifacts_from_catalog

__all__ = ["FilesConnectorFactory", "FilesConnector", "FILES_SCHEMA", "load_tables"]

logger = get_logger("adapters.connectors.files")

#: The schema every file table lives in; SQL templates address ``files.<table>``.
FILES_SCHEMA = "files"
CATALOG_EXTRACTOR = "files_catalog"
PROFILE_EXTRACTOR = "files_profile"

_SUFFIXES = (".csv", ".json")
#: A file larger than this is skipped with an issue rather than read into memory.
MAX_FILE_BYTES = 20 * 1024 * 1024
#: Rows read from one file; the rest is reported as truncation, never silently dropped.
MAX_ROWS = 100_000
_MAX_IDENT = 63
#: The PostgreSQL connector's pattern rules, as Python regexes (its POSIX class spelled ``\s``).
_PY_PATTERNS = tuple((name, re.compile(regex.replace("[:space:]", r"\s"))) for name, regex in _PATTERNS)

_INTEGER = re.compile(r"^-?(0|[1-9][0-9]*)$")
_NUMBER = re.compile(r"^-?(0|[1-9][0-9]*)(\.[0-9]+)?([eE][-+]?[0-9]+)?$")
_DATE = re.compile(r"^[0-9]{4}-[0-9]{2}-[0-9]{2}$")
_TIMESTAMP = re.compile(r"^[0-9]{4}-[0-9]{2}-[0-9]{2}[T ][0-9]{2}:[0-9]{2}(:[0-9]{2}(\.[0-9]+)?)?(Z|[+-][0-9]{2}:?[0-9]{2})?$")
_BOOLEAN = {"true": True, "false": False}
_INT32 = 2**31 - 1


@dataclass(frozen=True, slots=True)
class _Table:
    name: str
    columns: tuple[str, ...]
    #: Postgres type name per column, as the catalog would report it.
    types: tuple[str, ...]
    rows: tuple[Mapping[str, Any], ...]
    truncated: bool


def _issue(code: str, locator: str) -> dict[str, str]:
    return {"code": code, "locator": locator[:512]}


def _ident_ok(name: str) -> bool:
    return 0 < len(name) <= _MAX_IDENT and not any(ord(c) < 32 for c in name)


def resolve_directory(root: str | None, endpoint: str) -> Path | None:
    """The connector's directory, or ``None`` when it is not a directory inside ``root``."""
    if not root:
        return None
    base = Path(root).resolve()
    relative = Path(endpoint)
    if relative.is_absolute() or ".." in relative.parts:
        return None
    target = (base / relative).resolve()
    if target != base and base not in target.parents:
        return None
    return target if target.is_dir() else None


def _infer_type(values: Sequence[Any]) -> str:
    present = [v for v in values if v is not None]
    if not present:
        return "text"
    if all(isinstance(v, bool) for v in present):
        return "boolean"
    if all(isinstance(v, int) and not isinstance(v, bool) for v in present):
        return "integer" if all(abs(v) <= _INT32 for v in present) else "bigint"
    if all(isinstance(v, (int, float)) and not isinstance(v, bool) for v in present):
        return "numeric"
    if all(isinstance(v, str) and _DATE.match(v) for v in present):
        return "date"
    if all(isinstance(v, str) and _TIMESTAMP.match(v) for v in present):
        has_zone = all(v.endswith("Z") or re.search(r"[+-][0-9]{2}:?[0-9]{2}$", v) for v in present)
        return "timestamp with time zone" if has_zone else "timestamp without time zone"
    return "text"


def _cell(text: str) -> Any:
    """A CSV cell as its natural JSON value: empty -> None, then bool, int, float, else text."""
    if text == "":
        return None
    lowered = text.lower()
    if lowered in _BOOLEAN:
        return _BOOLEAN[lowered]
    if _INTEGER.match(text):
        return int(text)
    if _NUMBER.match(text):
        return float(text)
    return text


def _coerce(column_type: str, value: Any) -> Any:
    """A value under its column's inferred type; mixed columns are text, so their cells become text."""
    if value is None or column_type != "text" or isinstance(value, str):
        return value
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (dict, list)):
        return json.dumps(value, ensure_ascii=False, sort_keys=True)
    return str(value)


def _read_csv(data: bytes) -> tuple[list[str], list[list[Any]]]:
    text = data.decode("utf-8-sig")
    reader = csv.reader(io.StringIO(text, newline=""))
    header = next(reader, None) or []
    rows = [[_cell(cell) for cell in row] for row in reader if any(cell.strip() for cell in row)]
    return [h.strip() for h in header], rows


def _read_json(data: bytes) -> tuple[list[str], list[list[Any]]]:
    parsed = json.loads(data.decode("utf-8-sig"))
    if not isinstance(parsed, list) or not all(isinstance(item, dict) for item in parsed):
        raise ValueError("a JSON table file must be an array of objects")
    header: list[str] = []
    for item in parsed:
        for key in item:
            if key not in header:
                header.append(str(key))
    return header, [[item.get(key) for key in header] for item in parsed]


def _load(path: Path, name: str, issues: list[dict[str, str]]) -> _Table | None:
    locator = f"table:{FILES_SCHEMA}.{name}"
    try:
        size = path.stat().st_size
    except OSError:
        issues.append(_issue("unreadable", locator))
        return None
    if size > MAX_FILE_BYTES:
        issues.append(_issue("file_too_large", locator))
        return None
    try:
        flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
        fd = os.open(path, flags)
        with os.fdopen(fd, "rb") as handle:
            data = handle.read(MAX_FILE_BYTES + 1)
        header, raw_rows = _read_csv(data) if path.suffix.lower() == ".csv" else _read_json(data)
    except (OSError, UnicodeDecodeError, ValueError, csv.Error):
        issues.append(_issue("unparseable", locator))
        return None
    keep = [i for i, h in enumerate(header) if _ident_ok(h) and header.index(h) == i]
    if len(keep) != len(header):
        issues.append(_issue("columns_skipped", locator))
    if not keep:
        issues.append(_issue("no_columns", locator))
        return None
    truncated = len(raw_rows) > MAX_ROWS
    if truncated:
        issues.append(_issue("rows_truncated", locator))
        raw_rows = raw_rows[:MAX_ROWS]
    columns = tuple(header[i] for i in keep)
    cells = [[row[i] if i < len(row) else None for i in keep] for row in raw_rows]
    types = tuple(_infer_type([row[j] for row in cells]) for j in range(len(columns)))
    rows = tuple({col: _coerce(types[j], row[j]) for j, col in enumerate(columns)} for row in cells)
    return _Table(name, columns, types, rows, truncated)


def load_tables(directory: Path) -> tuple[list[_Table], list[dict[str, str]]]:
    """Every readable table file directly in ``directory``, sorted by name, plus the issues."""
    issues: list[dict[str, str]] = []
    tables: list[_Table] = []
    seen: set[str] = set()
    for path in sorted(directory.iterdir(), key=lambda p: p.name):
        if path.suffix.lower() not in _SUFFIXES:
            continue
        name = path.stem
        locator = f"table:{FILES_SCHEMA}.{name}"
        if path.is_symlink():
            issues.append(_issue("symlink_skipped", locator))
            continue
        if not path.is_file():
            continue
        if not _ident_ok(name) or name in seen:
            issues.append(_issue("name_skipped", locator))
            continue
        seen.add(name)
        table = _load(path, name, issues)
        if table is not None:
            tables.append(table)
    return tables, issues


def _primary_key(table: _Table) -> str | None:
    for candidate in ("id", f"{table.name}_id"):
        if candidate not in table.columns:
            continue
        values = [row[candidate] for row in table.rows]
        if values and all(v is not None for v in values) and len(set(map(json.dumps, values))) == len(values):
            return candidate
    return None


def _foreign_keys(table: _Table, keys: Mapping[str, tuple[str, set[str]]]) -> list[dict[str, Any]]:
    out = []
    for column in table.columns:
        if not column.endswith("_id") or column == "id" or column == f"{table.name}_id":
            continue
        stem = column[:-3]
        for target in (stem, f"{stem}s"):
            if target not in keys or target == table.name:
                continue
            key_column, key_values = keys[target]
            values = {json.dumps(row[column]) for row in table.rows if row[column] is not None}
            if values and values <= key_values:
                out.append({"schema": FILES_SCHEMA, "table": table.name, "name": f"{table.name}_{column}_fkey",
                            "type": "f", "columns": [column], "ref_schema": FILES_SCHEMA, "ref_table": target,
                            "ref_columns": [key_column]})
            break
    return out


def _profile(table: _Table, j: int, budget: DiscoveryBudget) -> dict[str, Any]:
    column, data_type = table.columns[j], table.types[j]
    sample = [row[column] for row in table.rows[: budget.sample_rows]]
    present = [v for v in sample if v is not None]
    texts = [v if isinstance(v, str) else json.dumps(v) for v in present]
    textual = data_type == "text"
    patterns = {name: sum(1 for t in texts if rx.search(t)) for name, rx in _PY_PATTERNS} if textual else {}
    distinct = len(set(texts))
    profile = {
        "schema": FILES_SCHEMA, "table": table.name, "column": column, "data_type": data_type,
        "sampled_rows": len(sample), "non_null": len(present), "distinct": distinct,
        "patterns": patterns, "values": None,
    }
    if budget.sample_values and textual and 0 < distinct <= _CATEGORICAL_MAX_DISTINCT:
        profile["values"] = sorted({t[:_MAX_VALUE_CHARS] for t in texts})
    return profile


def catalog_snapshot(tables: Sequence[_Table], issues: Sequence[Mapping[str, str]],
                     budget: DiscoveryBudget) -> dict[str, Any]:
    """The PostgreSQL connector's snapshot shape, built from file tables."""
    keys: dict[str, tuple[str, set[str]]] = {}
    constraints: list[dict[str, Any]] = []
    for table in tables:
        pk = _primary_key(table)
        if pk is not None:
            keys[table.name] = (pk, {json.dumps(row[pk]) for row in table.rows})
            constraints.append({"schema": FILES_SCHEMA, "table": table.name, "name": f"{table.name}_pkey",
                                "type": "p", "columns": [pk]})
    for table in tables:
        constraints.extend(_foreign_keys(table, keys))
    profiles: list[dict[str, Any]] = []
    remaining = budget.max_profiled_columns if budget.sample_rows else 0
    snapshot_issues = list(issues)
    for table in tables:
        if remaining <= 0:
            if budget.sample_rows:
                snapshot_issues.append(_issue("profile_budget_exhausted", f"table:{FILES_SCHEMA}.{table.name}"))
            continue
        count = min(remaining, len(table.columns))
        profiles.extend(_profile(table, j, budget) for j in range(count))
        remaining -= count
    return {
        "relations": [{"schema": FILES_SCHEMA, "name": t.name, "kind": "r", "comment": None, "readable": True}
                      for t in tables],
        "columns": [{"schema": FILES_SCHEMA, "table": t.name, "name": col, "position": j + 1,
                     "data_type": t.types[j], "not_null": bool(t.rows) and all(r[col] is not None for r in t.rows),
                     "comment": None, "is_enum": False, "type_schema": "pg_catalog", "type_name": t.types[j],
                     "has_default": False}
                    for t in tables for j, col in enumerate(t.columns)],
        "constraints": constraints,
        "enums": [],
        "grants": [],
        "profiles": profiles,
        "issues": snapshot_issues,
        "truncated": any(t.truncated for t in tables) or any(i["code"] == "profile_budget_exhausted"
                                                             for i in snapshot_issues),
    }


class FilesConnectorFactory:
    def __init__(self, *, root: str | None) -> None:
        self._root = root or None

    @property
    def available(self) -> bool:
        return self._root is not None

    async def open(self, descriptor: ConnectorDescriptor, credential: Credential | None) -> "FilesConnector":
        return FilesConnector(descriptor, root=self._root)


class FilesConnector:
    def __init__(self, descriptor: ConnectorDescriptor, *, root: str | None) -> None:
        self.descriptor = descriptor
        self._root = root

    def _directory(self) -> tuple[Path | None, str]:
        if not self._root:
            return None, "files_disabled"
        directory = resolve_directory(self._root, self.descriptor.endpoint)
        return (directory, "ok") if directory is not None else (None, "invalid_endpoint")

    async def health(self) -> ConnectorHealth:
        started = time.perf_counter()
        directory, reason = self._directory()
        latency = (time.perf_counter() - started) * 1000.0
        if directory is None:
            return ConnectorHealth(HealthStatus.UNHEALTHY, reason, latency_ms=latency)
        if not os.access(directory, os.R_OK | os.X_OK):
            return ConnectorHealth(HealthStatus.UNHEALTHY, "not_readable", latency_ms=latency)
        # AURA opens these files read-only and has no code path that writes them.
        return ConnectorHealth(HealthStatus.HEALTHY, "ok", write_capable=False, latency_ms=latency)

    def _tables(self) -> tuple[list[_Table], list[dict[str, str]]]:
        directory, reason = self._directory()
        if directory is None:
            raise DependencyUnavailableError(
                "the files directory is not available", dependency="connector",
                details={"connector": self.descriptor.id, "reason": reason},
            )
        return load_tables(directory)

    async def discover(self, budget: DiscoveryBudget) -> DiscoveryReport:
        tables, issues = self._tables()
        snapshot = catalog_snapshot(tables, issues, budget)
        return artifacts_from_catalog(self.descriptor.id, snapshot, budget, extractor="files",
                                      catalog_extractor=CATALOG_EXTRACTOR, profile_extractor=PROFILE_EXTRACTOR)

    async def read(self, template: ReadTemplate, arguments: Mapping[str, Any]) -> list[dict[str, Any]]:
        if template.connector != self.descriptor.id:
            raise ToolExecutionError("this template does not belong to this connector", tool=template.id)
        try:
            tables, _ = self._tables()
        except DependencyUnavailableError:
            raise ToolExecutionError("the files directory is not available right now", tool=template.id) from None
        replica = InMemoryReadConnector(
            self.descriptor.id, {(FILES_SCHEMA, t.name): t.rows for t in tables}, row_cap=self.descriptor.row_cap,
        )
        return await replica.read(template, arguments)

    async def aclose(self) -> None:
        return None
