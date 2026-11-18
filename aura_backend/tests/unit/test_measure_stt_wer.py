"""The scoring half of ``scripts/measure_stt_wer.py``: the numbers GAP-020's report will carry.

Measuring itself needs model weights and is opt-in; what is checked here is that a WER the
report states means what it says.
"""

from __future__ import annotations

import json

from scripts.measure_stt_wer import Score, load_manifest, normalise, render, word_errors


class TestWordErrors:
    def test_identical_is_zero(self) -> None:
        assert word_errors(["a", "b", "c"], ["a", "b", "c"]) == 0

    def test_counts_substitution_deletion_and_insertion(self) -> None:
        assert word_errors(["a", "b", "c"], ["a", "x", "c"]) == 1
        assert word_errors(["a", "b", "c"], ["a", "c"]) == 1
        assert word_errors(["a", "b", "c"], ["a", "b", "c", "d"]) == 1

    def test_empty_hypothesis_is_every_word_missed(self) -> None:
        assert word_errors(["a", "b", "c"], []) == 3


class TestNormalise:
    def test_english_ignores_case_and_punctuation(self) -> None:
        assert normalise("The meeting, at NOON!", "en") == ["the", "meeting", "at", "noon"]

    def test_arabic_diacritics_and_tatweel_are_not_errors(self) -> None:
        assert normalise("مَرْحَبًا بِكَ", "ar") == normalise("مرحبا بك", "ar")
        assert normalise("مـرحـبا", "ar") == ["مرحبا"]

    def test_arabic_letter_variants_are_unified(self) -> None:
        assert normalise("أحمد إلى آخر مدرسة", "ar") == normalise("احمد الي اخر مدرسه", "ar")

    def test_arabic_punctuation_and_digits(self) -> None:
        assert normalise("الساعة ١٢، الآن؟", "ar") == ["الساعه", "12", "الان"]


class TestReport:
    def test_wer_and_real_time_factor(self) -> None:
        score = Score(clips=2, reference_words=10, errors=2, audio_s=8.0, compute_s=2.0)
        assert score.wer == 0.2
        assert score.rtf == 0.25
        report = render({("ar", True): score}, model="base", revision="abc")
        assert "| ar | yes | 2 | 10 | 20.0% | 0.25 | n/a |" in report
        assert "`abc`" in report

    def test_the_manifest_resolves_paths_next_to_it(self, tmp_path) -> None:
        (tmp_path / "m.jsonl").write_text(
            json.dumps({"audio": "c/1.mp3", "language": "ar", "text": "مرحبا"}) + "\n\n",
            encoding="utf-8",
        )
        [clip] = load_manifest(tmp_path / "m.jsonl")
        assert clip["audio"] == str((tmp_path / "c" / "1.mp3").resolve())
