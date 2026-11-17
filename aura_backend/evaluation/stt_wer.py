#!/usr/bin/env python3
"""Word error rate of a faster-whisper model on a FLEURS-style test split (GAP-020).

GAP-020's exit condition is "a WER report in docs/reports/ for both languages on the production
ASR model" (docs/production/gap_register.yaml). This produces the numbers for that report from
real recorded speech, not synthesized audio: Google's FLEURS test splits (``ar_eg`` is Egyptian
Arabic, ``en_us`` American English), downloaded by the operator:

    https://huggingface.co/datasets/google/fleurs/resolve/main/data/<lang>/test.tsv
    https://huggingface.co/datasets/google/fleurs/resolve/main/data/<lang>/audio/test.tar.gz

Usage (from aura_backend/, with the dev venv or inside the gateway image)::

    python evaluation/stt_wer.py --split /data/fleurs/ar_eg --model small --language ar --limit 100
    python evaluation/stt_wer.py --split /data/fleurs/en_us --model tiny.en --language en

Both sides are normalised before comparing, the way WER is conventionally reported: lower-case,
punctuation removed, digits as Western digits, and for Arabic the same folding search and the
guards use (``aura_core/text``: diacritics, tatweel, hamza / alef-maqsura / teh-marbuta
variants). The output is one JSON object: WER, real-time factor, counts, and the settings used.
"""

from __future__ import annotations

import argparse
import csv
import json
import re
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from aura_core.text import fold  # noqa: E402

_PUNCT = re.compile(r"[^\w\s]|_", re.UNICODE)


def normalise(text: str) -> list[str]:
    """Words to compare: folded, lower-case, punctuation removed."""
    return _PUNCT.sub(" ", fold(text).lower()).split()


def edit_distance(ref: list[str], hyp: list[str]) -> int:
    """Word-level Levenshtein distance (substitutions + deletions + insertions)."""
    previous = list(range(len(hyp) + 1))
    for i, r in enumerate(ref, 1):
        current = [i] + [0] * len(hyp)
        for j, h in enumerate(hyp, 1):
            current[j] = min(previous[j] + 1, current[j - 1] + 1, previous[j - 1] + (r != h))
        previous = current
    return previous[-1]


def load_split(split_dir: Path, limit: int | None) -> list[tuple[Path, str]]:
    """(audio path, reference transcript) pairs from a FLEURS ``test.tsv`` + ``test/*.wav``."""
    rows: list[tuple[Path, str]] = []
    with (split_dir / "test.tsv").open(encoding="utf-8", newline="") as handle:
        for record in csv.reader(handle, delimiter="\t", quoting=csv.QUOTE_NONE):
            if len(record) < 3:
                continue
            audio = split_dir / "test" / record[1]
            if audio.exists():
                rows.append((audio, record[2]))
            if limit and len(rows) >= limit:
                break
    return rows


def audio_seconds(path: Path) -> float:
    """Duration via faster-whisper's own decoder (FLEURS WAVs are 32-bit float, which the
    stdlib ``wave`` module cannot read)."""
    from faster_whisper.audio import decode_audio

    return len(decode_audio(str(path), sampling_rate=16_000)) / 16_000.0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--split", type=Path, required=True, help="dir with test.tsv and test/")
    parser.add_argument("--model", default="small", help="faster-whisper size, e.g. small, tiny.en")
    parser.add_argument("--language", default=None, help="language hint (ar, en); omit to detect")
    parser.add_argument("--limit", type=int, default=None, help="first N utterances only")
    parser.add_argument("--threads", type=int, default=0, help="CPU threads (0 = library default)")
    args = parser.parse_args(argv)

    from faster_whisper import WhisperModel

    pairs = load_split(args.split, args.limit)
    if not pairs:
        print(f"no utterances found under {args.split}", file=sys.stderr)
        return 2
    model = WhisperModel(args.model, device="cpu", compute_type="int8", cpu_threads=args.threads)

    errors = words = 0
    audio_total = decode_total = 0.0
    worst: list[tuple[float, str, str]] = []
    for path, reference in pairs:
        started = time.perf_counter()
        segments, _info = model.transcribe(str(path), language=args.language, beam_size=5)
        hypothesis = " ".join(s.text.strip() for s in segments)
        decode_total += time.perf_counter() - started
        audio_total += audio_seconds(path)
        ref, hyp = normalise(reference), normalise(hypothesis)
        distance = edit_distance(ref, hyp)
        errors += distance
        words += len(ref)
        worst.append((distance / max(1, len(ref)), reference, hypothesis))

    worst.sort(key=lambda item: item[0], reverse=True)
    print(json.dumps({
        "split": args.split.name,
        "model": args.model,
        "language_hint": args.language,
        "utterances": len(pairs),
        "reference_words": words,
        "wer": round(errors / max(1, words), 4),
        "audio_seconds": round(audio_total, 1),
        "decode_seconds": round(decode_total, 1),
        "real_time_factor": round(decode_total / max(1e-9, audio_total), 3),
        "worst_examples": [
            {"wer": round(w, 3), "reference": r, "hypothesis": h} for w, r, h in worst[:3]
        ],
    }, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
