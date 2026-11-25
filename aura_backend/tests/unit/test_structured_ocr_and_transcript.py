"""Structured OCR (words, confidences, boxes) and timestamped transcripts.

Two additive capabilities next to the plain ones: ``TesseractOcrEngine.image_data`` and
``FasterWhisperEngine.transcribe_segments``.

* OCR tests use the REAL tesseract engine on a real Pillow-rendered image and skip cleanly when
  the binary is absent (same convention as ``test_tesseract_ocr.py``).
* Whisper tests are ADAPTER-BOUNDARY tests of the segment mapping only: no model is downloaded,
  so a tiny fake model object mimics faster-whisper's ``transcribe`` return shape
  (``(iterable_of_segments, info)``). They prove how the adapter maps that shape, not that
  Whisper recognises speech -- that is ``test_voice_adapters.py``'s opt-in real round trip.
"""

from __future__ import annotations

import io
from types import SimpleNamespace

import pytest

from aura_core.adapters import tesseract_ocr as ocr_mod
from aura_core.adapters import whisper_stt as stt_mod
from aura_core.adapters.tesseract_ocr import (
    TesseractOcrEngine,
    parse_tesseract_data,
    tesseract_available,
)
from aura_core.adapters.whisper_stt import FasterWhisperEngine
from aura_core.ports.documents import OcrEngine, OcrResult, StructuredOcrEngine
from aura_core.ports.speech import (
    SpeechToText,
    TimestampedSpeechToText,
    Transcript,
)

needs_tesseract = pytest.mark.skipif(
    not tesseract_available(), reason="tesseract binary not on PATH in this environment"
)

IMG_W, IMG_H = 900, 260


def _png(lines: list[str], *, size: tuple[int, int] = (IMG_W, IMG_H)) -> bytes:
    from PIL import Image, ImageDraw, ImageFont

    image = Image.new("RGB", size, color="white")
    draw = ImageDraw.Draw(image)
    try:
        font = ImageFont.load_default(size=36)
    except TypeError:  # very old Pillow: fixed bitmap font only
        font = ImageFont.load_default()
    for i, text in enumerate(lines):
        draw.text((20, 20 + i * 70), text, fill="black", font=font)
    buf = io.BytesIO()
    image.save(buf, format="PNG")
    return buf.getvalue()


# --------------------------------------------------------------------------- structured OCR


