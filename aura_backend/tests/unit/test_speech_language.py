"""Script detection for the language-aware TTS router."""

from __future__ import annotations

from aura_core.adapters.speech_language import detect_speech_language


class TestDetectSpeechLanguage:
    def test_arabic(self) -> None:
        assert detect_speech_language("مرحبا، كيف حالك؟") == "ar"

    def test_english(self) -> None:
        assert detect_speech_language("The quarterly report is ready.") == "en"

    def test_mixed_arabic_majority_or_tie_is_arabic(self) -> None:
        # Five Arabic letters, five Latin -- Arabic count >= Latin and Arabic > 0.
        assert detect_speech_language("مرحبا hello") == "ar"

    def test_mixed_latin_majority_is_english(self) -> None:
        assert detect_speech_language("Hello there مرحبا") == "en"

    def test_empty_and_punctuation_are_english(self) -> None:
        assert detect_speech_language("") == "en"
        assert detect_speech_language("   ") == "en"
        assert detect_speech_language("...") == "en"
        assert detect_speech_language("123") == "en"

    def test_tashkeel_counts_as_arabic(self) -> None:
        assert detect_speech_language("مَرْحَبًا") == "ar"
