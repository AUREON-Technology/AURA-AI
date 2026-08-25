"""``piper`` implementation of the ``TextToSpeech`` port.

Self-hosted: shells out to the ``piper`` binary with a baked ONNX voice. No vendor API, no
Python Piper SDK -- ``aura_core`` stays importable on bare CPython (ADR-001).
``piper_available(model)`` is true only when the binary **and** that model's ``.onnx`` +
``.onnx.json`` sit on disk, so a half-installed image does not advertise a neural voice it
cannot produce.
"""

from __future__ import annotations

import asyncio
import logging
import shutil
import tempfile
from pathlib import Path

from ..domain.errors import DependencyUnavailableError
from .spoken_text import prepare_spoken_text

logger = logging.getLogger(__name__)

__all__ = [
    "PiperTtsEngine",
    "piper_available",
    "piper_voice_name",
    "build_piper_command",
    "piper_config_path",
    "DEFAULT_PIPER_EN_MODEL",
    "DEFAULT_PIPER_AR_MODEL",
    "DEFAULT_PIPER_LENGTH_SCALE",
]

_BINARY = "piper"

# Baked by the gateway Dockerfile. Operators override via ``VoiceSettings``.
DEFAULT_PIPER_EN_MODEL = "/opt/piper/voices/en_US-lessac-medium.onnx"
DEFAULT_PIPER_AR_MODEL = "/opt/piper/voices/ar_JO-kareem-medium.onnx"
# Slightly slower than Piper's stock 1.0 -- neural pacing, not espeak rate.
DEFAULT_PIPER_LENGTH_SCALE = 1.08


def piper_config_path(model_path: str | Path) -> Path:
    """Piper reads ``<model>.onnx.json`` next to the ONNX file."""
    return Path(f"{model_path}.json")


def piper_voice_name(model_path: str | Path) -> str:
    """Health/log label: ``piper-ar`` for Arabic models, otherwise ``piper-en``."""
    name = Path(model_path).name.lower()
    if name.startswith("ar_") or name.startswith("ar-"):
        return "piper-ar"
    return "piper-en"


def piper_available(model_path: str | Path) -> bool:
    """True only when the binary and this language's ONNX+JSON are all present."""
    if shutil.which(_BINARY) is None:
        return False
    model = Path(model_path)
    return model.is_file() and piper_config_path(model).is_file()


def build_piper_command(
    out_path: str,
    *,
    model_path: str,
    length_scale: float = DEFAULT_PIPER_LENGTH_SCALE,
) -> list[str]:
    """argv for one synthesis. Text is supplied on stdin, not as a positional -- Piper's
    documented interface, and it keeps Arabic UTF-8 out of the argument vector."""
    return [
        _BINARY,
        "--model",
        model_path,
        "--output_file",
        out_path,
        "--length-scale",
        str(length_scale),
    ]


class PiperTtsEngine:
    def __init__(
        self,
        *,
        model_path: str,
        length_scale: float = DEFAULT_PIPER_LENGTH_SCALE,
    ) -> None:
        if not piper_available(model_path):
            raise DependencyUnavailableError(
                "the piper binary or the ONNX voice is missing",
                dependency="piper",
            )
        self._model_path = model_path
        self._length_scale = length_scale
        self._name = piper_voice_name(model_path)

    @property
    def name(self) -> str:
        return self._name

    async def synthesize(self, text: str, *, voice: str | None = None) -> bytes:
        del voice  # routing happens above this adapter; one baked model per instance
        spoken = prepare_spoken_text(text)
        if not spoken:
            return b""
        with tempfile.TemporaryDirectory() as tmp:
            out_path = Path(tmp) / "speech.wav"
            args = build_piper_command(
                str(out_path),
                model_path=self._model_path,
                length_scale=self._length_scale,
            )
            process = await asyncio.create_subprocess_exec(
                *args,
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            _stdout, stderr = await process.communicate((spoken + "\n").encode("utf-8"))
            if process.returncode != 0:
                logger.warning(
                    "piper failed", extra={"stderr": stderr.decode(errors="replace")[:500]},
                )
                raise RuntimeError(f"piper exited {process.returncode}")
            if not out_path.is_file():
                raise RuntimeError("piper produced no WAV file")
            return out_path.read_bytes()
