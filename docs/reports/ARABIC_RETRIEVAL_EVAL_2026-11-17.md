# Arabic retrieval: embedder and relevance-gate evaluation

**Date:** 2026-11-17.
**Branch:** `anas/kind-rubin-dpm0sq`.
**Plan:** `docs/plans/arabic-language_plan.md` stage 4, owner decisions 10 and 17.
**Harness:** `aura_backend/evaluation/multilingual_retrieval_eval.py`.

**Evidence level: TESTED offline, in-memory container, development CPU. Not measured on a
deploy target, and no latency figure here is from production.**

## What was measured

Each configuration gets a fresh in-memory container, the documents are ingested through the
real `KnowledgeEngine` (chunking, BM25, dense vectors, RRF, rerank, relevance gate), and the
regression gate's own `RetrievalEvaluator` (k = 5) scores three suites:

| Suite | Questions | Documents |
|---|---|---|
| `docs_corpus` | 23 English questions (phase-a3) | the architecture ADRs, English |
| `docs_corpus_ar` | the same 23, asked in Egyptian Arabic | the same English ADRs |
| `arabic_corpus` | 60 Arabic questions | 12 Arabic documents (`evaluation/suites/arabic_corpus/`) |

Each suite also contains questions with no answer in the corpus. "No-answer correct" is the share
of those for which retrieval correctly returned nothing.

The relevance gate (`AURA_KNOWLEDGE_SCORE_THRESHOLD`, 0.35 deployed) is applied to the
**reranker's** score. That is why the reranker, not the embedder, decided most of the Arabic
results: the old word-overlap heuristic gives an Arabic question about an English passage a score
near zero, so the gate drops it however well the embedder found it.

## Results (recall@5; MRR in brackets)

| Embedder | Gate | English | Arabic asks English | Arabic corpus |
|---|---|---|---|---|
| `bge-small-en-v1.5` | heuristic @ 0.35 (**today**) | 0.70 (0.50) | 0.10 (0.10) | 0.60 (0.52) |
| `multilingual-e5-small` | heuristic @ 0.35 | 0.70 (0.50) | 0.10 (0.10) | 0.60 (0.52) |
| `multilingual-e5-small` | cross-encoder only @ 0 | 0.85 (0.85) | 0.75 (0.64) | 0.37 (0.38) |
| `multilingual-e5-small` | cross-encoder only @ 0.35 | 0.85 (0.85) | 0.65 (0.56) | 0.37 (0.38) |
| `bge-small-en-v1.5` | **either** @ 0.35 | 0.95 (0.92) | 0.80 (0.72) | 0.65 (0.60) |
| `multilingual-e5-small` | **either** @ 0.35 (**new default**) | **0.95 (0.92)** | **0.90 (0.79)** | **0.65 (0.60)** |
| `multilingual-e5-small` | either @ 0.5 | 0.85 (0.85) | 0.75 (0.64) | 0.45 (0.45) |

Cross-encoder: `cross-encoder/mmarco-mMiniLMv2-L12-H384-v1` (multilingual). "Either" is
`EitherGateReranker`: score = max(heuristic, sigmoid(cross-encoder logit)), so a passage passes
when either one accepts it.

No-answer correct, today vs the new default: English 0.33 vs 0.33, Arabic-asks-English 1.00 vs
1.00, Arabic corpus 0.20 vs 0.20. The new gate lets more right answers through without letting
more wrong ones through on these suites.

With the gate removed (threshold 0, heuristic), recall@5 is 1.00 / 0.80 / 0.87 for bge and
1.00 / 0.80 / 0.89 for E5, with no-answer correct at 0 everywhere. Retrieval itself finds the
passages; the gate is what threw them away.

## What the numbers show

- **The gate was the Arabic problem, not the embedder.** With the heuristic gate, switching to E5
  changes nothing measurable. The cross-encoder alone fixes cross-lingual questions but is too
  strict on Arabic-only passages (0.37). Combining them covers both.
- **English improves** (0.70 → 0.95 recall@5, MRR 0.50 → 0.92), so the R2.4 "English must not
  regress" condition holds.
- **E5 adds over bge only on Arabic questions about English documents** (0.80 → 0.90). That is
  the common case for an Arabic speaker using English technical documents, so E5 is the default
  per decision 10.
- **0.35 stays.** At 0.5 every suite loses recall.

## Decision

Both compose files now default to `AURA_EMBEDDING_MODEL=intfloat/multilingual-e5-small`,
`AURA_KNOWLEDGE_RERANK_PROVIDER=either` and
`AURA_KNOWLEDGE_RERANK_MODEL=cross-encoder/mmarco-mMiniLMv2-L12-H384-v1`. Railway reads its
dashboard, so the same four variables must be set there (`docs/deployment/control-plane.md`).

## Costs and what is not settled

- **A re-index is required.** The embedder's `model_id` is part of the index identity; reads
  refuse the old index until `scripts/rebuild_index.py` has built, gated and promoted a new one.
- **Latency is NOT MEASURED on a deploy target.** The cross-encoder scores every reranked
  candidate on CPU; the owner accepted roughly 100–300 ms per search (decision 17). It must be
  measured on the production host before this is called proven.
- **Model downloads on first start:** E5-small ~470 MB, the cross-encoder ~470 MB. Revisions are
  unpinned (GAP-031), like the models they replace.
- **Small suites.** 23 + 23 + 60 questions; one question moves English recall by about 0.04.
  The Arabic corpus is written for this evaluation, not drawn from real user documents.
- **Not evaluated:** `BAAI/bge-m3` (decision 10 keeps it as the future upgrade: 1024-dim, about
  5× the cost, a new collection).
- The harness reports nDCG above 1.0 in some runs (visible with the gate off), so nDCG is left
  out of this report; recall and MRR are unaffected. That is tracked as a separate fix.

## Reproduce

From `aura_backend/` (downloads the models on first use):

```bash
python evaluation/multilingual_retrieval_eval.py --out report.json \
  --embedder BAAI/bge-small-en-v1.5 --embedder intfloat/multilingual-e5-small \
  --reranker heuristic \
  --reranker cross-encoder/mmarco-mMiniLMv2-L12-H384-v1 \
  --reranker either:cross-encoder/mmarco-mMiniLMv2-L12-H384-v1 \
  --threshold 0.35 --threshold 0.5
```
