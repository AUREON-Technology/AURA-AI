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
    "tashkeel_model_for",
    "DEFAULT_PIPER_EN_MODEL",
    "DEFAULT_PIPER_AR_MODEL",
    "DEFAULT_PIPER_LENGTH_SCALE",
    "DEFAULT_PIPER_NOISE_SCALE",
    "DEFAULT_PIPER_NOISE_W",
    "DEFAULT_PIPER_SENTENCE_SILENCE",
    "DEFAULT_PIPER_TASHKEEL_MODEL",
]

_BINARY = "piper"

# Baked by the gateway Dockerfile. Operators override via ``VoiceSettings``.
DEFAULT_PIPER_EN_MODEL = "/opt/piper/voices/en_US-lessac-medium.onnx"
DEFAULT_PIPER_AR_MODEL = "/opt/piper/voices/ar_JO-kareem-medium.onnx"

# Prosody. The voice's own ``.onnx.json`` declares Piper's stock inference values --
# ``noise_scale 0.667``, ``length_scale 1.0``, ``noise_w 0.8`` -- and the binary defaults
# ``sentence_silence`` to 0.2s. Every value below is a deliberate, bounded step away from
# those, in the direction of speech rather than readout:
#
# * ``length_scale`` slightly slower than stock -- neural pacing, not espeak rate.
# * ``noise_w`` is per-phoneme *duration* variance. At the stock 0.8 every syllable gets
#   near-identical length, which is the metronomic quality people hear as "robotic"; raising
#   it loosens the rhythm. Past ~1.0 the voice starts to slur, so this stays well under.
# * ``noise_scale`` is generator noise -- a little more timbral variation between utterances.
#   Above ~0.8 it turns into audible artefacting.
# * ``sentence_silence`` at 0.2s runs sentences together; a real speaker pauses roughly half
#   a second. This one carries no artefact risk, only length.
DEFAULT_PIPER_LENGTH_SCALE = 1.08
DEFAULT_PIPER_NOISE_SCALE = 0.72
DEFAULT_PIPER_NOISE_W = 0.9
DEFAULT_PIPER_SENTENCE_SILENCE = 0.45

# Shipped inside the Piper release tarball and extracted alongside the binary, so it is
# already on the image -- it was simply never passed. Piper needs it to diacritize Arabic
# before phonemization; without it, undiacritized Arabic gets guessed vowels.
DEFAULT_PIPER_TASHKEEL_MODEL = "/opt/piper/libtashkeel_model.ort"


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


def tashkeel_model_for(model_path: str | Path, tashkeel_model: str | None) -> str | None:
    """The tashkeel model to pass for this voice, or ``None``.

    Only Arabic voices need it, and only when the file is actually present -- Piper ignores
    flags it does not recognise but fails outright on a missing tashkeel model, so a partial
    image must degrade to undiacritized Arabic rather than to no speech at all.
    """
    if not tashkeel_model:
        return None
    if piper_voice_name(model_path) != "piper-ar":
        return None
    return tashkeel_model if Path(tashkeel_model).is_file() else None


def build_piper_command(
    out_path: str,
    *,
    model_path: str,
    length_scale: float = DEFAULT_PIPER_LENGTH_SCALE,
    noise_scale: float = DEFAULT_PIPER_NOISE_SCALE,
    noise_w: float = DEFAULT_PIPER_NOISE_W,
    sentence_silence: float = DEFAULT_PIPER_SENTENCE_SILENCE,
    tashkeel_model: str | None = None,
) -> list[str]:
    """argv for one synthesis. Text is supplied on stdin, not as a positional -- Piper's
    documented interface, and it keeps Arabic UTF-8 out of the argument vector.

    Flag spellings are the hyphenated forms the 2023.11.14 binary accepts. This matters more
    than it looks: Piper's parser silently ignores an argument it does not recognise, so a
    misspelled prosody flag would leave the voice at stock settings with nothing in the logs.
    """
    args = [
        _BINARY,
        "--model",
        model_path,
        "--output_file",
        out_path,
        "--length-scale",
        str(length_scale),
        "--noise-scale",
        str(noise_scale),
        "--noise-w",
        str(noise_w),
        "--sentence-silence",
        str(sentence_silence),
    ]
    if tashkeel_model:
        args += ["--tashkeel-model", tashkeel_model]
    return args


class PiperTtsEngine:
    def __init__(
        self,
        *,
        model_path: str,
        length_scale: float = DEFAULT_PIPER_LENGTH_SCALE,
        noise_scale: float = DEFAULT_PIPER_NOISE_SCALE,
        noise_w: float = DEFAULT_PIPER_NOISE_W,
        sentence_silence: float = DEFAULT_PIPER_SENTENCE_SILENCE,
        tashkeel_model: str | None = DEFAULT_PIPER_TASHKEEL_MODEL,
    ) -> None:
        if not piper_available(model_path):
            raise DependencyUnavailableError(
                "the piper binary or the ONNX voice is missing",
                dependency="piper",
            )
        self._model_path = model_path
        self._length_scale = length_scale
        self._noise_scale = noise_scale
        self._noise_w = noise_w
        self._sentence_silence = sentence_silence
        self._tashkeel_model = tashkeel_model_for(model_path, tashkeel_model)
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
                noise_scale=self._noise_scale,
                noise_w=self._noise_w,
                sentence_silence=self._sentence_silence,
                tashkeel_model=self._tashkeel_model,
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
