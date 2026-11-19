"""``AURA_VOICE_LIVE_VAD`` picks the live-voice detector, and readiness names the one in use."""

from __future__ import annotations

import pytest

from aura_core.config.settings import VoiceSettings
from aura_core.domain.errors import ConfigurationError


class TestTheSetting:
    def test_energy_is_the_default(self, monkeypatch) -> None:
        monkeypatch.delenv("AURA_VOICE_LIVE_VAD", raising=False)
        assert VoiceSettings.from_env().live_vad == "energy"

    def test_silero_is_accepted_case_insensitively(self, monkeypatch) -> None:
        monkeypatch.setenv("AURA_VOICE_LIVE_VAD", " Silero ")
        assert VoiceSettings.from_env().live_vad == "silero"

    def test_an_unknown_detector_is_refused_at_boot(self, monkeypatch) -> None:
        monkeypatch.setenv("AURA_VOICE_LIVE_VAD", "webrtc")
        with pytest.raises(ConfigurationError):
            VoiceSettings.from_env()

    def test_barge_in_cannot_be_easier_than_starting_an_utterance(self, monkeypatch) -> None:
        monkeypatch.setenv("AURA_VOICE_LIVE_VAD_THRESHOLD", "0.8")
        monkeypatch.setenv("AURA_VOICE_LIVE_VAD_BARGE_IN_THRESHOLD", "0.6")
        with pytest.raises(ConfigurationError):
            VoiceSettings.from_env()


def _live_container(monkeypatch, vad: str):
    from aura_core.adapters.espeak_tts import espeak_available
    from aura_core.adapters.whisper_stt import faster_whisper_available
    from aura_core.config import load_settings
    from aura_core.container import Container

    if not (espeak_available() and faster_whisper_available()):
        pytest.skip("live voice needs both speech engines (espeak-ng and faster-whisper)")
    monkeypatch.setenv("AURA_VOICE_LIVE_ENABLED", "true")
    monkeypatch.setenv("AURA_VOICE_LIVE_VAD", vad)
    container = Container(load_settings(refresh=True, validate=False))
    assert container.live_voice is not None
    return container


class TestTheContainerHandsEachSessionItsDetector:
    async def test_energy_gives_sessions_the_default(self, monkeypatch) -> None:
        container = _live_container(monkeypatch, "energy")
        assert container.live_voice["vad_factory"]() is None
        report = await container.health()
        assert report["voice"]["vad"] == "energy"

    async def test_silero_gives_each_session_a_fresh_detector(self, monkeypatch) -> None:
        from aura_core.adapters.silero_vad import SileroVad, silero_vad_available

        if not silero_vad_available():
            pytest.skip("Silero model not installed")
        container = _live_container(monkeypatch, "silero")
        factory = container.live_voice["vad_factory"]
        first, second = factory(), factory()
        assert isinstance(first, SileroVad) and isinstance(second, SileroVad)
        assert first is not second
        report = await container.health()
        assert report["voice"]["vad"] == "silero"

    async def test_silero_on_a_48k_capture_rate_reports_the_fallback(self, monkeypatch) -> None:
        monkeypatch.setenv("AURA_VOICE_LIVE_SAMPLE_RATE", "48000")
        container = _live_container(monkeypatch, "silero")
        assert container.live_voice["vad_factory"]() is None
        report = await container.health()
        assert report["voice"]["vad"] == "energy"
