"""Does this interpreter have the packages the lockfiles pin? (audit G-07 / FSA-080)

Every "tested locally" claim in ``docs/status/`` was once produced on a venv that had drifted from
``requirements.lock``: CPython 3.14 and FastAPI 0.136 against a lock at 0.141. The route-auth suite
could not even be collected there, and nothing said so. ``tests/conftest.py`` calls :func:`report`
at session start and turns every line into a pytest warning, so a drifted environment still runs
the suite but can no longer pass for the locked one silently.

Stdlib only. The watch list is short on purpose: the packages whose version changes what the
suite can collect or how the gateway behaves, not every transitive pin.

    python scripts/lock_drift.py          # prints the report, exits 1 if anything drifted
"""

from __future__ import annotations

import re
import sys
from importlib import metadata
from pathlib import Path
from typing import Iterable, Mapping

#: Distributions whose drift has changed a test result before, or would change what the gateway
#: does. Names in the normalised form `importlib.metadata` reports.
WATCHED: tuple[str, ...] = (
    "fastapi", "starlette", "pydantic", "httpx", "sqlalchemy", "alembic", "asyncpg",
    "prometheus-client", "pytest", "pytest-asyncio", "anyio",
)

LOCKS: tuple[str, ...] = ("requirements.lock", "requirements-dev.lock")

_PIN = re.compile(r"^([A-Za-z0-9][A-Za-z0-9._-]*)==([^\s\\;]+)", re.M)


def normalise(name: str) -> str:
    """PEP 503 normalisation: one spelling for `Prometheus_Client`, `prometheus-client`, …"""
    return re.sub(r"[-_.]+", "-", name).lower()


def pinned_versions(lock_text: str) -> dict[str, str]:
    """`{normalised name: version}` for every `name==version` line; hashes and comments ignored."""
    return {normalise(name): version for name, version in _PIN.findall(lock_text)}


def locked_pins(backend: Path) -> dict[str, str]:
    """The union of both locks' pins. The dev lock is compiled against the runtime lock as a
    constraint, so a name in both carries one version; the runtime lock wins if they ever differ."""
    pins: dict[str, str] = {}
    for lock in reversed(LOCKS):
        path = backend / lock
        if path.is_file():
            pins.update(pinned_versions(path.read_text(encoding="utf-8")))
    return pins


def installed_versions(names: Iterable[str]) -> dict[str, str | None]:
    out: dict[str, str | None] = {}
    for name in names:
        try:
            out[name] = metadata.version(name)
        except metadata.PackageNotFoundError:
            out[name] = None
    return out


def drift(pins: Mapping[str, str], installed: Mapping[str, str | None]) -> list[str]:
    """One line per installed package that is not at its pinned version. A package the locks do
    not pin is not drift, whatever is installed."""
    lines: list[str] = []
    for name, have in installed.items():
        want = pins.get(normalise(name))
        if want is None:
            continue
        if have is None:
            lines.append(f"{name}: not installed, lock pins {want}")
        elif normalise(have) != normalise(want):
            lines.append(f"{name}: installed {have}, lock pins {want}")
    return lines


def report(backend: Path, watched: Iterable[str] = WATCHED) -> list[str]:
    return drift(locked_pins(backend), installed_versions(watched))


def main() -> int:
    lines = report(Path(__file__).resolve().parents[1])
    if not lines:
        print("installed packages match the locks for every watched distribution")
        return 0
    print("installed packages differ from the locks (see requirements-dev.txt for the sanctioned install):")
    for line in lines:
        print(f"  {line}")
    return 1


if __name__ == "__main__":
    sys.exit(main())