@needs_tesseract
class TestStructuredOcrReal:
    def test_engine_satisfies_both_ports(self) -> None:
        engine = TesseractOcrEngine()
        assert isinstance(engine, OcrEngine)
        assert isinstance(engine, StructuredOcrEngine)

    async def test_words_have_confidence_boxes_and_reading_order(self) -> None:
        engine = TesseractOcrEngine(lang="eng")
        result = await engine.image_data(_png(["Hello world", "Second line here"]), page=1)

        assert isinstance(result, OcrResult)
        assert (result.image_width, result.image_height) == (IMG_W, IMG_H)
        texts = [w.text.lower() for w in result.words]
        assert "hello" in texts and "world" in texts
        assert len(result.lines) == 2

        for word in result.words:
            assert word.confidence is not None
            assert 0 <= word.confidence <= 100
            box = word.box
            assert box.width > 0 and box.height > 0
            assert box.left >= 0 and box.top >= 0
            assert box.left + box.width <= IMG_W
            assert box.top + box.height <= IMG_H

        # Real glyphs rendered cleanly: the page should read with high confidence.
        assert result.mean_confidence is not None and result.mean_confidence > 60

        # Reading order: lines go top to bottom, words left to right (English).
        assert result.lines[0].box.top < result.lines[1].box.top
        first_line = result.lines[0].words
        assert [w.box.left for w in first_line] == sorted(w.box.left for w in first_line)
        assert result.lines[0].text.lower().startswith("hello world")
        # Line box is the union of its word boxes; line confidence is within the words' range.
        assert result.lines[0].box.left == min(w.box.left for w in first_line)
        confs = [w.confidence for w in first_line]
        assert min(confs) <= result.lines[0].confidence <= max(confs)
        assert result.text == "\n".join(line.text for line in result.lines)

    async def test_no_structural_or_negative_rows_leak_into_the_result(self) -> None:
        """tesseract's raw TSV interleaves page/block/paragraph/line rows with conf == -1 and no
        text; the structured result must contain none of them."""
        engine = TesseractOcrEngine(lang="eng")
        image = _png(["Hello world"])
        from PIL import Image

        with Image.open(io.BytesIO(image)) as pil:
            raw = engine._pytesseract.image_to_data(
                pil, lang="eng", output_type=engine._pytesseract.Output.DICT
            )
        assert any(float(c) == -1 for c in raw["conf"]), "premise: raw output has -1 rows"

        result = await engine.image_data(image, page=1)
        assert result.words
        assert all(w.confidence is None or w.confidence >= 0 for w in result.words)
        assert all(w.text.strip() for w in result.words)
        assert len(result.words) < len(raw["conf"])

    async def test_blank_image_gives_empty_result_with_no_confidence(self) -> None:
        engine = TesseractOcrEngine(lang="eng")
        result = await engine.image_data(_png([]), page=1)
        assert result.lines == ()
        assert result.words == ()
        assert result.text == ""
        assert result.mean_confidence is None

    async def test_undecodable_image_raises_instead_of_returning_empty(self) -> None:
        engine = TesseractOcrEngine(lang="eng")
        with pytest.raises(Exception):
            await engine.image_data(b"definitely not an image", page=3)

    async def test_arabic_is_recognised_when_the_language_pack_is_installed(self) -> None:
        engine = TesseractOcrEngine(lang="ara+eng")
        if "ara" not in engine._lang:
            pytest.skip("tesseract 'ara' traineddata not installed")
        # No guarantee the default bitmap font has Arabic glyphs, so this only proves the
        # combined-language call works end to end and yields a well-formed result.
        result = await engine.image_data(_png(["Hello world"]), page=1)
        assert isinstance(result, OcrResult)
        assert result.mean_confidence is None or 0 <= result.mean_confidence <= 100


class TestEngineFailureSurfaces:
    async def test_tesseract_failure_propagates_as_the_modules_usual_error(
        self, monkeypatch
    ) -> None:
        """Same behaviour as ``image_text``: the underlying error is logged and re-raised, so a
        broken install reads as "unread", never as an empty page."""
        if not tesseract_available():
            pytest.skip("tesseract binary not on PATH in this environment")
        import pytesseract

        class Boom(pytesseract.TesseractError):
            def __init__(self) -> None:
                super().__init__(1, "boom")

        def _fail(*_a, **_k):
            raise Boom()

        engine = TesseractOcrEngine(lang="eng")
        monkeypatch.setattr(engine._pytesseract, "image_to_data", _fail)
        with pytest.raises(pytesseract.TesseractError):
            await engine.image_data(_png(["hi"]), page=1)


