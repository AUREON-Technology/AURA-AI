"""``piper`` implementation of the ``TextToSpeech`` port for Arabic neural TTS.

Self-hosted: shells out to the ``piper`` binary with a baked ONNX voice. No vendor API, no
Python Piper SDK -- ``aura_core`` stays importable on bare CPython (ADR-001). ``piper_available``
is true only when the binary **and** the Arabic model+config sit on disk, so a half-installed
image does not advertise a neural voice it cannot produce.
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
    "build_piper_command",
    "piper_config_path",
    "DEFAULT_PIPER_AR_MODEL",
]

_BINARY = "piper"

# Baked by the gateway Dockerfile. Operators override via ``VoiceSettings.piper_ar_model``.
DEFAULT_PIPER_AR_MODEL = "/opt/piper/voices/ar_JO-kareem-medium.onnx"


def piper_config_path(model_path: str | Path) -> Path:
    """Piper reads ``<model>.onnx.json`` next to the ONNX file."""
    return Path(f"{model_path}.json")


def piper_available(model_path: str | Path | None = None) -> bool:
    """True only when the binary and the Arabic ONNX+JSON are all present."""
    if shutil.which(_BINARY) is None:
        return False
    model = Path(model_path or DEFAULT_PIPER_AR_MODEL)
    return model.is_file() and piper_config_path(model).is_file()


def build_piper_command(out_path: str, *, model_path: str) -> list[str]:
    """argv for one synthesis. Text is supplied on stdin, not as a positional -- Piper's
    documented interface, and it keeps Arabic UTF-8 out of the argument vector."""
    return [
        _BINARY,
        "--model",
        model_path,
        "--output_file",
        out_path,
    ]


class PiperTtsEngine:
    def __init__(self, *, model_path: str = DEFAULT_PIPER_AR_MODEL) -> None:
        if not piper_available(model_path):
            raise DependencyUnavailableError(
                "the piper binary or the Arabic ONNX voice is missing",
                dependency="piper",
            )
        self._model_path = model_path

    @property
    def name(self) -> str:
        return "piper-ar"

    async def synthesize(self, text: str, *, voice: str | None = None) -> bytes:
        del voice  # one baked Arabic model; routing happens above this adapter
        spoken = prepare_spoken_text(text)
        if not spoken:
            return b""
        with tempfile.TemporaryDirectory() as tmp:
            out_path = Path(tmp) / "speech.wav"
            args = build_piper_command(str(out_path), model_path=self._model_path)
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
