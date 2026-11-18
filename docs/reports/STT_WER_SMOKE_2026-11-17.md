# STT WER smoke run: faster-whisper `base` (2026-11-17)

**This is not GAP-020's evidence, and is superseded** by the FLEURS run of `small` on human speech (`docs/reports/STT_WER_ARABIC_ENGLISH_2026-11-17.md`, Arabic plan stage 4). The clips below are synthetic: text-to-speech output, not
people. A model that hears synthetic speech well can still fail on accents, noise and real
microphones. GAP-020 closes only on a report from human speech (e.g. Common Voice) on the model
production runs. This run shows that `scripts/measure_stt_wer.py` works end to end, and that the
multilingual path behaves sensibly in Arabic.

- Model: faster-whisper `base`, Hub head (unpinned), CPU int8.
- Host: development container, shared with a running test suite, so the real-time factors are
  indicative only.
- Branch: `anas/laughing-hamilton-glfevj` (Phase V).

## Arabic: Piper `ar_JO-kareem-medium`, the production Arabic voice

Input text carried full diacritics. The references are the same sentences without them.

| Language | Language sent | Clips | Words | WER | Real-time factor | Wrong language detected |
| --- | --- | ---: | ---: | ---: | ---: | ---: |
| ar | no (detected) | 4 | 18 | 22.2% | 0.47 | 0/4 |
| ar | yes | 4 | 18 | 22.2% | 0.33 | n/a |

- `par2.wav` (40%): ref «الاجتماع غدا في الساعة العاشرة» / got «الاشتماع غد في الساعة العاشرة»
- `par1.wav` (25%): ref «مرحبا كيف حالك اليوم» / got «مرحباً كيف حالك اليومة»
- `par3.wav` (20%): ref «أرسل التقرير قبل يوم الجمعة» / got «أرسل التقرير قبل يوم الجموعة»
- `par4.wav` (0%): ref «ذكرني بشراء الحليب مساء» / got «ذكرني بشراء الحليب مساء»

The errors are near misses on single letters. No word is missing.

## English and Arabic: espeak-ng

| Language | Language sent | Clips | Words | WER | Real-time factor | Wrong language detected |
| --- | --- | ---: | ---: | ---: | ---: | ---: |
| ar | no (detected) | 2 | 9 | 111.1% | 1.44 | 2/2 |
| ar | yes | 2 | 9 | 100.0% | 1.86 | n/a |
| en | no (detected) | 2 | 12 | 0.0% | 0.95 | 0/2 |
| en | yes | 2 | 12 | 0.0% | 0.59 | n/a |

espeak-ng's Arabic voice is close to unintelligible: it gets no vowel marks and its phonemes are
crude. These two Arabic rows measure the synthesiser, not Whisper. They are kept so nobody reruns
that pairing and reads it as a Whisper result. The first call of each run includes model load.

## What closes GAP-020

50 or more validated Common Voice clips per language, run through the size and revision Railway
actually serves:

```bash
python scripts/measure_stt_wer.py --manifest <manifest.jsonl> --model base \
    --revision <sha> --out ../docs/reports/STT_WER_<date>.md
```