class TestParseTesseractData:
    """The parser on a hand-built TSV dict: structural rows, -1 words and RTL order."""

    @staticmethod
    def _data(rows: list[tuple]) -> dict:
        keys = ["level", "block_num", "par_num", "line_num", "word_num",
                "left", "top", "width", "height", "conf", "text"]
        return {k: [r[i] for r in rows] for i, k in enumerate(keys)}

    def test_filters_structural_rows_and_blank_words_and_maps_unknown_confidence(self) -> None:
        rows = [
            (1, 0, 0, 0, 0, 0, 0, 500, 100, -1, ""),
            (2, 1, 0, 0, 0, 0, 0, 500, 100, -1, ""),
            (3, 1, 1, 0, 0, 0, 0, 500, 100, -1, ""),
            (4, 1, 1, 1, 0, 10, 10, 300, 30, -1, ""),
            (5, 1, 1, 1, 1, 10, 10, 100, 30, 96.5, "alpha"),
            (5, 1, 1, 1, 2, 120, 10, 100, 30, 80, "  "),       # blank word: dropped
            (5, 1, 1, 1, 3, 230, 10, 80, 30, -1, "beta"),       # unknown confidence: kept, None
            (4, 1, 1, 2, 0, 10, 60, 300, 30, -1, ""),
            (5, 1, 1, 2, 1, 10, 60, 90, 30, 50, "gamma"),
        ]
        result = parse_tesseract_data(self._data(rows), image_width=500, image_height=100)

        assert [w.text for w in result.words] == ["alpha", "beta", "gamma"]
        assert [w.confidence for w in result.words] == [96.5, None, 50.0]
        assert [line.text for line in result.lines] == ["alpha beta", "gamma"]
        assert result.lines[0].confidence == 96.5  # mean of the known confidences only
        assert result.mean_confidence == 73.25
        assert result.lines[0].box.width == 230 - 10 + 80  # union of alpha and beta

    def test_rtl_words_keep_the_engines_order(self) -> None:
        """Arabic lines come out of tesseract right to left (descending ``left``). The parser must
        not re-sort them by x."""
        rows = [
            (5, 1, 1, 1, 1, 400, 10, 90, 30, 90, "مرحبا"),
            (5, 1, 1, 1, 2, 250, 10, 90, 30, 88, "بالعالم"),
            (5, 1, 1, 1, 3, 100, 10, 90, 30, 85, "اليوم"),
        ]
        result = parse_tesseract_data(self._data(rows))
        assert [w.text for w in result.words] == ["مرحبا", "بالعالم", "اليوم"]
        assert result.lines[0].text == "مرحبا بالعالم اليوم"
        assert [w.box.left for w in result.words] == [400, 250, 100]

    def test_nothing_recognised_is_an_empty_result_with_no_confidence(self) -> None:
        rows = [(1, 0, 0, 0, 0, 0, 0, 10, 10, -1, ""), (2, 1, 0, 0, 0, 0, 0, 10, 10, -1, "")]
        result = parse_tesseract_data(self._data(rows))
        assert result.lines == () and result.text == "" and result.mean_confidence is None

    def test_module_parser_is_exported(self) -> None:
        assert ocr_mod.parse_tesseract_data is parse_tesseract_data


# --------------------------------------------------------------------------- transcripts


class _FakeModel:
    """Mimics ``WhisperModel.transcribe``: returns ``(lazy segments, info)`` and records kwargs."""

    def __init__(self, segments, info) -> None:
        self._segments = segments
        self._info = info
        self.calls: list[dict] = []

    def transcribe(self, audio, **kwargs):
        self.calls.append(kwargs)
        return iter(self._segments), self._info


def _seg(start, end, text, *, avg_logprob=None, no_speech_prob=None, words=None):
    return SimpleNamespace(
        start=start, end=end, text=text, avg_logprob=avg_logprob,
        no_speech_prob=no_speech_prob, words=words,
    )


def _word(start, end, word, probability):
    return SimpleNamespace(start=start, end=end, word=word, probability=probability)


@pytest.fixture
def engine(monkeypatch) -> FasterWhisperEngine:
    """An engine whose package check passes without faster-whisper being installed."""
    monkeypatch.setattr(stt_mod, "faster_whisper_available", lambda: True)
    return FasterWhisperEngine(model_size="base")


