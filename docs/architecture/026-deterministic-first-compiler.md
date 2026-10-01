# ADR-026 — Deterministic-first compiler; the model labels and proposes only

**Status:** Proposed · **Date:** 2026-10-01 · **Track:** [AP](../roadmaps/AURA_ADAPTIVE_PLATFORM_ROADMAP.md) · **Keeps:** ADR-003

## Context

Semantic understanding of a foreign system looks like an LLM task, but production inference is
CPU `qwen2.5:3b` at about 5–10 tokens/s (GAP-011), and the self-hosting invariant
(`test_no_external_inference.py`) forbids hosted vendors. Model-driven structure would also be
non-reproducible and untestable.

## Decision

1. **Structure is deterministic:** catalogs, keys, constraints, grants, OpenAPI documents, code
   models and statistical profiles produce the AUSM. Same input, same digest.
2. **The model only labels and proposes:** display names, descriptions, metric candidates and
   merge hints, stored with `extractor="llm"` and confidence < 1. It never decides structure,
   permissions, grants or risk.
3. **Labeling is batch and offline**, never on the turn path. Results are cached by evidence hash,
   so re-discovery relabels only the diff. It uses the largest self-hosted model the inference
   plane offers (GPU per M4).
4. **A degraded mode is mandatory:** without a capable model, labels fall back to identifier
   humanization and the pipeline still completes. CI runs it CPU-only.
5. The self-hosting invariant applies to compile time exactly as to runtime.

## Alternatives rejected

- **GPU required for adaptation.** Couples a governance pipeline to inference availability.
- **A hosted model for compile time only.** Breaks ADR-003 and sends customer schema and document
  text to a third party.

## Consequences

- Label quality tracks M4. Function does not.
- Reproducibility makes twins and blueprints testable with goldens.
