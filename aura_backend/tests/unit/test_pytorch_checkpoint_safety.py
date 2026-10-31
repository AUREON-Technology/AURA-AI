"""PyTorch checkpoints load with ``weights_only=True`` (audit F-38 / SEC-13).

``torch.load(..., weights_only=False)`` is a full pickle load: whoever controls the checkpoint
path controls code execution in the process that loads it. Everything ``save_checkpoint`` writes
(state dicts, optimizer state, strings, ints, a metrics dict) is representable under the
restricted unpickler, so nothing needs the full one.

Two halves: a checkpoint this adapter wrote -- with optimizer state, the richest shape it
produces -- still round-trips; and a pickle carrying a callable is refused rather than executed.
"""

from __future__ import annotations

import pickle
from pathlib import Path

import pytest

torch = pytest.importorskip("torch")

from aura_core.adapters.ml.pytorch_adapter import PyTorchAdapter, TorchModelConfig  # noqa: E402


def _adapter(checkpoint_path: str | None = None) -> PyTorchAdapter:
    return PyTorchAdapter(
        TorchModelConfig(architecture="transformer", model_id="m", checkpoint_path=checkpoint_path)
    )


def test_own_checkpoint_with_optimizer_round_trips_under_weights_only(tmp_path: Path) -> None:
    writer = _adapter()
    writer.load_model()
    writer.setup_optimizer(lr=1e-3)
    path = tmp_path / "ckpt.pt"
    saved = writer.save_checkpoint(str(path), epoch=2, step=7, metrics={"loss": 0.25},
                                   dataset_digest="abc")
    assert saved.optimizer_state

    reader = _adapter()
    reader.load_model()
    reader.setup_optimizer(lr=1e-3)
    loaded = reader.load_checkpoint(str(path))
    assert (loaded.epoch, loaded.step, loaded.metrics, loaded.dataset_digest) == (
        2, 7, {"loss": 0.25}, "abc"
    )
    for name, tensor in writer._model.state_dict().items():
        assert torch.equal(tensor, reader._model.state_dict()[name]), name

    # The load_model() path reads the same file through config.checkpoint_path.
    from_config = _adapter(str(path))
    from_config.load_model()
    assert from_config._loaded


class _Exploit:
    def __init__(self, sentinel: Path) -> None:
        self.sentinel = sentinel

    def __reduce__(self):  # pragma: no cover - only ever invoked by an unsafe unpickler
        return (self.sentinel.touch, ())


def test_a_pickle_carrying_a_callable_is_refused(tmp_path: Path) -> None:
    sentinel = tmp_path / "executed"
    path = tmp_path / "evil.pt"
    with path.open("wb") as fh:
        pickle.dump({"model": _Exploit(sentinel)}, fh, protocol=2)

    adapter = _adapter()
    adapter.load_model()
    with pytest.raises(pickle.UnpicklingError):
        adapter.load_checkpoint(str(path))
    assert not sentinel.exists()