class TestTranscribeSegments:
    async def test_maps_segments_words_language_and_duration(self, engine) -> None:
        model = _FakeModel(
            [
                _seg(0.0, 2.4, " Hello there.", avg_logprob=-0.1, no_speech_prob=0.02,
                     words=[_word(0.0, 0.9, " Hello", 0.98), _word(1.0, 2.4, " there.", 0.91)]),
                _seg(2.4, 5.0, " General Kenobi.", avg_logprob=-0.5, no_speech_prob=0.1,
                     words=[_word(2.5, 3.9, " General", 0.8), _word(4.0, 5.0, " Kenobi.", 0.7)]),
            ],
            SimpleNamespace(language="en", language_probability=0.97, duration=5.2),
        )
        engine._model = model

        transcript = await engine.transcribe_segments(b"audio")

        assert isinstance(transcript, Transcript)
        assert transcript.text == "Hello there. General Kenobi."
        assert transcript.language == "en"
        assert transcript.language_probability == 0.97
        assert transcript.duration == 5.2
        assert model.calls == [{"language": None, "word_timestamps": True}]

        starts = [s.start for s in transcript.segments]
        ends = [s.end for s in transcript.segments]
        assert starts == sorted(starts) and ends == sorted(ends)
        assert all(s.end >= s.start for s in transcript.segments)
        assert transcript.segments[0].end - transcript.segments[0].start == pytest.approx(2.4)
        assert transcript.segments[1].start >= transcript.segments[0].end

        first = transcript.segments[0]
        assert first.text == "Hello there."
        assert first.avg_logprob == -0.1 and first.no_speech_prob == 0.02
        assert 0.8 < first.confidence <= 1.0
        assert transcript.segments[1].confidence < first.confidence
        assert [w.text for w in first.words] == ["Hello", "there."]
        assert first.words[0].confidence == 0.98
        all_words = [w for s in transcript.segments for w in s.words]
        assert [w.start for w in all_words] == sorted(w.start for w in all_words)

    async def test_speaker_is_never_invented(self, engine) -> None:
        engine._model = _FakeModel(
            [_seg(0, 1, "a"), _seg(1, 2, "b")], SimpleNamespace(language="en", duration=2.0)
        )
        transcript = await engine.transcribe_segments(b"audio")
        assert [s.speaker for s in transcript.segments] == [None, None]

    async def test_missing_optional_fields_become_none_not_zero(self, engine) -> None:
        engine._model = _FakeModel(
            [_seg(0.0, 1.0, " bare ")], SimpleNamespace(language="en")
        )
        transcript = await engine.transcribe_segments(b"audio", word_timestamps=False)
        segment = transcript.segments[0]
        assert segment.confidence is None
        assert segment.avg_logprob is None and segment.no_speech_prob is None
        assert segment.words == ()
        assert transcript.duration == 1.0  # falls back to the last segment's end
        assert engine._model.calls == [{"language": None, "word_timestamps": False}]

    async def test_language_hint_wins_over_detection(self, engine) -> None:
        engine._model = _FakeModel(
            [_seg(0, 1, "مرحبا")], SimpleNamespace(language="en", language_probability=0.4, duration=1.0)
        )
        transcript = await engine.transcribe_segments(b"audio", language="ar")
        assert transcript.language == "ar"
        assert transcript.language_probability is None  # detector did not decide it

    async def test_silence_is_an_empty_transcript_not_an_error(self, engine) -> None:
        engine._model = _FakeModel([], SimpleNamespace(language="en", duration=3.0))
        transcript = await engine.transcribe_segments(b"audio")
        assert transcript.text == "" and transcript.segments == ()
        assert transcript.duration == 3.0

    async def test_engine_failure_propagates(self, engine) -> None:
        class Broken:
            def transcribe(self, *_a, **_k):
                raise RuntimeError("decode failed")

        engine._model = Broken()
        with pytest.raises(RuntimeError):
            await engine.transcribe_segments(b"audio")

    async def test_existing_transcribe_is_unchanged(self, engine) -> None:
        model = _FakeModel(
            [_seg(0, 1, " one "), _seg(1, 2, " two ")], SimpleNamespace(language="en", duration=2.0)
        )
        engine._model = model
        assert await engine.transcribe(b"audio") == "one two"
        assert await engine.transcribe_detect(b"audio") == ("one two", "en")
        assert model.calls == [{"language": None}, {"language": None}]


class TestLazyInit:
    def test_construction_loads_no_model(self, monkeypatch) -> None:
        monkeypatch.setattr(stt_mod, "faster_whisper_available", lambda: True)

        def _no_load(self):
            raise AssertionError("the model must not load in __init__")

        monkeypatch.setattr(FasterWhisperEngine, "_load_model", _no_load)
        engine = FasterWhisperEngine(model_size="tiny.en")
        assert engine._model is None

    async def test_first_call_loads_once(self, monkeypatch, engine) -> None:
        loads: list[int] = []
        model = _FakeModel([_seg(0, 1, "x")], SimpleNamespace(language="en", duration=1.0))

        def _load(self):
            loads.append(1)
            return model

        monkeypatch.setattr(FasterWhisperEngine, "_load_model", _load)
        await engine.transcribe_segments(b"a")
        await engine.transcribe_segments(b"a")
        assert loads == [1]

    def test_engine_satisfies_both_speech_ports(self, engine) -> None:
        assert isinstance(engine, SpeechToText)
        assert isinstance(engine, TimestampedSpeechToText)
