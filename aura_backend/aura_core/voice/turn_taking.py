"""Turn-taking / endpointing state machine (Phase H)."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

__all__ = ["EndpointConfig", "EndpointDetector", "EndpointEvent", "ListenPhase"]


class ListenPhase(str, Enum):
    IDLE = "idle"
    IN_SPEECH = "in_speech"


class EndpointEvent(str, Enum):
    NONE = "none"
    SPEECH_STARTED = "speech_started"
    SPEECH_ENDED = "speech_ended"
    BARGE_IN = "barge_in"
    MAX_UTTERANCE = "max_utterance"


@dataclass(frozen=True, slots=True)
class EndpointConfig:
    frame_ms: int = 20
    min_speech_ms: int = 240
    silence_hangover_ms: int = 600
    barge_in_ms: int = 280
    max_utterance_ms: int = 15_000


class EndpointDetector:
    """Accumulates VAD decisions into start/end/barge-in events."""

    def __init__(self, config: EndpointConfig | None = None) -> None:
        self.config = config or EndpointConfig()
        self.phase = ListenPhase.IDLE
        self._speech_ms = 0
        self._silence_ms = 0
        self._barge_ms = 0
        self._utterance_ms = 0

    def reset(self) -> None:
        self.phase = ListenPhase.IDLE
        self._speech_ms = 0
        self._silence_ms = 0
        self._barge_ms = 0
        self._utterance_ms = 0

    def feed(
        self,
        is_speech: bool,
        *,
        session_busy: bool = False,
    ) -> EndpointEvent:
        """Feed one frame's speech decision.

        ``session_busy`` is True while the session is THINKING or SPEAKING — sustained speech
        then becomes barge-in rather than a new utterance start.
        """
        ms = self.config.frame_ms
        if session_busy:
            if is_speech:
                self._barge_ms += ms
                if self._barge_ms >= self.config.barge_in_ms:
                    self._barge_ms = 0
                    return EndpointEvent.BARGE_IN
            else:
                self._barge_ms = 0
            return EndpointEvent.NONE

        if is_speech:
            self._silence_ms = 0
            if self.phase is ListenPhase.IDLE:
                self._speech_ms += ms
                if self._speech_ms >= self.config.min_speech_ms:
                    self.phase = ListenPhase.IN_SPEECH
                    self._utterance_ms = self._speech_ms
                    return EndpointEvent.SPEECH_STARTED
                return EndpointEvent.NONE
            self._utterance_ms += ms
            if self._utterance_ms >= self.config.max_utterance_ms:
                self.phase = ListenPhase.IDLE
                self._speech_ms = 0
                return EndpointEvent.MAX_UTTERANCE
            return EndpointEvent.NONE

        # silence
        if self.phase is ListenPhase.IDLE:
            self._speech_ms = 0
            return EndpointEvent.NONE
        self._silence_ms += ms
        if self._silence_ms >= self.config.silence_hangover_ms:
            self.phase = ListenPhase.IDLE
            self._speech_ms = 0
            self._silence_ms = 0
            return EndpointEvent.SPEECH_ENDED
        return EndpointEvent.NONE
