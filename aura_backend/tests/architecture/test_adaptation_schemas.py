"""The published Track AP schemas match the domain model they describe (AP1, ADR-020/024).

``docs/schemas/*.json`` is what tooling and reviewers read; the strict decoder in
``aura_core/domain/serialization.py`` is what the code enforces. Both come from one type walk,
so the only way they can disagree is a stale committed file -- which this catches.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[3]


def _exporter():
    spec = importlib.util.spec_from_file_location(
        "export_adaptation_schemas", REPO / "aura_backend" / "scripts" / "export_adaptation_schemas.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


EXPORTER = _exporter()


@pytest.mark.parametrize("name", sorted(EXPORTER.SCHEMAS))
def test_committed_schema_matches_the_domain_model(name: str) -> None:
    committed = (EXPORTER.SCHEMA_DIR / name).read_text(encoding="utf-8")
    assert committed == EXPORTER.render(name), (
        f"docs/schemas/{name} is out of date with the domain model -- run "
        "`python aura_backend/scripts/export_adaptation_schemas.py` from the repo root"
    )


def test_every_blueprint_object_rejects_unknown_keys() -> None:
    import json

    schema = json.loads(EXPORTER.render("blueprint.v1.json"))
    loose = sorted(n for n, d in schema["$defs"].items() if d.get("additionalProperties") is not False)
    assert not loose, f"schema objects accept unknown keys: {loose}"
