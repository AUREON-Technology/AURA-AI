# Speech-to-text WER: Egyptian Arabic and English (GAP-020)

**Date:** 2026-11-17.
**Branch:** `anas/kind-rubin-dpm0sq`.
**Plan:** `docs/plans/arabic-language_plan.md` stage 4, decision 11.
**Harness:** `aura_backend/evaluation/stt_wer.py`.

**Evidence level: TESTED offline on real recorded speech. Not measured on the production
host.** The decode times below come from a 4-core Intel Xeon @ 2.10 GHz development container,
not the VPS or Railway.

## What was measured

- **Audio:** Google FLEURS `test` splits, read speech recorded by native speakers. `ar_eg` is
  Egyptian Arabic and `en_us` is American English. Each run uses the first 100 utterances of the
  split: 1,071 s of audio and 1,883 reference words for Arabic, 956 s and 2,199 words for
  English. FLEURS is read text, so it is cleaner than conversational speech. Treat the Arabic
  number as optimistic for colloquial, spontaneous Egyptian.
- **Engine:**
  - faster-whisper 1.2.1, CTranslate2 4.8.1.
  - CPU, `int8`, beam size 5, the same settings the gateway's `FasterWhisperEngine` uses.
- **Normalisation** is applied to both sides before comparing, the conventional way:
  - lower-case, punctuation removed;
  - Arabic folded with `aura_core.text.fold`, which removes diacritics and tatweel, unifies the
    hamza / alef-maqsura / teh-marbuta variants, and turns Arabic-Indic digits into ASCII.
- **WER** is word-level edit distance over reference words, summed across all utterances.
- **Real-time factor (RTF)** is decode seconds divided by audio seconds.

## Results

| Model | Language hint | Split | WER | RTF |
|---|---|---|---|---|
| `tiny.en` (production default until now) | `en` | en_us | **11.96%** | 0.10 |
| `small` | `en` | en_us | **6.46%** | 0.34 |
| `small` | `ar` | ar_eg | **24.96%** | 0.28 |
| `small` | none (auto-detect) | ar_eg | **24.96%** | 0.37 |

What the numbers show:

- **English gets better, not worse.** `small` halves the English error rate of `tiny.en`, the
  model it replaces.
- **Egyptian Arabic: about 1 word in 4 is wrong.**
  - Proper nouns and loanwords fail most often, e.g. «كاليفورنيا» became «كلوفانيا».
  - One utterance degenerated into a repeated-vowel hallucination («اووووو…»). That is a
    known Whisper failure mode on hard audio, and it alone is 1.0 WER on its utterance.
  - Usable for dictation and short commands, with mistakes. Large-v3-turbo on a GPU is the
    upgrade path (decision 11).
- **Auto-detection identified Arabic correctly.** It gave exactly the same WER as the explicit
  hint, at about 30% more decode time. So the app hints `ar` only when it is in Arabic
  (`voice_client.dart`) and lets the model detect otherwise.
- **Cost:** about 3.3× the CPU per second of audio compared with `tiny.en`. It is still well
  under real time on 4 cores, but it competes with the generator on a shared CPU box. RAM for
  `small` int8 is roughly 0.5 GB more, and the first start downloads about 480 MB of weights.

## Decision

The code default is now `small` (`VoiceSettings.stt_model`), on both deploy targets, because
Railway reads the code default when no variable is set. `AURA_VOICE_STT_MODEL=tiny.en` restores
the old model and makes live voice English-only again.

## What this does not settle (GAP-020 exit)

GAP-020's exit condition is a WER report "on the production ASR model". This report measures the
model that becomes production when this branch deploys, on a development CPU. GAP-020 closes when
both of these are true:

1. `small` is live: `/health` names `faster-whisper-small`.
2. The harness has run on the production host:

   ```bash
   python evaluation/stt_wer.py --split <fleurs>/ar_eg --model small --language ar --limit 100
   python evaluation/stt_wer.py --split <fleurs>/en_us --model small --language en --limit 100
   ```

   This gives the real-time factor under real load.

Not measured anywhere: spontaneous conversational Egyptian speech, noisy rooms, and the
live-voice VAD path (`voice/vad.py` is energy-based).

## Reproduce

Download `https://huggingface.co/datasets/google/fleurs/resolve/main/data/<lang>/test.tsv` and
`.../<lang>/audio/test.tar.gz`, then unpack the archive next to the TSV. Then run
`stt_wer.py` as above with `--model tiny.en --language en` and with `--model small`, adding
`--language` (or omitting it for auto-detect). Each run prints one JSON object with the numbers
in the table.
