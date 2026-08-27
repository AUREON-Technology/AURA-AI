# Phase I — Transcript / Text NLP Layer

**Status: scaffolding landed; not proven as a product capability.** This document defines
Phase I against the same standing rule as the roadmap (`docs/AURA_UPGRADE_ROADMAP.md:19-21`):
a phase may not be marked complete without the evidence its exit criteria name.

---

## Goal

Add a **self-hosted text NLP layer** that runs on transcripts and typed chat messages
**before / alongside `AIKernel`**, not as a parallel shadow path.

Phase I covers:

- Transcript / message **normalization** (whitespace, filler collapse, trivial ASR artifacts).
- Optional **shallow intent / entity hooks** reusing `RuleEntityExtractor` patterns (stdlib
  regex; no spaCy, no NLTK, no hosted models).
- A clear **extension point** Phase H live voice calls after STT (`normalize_for_turn`) before
  building `TurnRequest(route="voice_live")`.
- Chat HTTP turns (`/chat`, `/chat/stream`) normalize the user message the same way so voice and
  typed chat share one preprocessing path.

Speakable-text shaping for TTS already exists (`adapters/spoken_text.py`) and is **out of
scope** here — Phase I is input-side NLP, not output prosody.

---

## Architecture

```
typed chat / STT transcript
        │
        ▼
   NlpEngine.process(text)     ← aura_core/nlp/ (engine; domain + ports only)
        │  normalize
        │  optional shallow intent
        │  optional RuleEntityExtractor-shaped entity pass (metadata only)
        ▼
   TurnRequest.message = normalized text
        │
        ▼
   AIKernel.stream() / .run()   ← unchanged safety, memory, tools, citations
```

Wiring is through `container.nlp` (lazy attribute on `Container`). The API adapter and the
live-voice session both call the same engine. Nothing bypasses the kernel for the turn itself.

---

## Self-hosting constraints

- **No hosted inference.** No new vendor HTTP clients for NLP.
- **No spaCy / NLTK** unless a future phase proves a fully local model path and documents the
  disk/CPU cost. Phase I is stdlib + existing `RuleEntityExtractor` patterns.
- Optional future local sentence-transformers use is allowed only via packages already in
  `requirements` and only behind an explicit setting — not required for Phase I exit.

---

## Implementation (scaffolding)

| Piece | Role |
| --- | --- |
| `aura_core/nlp/normalize.py` | Pure normalize helpers |
| `aura_core/nlp/intent.py` | Shallow regex intent labels (informational metadata) |
| `aura_core/nlp/engine.py` | `NlpEngine` + `NlpResult`; `normalize_for_turn()` extension point |
| `container.nlp` | Always built (pure; no binary gate) |
| `api/v3.py` `to_turn` | Normalizes `payload.message` via `container.nlp` |
| Phase H session | Calls `normalize_for_turn` after STT before `TurnRequest` |

No new I/O port: processing is CPU-local and deterministic. Entity extraction reuses
`RuleEntityExtractor` from the knowledge package (engine→engine is allowed); results are
**metadata only** and do not mutate the knowledge graph on chat turns.

Add `"nlp"` to `ENGINE_PACKAGES` in `tests/architecture/test_dependency_rules.py`.

---

## Exit criteria

Phase I scaffolding is **landed** when:

1. `docs/plans/phase-i-nlp.md` exists (this file).
2. `aura_core/nlp/` ships with unit tests in default `pytest` (no Docker, network, or model).
3. Chat `to_turn` and the live-voice session extension point both call the same normalizer.
4. Roadmap Phase I row exists and is marked **partial / scaffolding**, not done.
5. Architecture tests include `"nlp"` in `ENGINE_PACKAGES`.

Phase I is **proven** only when (future work; not claimed by scaffolding):

- A measured before/after on real ASR transcripts (WER-adjacent or human preference) under a
  named STT model.
- Intent labels shown to improve routing or tool selection with evidence, not anecdotes.

---

## Will not claim

- Classical NLP stack (spaCy pipelines, constituency parses, neural NER).
- That shallow intent replaces planner / kernel tool selection.
- That entity metadata from chat turns is written into the knowledge graph.
- Latency or quality gains on production voice without device / load measurements.
- That Phase H is done because Phase I normalizes text.

---

## Sequencing relative to Phase H

Phase I is independent of WebSocket transport. Live voice **must** call
`normalize_for_turn` after endpointed STT; chat already does. Implement Phase I scaffolding
before or with Phase H engine work so the session never invents a second preprocess path.
