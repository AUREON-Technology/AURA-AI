"""ADR-034: shareable data is scoped by ``caller.knowledge_owner``, never ``caller.user_id``.

Inside a team workspace the knowledge, graph, document and CAG stores must read and write the
team's scope. A route that hands them ``caller.user_id`` would silently put a team's documents in
one member's private corpus (or read a member's private corpus inside the team). This walks
``api/v3.py`` and fails on any such call, keyword or positional.
"""

from __future__ import annotations

import ast
from pathlib import Path

V3 = Path(__file__).resolve().parents[2] / "aura_core" / "api" / "v3.py"
SHAREABLE = {"knowledge", "graph", "cag", "documents"}


def _chain(node: ast.AST) -> list[str]:
    names = []
    while isinstance(node, ast.Attribute):
        names.append(node.attr)
        node = node.value
    if isinstance(node, ast.Name):
        names.append(node.id)
    return names


def _is_caller_user_id(node: ast.AST) -> bool:
    return _chain(node) == ["user_id", "caller"]


def test_shareable_stores_never_receive_caller_user_id() -> None:
    offenders = []
    for node in ast.walk(ast.parse(V3.read_text())):
        if not isinstance(node, ast.Call) or not SHAREABLE & set(_chain(node.func)[1:]):
            continue
        arguments = list(node.args) + [k.value for k in node.keywords]
        if any(_is_caller_user_id(a) for a in arguments):
            offenders.append(f"v3.py:{node.lineno} {'.'.join(reversed(_chain(node.func)))}")
    assert not offenders, "use caller.knowledge_owner for shareable data:\n" + "\n".join(offenders)
