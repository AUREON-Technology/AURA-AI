"""Measure speech-to-text word error rate per language (GAP-020's evidence).

GAP-020 stays open until Arabic and English recognition have a *measured* word error rate on the
model production runs. This script produces that number. It runs a labelled clip set through
the configured faster-whisper size, exactly as ``POST /api/v3/voice/transcribe`` does, and writes
a Markdown report.

The clips are not in the repository: they are audio, and their licence is the dataset's. A
manifest is JSON Lines, one clip per line, paths relative to the manifest::

    {"audio": "clips/ar_0001.mp3", "language": "ar", "text": "مرحبا بك"}
    {"audio": "clips/en_0001.mp3", "language": "en", "text": "The meeting starts at noon."}

Common Voice (CC0) is the suggested source: take 50+ validated clips per language from its
``validated.tsv`` (``path`` and ``sentence`` columns). Opt-in, needs the model weights (a Hub
download on first use), and never part of the default ``pytest`` run::

    python scripts/measure_stt_wer.py --manifest ~/wer/manifest.jsonl --model base \\
        --revision <hub-sha> --out ../docs/reports/STT_WER_2026-11-18.md

Each language is scored twice: with its language passed, as the app sends it after Phase V,
and with no language (Whisper detects it), which is what an older client gets.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import re
import sys
import time
import unicodedata
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable, Sequence

# `python scripts/measure_stt_wer.py` puts scripts/ on sys.path, not the backend root.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

#: Arabic diacritics (harakat, tanwin, shadda, sukun, dagger alef) and tatweel. Whisper writes
#: them inconsistently and Common Voice transcripts rarely carry them, so a diacritic is not a
#: word error. The usual Arabic ASR normalisation.
_AR_MARKS = re.compile("[ؐ-ًؚ-ٰٟۖ-ۭـ]")
_AR_LETTERS = str.maketrans({"أ": "ا", "إ": "ا", "آ": "ا", "ٱ": "ا", "ى": "ي", "ة": "ه"})
_AR_DIGITS = str.maketrans("٠١٢٣٤٥٦٧٨٩", "0123456789")


def normalise(text: str, language: str) -> list[str]:
    """Words to compare: case-folded, punctuation removed, Arabic letter variants unified."""
    text = unicodedata.normalize("NFKC", text or "")
    if language == "ar":
        text = _AR_MARKS.sub("", text).translate(_AR_LETTERS).translate(_AR_DIGITS)
    text = text.casefold()
    # Unicode punctuation (P*) and symbols (S*) become spaces; letters and digits stay.
    text = "".join(" " if unicodedata.category(c)[0] in "PS" else c for c in text)
    return text.split()


def word_errors(reference: Sequence[str], hypothesis: Sequence[str]) -> int:
    """Substitutions + deletions + insertions: word-level Levenshtein distance."""
    previous = list(range(len(hypothesis) + 1))
    for i, ref_word in enumerate(reference, start=1):
        current = [i]
        for j, hyp_word in enumerate(hypothesis, start=1):
            current.append(
                min(
                    previous[j] + 1,  # deletion
                    current[j - 1] + 1,  # insertion
                    previous[j - 1] + (ref_word != hyp_word),  # substitution
                )
            )
        previous = current
    return previous[-1]


@dataclass
class Score:
    clips: int = 0
    reference_words: int = 0
    errors: int = 0
    audio_s: float = 0.0
    compute_s: float = 0.0
    detected_wrong: int = 0
    worst: list[tuple[float, str, str, str]] = field(default_factory=list)

    @property
    def wer(self) -> float:
        return self.errors / self.reference_words if self.reference_words else 0.0

    @property
    def rtf(self) -> float:
        """Real-time factor: seconds of compute per second of audio (below 1 keeps up)."""
        return self.compute_s / self.audio_s if self.audio_s else 0.0


def load_manifest(path: Path) -> list[dict]:
    clips = []
    for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        if not line.strip():
            continue
        row = json.loads(line)
        missing = {"audio", "language", "text"} - row.keys()
        if missing:
            raise SystemExit(f"{path}:{number}: missing {sorted(missing)}")
        row["audio"] = str((path.parent / row["audio"]).resolve())
        clips.append(row)
    return clips


async def measure(
    clips: Iterable[dict], *, model: str, revision: str
) -> dict[tuple[str, bool], Score]:
    """Scores keyed by ``(language, language_passed)``."""
    from faster_whisper import decode_audio

    from aura_core.adapters.whisper_stt import FasterWhisperEngine

    engine = FasterWhisperEngine(model_size=model, revision=revision)
    await engine.warm()
    scores: dict[tuple[str, bool], Score] = {}
    for clip in clips:
        audio = Path(clip["audio"]).read_bytes()
        duration = len(decode_audio(clip["audio"])) / 16_000
        language = clip["language"]
        reference = normalise(clip["text"], language)
        for passed in (True, False):
            if passed and not engine.multilingual and language != "en":
                continue  # the route refuses this pairing (422); nothing to measure
            started = time.perf_counter()
            text, used = await engine.transcribe_detect(
                audio, language=language if passed else None
            )
            elapsed = time.perf_counter() - started
            score = scores.setdefault((language, passed), Score())
            errors = word_errors(reference, normalise(text, language))
            score.clips += 1
            score.reference_words += len(reference)
            score.errors += errors
            score.audio_s += duration
            score.compute_s += elapsed
            score.detected_wrong += int(not passed and used != language)
            rate = errors / len(reference) if reference else 0.0
            score.worst.append((rate, Path(clip["audio"]).name, clip["text"], text))
    return scores


def render(scores: dict[tuple[str, bool], Score], *, model: str, revision: str) -> str:
    lines = [
        f"# Speech-to-text WER: faster-whisper `{model}`",
        "",
        f"Revision `{revision or 'unpinned (Hub head)'}`, CPU int8, "
        f"generated by `scripts/measure_stt_wer.py`.",
        "",
        "| Language | Language sent | Clips | Words | WER | Real-time factor | Wrong language detected |",
        "| --- | --- | ---: | ---: | ---: | ---: | ---: |",
    ]
    for (language, passed), s in sorted(scores.items()):
        wrong = "n/a" if passed else f"{s.detected_wrong}/{s.clips}"
        lines.append(
            f"| {language} | {'yes' if passed else 'no (detected)'} | {s.clips} | "
            f"{s.reference_words} | {s.wer:.1%} | {s.rtf:.2f} | {wrong} |"
        )
    for (language, passed), s in sorted(scores.items()):
        if not passed:
            continue
        lines += ["", f"## Worst {language} clips (language sent)", ""]
        for rate, name, reference, hypothesis in sorted(s.worst, reverse=True)[:5]:
            lines.append(f"- `{name}` ({rate:.0%}): ref «{reference}» / got «{hypothesis}»")
    return "\n".join(lines) + "\n"


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--model", default="base")
    parser.add_argument("--revision", default="")
    parser.add_argument("--out", type=Path, help="write the report here (default: stdout)")
    args = parser.parse_args(argv)
    clips = load_manifest(args.manifest)
    if not clips:
        print("the manifest lists no clips", file=sys.stderr)
        return 1
    report = render(
        asyncio.run(measure(clips, model=args.model, revision=args.revision)),
        model=args.model,
        revision=args.revision,
    )
    if args.out:
        args.out.write_text(report, encoding="utf-8")
    else:
        sys.stdout.write(report)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
