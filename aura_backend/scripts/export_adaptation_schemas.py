"""Regenerate the published JSON Schemas of the Track AP domain model (ADR-020, ADR-024).

The schemas are derived from the frozen dataclasses in ``aura_core/domain/{blueprint,ausm}.py``
by the same type walk the strict decoder uses (``aura_core/domain/serialization.py``), so they
cannot describe a shape the code does not accept. ``tests/architecture/test_adaptation_schemas.py``
fails when a committed file differs from what this renders, so run it after changing either model:

    python aura_backend/scripts/export_adaptation_schemas.py     # from the repo root
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any, Callable

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "aura_backend"))

from aura_core.domain.ausm import project_twin_json_schema  # noqa: E402
from aura_core.domain.blueprint import blueprint_json_schema  # noqa: E402

SCHEMA_DIR = ROOT / "docs" / "schemas"
SCHEMAS: dict[str, Callable[[], dict[str, Any]]] = {
    "blueprint.v1.json": blueprint_json_schema,
    "project_twin.v1.json": project_twin_json_schema,
}


def render(name: str) -> str:
    return json.dumps(SCHEMAS[name](), indent=2, ensure_ascii=False) + "\n"


def main() -> int:
    SCHEMA_DIR.mkdir(parents=True, exist_ok=True)
    for name in SCHEMAS:
        (SCHEMA_DIR / name).write_text(render(name), encoding="utf-8")
        print(f"wrote docs/schemas/{name}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
