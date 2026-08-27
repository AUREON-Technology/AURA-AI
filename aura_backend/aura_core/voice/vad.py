"""Energy-based voice activity detection (Phase H).

Pure functions over PCM16 frames. Deterministic and free of vendor SDKs. Not equivalent to a
neural VAD; barge-in while SPEAKING uses an elevated threshold via :class:`VadConfig`.
"""

from __future__ import annotations

import math
import struct
from dataclasses import dataclass

__all__ = ["VadConfig", "EnergyVad", "VadDecision"]


@dataclass(frozen=True, slots=True)
class VadConfig:
    sample_rate: int = 16_000
    frame_ms: int = 20
    # Absolute floor; adaptive floor starts here and tracks upward slowly.
    noise_floor_rms: float = 200.0
    speech_ratio: float = 2.5
    barge_in_ratio: float = 4.0
    min_zero_crossings: int = 2
    max_zero_crossings: int = 80


@dataclass(frozen=True, slots=True)
class VadDecision:
    is_speech: bool
    rms: float
    zero_crossings: int


class EnergyVad:
    """RMS + zero-crossing VAD over 20 ms PCM16 mono frames."""

    def __init__(self, config: VadConfig | None = None) -> None:
        self.config = config or VadConfig()
        self._noise_floor = self.config.noise_floor_rms

    @property
    def frame_bytes(self) -> int:
        samples = max(1, int(self.config.sample_rate * self.config.frame_ms / 1000))
        return samples * 2

    def reset(self) -> None:
        self._noise_floor = self.config.noise_floor_rms

    def decide(self, frame: bytes, *, elevated: bool = False) -> VadDecision:
        rms, zc = _frame_features(frame)
        if rms < self._noise_floor * 1.2:
            # Slow adapt toward quieter floors.
            self._noise_floor = (0.95 * self._noise_floor) + (0.05 * max(rms, 1.0))
        ratio = self.config.barge_in_ratio if elevated else self.config.speech_ratio
        threshold = max(self.config.noise_floor_rms, self._noise_floor * ratio)
        speech = (
            rms >= threshold
            and self.config.min_zero_crossings <= zc <= self.config.max_zero_crossings
        )
        return VadDecision(is_speech=speech, rms=rms, zero_crossings=zc)


def _frame_features(frame: bytes) -> tuple[float, int]:
    if len(frame) < 2:
        return 0.0, 0
    # Truncate odd trailing byte.
    n = len(frame) // 2
    samples = struct.unpack(f"<{n}h", frame[: n * 2])
    if not samples:
        return 0.0, 0
    acc = 0.0
    zc = 0
    prev = samples[0]
    for s in samples:
        acc += float(s) * float(s)
        if (prev >= 0 > s) or (prev < 0 <= s):
            zc += 1
        prev = s
    rms = math.sqrt(acc / len(samples))
    return rms, zc
