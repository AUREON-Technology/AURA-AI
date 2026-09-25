"""The API reachability matrix (docs/guides/api-reachability.md) stays current and triaged.

CLAUDE.md: "A backend endpoint shipping does not mean the client can reach it." The matrix makes
that visible; these keep it from rotting. A new route with no Flutter/admin/hostd caller must be
given a reason in ``scripts/api_reachability.py``'s ``TRIAGE`` -- wire it, mark it server-only,
or remove it -- rather than silently joining the pile of unreached surfaces (GAP-013).
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[2]


def _script():
    spec = importlib.util.spec_from_file_location(
        "api_reachability", BACKEND / "scripts" / "api_reachability.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_the_committed_matrix_is_current() -> None:
    script = _script()
    rendered, _ = script.render()
    assert script.OUT.read_text(encoding="utf-8") == rendered, (
        "docs/guides/api-reachability.md is stale -- run `python scripts/api_reachability.py` "
        "from aura_backend/"
    )


def test_every_uncalled_route_is_triaged() -> None:
    rendered, _ = _script().render()
    untriaged = [line for line in rendered.splitlines() if line.endswith("| none | untriaged |")]
    assert not untriaged, (
        "routes with no client caller and no decision -- add them to TRIAGE in "
        "scripts/api_reachability.py:\n" + "\n".join(untriaged)
    )
