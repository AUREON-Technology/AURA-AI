"""Neural voice-activity detection for live voice: Silero VAD, streamed frame by frame (GAP-020).

The model is the ``silero_vad_v6.onnx`` that faster-whisper already ships for its own
``vad_filter``, run with onnxruntime -- both already in ``requirements.lock``, so this adds no
dependency and downloads nothing. faster-whisper's own wrapper resets the model's recurrent
state on every call, which suits a whole clip but not a live stream, so this adapter drives the
ONNX session directly and keeps that state, plus the 64-sample context the model expects, for
the life of one session.

Silero scores 512-sample windows (32 ms at 16 kHz); a live client sends 20 ms frames. Samples
are buffered until a window is full, and each frame's decision uses the newest score -- a lag of
at most one window, which the endpointer's 240 ms minimum-speech and 600 ms hangover dwarf.

One ``InferenceSession`` is shared by every live session in the process (``run`` is
thread-safe); each :class:`SileroVad` holds only its own state. The model loads on the first
:func:`build_silero_vad` call, never at import, and a failure to load is reported by returning
``None`` so the container falls back to the energy detector rather than refusing a session.
"""

from __future__ import annotations

import os
import threading
from typing import Any

from ..observability.logging import get_logger
from ..voice.vad import VadDecision, frame_features

__all__ = ["SileroVad", "build_silero_vad", "silero_vad_available"]

logger = get_logger(__name__)

#: The only rate this model scores in 512-sample windows (8 kHz would need 256).
SILERO_SAMPLE_RATE = 16_000
_WINDOW = 512
_CONTEXT = 64
_STATE_SHAPE = (1, 1, 128)

_session_lock = threading.Lock()
_session: Any = None


def silero_vad_available() -> bool:
    """Whether onnxruntime and faster-whisper's bundled Silero model are both importable."""
    try:
        import onnxruntime  # noqa: F401
        from faster_whisper.vad import get_assets_path
    except ImportError:
        return False
    return os.path.isfile(os.path.join(get_assets_path(), "silero_vad_v6.onnx"))


def _shared_session() -> Any:
    global _session
    if _session is not None:
        return _session
    with _session_lock:
        if _session is None:
            import onnxruntime
            from faster_whisper.vad import get_assets_path

            opts = onnxruntime.SessionOptions()
            # One thread each: a frame is 576 floats, and live sessions run concurrently on
            # the event loop; a thread pool per call would cost more than the inference.
            opts.inter_op_num_threads = 1
            opts.intra_op_num_threads = 1
            opts.log_severity_level = 4
            _session = onnxruntime.InferenceSession(
                os.path.join(get_assets_path(), "silero_vad_v6.onnx"),
                providers=["CPUExecutionProvider"],
                sess_options=opts,
            )
    return _session


class SileroVad:
    """A :class:`~aura_core.voice.vad.VoiceActivityDetector` backed by Silero VAD.

    ``threshold`` is the speech probability a frame needs while the session is listening;
    ``barge_in_threshold`` the probability it needs while AURA is thinking or speaking.
    """

    def __init__(
        self,
        session: Any,
        *,
        threshold: float = 0.5,
        barge_in_threshold: float = 0.7,
    ) -> None:
        import numpy as np

        self._np = np
        self._session = session
        self.threshold = threshold
        self.barge_in_threshold = barge_in_threshold
        self._pending = np.zeros(0, dtype=np.float32)
        self.reset()

    @property
    def probability(self) -> float:
        """The newest speech probability (0 before the first full window)."""
        return self._probability

    def reset(self) -> None:
        np = self._np
        self._h = np.zeros(_STATE_SHAPE, dtype=np.float32)
        self._c = np.zeros(_STATE_SHAPE, dtype=np.float32)
        self._context = np.zeros(_CONTEXT, dtype=np.float32)
        self._pending = np.zeros(0, dtype=np.float32)
        self._probability = 0.0

    def decide(self, frame: bytes, *, elevated: bool = False) -> VadDecision:
        np = self._np
        rms, zero_crossings = frame_features(frame)
        usable = len(frame) - (len(frame) % 2)
        if usable:
            samples = np.frombuffer(frame[:usable], dtype="<i2").astype(np.float32) / 32768.0
            self._pending = np.concatenate((self._pending, samples))
        while self._pending.shape[0] >= _WINDOW:
            window = self._pending[:_WINDOW]
            self._pending = self._pending[_WINDOW:]
            batch = np.concatenate((self._context, window)).reshape(1, _CONTEXT + _WINDOW)
            out, self._h, self._c = self._session.run(
                None, {"input": batch, "h": self._h, "c": self._c}
            )
            self._context = window[-_CONTEXT:]
            self._probability = float(out.reshape(-1)[0])
        bar = self.barge_in_threshold if elevated else self.threshold
        return VadDecision(
            is_speech=self._probability >= bar, rms=rms, zero_crossings=zero_crossings
        )


def build_silero_vad(
    *, sample_rate: int, threshold: float, barge_in_threshold: float
) -> SileroVad | None:
    """A fresh detector for one live session, or ``None`` (logged) when Silero cannot serve:
    a capture rate other than 16 kHz, a missing package or model, or a model that fails to
    load. The caller falls back to the energy detector."""
    if sample_rate != SILERO_SAMPLE_RATE:
        logger.warning(
            "silero vad needs a 16 kHz capture rate; using the energy detector",
            extra={"sample_rate": sample_rate},
        )
        return None
    if not silero_vad_available():
        logger.warning("silero vad is not installed; using the energy detector")
        return None
    try:
        session = _shared_session()
    except Exception as exc:
        logger.warning(
            "silero vad failed to load; using the energy detector",
            extra={"error": type(exc).__name__},
        )
        return None
    return SileroVad(session, threshold=threshold, barge_in_threshold=barge_in_threshold)
