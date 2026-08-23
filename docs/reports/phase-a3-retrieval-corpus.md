# Phase A3 — First Labelled Retrieval Corpus and CI Gate

Run 2026-08-23 against `c18ada8`, in-process against the in-memory adapter stack (no VPS, no
external services). Roadmap reference: [`docs/AURA_UPGRADE_ROADMAP.md`](../AURA_UPGRADE_ROADMAP.md)
Phase A3.

## Status: a real gate, with a real and imperfect number behind it

`aura_core/evaluation/harness.py` (`EvalSuite`, `RetrievalEvaluator`, `RegressionGate`) already
existed, complete, but nothing used it against real content — the only prior consumer,
`tests/unit/test_evaluation.py`, checks the metric arithmetic against hand-picked numbers, not a
corpus. This phase adds the first one: the repository's own architecture ADRs
(`docs/architecture/*.md`, ten documents) as the knowledge base, twenty-three graded queries
against them, and a pytest gate (`tests/e2e/test_docs_corpus_gate.py`) that fails the build if
retrieval quality drops below a recorded baseline.

The suite is retrieval-only. `AnswerEvaluator` also exists and is not exercised here:
`EchoProvider`'s default behavior is to echo the user's own last message rather than answer from
retrieved context (`aura_core/llm/providers/echo.py`), so its content, citation, and groundedness
assertions would be scoring noise, not the system. Gating those needs a real or scripted provider
wired into CI — left as follow-up, not silently dropped.

## What was built

| File | Purpose |
| --- | --- |
| `aura_backend/evaluation/suites/docs_corpus_sources.py` | Maps 10 document ids to the `docs/architecture/*.md` files and loads their content at test time. |
| `aura_backend/evaluation/suites/docs_corpus.jsonl` | 23 `EvalCase` rows: 20 grounded (2 per ADR, each `expected_substrings` entry verified as a literal, case-insensitive match against the source file), 3 out-of-corpus (`relevant_documents: []`, asserting an honest empty result). |
| `aura_backend/evaluation/suites/docs_corpus_baseline.json` | `RegressionGate.save_baseline()` output from the run below — the number the gate holds, not a target. |
| `aura_backend/tests/e2e/test_docs_corpus_gate.py` | Ingests the 10 documents into a real `Container`/`KnowledgeEngine`, runs `RetrievalEvaluator`, checks `RegressionGate`. |

No CI workflow change was needed. `backend-ci.yml`'s existing `aura_core suite (pytest)` step runs
`pytest tests`, and `pytest.ini`'s `testpaths = tests` already discovers the new file — it is
gating CI as of this commit.

## The measured baseline

```json
{
  "suite": "docs_corpus",
  "metrics": {
    "pass_rate": 0.6087,
    "empty_correct": 0.3333,
    "mrr": 0.3683,
    "ndcg": 0.5114,
    "precision_at_k": 0.2867,
    "recall_at_k": 0.65
  }
}
```

`k=5`, default settings (`hybrid_enabled=True`, BM25 + dense fused via RRF, `HashEmbeddingProvider`
as the embedder, `score_threshold=0.35`).

This is not the number a corpus author wants to publish, and it was not adjusted to look better.
Two things drive it, both structural rather than bugs:

1. **The default embedder is not semantic.** `HashEmbeddingProvider` is deterministic
   character-n-gram hashing — its own docstring says so, and `AuraSettings.validate()` refuses it
   in staging/production for this reason. A natural paraphrase of an ADR sentence ("What must be
   true of recall at 5 before a reindexed target index can replace the source index?" for
   ADR-007's "require target recall@5 ≥ source recall@5 − tolerance") does not reliably out-rank
   the other nine documents on lexical overlap alone; hybrid fusion helps but does not fully cover
   the gap. 7 of 20 grounded cases miss their document in the top 5.
2. **The 0.35 score threshold does not cleanly separate a 10-document corpus.** 2 of 3
   out-of-corpus queries ("What is the melting point of tungsten?", a pricing question) still
   return chunks above threshold — the least-dissimilar document in a small corpus can score just
   high enough by chance under a non-semantic embedder, even with zero real topical overlap.

The alternative — writing queries as near-verbatim copies of the source text until every case
passes — would have produced a green suite that measures nothing, exactly the failure mode the
roadmap's evidence-first phase exists to avoid. The number is published as-is.

## What the gate is for now

`RegressionGate`'s default tolerances (`pass_rate` 0.02, `recall_at_k` 0.02, `ndcg` 0.03) hold this
baseline steady: a future change to chunking, fusion weights, or the embedder that drops recall or
pass rate beyond tolerance fails CI with the specific metric and delta named. A change that
improves it is not blocked — the baseline is a floor, not a ceiling — and re-running
`RegressionGate.save_baseline()` after a deliberate improvement is the intended way to raise it.

## Exit criteria this closes, and what is still open

Closes the "add suites, wire the gate, use the existing harness" scope of the roadmap's A3 row.
Does **not** close the roadmap's own Phase A exit criteria in full:

- **Groundedness / citation precision** — still `Unmeasured`. Needs `AnswerEvaluator` gated on a
  real or scripted provider, not `EchoProvider`'s default echo.
- **Per-RAG-strategy comparison** — `knowledge.retrieve()`'s `strategy` field distinguishes
  hybrid vs. dense, not the higher-level modules in `aura_core/rag/{adaptive,agentic,base,
  expansion,leaves}/`; comparing those needs each wired as an alternative retrieval path in the
  harness, not implied by this suite.
- **Raising retrieval quality itself** — a real semantic embedder is the lever named in ADR-007's
  own design (an index-alias switch, not a rewrite); not attempted here, since Phase A measures.

## Environment note

Run with a scratch virtualenv (`pip install -r requirements.lock -r requirements-dev.txt`); the
sandbox's system Python has no writable site-packages for a broad install. `pytest tests -q` (438
existing tests plus the 3 new ones) passes in full, 2 pre-existing skips, no regressions.
