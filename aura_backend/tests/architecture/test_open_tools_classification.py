"""Fitness: every ``tools/open`` module is importable, conflict-free and explicitly classified.

ADR-036 exposes some ``aura_core/tools/open`` classes as tools and keeps the rest as reference code
that nothing wires in. Three things went wrong with that directory that the unit tests could not
see: a merge left conflict markers in it and the whole of ``aura_core`` stopped importing (fixed in
9ba1dbd5, KAN-31); a merge silently reverted reviewed fixes (KAN-30); and 25 modules arrived in one
commit with no decision about any of them (KAN-32/35). So:

- every module imports, and no file carries a merge-conflict marker;
- every public module has an entry in ``tools/open/_classification.py`` -- the one list -- and every
  entry names a module that exists, so a new module fails here until somebody decides about it;
- the tool builders import exactly the ``wrapped`` modules, and nothing else under ``aura_core``
  imports any module that is not ``wrapped`` -- which is what makes "not exposed" mean "reachable
  from no persona";
- each module that is not wrapped says so at the top of its file and cites ADR-036.
"""

from __future__ import annotations

import ast
import importlib
import pkgutil
import re
from pathlib import Path

import pytest

from aura_core.tools.open import _classification as cls

BACKEND = Path(__file__).resolve().parents[2]
AURA_CORE = BACKEND / "aura_core"
OPEN_DIR = AURA_CORE / "tools" / "open"
OPEN_PACKAGE = "aura_core.tools.open"
#: The tool builders: the only modules allowed to import a ``tools/open`` class.
BUILDERS = {
    "analysis.py": AURA_CORE / "tools" / "analysis.py",
    "open_exec.py": AURA_CORE / "tools" / "open_exec.py",
    "desktop_ops.py": AURA_CORE / "tools" / "desktop_ops.py",
    # ADR-044 / ADR-045 / ADR-046: the pure-text, workspace-reading and research builders.
    "open_pure.py": AURA_CORE / "tools" / "open_pure.py",
    "open_workspace.py": AURA_CORE / "tools" / "open_workspace.py",
    "research.py": AURA_CORE / "tools" / "research.py",
    "release.py": AURA_CORE / "tools" / "release.py",
    "ops.py": AURA_CORE / "tools" / "ops.py",
}
#: Directories that are not project source (a stray interpreter install, caches, virtualenvs).
_SKIP_PARTS = {"__pycache__", ".venv", "venv", "Python", "node_modules", "site-packages"}

_CONFLICT_OPEN = re.compile(r"^<{7}(?:\s|$)")
_CONFLICT_BASE = re.compile(r"^\|{7}(?:\s|$)")
_CONFLICT_MID = re.compile(r"^={7}\s*$")
_CONFLICT_CLOSE = re.compile(r"^>{7}(?:\s|$)")


def _public_modules() -> set[str]:
    return {p.stem for p in OPEN_DIR.glob("*.py") if not p.stem.startswith("_")}


def _project_files() -> list[Path]:
    return [p for p in AURA_CORE.rglob("*.py") if not _SKIP_PARTS & set(p.relative_to(AURA_CORE).parts)]


def _absolute_imports(path: Path) -> set[str]:
    """Every module (and ``module.name``) a file imports, with relative imports resolved."""
    rel = path.relative_to(BACKEND).with_suffix("")
    parts = list(rel.parts)
    package = parts[:-1]  # a module's package, or for ``__init__`` the package itself
    names: set[str] = set()
    for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"), filename=str(path))):
        if isinstance(node, ast.Import):
            names.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            if node.level:
                base = package[: len(package) - (node.level - 1)]
                module = ".".join(base + ([node.module] if node.module else []))
            else:
                module = node.module or ""
            names.add(module)
            names.update(f"{module}.{alias.name}" for alias in node.names)
    return names


