"""``espeak-ng`` implementation of the ``TextToSpeech`` port (Phase F2).

Self-hosted, real synthesis via the ``espeak-ng`` binary -- no vendor API. The voice is
unmistakably synthetic (a formant synthesizer, not a neural TTS model); that is an honest
tradeoff for a deployment that cannot spare the disk or CPU budget a neural voice model needs. A
deployment that can afford one plugs it in behind this same port -- nothing above this adapter
knows or cares which kind of synthesizer produced the bytes.
"""

from __future__ import annotations

import asyncio
import logging
import shutil
import tempfile
from pathlib import Path

from ..domain.errors import DependencyUnavailableError

logger = logging.getLogger(__name__)

__all__ = ["EspeakTtsEngine", "espeak_available"]

_BINARY = "espeak-ng"


def espeak_available() -> bool:
    return shutil.which(_BINARY) is not None


class EspeakTtsEngine:
    def __init__(self) -> None:
        if not espeak_available():
            raise DependencyUnavailableError(
                "the espeak-ng binary is not on PATH", dependency="espeak-ng",
            )

    @property
    def name(self) -> str:
        return "espeak-ng"

    async def synthesize(self, text: str, *, voice: str | None = None) -> bytes:
        if not text.strip():
            return b""
        with tempfile.TemporaryDirectory() as tmp:
            out_path = Path(tmp) / "speech.wav"
            args = [_BINARY, "-w", str(out_path)]
            if voice:
                args += ["-v", voice]
            args.append(text)
            process = await asyncio.create_subprocess_exec(
                *args, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
            )
            _stdout, stderr = await process.communicate()
            if process.returncode != 0:
                logger.warning("espeak-ng failed", extra={"stderr": stderr.decode(errors="replace")[:500]})
                raise RuntimeError(f"espeak-ng exited {process.returncode}")
            return out_path.read_bytes()
