"""Golden Project Twins and twin diffs for the Track AP reference pilots (AP4).

Run from the repo root (no database, no model):

    python aura_backend/scripts/pilot_twin_goldens.py

For each pilot it builds the twin from the committed discovery goldens (Postgres + OpenAPI) and
writes ``golden/twin.json``. For the ``hr`` pilot it also applies three schema changes to the
recorded catalog -- the same kind of change a real system goes through -- and writes the diff
of each against the base twin:

* ``diff_add.json``: a new column (``employees.badge_number``) and a new table (``trainings``);
* ``diff_drop.json``: a dropped column (``employees.salary``) and a dropped table (``assets``);
* ``diff_rename.json``: a renamed column (``employees.full_name`` -> ``display_name``) and a
  renamed table (``attendance`` -> ``time_entries``).

``tests/unit/test_adaptation_twin.py`` fails when a committed golden differs from what this
renders. The twins are built without a labeling model: the goldens *are* the degraded mode.
"""

from __future__ import annotations

import copy
import json
import sys
from pathlib import Path
from typing import Any, Callable

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "aura_backend"))

from aura_core.adapters.connectors.openapi import artifacts_from_openapi, parse_spec  # noqa: E402
from aura_core.adapters.connectors.postgres_catalog import artifacts_from_catalog  # noqa: E402
from aura_core.adaptation.twin import build_twin, diff_twins  # noqa: E402
from aura_core.domain.ausm import ProjectTwin  # noqa: E402
from aura_core.domain.discovery import DiscoveryBudget, DiscoveryReport  # noqa: E402
from aura_core.domain.serialization import to_data  # noqa: E402

PILOTS_DIR = ROOT / "aura_backend" / "tests" / "fixtures" / "pilots"
PILOTS = ("hr", "ecommerce")
PROJECT_ID = "p0"


def _dump(data: object) -> str:
    return json.dumps(data, indent=2, sort_keys=True, ensure_ascii=False) + "\n"


def catalog(pilot: str) -> dict[str, Any]:
    return json.loads((PILOTS_DIR / pilot / "golden" / "catalog.json").read_text(encoding="utf-8"))


def openapi_report(pilot: str) -> DiscoveryReport:
    for name in ("openapi.yaml", "openapi.json"):
        path = PILOTS_DIR / pilot / name
        if path.exists():
            return artifacts_from_openapi(f"{pilot}-api", parse_spec(path.read_bytes()), DiscoveryBudget())
    raise FileNotFoundError(pilot)


def twin_for(pilot: str, version: int = 1, cat: dict[str, Any] | None = None) -> ProjectTwin:
    postgres = artifacts_from_catalog(f"{pilot}-db", cat if cat is not None else catalog(pilot), DiscoveryBudget())
    return build_twin(PROJECT_ID, version, [postgres, openapi_report(pilot)]).twin


# ------------------------------------------------------------------------- catalog mutations


def _column(table: str, name: str, position: int, data_type: str = "text", not_null: bool = False) -> dict:
    return {
        "comment": None, "data_type": data_type, "has_default": False, "is_enum": False, "name": name,
        "not_null": not_null, "position": position, "schema": "public", "table": table,
        "type_name": {"text": "text", "integer": "int4", "timestamp with time zone": "timestamptz"}[data_type],
        "type_schema": "pg_catalog",
    }


def add_change(cat: dict[str, Any]) -> dict[str, Any]:
    cat = copy.deepcopy(cat)
    cat["columns"].append(_column("employees", "badge_number", 10))
    cat["relations"].append({"comment": "Courses employees completed.", "kind": "r", "name": "trainings",
                             "readable": True, "schema": "public"})
    cat["columns"] += [
        _column("trainings", "id", 1, "integer", True),
        _column("trainings", "employee_id", 2, "integer", True),
        _column("trainings", "title", 3, "text", True),
        _column("trainings", "completed_at", 4, "timestamp with time zone"),
    ]
    cat["constraints"] += [
        {"columns": ["id"], "comment": None, "definition": None, "name": "trainings_pkey", "ref_columns": None,
         "ref_schema": None, "ref_table": None, "schema": "public", "table": "trainings", "type": "p"},
        {"columns": ["employee_id"], "comment": None, "definition": None, "name": "trainings_employee_id_fkey",
         "ref_columns": ["id"], "ref_schema": "public", "ref_table": "employees", "schema": "public",
         "table": "trainings", "type": "f"},
    ]
    return cat


def drop_change(cat: dict[str, Any]) -> dict[str, Any]:
    cat = copy.deepcopy(cat)
    keep = lambda row: row.get("table") != "assets"  # noqa: E731
    cat["relations"] = [r for r in cat["relations"] if r["name"] != "assets"]
    for key in ("columns", "constraints", "grants", "profiles"):
        cat[key] = [r for r in cat[key] if keep(r)]
    is_salary = lambda r: r.get("table") == "employees" and (r.get("name") == "salary" or r.get("column") == "salary")  # noqa: E731
    cat["columns"] = [r for r in cat["columns"] if not is_salary(r)]
    cat["profiles"] = [r for r in cat["profiles"] if not is_salary(r)]
    return cat


def rename_change(cat: dict[str, Any]) -> dict[str, Any]:
    cat = copy.deepcopy(cat)
    for row in cat["columns"]:
        if row["table"] == "employees" and row["name"] == "full_name":
            row["name"] = "display_name"
    for row in cat["profiles"]:
        if row["table"] == "employees" and row["column"] == "full_name":
            row["column"] = "display_name"
    for row in cat["relations"]:
        if row["name"] == "attendance":
            row["name"] = "time_entries"
    for key in ("columns", "constraints", "grants", "profiles"):
        for row in cat[key]:
            if row.get("table") == "attendance":
                row["table"] = "time_entries"
            if row.get("ref_table") == "attendance":
                row["ref_table"] = "time_entries"
    return cat


CHANGES: dict[str, Callable[[dict[str, Any]], dict[str, Any]]] = {
    "add": add_change, "drop": drop_change, "rename": rename_change,
}


def render_twin(pilot: str) -> str:
    return _dump(to_data(twin_for(pilot)))


def render_diff(change: str, pilot: str = "hr") -> str:
    base = twin_for(pilot)
    changed = twin_for(pilot, version=2, cat=CHANGES[change](catalog(pilot)))
    diff = diff_twins(base, changed)
    return _dump({"summary": diff.summary(), **to_data(diff)})


def main() -> int:
    for pilot in PILOTS:
        (PILOTS_DIR / pilot / "golden" / "twin.json").write_text(render_twin(pilot), encoding="utf-8")
    for change in CHANGES:
        (PILOTS_DIR / "hr" / "golden" / f"diff_{change}.json").write_text(render_diff(change), encoding="utf-8")
    print("wrote twin goldens for", ", ".join(PILOTS), "and diffs:", ", ".join(CHANGES))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