def _open_modules_imported_by(path: Path) -> set[str]:
    prefix = OPEN_PACKAGE + "."
    return {
        name[len(prefix):].split(".")[0]
        for name in _absolute_imports(path)
        if name.startswith(prefix)
    } & _public_modules()


# --- KAN-31 -----------------------------------------------------------------------------


def test_every_open_module_imports() -> None:
    package = importlib.import_module(OPEN_PACKAGE)
    found = [m.name for m in pkgutil.iter_modules(package.__path__)]
    assert set(found) >= _public_modules()  # the walk saw every file, so this is not vacuous
    failures: list[str] = []
    for name in found:
        try:
            importlib.import_module(f"{OPEN_PACKAGE}.{name}")
        except Exception as exc:  # noqa: BLE001 -- report every broken module, not the first
            failures.append(f"{name}: {type(exc).__name__}: {exc}")
    assert not failures, "tools/open modules that do not import:\n  " + "\n  ".join(failures)


def test_no_open_module_carries_a_merge_conflict_marker() -> None:
    offenders: list[str] = []
    for path in sorted(OPEN_DIR.rglob("*.py")):
        if _SKIP_PARTS & set(path.relative_to(OPEN_DIR).parts):
            continue
        inside = False
        for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
            hit = bool(_CONFLICT_OPEN.match(line) or _CONFLICT_CLOSE.match(line))
            if inside and (_CONFLICT_MID.match(line) or _CONFLICT_BASE.match(line)):
                hit = True
            if _CONFLICT_OPEN.match(line):
                inside = True
            elif _CONFLICT_CLOSE.match(line):
                inside = False
            if hit:
                offenders.append(f"{path.relative_to(AURA_CORE).as_posix()}:{number}: {line[:40]}")
    assert not offenders, "merge-conflict markers in tools/open:\n  " + "\n  ".join(offenders)


def test_the_conflict_check_recognises_each_marker() -> None:
    """Guard the guard: the patterns match real git markers and not a Markdown underline."""
    assert _CONFLICT_OPEN.match("<<<<<<< HEAD")
    assert _CONFLICT_BASE.match("||||||| merged common ancestors")
    assert _CONFLICT_MID.match("=======")
    assert _CONFLICT_CLOSE.match(">>>>>>> feature/x")
    assert not _CONFLICT_MID.match("======== heading underline")
    assert not _CONFLICT_OPEN.match("x <<<<<<< y")


# --- KAN-35 -----------------------------------------------------------------------------


def test_every_open_module_is_classified_and_every_entry_exists() -> None:
    present, listed = _public_modules(), set(cls.CLASSIFICATION)
    assert not present - listed, (
        "unclassified tools/open modules -- add each to tools/open/_classification.py as wrapped, "
        f"superseded, deferred or not_exposed, and record the decision in ADR-036: {sorted(present - listed)}"
    )
    assert not listed - present, f"classified modules that do not exist: {sorted(listed - present)}"


@pytest.mark.parametrize("name", sorted(cls.CLASSIFICATION))
def test_each_entry_has_a_known_status_and_a_note(name: str) -> None:
    status, note = cls.CLASSIFICATION[name]
    assert status in cls.STATUSES, f"{name}: unknown status {status!r}"
    assert note.strip(), f"{name}: no note"


def test_the_builders_import_exactly_the_wrapped_modules() -> None:
    wrapped = cls.modules_with(cls.WRAPPED)
    imported_by = {builder: _open_modules_imported_by(path) for builder, path in BUILDERS.items()}
    imported = set().union(*imported_by.values())
    assert imported == wrapped, (
        f"imported by a builder but not classified wrapped: {sorted(imported - wrapped)}; "
        f"classified wrapped but imported by no builder: {sorted(wrapped - imported)}"
    )
    for name in wrapped:
        builder = cls.CLASSIFICATION[name][1].split(":", 1)[0]
        assert name in imported_by.get(builder, set()), f"{name}: note names {builder!r}, which does not import it"


