# ADR-020 — AUSM (Universal Semantic Model) and the versioned Project Twin

**Status:** Proposed · **Date:** 2026-10-01 · **Track:** [AP](../roadmaps/AURA_ADAPTIVE_PLATFORM_ROADMAP.md)

## Context

Target systems describe the same concept in different shapes — a SQL table, a Mongo collection, a
REST path, a GraphQL type, a CSV file — with different names (`employee_id`, `emp_id`,
`employeeCode`). Reasoning directly over raw schemas would make every downstream component
source-specific. The closest existing structure, the closed-world `GraphSchema`
(`aura_core/domain/graph.py:196`), is one fixed schema per install for extracted facts, not a model
of an external system.

## Decision

1. **AUSM** has fourteen concepts: thirteen element kinds — Entity, Attribute, Relation, Event,
   Action, Workflow, Role, Permission, Document, Metric, State, Constraint, Source — plus the
   Provenance record every element carries (attached to elements, not an element itself). Stdlib
   dataclasses in `aura_core/domain/ausm.py`; per-kind shape rules (which kinds need a parent,
   which named refs) are enforced there and resolved against the twin by `ProjectTwin`.
2. Every element has a **stable id** (`source_id + locator`) and a mandatory **Provenance**
   (`source_id, locator, extractor, confidence, evidence_hash`), with confidence semantics copied
   from `FactProvenance`.
3. Cross-source merges (one Entity from a table, a path and a file) and identifier-variant merges
   are **confidence-scored proposals** based on profile similarity, reviewable by the Super Admin —
   never silent name guessing.
4. The **Project Twin** is an append-only sequence of AUSM snapshots, each with a digest.
   `TwinDiff` (added / removed / changed / rename candidates) is computed by stable id. Downstream
   artefacts link to AUSM ids as evidence, so drift propagates by link, not by re-analysis.

## Alternatives rejected

- **Extend `GraphSchema` to model external systems.** Different purpose (validated facts vs.
  observed structure) and closed-world by design.
- **Let the LLM produce the model.** Not reproducible, not diffable, not testable on CPU.

## Consequences

- Downstream components are written once against AUSM, independent of source technology.
- Rename detection is heuristic; a wrong rename is a proposal the admin rejects, not silent data loss.
