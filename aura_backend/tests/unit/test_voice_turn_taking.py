"""Endpoint / barge-in turn-taking tests (Phase H)."""

from __future__ import annotations

from aura_core.voice.turn_taking import EndpointConfig, EndpointDetector, EndpointEvent


class TestEndpointDetector:
    def test_min_speech_rejects_cough(self) -> None:
        det = EndpointDetector(EndpointConfig(frame_ms=20, min_speech_ms=100, silence_hangover_ms=60))
        # 40 ms of speech — below min
        assert det.feed(True) is EndpointEvent.NONE
        assert det.feed(True) is EndpointEvent.NONE
        assert det.feed(False) is EndpointEvent.NONE

    def test_speech_start_and_hangover_end(self) -> None:
        det = EndpointDetector(EndpointConfig(frame_ms=20, min_speech_ms=40, silence_hangover_ms=40))
        assert det.feed(True) is EndpointEvent.NONE
        assert det.feed(True) is EndpointEvent.SPEECH_STARTED
        assert det.feed(False) is EndpointEvent.NONE
        assert det.feed(False) is EndpointEvent.SPEECH_ENDED

    def test_barge_in_while_busy(self) -> None:
        det = EndpointDetector(EndpointConfig(frame_ms=20, barge_in_ms=40))
        assert det.feed(True, session_busy=True) is EndpointEvent.NONE
        assert det.feed(True, session_busy=True) is EndpointEvent.BARGE_IN

    def test_max_utterance(self) -> None:
        det = EndpointDetector(
            EndpointConfig(frame_ms=20, min_speech_ms=20, max_utterance_ms=40, silence_hangover_ms=200)
        )
        assert det.feed(True) is EndpointEvent.SPEECH_STARTED
        assert det.feed(True) is EndpointEvent.MAX_UTTERANCE
