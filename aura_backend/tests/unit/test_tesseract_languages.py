"""OCR reads Arabic and English, and a host missing a language pack degrades instead of failing
(docs/plans/arabic-language_plan.md, stage 4, decision 16).

Runs without the tesseract binary: the two ``pytesseract`` calls the constructor makes are faked.
"""

from __future__ import annotations

import pytest

from aura_core.adapters.tesseract_ocr import TesseractOcrEngine
from aura_core.config.settings import KnowledgeSettings


@pytest.fixture
def fake_tesseract(monkeypatch):
    pytesseract = pytest.importorskip("pytesseract")
    monkeypatch.setattr(pytesseract, "get_tesseract_version", lambda: "5.3.0")

    def install(*languages: str) -> None:
        monkeypatch.setattr(pytesseract, "get_languages", lambda config="": list(languages))

    return install


def test_arabic_and_english_is_the_default(monkeypatch) -> None:
    monkeypatch.delenv("AURA_KNOWLEDGE_OCR_LANG", raising=False)
    assert KnowledgeSettings.from_env().ocr_lang == "ara+eng"


def test_both_installed_both_used(fake_tesseract) -> None:
    fake_tesseract("eng", "ara", "osd")
    assert TesseractOcrEngine(lang="ara+eng")._lang == "ara+eng"


def test_a_missing_arabic_pack_keeps_english_working(fake_tesseract) -> None:
    """Tesseract refuses a whole request naming an uninstalled language; dropping it keeps OCR."""
    fake_tesseract("eng", "osd")
    assert TesseractOcrEngine(lang="ara+eng")._lang == "eng"


def test_nothing_installed_keeps_the_configuration_so_the_error_is_real(fake_tesseract) -> None:
    fake_tesseract("osd")
    assert TesseractOcrEngine(lang="ara+eng")._lang == "ara+eng"
