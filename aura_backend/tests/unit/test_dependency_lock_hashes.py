"""The runtime lockfiles are hash-pinned and installed hash-checked (audit F-38 / SEC-10).

Before this, requirements.lock carried no hashes and was compiled with
``--extra-index-url https://download.pytorch.org/whl/cpu --index-strategy unsafe-best-match``:
both indexes could serve any package name and the resolver took the best version from either.
That is the dependency-confusion shape of the 2022 torchtriton incident. Now:

* the lock is compiled with ``--torch-backend cpu`` (only PyTorch packages resolve against the
  CPU index) and ``--generate-hashes``;
* every install that reads it passes ``--require-hashes``, so a file whose sha256 is not in the
  lock is refused whichever index served it.

These are static checks over files, so the default suite still needs no network.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

_BACKEND = Path(__file__).resolve().parents[2]
_REPO = _BACKEND.parent
_WORKFLOWS = _REPO / ".github" / "workflows"

_PIN = re.compile(r"^([A-Za-z0-9._-]+)==(\S+?)(?:\s*\\)?$")


def _entries(path: Path) -> dict[str, tuple[str, int]]:
    """name -> (version, number of --hash lines) for each pinned requirement."""
    entries: dict[str, tuple[str, int]] = {}
    current: str | None = None
    for line in path.read_text(encoding="utf-8").splitlines():
        match = _PIN.match(line)
        if match:
            current = match.group(1).lower().replace("_", "-")
            entries[current] = (match.group(2), 0)
        elif current and line.strip().startswith("--hash=sha256:"):
            version, count = entries[current]
            entries[current] = (version, count + 1)
        elif not line.startswith(" "):
            current = None
    return entries


_LOCKS = ["requirements.lock", "requirements-train.lock"]


@pytest.mark.parametrize("name", _LOCKS)
def test_every_pin_carries_a_hash(name: str) -> None:
    entries = _entries(_BACKEND / name)
    assert entries, f"{name} has no pins"
    unhashed = sorted(pkg for pkg, (_, hashes) in entries.items() if hashes == 0)
    assert not unhashed, f"{name}: pins without --hash: {unhashed}"


@pytest.mark.parametrize("name", _LOCKS)
def test_the_lock_was_compiled_with_scoped_indexes(name: str) -> None:
    text = (_BACKEND / name).read_text(encoding="utf-8")
    header = "\n".join(line for line in text.splitlines()[:3] if line.startswith("#"))

    assert "--generate-hashes" in header
    assert "--torch-backend cpu" in header
    assert "unsafe-best-match" not in text
    assert "--extra-index-url" not in text
    # An index line inside a requirements file replaces the installer's --extra-index-url,
    # which is how the CPU wheel is found -- the install would fail to find torch==...+cpu.
    assert not re.search(r"^--(index-url|extra-index-url|find-links)", text, re.M)


def test_torch_is_the_cpu_build() -> None:
    version, _ = _entries(_BACKEND / "requirements.lock")["torch"]
    assert version.endswith("+cpu"), version
    lock = (_BACKEND / "requirements.lock").read_text(encoding="utf-8")
    assert not re.search(r"^(nvidia-|triton==)", lock, re.M), "CUDA runtime pulled into the lock"


def test_the_training_lock_matches_the_runtime_lock() -> None:
    runtime = _entries(_BACKEND / "requirements.lock")
    training = _entries(_BACKEND / "requirements-train.lock")
    drift = {
        pkg: (runtime[pkg][0], version)
        for pkg, (version, _) in training.items()
        if pkg in runtime and runtime[pkg][0] != version
    }
    assert not drift, f"requirements-train.lock drifted from requirements.lock: {drift}"
    assert {"peft", "accelerate"} <= set(training)


def _install_commands(text: str) -> list[str]:
    # Join shell continuations so a multi-line `pip install \` reads as one command.
    joined = re.sub(r"\\\s*\n\s*", " ", text)
    return [
        line.strip()
        for line in joined.splitlines()
        if re.search(r"pip install\b", line)
        and re.search(r"requirements(-train)?\.lock\b", line)
        and not line.strip().startswith("#")
    ]


def _install_sites() -> list[Path]:
    workflows = sorted(_WORKFLOWS.glob("*.yml")) + sorted(_WORKFLOWS.glob("*.yaml"))
    return [_BACKEND / "Dockerfile", *workflows]


def test_the_scan_finds_the_known_installs() -> None:
    found = {p.name for p in _install_sites() if _install_commands(p.read_text(encoding="utf-8"))}
    assert {"Dockerfile", "backend-ci.yml", "eval-ci.yml"} <= found, found


@pytest.mark.parametrize("path", _install_sites(), ids=lambda p: p.name)
def test_every_lock_install_requires_hashes(path: Path) -> None:
    for command in _install_commands(path.read_text(encoding="utf-8")):
        assert "--require-hashes" in command, f"{path.name}: {command}"
        assert "download.pytorch.org/whl/cpu" in command or "TORCH_INDEX_URL" in command, (
            f"{path.name}: the CPU torch wheel is only on PyTorch's index: {command}"
        )
        assert "requirements-dev.txt" not in command, (
            f"{path.name}: an unhashed file in a --require-hashes install fails: {command}"
        )
        assert "requirements-train.txt" not in command, (
            f"{path.name}: install requirements-train.lock, not the .txt input: {command}"
        )