def test_nothing_under_aura_core_imports_a_module_that_is_not_wrapped() -> None:
    """This is what makes "not exposed" true: no import, so no tool, so no persona can reach it."""
    unwrapped = _public_modules() - cls.modules_with(cls.WRAPPED)
    offenders: list[str] = []
    for path in _project_files():
        here = path.stem if path.parent == OPEN_DIR else None
        for name in _open_modules_imported_by(path) & unwrapped:
            if name != here:
                offenders.append(f"{path.relative_to(AURA_CORE).as_posix()} imports {name}")
    assert not offenders, (
        "a tools/open module that ADR-036 does not expose was wired into aura_core; reclassify it "
        "in tools/open/_classification.py and ADR-036 first:\n  " + "\n  ".join(sorted(offenders))
    )


def test_the_import_resolver_sees_relative_imports() -> None:
    """Guard the guard: the builders reach tools/open through ``from .open.x import``."""
    assert "ast_code_analyzer" in _open_modules_imported_by(BUILDERS["analysis.py"])


@pytest.mark.parametrize(
    "name", sorted(set(cls.CLASSIFICATION) - cls.modules_with(cls.WRAPPED))
)
def test_each_module_that_is_not_wrapped_says_so_at_the_top(name: str) -> None:
    status = cls.CLASSIFICATION[name][0]
    tree = ast.parse((OPEN_DIR / f"{name}.py").read_text(encoding="utf-8"))
    doc = ast.get_docstring(tree) or ""
    assert doc.startswith(cls.HEADER_PREFIX[status]), (
        f"{name}.py is {status} but its module docstring does not start with "
        f"{cls.HEADER_PREFIX[status]!r}"
    )
    assert "ADR-036" in doc[:300], f"{name}.py does not cite ADR-036 in its header"


# --- KAN-34 -----------------------------------------------------------------------------

#: Process spawning, outbound network and fake session control: named in the ticket as modules no
#: persona may reach. They stay fenced.
KAN_34 = (
    "background_task_manager",
    "push_notification_dispatcher",
    "remote_session_controller",
)

#: The other four modules KAN-34 named read paths on the gateway or classify content. ADR-044 /
#: ADR-045 re-exposed them behind confinement; each may therefore be imported by exactly the one
#: builder that does the confining, and by nothing else (2026-11-25, decided when princess was
#: merged into main).
KAN_34_CONFINED = {
    "binary_file_inspector": "open_workspace.py",   # path under AURA_TOOLS_FILES_ROOT, base_dir enforced
    "notebook_reader": "open_workspace.py",         # text read through resolve_within first
    "stale_context_detector": "open_workspace.py",  # path under AURA_TOOLS_FILES_ROOT, base_dir enforced
    "file_type_detector_router": "open_pure.py",    # bytes only, never a path
}


@pytest.mark.parametrize("name", KAN_34)
def test_the_dangerous_modules_are_not_exposed(name: str) -> None:
    assert cls.CLASSIFICATION[name][0] == cls.NOT_EXPOSED
    doc = ast.get_docstring(ast.parse((OPEN_DIR / f"{name}.py").read_text(encoding="utf-8"))) or ""
    assert "KAN-34" in doc, f"{name}.py header does not record why it is fenced (KAN-34)"


@pytest.mark.parametrize("name", sorted(KAN_34_CONFINED))
def test_the_reexposed_kan_34_modules_are_reachable_only_through_their_confining_builder(name: str) -> None:
    assert cls.CLASSIFICATION[name][0] == cls.WRAPPED
    allowed = KAN_34_CONFINED[name]
    importers = sorted(
        path.relative_to(AURA_CORE).as_posix()
        for path in _project_files()
        if path.parent != OPEN_DIR and name in _open_modules_imported_by(path)
    )
    assert importers == [f"tools/{allowed}"], (
        f"{name} must be imported by tools/{allowed} alone (the builder that confines it); got {importers}"
    )
