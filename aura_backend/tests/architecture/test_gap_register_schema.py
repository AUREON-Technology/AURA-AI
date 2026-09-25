"""Schema fitness test for the production gap register.

``docs/production/gap_register.yaml`` is the machine-readable ledger of what stands between the
current tree and production. It is only trustworthy if every entry is complete and every
"done" claim carries its evidence -- the roadmap's standing rule ("built" and "tested" are not
"proven") applied to the register itself. This test enforces the shape; it cannot judge whether
the evidence is true, which is what review is for.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
import yaml

REPO = Path(__file__).resolve().parents[3]
REGISTER = REPO / "docs" / "production" / "gap_register.yaml"

REQUIRED_FIELDS = (
    "gap_id",
    "component",
    "severity",
    "current_state",
    "target_state",
    "owner",
    "dependencies",
    "test",
    "metric",
    "exit_condition",
    "status",
)
SEVERITIES = {"P0", "P1", "P2", "P3"}
STATUSES = {"open", "in_progress", "built_not_proven", "proven", "closed_stale"}
#: A status that asserts something is finished must say how we know.
EVIDENCE_REQUIRED = {"proven", "closed_stale"}


def _load() -> dict[str, Any]:
    assert REGISTER.is_file(), f"gap register missing at {REGISTER}"
    data = yaml.safe_load(REGISTER.read_text(encoding="utf-8"))
    assert isinstance(data, dict), "gap register must be a mapping at the top level"
    return data


@pytest.fixture(scope="module")
def gaps() -> list[dict[str, Any]]:
    data = _load()
    entries = data.get("gaps")
    assert isinstance(entries, list) and entries, "gap register must contain a non-empty `gaps` list"
    for entry in entries:
        assert isinstance(entry, dict), f"every gap must be a mapping, got {entry!r}"
    return entries


def _blank(value: Any) -> bool:
    return value is None or (isinstance(value, str) and not value.strip())


def test_every_entry_has_every_required_field(gaps: list[dict[str, Any]]) -> None:
    problems: list[str] = []
    for entry in gaps:
        gap_id = entry.get("gap_id", "<no gap_id>")
        for field in REQUIRED_FIELDS:
            if field not in entry:
                problems.append(f"{gap_id}: missing `{field}`")
            elif field != "dependencies" and _blank(entry[field]):
                problems.append(f"{gap_id}: `{field}` is empty")
        if "dependencies" in entry and not isinstance(entry["dependencies"], list):
            problems.append(f"{gap_id}: `dependencies` must be a list (use [] for none)")
    assert not problems, "\n".join(problems)


def test_gap_ids_are_unique(gaps: list[dict[str, Any]]) -> None:
    seen: set[str] = set()
    dupes: set[str] = set()
    for entry in gaps:
        if entry["gap_id"] in seen:
            dupes.add(entry["gap_id"])
        seen.add(entry["gap_id"])
    assert not dupes, f"duplicate gap_ids: {sorted(dupes)}"


def test_severity_and_status_are_in_the_allowed_sets(gaps: list[dict[str, Any]]) -> None:
    problems = [
        f"{g['gap_id']}: severity {g['severity']!r} not in {sorted(SEVERITIES)}"
        for g in gaps
        if g["severity"] not in SEVERITIES
    ] + [
        f"{g['gap_id']}: status {g['status']!r} not in {sorted(STATUSES)}"
        for g in gaps
        if g["status"] not in STATUSES
    ]
    assert not problems, "\n".join(problems)


def test_finished_entries_carry_evidence(gaps: list[dict[str, Any]]) -> None:
    missing = [
        f"{g['gap_id']} is {g['status']} but has no `evidence`"
        for g in gaps
        if g["status"] in EVIDENCE_REQUIRED and _blank(g.get("evidence"))
    ]
    assert not missing, "\n".join(missing)


def test_dependencies_reference_known_gaps(gaps: list[dict[str, Any]]) -> None:
    known = {g["gap_id"] for g in gaps}
    dangling = [
        f"{g['gap_id']} depends on unknown {dep!r}"
        for g in gaps
        for dep in g.get("dependencies") or []
        if dep not in known
    ]
    assert not dangling, "\n".join(dangling)


def test_the_rendered_markdown_matches_the_yaml(gaps: list[dict[str, Any]]) -> None:
    """GAP_REGISTER.md's Totals/All-gaps tables are generated; a YAML edit must re-render them."""
    import importlib.util

    spec = importlib.util.spec_from_file_location(
        "render_gap_register", REPO / "aura_backend" / "scripts" / "render_gap_register.py"
    )
    renderer = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(renderer)

    committed = renderer.MD_PATH.read_text(encoding="utf-8")
    assert committed == renderer.render(committed, gaps), (
        "docs/production/GAP_REGISTER.md is out of date with gap_register.yaml -- run "
        "`python aura_backend/scripts/render_gap_register.py` from the repo root"
    )
