"""Checkpoints are loaded with ``weights_only=True`` (audit F-38 / SEC-13).

``torch.load`` with ``weights_only=False`` is ``pickle.load``: a checkpoint file that carries an
object with a hostile ``__reduce__`` runs code the moment it is read. Checkpoint paths come from
configuration and from the training pipeline's output directory, so whoever can write a file
there would get code execution in the worker that loads it. ``save_checkpoint`` only writes
state dicts and plain values, so the restricted unpickler loses nothing.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

_BACKEND = Path(__file__).resolve().parents[2]
_REPO = _BACKEND.parent


def _torch_load_calls(path: Path) -> list[ast.Call]:
    tree = ast.parse(path.read_text(encoding="utf-8"), str(path))
    return [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "load"
        and isinstance(node.func.value, ast.Name)
        and node.func.value.id == "torch"
    ]


def _sources() -> list[Path]:
    roots = [_BACKEND / "aura_core", _BACKEND / "services", _REPO / "scripts"]
    return sorted(p for root in roots if root.is_dir() for p in root.rglob("*.py"))


def test_every_torch_load_passes_weights_only_true() -> None:
    offenders = []
    for path in _sources():
        for call in _torch_load_calls(path):
            kw = {k.arg: k.value for k in call.keywords}
            value = kw.get("weights_only")
            if not (isinstance(value, ast.Constant) and value.value is True):
                offenders.append(f"{path.relative_to(_REPO)}:{call.lineno}")
    assert not offenders, (
        "torch.load without weights_only=True unpickles arbitrary objects (pickle RCE): "
        f"{offenders}"
    )


def test_the_scan_sees_the_adapter() -> None:
    adapter = _BACKEND / "aura_core" / "adapters" / "ml" / "pytorch_adapter.py"
    assert len(_torch_load_calls(adapter)) == 2


class _Payload:
    """Pickles to a call that records it ran -- the shape of a malicious checkpoint."""

    def __reduce__(self):
        return (_mark_executed, ())


_EXECUTED: list[bool] = []


def _mark_executed() -> str:
    _EXECUTED.append(True)
    return "pwned"


def test_a_checkpoint_carrying_a_pickled_object_is_refused(tmp_path: Path) -> None:
    torch = pytest.importorskip("torch")
    import pickle

    from aura_core.adapters.ml.pytorch_adapter import PyTorchAdapter, TorchModelConfig

    path = tmp_path / "hostile.pt"
    torch.save({"model": {}, "payload": _Payload()}, path)
    _EXECUTED.clear()

    adapter = PyTorchAdapter(TorchModelConfig(architecture="transformer", model_id="t"))
    with pytest.raises(pickle.UnpicklingError):
        adapter.load_checkpoint(str(path))

    assert _EXECUTED == [], "the checkpoint's pickled callable ran during load"
