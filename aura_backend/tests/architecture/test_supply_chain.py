"""Supply-chain and deploy-trigger guards (audit 2026-10-26, F-38: SEC-10, SEC-11, SEC-13).

* **Lockfiles are hash-locked and resolved per-package (SEC-10).** The runtime lock used to be
  compiled with ``--extra-index-url download.pytorch.org --index-strategy unsafe-best-match`` and
  no hashes: either index could answer for any package name, which is the dependency-confusion
  shape of the 2022 torchtriton incident. Now uv's ``--torch-backend cpu`` sends only the PyTorch
  packages to the PyTorch index (everything else resolves from PyPI alone), every pin carries
  ``--hash``, and every install of a lock passes ``--require-hashes``.
* **workflow_run deploys trust only pushes to this repository's main (SEC-11).** ``branches:``
  on a ``workflow_run`` trigger filters on the upstream run's head *branch name*, which a fork PR
  controls.
* **No full-pickle torch.load (SEC-13).**
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

import pytest
import yaml

BACKEND = Path(__file__).resolve().parents[2]
REPO = BACKEND.parent
WORKFLOWS = REPO / ".github" / "workflows"

LOCKS = ("requirements.lock", "requirements-dev.lock", "requirements-train.lock")
PYTORCH_INDEX = "https://download.pytorch.org/whl/cpu"
PYPI = "https://pypi.org/simple"
#: Packages uv's --torch-backend routes to the PyTorch index. Anything else annotated as coming
#: from there means the per-package routing was lost.
TORCH_PACKAGES = {"torch", "torchvision", "torchaudio", "triton", "pytorch-triton"}

_PIN = re.compile(r"^([A-Za-z0-9._-]+)==(\S+)")


def _entries(lock: Path) -> list[tuple[str, list[str]]]:
    """(pin line, following continuation/comment lines) per requirement."""
    entries: list[tuple[str, list[str]]] = []
    for line in lock.read_text(encoding="utf-8").splitlines():
        if _PIN.match(line):
            entries.append((line, []))
        elif entries and line.startswith(" "):
            entries[-1][1].append(line.strip())
    return entries


@pytest.mark.parametrize("name", LOCKS)
def test_lock_is_fully_hashed_and_index_routed(name: str) -> None:
    lock = BACKEND / name
    text = lock.read_text(encoding="utf-8")
    header = "\n".join(text.splitlines()[:3])
    assert "--generate-hashes" in header and "--torch-backend cpu" in header, header
    assert "unsafe-best-match" not in text and "--extra-index-url" not in text
    # An --index-url line in the file would replace the install command's PyTorch extra index.
    assert not re.search(r"^--(extra-)?index-url", text, re.M)

    entries = _entries(lock)
    assert entries, f"{name} has no pins"
    for pin, rest in entries:
        package = _PIN.match(pin).group(1).lower()
        assert any(r.startswith("--hash=sha256:") for r in rest), f"{name}: {pin} has no hash"
        sources = [r.removeprefix("# from ").strip() for r in rest if r.startswith("# from ")]
        expected = PYTORCH_INDEX if package in TORCH_PACKAGES else PYPI
        assert sources == [expected], f"{name}: {package} resolved from {sources}, not {expected}"


def test_torch_is_the_cpu_wheel() -> None:
    pins = dict(
        _PIN.match(pin).groups() for pin, _ in _entries(BACKEND / "requirements.lock")
    )
    assert pins["torch"].endswith("+cpu"), "torch must stay the CPU wheel (6.5 GB of CUDA otherwise)"


def test_dev_and_train_locks_agree_with_the_runtime_lock() -> None:
    runtime = dict(_PIN.match(p).groups() for p, _ in _entries(BACKEND / "requirements.lock"))
    for name in ("requirements-dev.lock", "requirements-train.lock"):
        for pin, _ in _entries(BACKEND / name):
            package, version = _PIN.match(pin).groups()
            if package in runtime:
                assert runtime[package] == version, f"{name}: {package} {version} != {runtime[package]}"


def _install_commands() -> list[tuple[str, str]]:
    """Every `pip install ... -r requirements*.lock` in the Dockerfile and the workflows."""
    sources = [BACKEND / "Dockerfile", *sorted(WORKFLOWS.glob("*.y*ml"))]
    found: list[tuple[str, str]] = []
    for path in sources:
        text = path.read_text(encoding="utf-8").replace("\\\n", " ")
        for line in text.splitlines():
            if "pip install" in line and re.search(r"-r\s+requirements[\w-]*\.(lock|txt)", line):
                found.append((path.name, " ".join(line.split())))
    return found


def test_every_lock_install_requires_hashes() -> None:
    commands = _install_commands()
    assert any(src == "Dockerfile" for src, _ in commands), "Dockerfile install not found"
    for src, cmd in commands:
        assert "requirements-dev.txt" not in cmd and "requirements-train.txt" not in cmd, (
            f"{src}: install the hashed .lock, not the unpinned .txt: {cmd}"
        )
        assert "--require-hashes" in cmd, f"{src}: {cmd}"
        assert f"--extra-index-url {PYTORCH_INDEX}" in cmd, f"{src}: {cmd}"


def _workflow(name: str) -> dict:
    return yaml.safe_load((WORKFLOWS / name).read_text(encoding="utf-8"))


def _workflow_run_deploys() -> list[str]:
    names = []
    for path in sorted(WORKFLOWS.glob("*.y*ml")):
        payload = yaml.safe_load(path.read_text(encoding="utf-8"))
        triggers = payload.get("on", payload.get(True)) or {}
        if isinstance(triggers, dict) and "workflow_run" in triggers:
            names.append(path.name)
    return names


def test_known_workflow_run_deploys_are_covered() -> None:
    assert {"deploy-backend.yml", "control-plane-deploy.yml"} <= set(_workflow_run_deploys())


@pytest.mark.parametrize("name", _workflow_run_deploys())
def test_workflow_run_jobs_trust_only_pushes_from_this_repository(name: str) -> None:
    """Every job of a workflow_run workflow gates on push + this repo, or needs one that does."""
    jobs = _workflow(name)["jobs"]
    required = (
        "github.event.workflow_run.event == 'push'",
        "github.event.workflow_run.head_repository.full_name == github.repository",
    )
    for job_id, job in jobs.items():
        condition = " ".join(str(job.get("if", "")).split())
        if all(r in condition for r in required):
            continue
        needs = job.get("needs", [])
        needs = [needs] if isinstance(needs, str) else needs
        gated_upstream = any(
            all(r in " ".join(str(jobs[n].get("if", "")).split()) for r in required) for n in needs
        )
        assert gated_upstream, f"{name}: job `{job_id}` runs for any workflow_run: {condition!r}"


def test_no_full_pickle_torch_load() -> None:
    """torch.load must pass weights_only=True explicitly (default changed across versions)."""
    offenders: list[str] = []
    for path in sorted((BACKEND / "aura_core").rglob("*.py")) + sorted((REPO / "scripts").rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"), str(path))
        for node in ast.walk(tree):
            if not (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)):
                continue
            if node.func.attr != "load" or not isinstance(node.func.value, ast.Name):
                continue
            if node.func.value.id != "torch":
                continue
            ok = any(
                kw.arg == "weights_only" and isinstance(kw.value, ast.Constant)
                and kw.value.value is True
                for kw in node.keywords
            )
            if not ok:
                offenders.append(f"{path.relative_to(REPO)}:{node.lineno}")
    assert not offenders, "torch.load without weights_only=True:\n  " + "\n  ".join(offenders)
